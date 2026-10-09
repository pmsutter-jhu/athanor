"""
ATHANOR Core Execution Engine.

This module contains the AthanorEngine class, which serves as the primary orchestrator 
for the research lifecycle (Stages 1-4). it manages state transitions, checkpointing, 
and coordinates between agents and assistants.
"""
import os
from src.athanor.core.state import ProjectState
from src.athanor.core.state_manager import StateManager
from src.athanor.engines.research_planner import ResearchPlanner
from src.athanor.engines.team_profiler import TeamProfiler
from src.athanor.assistants.scribe import Scribe
from src.athanor.core.config_loader import config
from src.athanor.core.tracker import tracker
from src.athanor.core.ui import UIProvider
from src.athanor.engines.hypothesis_refiner import HypothesisRefiner
from src.athanor.engines.grant_weaver import GrantWeaver
from src.athanor.engines.execution_tracker import ExecutionTracker
from src.athanor.engines.paper_writer import PaperWriter

STAGE_NAMES = {
    1: "RESEARCHER PROFILE",
    2: "HYPOTHESIS DEBATE & STRESS-TEST",
    3: "RESEARCH PLAN",
    4: "GRANT PROPOSAL",
    5: "EXECUTION TRACKING",
    6: "PAPER WRITING",
}

STAGE_ARTIFACTS = {
    1: "researcher_profile",
    2: "hypothesis_analysis",
    3: "research_plan",
    4: "grant_proposal",
    5: "execution_summary",
    6: "manuscript",
}


class AthanorEngine:
    """
    Core Orchestrator for ATHANOR.
    Manages the lifecycle of a research project, independent of the UI.
    """
    def __init__(self, ui: UIProvider, project_state: ProjectState = None, manager: StateManager = None):
        self.ui = ui
        self.state = project_state
        self.manager = manager if manager else StateManager()
        
        Scribe.set_ui(self.ui)

    def run(self, verbose: bool = False):
        """
        Main Execution Loop.
        """
        tracker.reset()
        self.ui.log_status("Starting ATHANOR Engine...", level="verbose")

        # --- Initialization / Resume ---
        if not self.state:
            # Check for existing project requirement
            if config.start_stage > 1:
                # 1. Try to load from fixed directory if set
                if self.manager.fixed_project_dir:
                    try:
                        self.ui.log_status(f"Resuming project from: {self.manager.fixed_project_dir}")
                        # ID is ignored in fixed mode load_state
                        self.state = self.manager.load_state("FIXED_MODE_AUTOLOAD")
                    except FileNotFoundError:
                        self.ui.log_status(f"Error: Start stage is {config.start_stage}, but no 'state.json' found in: {self.manager.fixed_project_dir}", level="error")
                        return
                    except Exception as e:
                        self.ui.log_status(f"Error loading state: {e}", level="error")
                        return
                else:
                    # 2. No fixed dir, and no state passed via CLI logic
                    self.ui.log_status(f"Error: Cannot start at Stage {config.start_stage} without an existing project state.", level="error")
                    self.ui.log_status("Please either:", level="error")
                    self.ui.log_status("  1. Use '--restart <project_id>' to resume a project.", level="error")
                    self.ui.log_status("  2. Set 'output_directory' in your config file.", level="error")
                    self.ui.log_status("  3. Set 'start_stage = 1' to begin a new project.", level="error")
                    return
            else:
                # Start Stage is 1, so creating a new project is expected
                self._initialize_project(verbose)
            
        # --- AUTO-PRUNE FOR DEV ---
        if self.state and config.start_stage > 0:
            # 1. Prune In-Memory State
            self.state.prune_from_stage(config.start_stage)
            
            # 2. Purge Filesystem Artifacts
            import shutil
            target_dir = self.manager.get_project_dir(self.state)
            
            # 3. Set up Trace Log
            log_path = os.path.join(target_dir, "trace.log")
            self.ui.set_log_file(log_path)
            
            if os.path.exists(target_dir):
                if config.start_stage == 1:
                    # COMPLETE PURGE
                    self.ui.log_status(f"[PURGE] Wiping directory for fresh start: {target_dir}")
                    try:
                        # If fixed dir, keep the dir but empty content? 
                        # Or safer: just delete contents.
                        for filename in os.listdir(target_dir):
                            file_path = os.path.join(target_dir, filename)
                            try:
                                if os.path.isfile(file_path) or os.path.islink(file_path):
                                    os.unlink(file_path)
                                elif os.path.isdir(file_path):
                                    shutil.rmtree(file_path)
                            except Exception as e:
                                self.ui.log_status(f"Failed to delete {file_path}. Reason: {e}", level="verbose")
                    except Exception as e:
                        self.ui.log_status(f"Error checking directory {target_dir}: {e}", level="error")
                
                else:
                    # PARTIAL PURGE (Stage N and Downstream)
                    # Mapping of stages to their artifact prefixes/folders
                    stage_artifacts = {
                        1: ["researcher_profile"],
                        2: ["hypothesis_analysis"],
                        3: ["research_plan", "figures"],
                        4: ["grant_proposal"]
                    }

                    to_purge = []
                    for stage_num in range(config.start_stage, 5):
                        if stage_num in stage_artifacts:
                            to_purge.extend(stage_artifacts[stage_num])

                    for item in to_purge:
                        # 1. Try as directory
                        item_path = os.path.join(target_dir, item)
                        if os.path.isdir(item_path):
                            self.ui.log_status(f"[PURGE] Clearing downstream folder: {item}/")
                            shutil.rmtree(item_path, ignore_errors=True)
                        
                        # 2. Try as file prefix (e.g. hypothesis_analysis.pdf, .json, .tex, .md)
                        try:
                            for f in os.listdir(target_dir):
                                if f.startswith(item + ".") or f == item:
                                    exts = (".json", ".pdf", ".tex", ".md", ".png")
                                    if any(f.endswith(ext) for ext in exts):
                                        file_to_del = os.path.join(target_dir, f)
                                        if os.path.isfile(file_to_del):
                                            os.remove(file_to_del)
                        except Exception:
                            pass

        # 3. Stabilize Project Identity
        project_id = self.manager.stabilize_project_identity(self.state)
        self.manager.save_state(self.state)
        
        # 4. Refresh UI to reflect purged state (remove ghost artifacts)
        self.ui.refresh_artifacts(self.state)

        # --- VALIDATION: Prerequisite Checks ---
        if config.start_stage > 3:
             if not self.state.dag:
                 self.ui.log_status(f"Error: Starting at Stage {config.start_stage} requires a Research Planning DAG (Stage 3).", level="error")
                 return
             # DAG must be structurally valid (no cycles, no missing deps)
             # before Stage 4 can run. Soft warnings like data contract
             # mismatches are surfaced in the Plan tab but do not block.
             from src.athanor.core.graph_validator import GraphValidator
             errors = GraphValidator.validate_errors_only(self.state.dag)
             if errors:
                 self.ui.log_status(
                     f"Error: Cannot proceed to Stage {config.start_stage} — the plan DAG has {len(errors)} blocking error(s):",
                     level="error",
                 )
                 for e in errors[:5]:
                     self.ui.log_status(f"  • {e}", level="error")
                 self.ui.log_status(
                     "Fix the plan (Re-plan with feedback from the Plan tab) before running Stage 4.",
                     level="error",
                 )
                 return
        if config.start_stage > 1:
             if not self.state.initial_shower_thought:
                  self.ui.log_status("Error: Project state corrupted (Missing initial thought).", level="error")
                  return

        # --- STAGE 1: Researcher Profile ---
        self.ui.check_interruption()
        if self._should_run_stage(1):
            self.ui.log_status(f"\n=== STAGE 1: {STAGE_NAMES[1]} ===")

            profiler_agent = TeamProfiler()
            profiler_agent.set_ui(self.ui)
            self.state = profiler_agent.build_profile(self.state, verbose=verbose)

            p = self.state.researcher_profile
            if config.human_in_the_loop:
                self.ui.log_status(f"    Profile built: {p.name} ({p.affiliation})")

            self._finalize_stage(1, project_id, verbose)

        elif config.end_stage >= 1:
            self.ui.log_status(f"\n=== STAGE 1: {STAGE_NAMES[1]} (LOADED/SKIPPED) ===")

            # Ensure project_id is synced from state
            if self.state and self.state.project_slug:
                project_id = self.state.project_slug


        # --- STAGE 2: Hypothesis Refiner ---
        self.ui.check_interruption()
        if self._should_run_stage(2):
            self.ui.log_status(f"\n=== STAGE 2: {STAGE_NAMES[2]} ===")
            
            tester = HypothesisRefiner()
            tester.set_ui(self.ui)
            
            accepted = False
            loop_count = 0
            
            while not accepted and loop_count < 3:
                self.ui.check_interruption()
                self.state = tester.run_stress_test(self.state, verbose=verbose)
                
                # Scorecard Display
                sc = self.state.scorecard
                if sc:
                    self.ui.log_status(f"    Debate Verdict: {sc.recommendation}")
                    
                    if config.human_in_the_loop:
                        self._display_scorecard(sc)

                    # Human Sovereignty
                    if config.human_in_the_loop:
                        choice = self.ui.request_approval(original=self.state.initial_shower_thought, refined=self.state.refined_hypothesis)
                    else:
                        choice = 'b' # Default accept refined

                    if choice == 'a':
                        self.state.refined_hypothesis = self.state.initial_shower_thought
                        accepted = True
                    elif choice == 'b':
                        accepted = True
                    elif choice == 'c':
                         if loop_count < 2:
                             self.ui.log_status(f"   > Retrying with Refined Hypothesis for Debate Round {loop_count+2}...")
                             self.state.initial_shower_thought = self.state.refined_hypothesis
                             loop_count += 1
                         else:
                             self.ui.log_status("   > Max loops reached. Accepting Refined.")
                             accepted = True
                    else:
                        accepted = True
                else:
                    break
            
            self._finalize_stage(2, project_id, verbose)

        elif config.end_stage >= 2:
            self.ui.log_status(f"\n=== STAGE 2: {STAGE_NAMES[2]} (LOADED/SKIPPED) ===")

        # --- STAGE 3: Research Planner ---
        self.ui.check_interruption()
        if self._should_run_stage(3) or (config.end_stage >=3 and not self.state.tasks):
             self.ui.log_status(f"\n=== STAGE 3: {STAGE_NAMES[3]} ===")

             pm_agent = ResearchPlanner()
             pm_agent.set_ui(self.ui)
             self.state = pm_agent.decompose_hypothesis(self.state, verbose=verbose)

             self._finalize_stage(3, project_id, verbose)

        elif config.end_stage >= 3:
             self.ui.log_status(f"\n=== STAGE 3: {STAGE_NAMES[3]} (LOADED/SKIPPED) ===")

        # --- STAGE 4: Grant Weaver / Implementation ---
        # --- STAGE 4: Grant Weaver ---
        self.ui.check_interruption()
        if self._should_run_stage(4):
            self.ui.log_status(f"\n=== STAGE 4: {STAGE_NAMES[4]} ===")
            rfp_target = config.rfp_target
            if not rfp_target:
                from src.athanor.data import mock_rfp_path, MOCK_RFP_NAME
                rfp_target = mock_rfp_path()
                self.ui.log_status(
                    f"    >> No RFP target set. Falling back to bundled mock RFP: {MOCK_RFP_NAME}",
                    level="warning",
                )

            # Funder profile (always set; defaults to "generic")
            self.state.funder_short_name = config.funder_short_name

            # Optional exemplar grant — ingest once and cache in state
            if config.exemplar_grant_path and not self.state.exemplar_grant:
                self.ui.log_status(
                    f"    >> Ingesting exemplar grant: {config.exemplar_grant_path}",
                )
                try:
                    from src.athanor.ingest import (
                        ingest_grant,
                        ResearchResources,
                        merge_exemplar_facilities_into_resources,
                    )
                    exemplar = ingest_grant(
                        config.exemplar_grant_path,
                        funder_short_name=self.state.funder_short_name,
                    )
                    self.state.exemplar_grant = exemplar.model_dump()
                    self.ui.log_status(
                        f"    >> Exemplar parsed: "
                        f"{len(exemplar.raw_excerpts)} excerpts, "
                        f"IDC={exemplar.budget.indirect_cost_rate or 'default'}",
                        level="verbose",
                    )

                    # Fold exemplar facilities/equipment/collaborators into
                    # state.resources so the planner and the writer both
                    # see them as available infrastructure.
                    existing = (
                        ResearchResources.model_validate(self.state.resources)
                        if self.state.resources
                        else None
                    )
                    merged = merge_exemplar_facilities_into_resources(
                        existing,
                        exemplar.institutional.model_dump(),
                    )
                    self.state.resources = merged.model_dump()
                except Exception as e:
                    self.ui.log_status(
                        f"    [!] Exemplar ingestion failed: {e}. Falling back to defaults.",
                        level="error",
                    )

            if rfp_target:
                weaver = GrantWeaver(rfp_path=rfp_target)
                weaver.set_ui(self.ui)

                self.ui.log_status("    >> Starting Grant Weaving process...")
                self.state = weaver.weave_proposal(self.state, verbose=verbose)

                self._finalize_stage(4, project_id, verbose)
            else:
                self.ui.log_status("No RFP provided. Skipping Stage 4.")

        # --- STAGE 5: Execution Tracker ---
        self.ui.check_interruption()
        if self._should_run_stage(5):
            self.ui.log_status(f"\n=== STAGE 5: {STAGE_NAMES[5]} ===")
            tracker_engine = ExecutionTracker()
            tracker_engine.set_ui(self.ui)
            self.state = tracker_engine.run(self.state, verbose=verbose)
            self._finalize_stage(5, project_id, verbose)

        # --- STAGE 6: Paper Writer ---
        self.ui.check_interruption()
        if self._should_run_stage(6):
            self.ui.log_status(f"\n=== STAGE 6: {STAGE_NAMES[6]} ===")
            try:
                writer = PaperWriter()
                writer.set_ui(self.ui)
                self.state = writer.draft_manuscript(self.state, verbose=verbose)
                self._finalize_stage(6, project_id, verbose)
            except Exception as e:
                self.ui.log_status(
                    f"    [Error] Paper drafting failed: {e}",
                    level="error",
                )

        # Persist this session to the lifetime tally before reporting metrics
        tracker.record_to_lifetime_tally()
        self.ui.display_metrics(tracker.get_metrics_data())

        if self.ui.is_web:
             self.ui.log_status("ATHANOR COMPLETE.", level="success")
        else:
             self.ui.log_status("\n" + "="*40 + "\n           ATHANOR COMPLETE           \n" + "="*40)
      
    def _initialize_project(self, verbose: bool):
        # 1. Input Shower Thought (if not in config)
        if config.initial_spark:
            shower_thought = config.initial_spark
            self.ui.log_status(f"[Config] Using pre-defined spark: {shower_thought}", level="verbose")
        else:
            default_thought = "I suspect that high-energy neutrinos might have a slight cross-section with heavy dark matter candidates."
            shower_thought = self.ui.get_initial_spark(default=default_thought)
        
        self.state = ProjectState(
            project_name="New-Research-Idea",
            initial_shower_thought=shower_thought
        )

    def _should_run_stage(self, stage_num: int) -> bool:
        return (config.start_stage <= stage_num and 
                config.end_stage >= stage_num and 
                (self.state.current_stage < stage_num))

    def _save_checkpoint(self, stage_num: int, project_id: str, verbose: bool):
        self.state.current_stage = stage_num
        filepath = self.manager.save_state(self.state, filename="state.json")
        if verbose:
            self.ui.log_status(f"    [System] State saved to: {filepath}", level="verbose")

    def _finalize_stage(self, stage_num: int, project_id: str, verbose: bool):
        """Checkpoint + generate reports + refresh artifacts."""
        self._save_checkpoint(stage_num, project_id, verbose)
        project_dir = self.manager.get_project_dir(self.state)
        if stage_num == 3:
            os.makedirs(os.path.join(project_dir, "figures"), exist_ok=True)
        base_path = os.path.join(project_dir, STAGE_ARTIFACTS[stage_num])
        Scribe.generate_reports(self.state, base_path, verbose=verbose, stage_num=stage_num)
        self.ui.refresh_artifacts(self.state)

    def _display_scorecard(self, sc):
        """Log the debate scorecard criteria."""
        criteria = [
            ("Novelty", sc.novelty_score, sc.novelty_rationale),
            ("Plausibility", sc.plausibility_score, sc.plausibility_rationale),
            ("Falsifiable", sc.falsifiability_score, sc.falsifiability_rationale),
            ("Executability", sc.executability_score, sc.executability_rationale),
            ("Impact", sc.impact_score, sc.impact_rationale),
            ("Reviewer Appeal", sc.reviewer_appeal_score, sc.reviewer_appeal_rationale),
            ("Safe", sc.is_safe, sc.safety_rationale),
        ]
        for i, (name, score, rationale) in enumerate(criteria, 1):
            self.ui.log_status(f"   {i}. {name}: {score}/5 - {rationale}", level="verbose")
        if sc.final_score_avg:
            self.ui.log_status(f"   Avg: {sc.final_score_avg:.1f}/5", level="verbose")
