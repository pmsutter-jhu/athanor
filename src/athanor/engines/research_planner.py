"""
ATHANOR Planner Agent Module.

Stage 3: takes a refined hypothesis and decomposes it into a concrete,
actionable research plan (DAG of executable nodes).

Uses sequential PydanticLLMClient calls for Draft -> Review -> Judge ->
Revise refinement loop.
"""
import json
import os
import re
from typing import Optional

from ..core.config_loader import config
from ..core.tracker import tracker
from ..core.llm_gateway import LLMGateway
from ..core.json_parser import JSONParser
from ..core.prompt_builder import ground_truth_block, provenance_instruction
from ..core.state import (
    ProjectState, Task as ATHANORTask, DAGNode, Artifact,
    ExecutionSpec, ComputeRequirements, NodeKind, TaskType, LedgerEntry,
)
from ..core.graph_validator import GraphValidator


class ResearchPlanner:
    def __init__(self, model_name: str = None):
        self.model_name = model_name or config.llm_model
        self.llm = LLMGateway.get_llm(name="ResearchPlanner")

        antagonist_name = config.antagonist_model
        if antagonist_name != self.model_name:
            self.reviewer_llm = LLMGateway.get_llm(
                name="PlanReviewer", model_name=antagonist_name
            )
        else:
            self.reviewer_llm = self.llm

        self.ui = None

    def set_ui(self, ui):
        self.ui = ui

    @staticmethod
    def _build_resource_context(state, profile) -> str:
        """Format the resource constraints for the planner system prompt.

        Prefers structured ResearchResources from state.resources; falls back
        to profile.available_resources free text; falls back to a generic
        academic baseline.
        """
        if state.resources:
            try:
                from ..ingest import ResearchResources
                rr = ResearchResources.model_validate(state.resources)
                return rr.to_planner_constraints()
            except Exception:
                pass

        if profile and profile.available_resources:
            return f"RESOURCES AVAILABLE:\n{profile.available_resources}"

        return (
            "RESOURCES AVAILABLE:\n"
            "- COMPUTE: Standard institutional HPC (assume modest GPU access)\n"
            "- PERSONNEL: PI only (no declared team)\n"
            "- DATA/INSTRUMENTS: None declared — propose only tasks that work with public datasets"
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def decompose_hypothesis(
        self,
        state: ProjectState,
        verbose: bool = True,
        feedback: Optional[str] = None,
    ) -> ProjectState:
        """Decompose the hypothesis into a plan DAG.

        Args:
            state: project state to update in place.
            verbose: verbose logging.
            feedback: optional free-text note from the human. When a re-plan
                is triggered from the Plan tab, this is injected into the
                Drafter's system prompt so the new plan honors the user's
                constraints (e.g. "uses too much HPC", "add an ablation").
        """
        hypothesis = state.refined_hypothesis or state.initial_shower_thought

        profile = state.researcher_profile
        if profile:
            team_size = len(profile.team_members) + 1
            team_context = f"Team Size: {team_size} (PI + {len(profile.team_members)} members). Team: {', '.join(profile.team_members)}."
            # Build a richer expertise context from structured fields
            expertise_parts = [f"Team Expertise: {profile.domain_expertise}"]
            if profile.h_index is not None:
                expertise_parts.append(
                    f"PI metrics: h-index {profile.h_index}, "
                    f"{profile.total_citations or '?'} total citations, "
                    f"{profile.recent_publication_count or '?'} publications in last 5 years"
                )
            if profile.career_timeline.get("career_stage"):
                expertise_parts.append(
                    f"Career stage: {profile.career_timeline['career_stage']} "
                    f"(PhD {profile.career_timeline.get('phd_year', '?')})"
                )
            if profile.collaborators:
                collab_names = [c.get("name", "") for c in profile.collaborators[:5] if c.get("name")]
                if collab_names:
                    expertise_parts.append(f"Key collaborators: {', '.join(collab_names)}")
            if profile.important_connections:
                expertise_parts.append(f"Network: {profile.important_connections[:300]}")
            expertise_context = ". ".join(expertise_parts)
        else:
            team_context = "Team Size: Unknown (Assume small team of 1-3)."
            expertise_context = "Expertise: Generic Scientist."

        # Resource context: prefer structured ResearchResources if present,
        # else fall back to the unstructured profile.available_resources blob.
        resource_context = self._build_resource_context(state, profile)

        # Scorecard context: if Stage 2 ran, surface any low scores or
        # Scorecard risk flags → actionable plan constraints.
        # Not just context — explicit planning instructions.
        scorecard_context = ""
        if state.scorecard:
            sc = state.scorecard
            constraints = []
            if sc.executability_score and sc.executability_score <= 3:
                constraints.append(
                    f"EXECUTABILITY RISK (scored {sc.executability_score}/5): "
                    f"{sc.executability_rationale}\n"
                    "  → MUST include a feasibility-establishing step (pilot study, "
                    "proof-of-concept, or collaborator consultation) BEFORE committing "
                    "the main budget. Use established protocols over novel methods."
                )
            if sc.falsifiability_score and sc.falsifiability_score <= 3:
                constraints.append(
                    f"FALSIFIABILITY RISK (scored {sc.falsifiability_score}/5): "
                    f"{sc.falsifiability_rationale}\n"
                    "  → MUST include a concrete falsification step with pre-registered "
                    "decision criteria. Specify what observation would DISPROVE the "
                    "hypothesis and include a CHECKPOINT node before full execution."
                )
            if sc.novelty_score and sc.novelty_score <= 2:
                constraints.append(
                    f"NOVELTY RISK (scored {sc.novelty_score}/5): "
                    f"{sc.novelty_rationale}\n"
                    "  → MUST include a literature review step that explicitly maps "
                    "the gap this work fills. Consider how to DIFFERENTIATE from "
                    "existing work in the plan structure."
                )
            if sc.unresolved_issues:
                constraints.append(
                    "UNRESOLVED ISSUES FROM STAGE 2 DEBATE:\n"
                    + "\n".join(f"  - {issue}" for issue in sc.unresolved_issues[:5])
                    + "\n  → Design plan steps that directly address each unresolved issue."
                )
            if constraints:
                scorecard_context = (
                    "\n=== HYPOTHESIS RISK CONSTRAINTS (from Stage 2 — these are HARD requirements) ===\n"
                    + "\n\n".join(constraints)
                    + "\n=== END RISK CONSTRAINTS ===\n"
                )

        # Preliminary work context — what the PI already has that the
        # plan can reuse instead of re-creating from scratch.
        preliminary_context = ""
        prior_items = [pw for pw in state.preliminary_work if pw.role == "prior_work"]
        prelim_items = [pw for pw in state.preliminary_work if pw.role == "preliminary_result"]
        if prior_items or prelim_items:
            parts = []
            if prior_items:
                parts.append("PI'S PRIOR WORK (can be cited or built upon — don't reinvent):")
                for pw in prior_items[:8]:
                    parts.append(f"  - [{pw.kind or '?'}] {pw.title} ({pw.date or '?'}): {pw.description[:150]}")
            if prelim_items:
                parts.append("PRELIMINARY RESULTS ALREADY DONE (tasks can start from here):")
                for pw in prelim_items[:5]:
                    parts.append(f"  - [{pw.kind or '?'}] {pw.title}: {pw.description[:150]}")
            preliminary_context = "\n" + "\n".join(parts) + "\n"

        if not os.getenv("GEMINI_API_KEY") or config.llm_provider == "mock":
            return self._mock_decomposition(state)

        search_protocol = LLMGateway.get_search_instruction()
        provenance_protocol = provenance_instruction()
        ground_truth = ground_truth_block()

        # -- Shared prompt fragments --
        drafter_system = self._drafter_system(
            search_protocol, provenance_protocol, ground_truth,
            team_context, resource_context, expertise_context,
            feedback=feedback,
        ) + scorecard_context + preliminary_context
        reviewer_system = self._reviewer_system(
            search_protocol, provenance_protocol, ground_truth,
            team_context, resource_context,
        )

        # =================================================================
        # PHASE 1: INITIAL DRAFT
        # =================================================================
        self.ui.log_status(message="    Generating Initial Draft...")
        self.ui.log_status(message="    >> Planner is writing first draft...")

        draft_task = self._draft_task_description(
            hypothesis, team_context, resource_context, expertise_context,
        )

        tracker.start_timer("Stage 3: Draft")
        current_plan = self.llm.run_text(
            f"{drafter_system}\n\n{draft_task}"
        ).content
        tracker.stop_timer("Stage 3: Draft")

        # =================================================================
        # PHASE 2: REFINEMENT LOOP
        # =================================================================
        max_iter = config.planner_max_revisions
        self.ui.log_status(f"    Entering Refinement Loop (Max {max_iter})...")

        previous_plan = None
        convergence_reached = False
        final_critique = "No review generated."

        for i in range(1, max_iter + 1):
            self.ui.log_status(message=f"    >> Round {i}: Reviewing plan...")

            # -- REVIEW --
            if previous_plan:
                review_instruction = (
                    f"You are reviewing a REVISION (Iteration {i}).\n\n"
                    f"**PREVIOUS PLAN (Iteration {i - 1})**:\n{previous_plan}\n\n"
                    f"**CURRENT PLAN (Iteration {i})**:\n{current_plan}\n\n"
                    "**DIFFERENTIAL REVIEW**: Only deep-verify CHANGED/NEW parts. "
                    "For unchanged parts, only check logical connections."
                )
            else:
                review_instruction = f"Review this Initial Draft:\n{current_plan}\n\nPerform FULL VERIFICATION."

            review_task = (
                f"{review_instruction}\n\n"
                "Perform an INDEPENDENT VERIFICATION of the Evidence Gap.\n"
                "- For HUMAN tasks: aggressively search for datasets/code that allow AGENT.\n"
                "- For AGENT tasks: verify cited tools exist.\n"
                "- For HYBRID tasks: verify human oversight is needed.\n"
                "- CHECK FOR OVERKILL.\n\n"
                "Produce output as JSON:\n"
                '{"summary": "...", "critical_issues": ["..."], "recommendations": ["..."]}'
            )

            tracker.start_timer(f"Stage 3: Round {i}")
            critique = self.reviewer_llm.run_text(
                f"{reviewer_system}\n\n{review_task}"
            ).content
            tracker.stop_timer(f"Stage 3: Round {i}")

            # -- JUDGE --
            self.ui.log_status(message=f"    >> Round {i}: Judging convergence...")
            judge_prompt = (
                f"You review feedback on a research plan.\n"
                f"{ground_truth}\n"
                f"Feedback:\n{critique}\n\n"
                "Output exactly one of: REVISE, REVISE (ESCALATE), or CONVERGED."
            )
            verdict = self.llm.run_text(judge_prompt).content.strip().upper()

            # Diminishing-returns guard
            if i > 1 and previous_plan and "CONVERGED" not in verdict:
                len_diff = abs(len(current_plan) - len(previous_plan)) / max(len(previous_plan), 1)
                if len_diff < 0.05:
                    self.ui.log_status(
                        message=f"    >> Round {i}: Minimal changes ({len_diff * 100:.1f}%). Forcing convergence."
                    )
                    verdict = "CONVERGED"

            if "CONVERGED" in verdict:
                self.ui.log_status(f"    >> Round {i}: Converged!")
                convergence_reached = True
                if critique and len(critique) > 10:
                    state.red_team_issues.append({
                        "stage": "Stage 3: Planning",
                        "issue": f"Round {i} Review (Converged)",
                        "details": critique,
                    })
                break

            # -- REVISE --
            escalation_msg = ""
            if "ESCALATE" in verdict:
                self.ui.log_status(f"    >> Round {i}: ESCALATING to Zenodo...")
                escalation_msg = "\n\nAUTHORIZATION: Use Zenodo/Mendeley Data for deep datasets."
            else:
                self.ui.log_status(message=f"    >> Round {i}: Revising...")

            final_critique = critique

            revise_prompt = (
                f"{drafter_system}\n\n"
                f"Original Plan:\n{current_plan}\n\nCritique:\n{critique}{escalation_msg}\n\n"
                "Update the plan. Structure: **CHANGES MADE** + **REVISED PLAN** + **PROVENANCE**."
            )

            self.ui.log_status(message=f"    >> Round {i}: Planner is revising...")
            tracker.start_timer(f"Stage 3: Round {i}")
            previous_plan = current_plan
            current_plan = self.llm.run_text(revise_prompt).content
            tracker.stop_timer(f"Stage 3: Round {i}")

        if not convergence_reached:
            self.ui.log_status(f"    [!] WARNING: Max iterations ({max_iter}) reached.")
            state.red_team_issues.append({
                "stage": "Stage 3: Planning",
                "issue": "Plan failed to converge within iteration limit.",
                "details": final_critique,
            })
            state.add_ledger_entry(LedgerEntry(
                stage="Stage 3: Planning",
                agent="Planner",
                description=f"Planning Loop Limit Reached. Unresolved: {final_critique[:200]}...",
            ))

        # =================================================================
        # HUMAN SOVEREIGNTY LOOP
        # =================================================================
        while config.human_in_the_loop:
            self.ui.log_status("\n" + "=" * 60 + "\n   HUMAN SOVEREIGNTY REVIEW\n" + "=" * 60)
            user_feedback = self.ui.get_input(
                "Review the plan above. Any revisions? (Press Enter for approval)"
            )
            if user_feedback.lower() in ("approve", "yes", "y", "ok", ""):
                self.ui.log_status("   > User approved.")
                break

            self.ui.log_status(level="verbose", message="      > Incorporating Human feedback...")
            revise_prompt = (
                f"{drafter_system}\n\n"
                f'URGENT: The user requested: "{user_feedback}"\n'
                "Implement immediately. Non-negotiable.\n\n"
                f"Current Plan:\n{current_plan}"
            )
            current_plan = self.llm.run_text(revise_prompt).content

        # =================================================================
        # ARCHITECTURE REFINEMENT
        # =================================================================
        self.ui.log_status("    Architecture Refinement (Defining Inputs/Outputs)...")
        arch_prompt = (
            "You are a Systems Architect. For each task in the plan, define:\n"
            "- WORKFLOW: detailed step-by-step instructions\n"
            "- IN/OUT ARTIFACTS: specific names (e.g., 'flux_table.csv')\n"
            "- EXECUTION SPEC: exact script/tool or human protocol\n"
            "Ensure Output(Task N) -> Input(Task N+1).\n\n"
            f"PLAN:\n{current_plan}"
        )
        tracker.start_timer("Stage 3: Architect")
        # Long prompt (full plan text) + long output — the default 240s timeout
        # is not enough for rich plans on real Gemini. Bumped to 600s.
        current_plan = self.llm.run_text(arch_prompt, timeout=600).content
        tracker.stop_timer("Stage 3: Architect")

        # =================================================================
        # PROVENANCE EXTRACTION
        # =================================================================
        provenance_log = "Provenance embedded in Plan."
        try:
            if "## Provenance" in current_plan:
                provenance_log = "## Provenance" + current_plan.split("## Provenance")[1]
        except Exception:
            pass

        try:
            match = re.search(r"```json_rejected\s*(\[.*?\])\s*```", current_plan, re.DOTALL)
            if match:
                state.rejected_alternatives = json.loads(match.group(1))
        except Exception as e:
            self.ui.log_status(
                level="verbose",
                message=f"    [Warning] Could not parse rejected alternatives: {e}",
            )

        state.add_ledger_entry(LedgerEntry(
            stage="Stage 3: Planning",
            agent="Planner/Scribe",
            description=provenance_log,
        ))

        # =================================================================
        # FINAL JSON CONVERSION
        # =================================================================
        self.ui.log_status("    Finalizing JSON...")
        json_prompt = self._json_conversion_prompt(current_plan)

        tracker.start_timer("Stage 3: Finalize")
        # JSON conversion takes the full arch-refined plan AND the richer
        # schema with all A+B fields — a single large call. Match the arch
        # refinement timeout so we're not the bottleneck on live Gemini.
        json_str = self.llm.run_text(json_prompt, timeout=600).content
        tracker.stop_timer("Stage 3: Finalize")

        # Parse JSON into DAG
        try:
            tasks_data = JSONParser.extract_array(json_str)
            if not tasks_data:
                raise ValueError("No valid task data found.")

            new_dag = []
            name_to_id = {}

            for t_data in tasks_data:
                node_type_str = t_data.get("execution_spec", {}).get("executor_type", "AGENT").upper()
                if node_type_str == "HYBRID":
                    n_type = TaskType.HYBRID
                elif node_type_str == "HUMAN":
                    n_type = TaskType.HUMAN
                else:
                    n_type = TaskType.AGENT

                exec_data = t_data.get("execution_spec", {}) or {}
                compute_spec = None
                cs_data = exec_data.get("compute_spec")
                if isinstance(cs_data, dict):
                    try:
                        compute_spec = ComputeRequirements(
                            cpu_hours=cs_data.get("cpu_hours"),
                            gpu_hours=cs_data.get("gpu_hours"),
                            gpu_type=cs_data.get("gpu_type"),
                            storage_gb=cs_data.get("storage_gb"),
                            memory_gb=cs_data.get("memory_gb"),
                            human_hours=cs_data.get("human_hours"),
                            wall_clock_days=cs_data.get("wall_clock_days"),
                            notes=cs_data.get("notes") or "",
                        )
                    except Exception:
                        compute_spec = None

                spec = ExecutionSpec(
                    executor_type=n_type,
                    agent_role=exec_data.get("agent_role") or t_data.get("assigned_agent"),
                    script_path=exec_data.get("script_path"),
                    protocol_description=exec_data.get("protocol_description"),
                    compute_spec=compute_spec,
                )

                # Coerce-None-to-default: dict.get() returns None when the key
                # is present-with-null (e.g. Gemini emitting "source_node": null),
                # NOT the default. Use `or` to fall through to the default.
                def _s(d, k, default):
                    v = d.get(k)
                    return v if v is not None else default

                inputs = [
                    Artifact(
                        name=_s(inp, "name", "Unknown"),
                        data_type=_s(inp, "data_type", "File"),
                        format=_s(inp, "format", "Any"),
                        source_node_id=_s(inp, "source_node", ""),
                    )
                    for inp in (t_data.get("inputs") or [])
                ]
                outputs = [
                    Artifact(
                        name=_s(out, "name", "Unknown"),
                        data_type=_s(out, "data_type", "File"),
                        format=_s(out, "format", "Any"),
                        source_node_id="SELF",
                    )
                    for out in (t_data.get("outputs") or [])
                ]

                # Parse node kind (default TASK if unspecified or unknown)
                kind_str = str(t_data.get("kind") or "TASK").upper().strip()
                try:
                    node_kind = NodeKind(kind_str)
                except ValueError:
                    node_kind = NodeKind.TASK

                # Coerce list-valued reviewer fields, tolerating None / strings
                def _as_list(v):
                    if v is None:
                        return []
                    if isinstance(v, list):
                        return [str(x) for x in v if x]
                    return [str(v)]

                risks = _as_list(t_data.get("risks"))
                mitigations = _as_list(t_data.get("mitigations"))
                alternatives = _as_list(t_data.get("alternatives_considered"))

                target_month = t_data.get("target_month")
                try:
                    target_month = int(target_month) if target_month is not None else None
                except (TypeError, ValueError):
                    target_month = None

                node = DAGNode(
                    name=_s(t_data, "name", "Unnamed Node"),
                    description=_s(t_data, "description", ""),
                    inputs=inputs,
                    outputs=outputs,
                    execution_spec=spec,
                    justification=_s(t_data, "justification", ""),
                    workflow=_s(t_data, "workflow", ""),
                    dependencies=t_data.get("dependencies") or [],
                    kind=node_kind,
                    risks=risks,
                    mitigations=mitigations,
                    alternatives_considered=alternatives,
                    target_month=target_month,
                    is_deliverable=bool(t_data.get("is_deliverable") or False),
                    deliverable_kind=t_data.get("deliverable_kind"),
                    deliverable_description=t_data.get("deliverable_description"),
                )
                name_to_id[node.name] = node.id
                new_dag.append(node)

            state.dag = new_dag

            # Resolve dependencies
            for node in state.dag:
                node.dependencies = [
                    name_to_id[d] for d in node.dependencies if d in name_to_id
                ]
                for inp in node.inputs:
                    if inp.source_node_id in name_to_id:
                        inp.source_node_id = name_to_id[inp.source_node_id]

            errors = GraphValidator.validate(state.dag)
            if errors and verbose:
                self.ui.log_status(
                    level="verbose",
                    message=f"    [DAG Warning] {len(errors)} validation errors",
                )
                for e in errors[:5]:
                    self.ui.log_status(level="verbose", message=f"    - {e}")

            # ── Post-JSON repair pass ───────────────────────────────
            # Check for nodes with empty enrichment fields and fill them
            # via a focused LLM call. This catches the common case where
            # the JSON conversion drops fields to save tokens.
            sparse_nodes = [
                n for n in state.dag
                if not n.risks or not n.mitigations or (n.is_deliverable and not n.deliverable_description)
            ]
            if sparse_nodes:
                self.ui.log_status(
                    f"    Repairing {len(sparse_nodes)} nodes with missing enrichment...",
                    level="verbose",
                )
                try:
                    self._repair_sparse_nodes(state.dag, sparse_nodes)
                except Exception as e:
                    self.ui.log_status(
                        f"    [!] Repair pass failed (non-fatal): {e}",
                        level="error",
                    )

            # ── Resource budget validation ──────────────────────────
            # Check if the aggregate compute spec exceeds the PI's
            # declared resources and warn if so.
            budget = state.aggregate_compute_budget()
            if budget.get("cpu_hours") and profile and hasattr(profile, "available_resources"):
                try:
                    from ..ingest import ResearchResources
                    if state.resources:
                        rr = ResearchResources.model_validate(state.resources)
                        if rr.compute.compute_hours_year and budget["cpu_hours"]:
                            if budget["cpu_hours"] > rr.compute.compute_hours_year * 1.5:
                                self.ui.log_status(
                                    f"    [!] Plan requires ~{budget['cpu_hours']:,.0f} CPU-h but PI "
                                    f"declares ~{rr.compute.compute_hours_year:,.0f} CPU-h/year. "
                                    f"Consider scaling down or requesting more compute.",
                                    level="verbose",
                                )
                except Exception:
                    pass

            # Legacy task mapping
            state.tasks = []
            for node in state.dag:
                legacy = ATHANORTask(
                    id=node.id,
                    name=node.name,
                    description=node.description,
                    assigned_to=node.execution_spec.agent_role or node.execution_spec.executor_type.value,
                    task_type=node.execution_spec.executor_type,
                    input_requirements=str([f"{i.name} ({i.format})" for i in node.inputs]),
                    expected_output=str([f"{o.name} ({o.format})" for o in node.outputs]),
                    justification=node.justification,
                    suggested_workflow=node.workflow,
                    dependencies=node.dependencies,
                )
                state.add_task(legacy)

        except Exception as e:
            self.ui.log_status(level="error", message=f"Error parsing PM output: {e}")
            self.ui.log_status(
                level="verbose",
                message=f"    [Debug] Raw Output:\n{json_str[:500]}...",
            )

        self._generate_summaries_and_abstract(state, verbose)
        return state

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    @staticmethod
    def _drafter_system(search_protocol, provenance_protocol, ground_truth,
                        team_context, resource_context, expertise_context,
                        feedback: Optional[str] = None):
        feedback_block = ""
        if feedback and feedback.strip():
            feedback_block = (
                "\n\nHUMAN REVIEWER FEEDBACK (MUST address in the new plan):\n"
                f"{feedback.strip()}\n"
                "The reviewer's concerns override your defaults. Treat them as "
                "hard constraints — restructure the plan to accommodate them."
            )
        return (
            "You are a Research Planner. You build Directed Acyclic Graphs (DAGs) "
            "of scientific research steps. Your plans are used for grant proposals "
            "AND as living plans that researchers execute and track.\n"
            f"{search_protocol}\n{provenance_protocol}\n{ground_truth}\n"
            "BACKCASTING: Start from the final artifact (paper / dataset / "
            "characterized material / trained model / validated method) and work "
            "backwards to preliminary steps.\n"
            f"SCOPE: {team_context}\nRESOURCES: {resource_context}\nEXPERTISE: {expertise_context}\n\n"
            "EXECUTOR TYPES (orthogonal to node kind):\n"
            "- AGENT: automated code, analysis, simulation, or model inference\n"
            "- HUMAN: physical work — wet lab, field collection, instrument use, "
            "clinical work, human judgment, manuscript writing\n"
            "- HYBRID: a human oversees or directs an automated step\n\n"
            "NODE KINDS (orthogonal to executor):\n"
            "- TASK (default): produces an artifact (data, sample, analysis, text)\n"
            "- CHECKPOINT: a human-judgment gate that produces a decision "
            "(proceed / refine / abort), not a new artifact — use for go/no-go "
            "reviews, safety sign-offs, PI approvals\n"
            "- DATA_ACQUISITION: consumes an external source the project does not "
            "produce — public datasets, samples from a collaborator, observation "
            "time on a shared instrument, archival material\n\n"
            "MODALITY: Be specific to the research domain. For computational work, "
            "cite exact libraries, datasets, and file formats. For wet-lab work, "
            "cite protocols, reagents, instruments, and catalog numbers. For field "
            "or clinical work, cite sites, cohorts, and inclusion criteria. Artifacts "
            "can be Files, Values, PhysicalSamples, Protocols, Equations, or any "
            "other scientific product — not only digital outputs.\n\n"
            "EVERY NODE MUST HAVE: risks (what could go wrong), mitigations (how "
            "you'll handle them), alternatives_considered (what else you thought of "
            "and why you chose this), and a target_month (relative start time, 0 = "
            "month 1 of the project). Mark reviewer-facing products as deliverables."
            f"{feedback_block}"
        )

    @staticmethod
    def _reviewer_system(search_protocol, provenance_protocol, ground_truth,
                         team_context, resource_context):
        return (
            "You are a Plan Reviewer — a rigorous evaluator of research plans.\n"
            f"{search_protocol}\n{provenance_protocol}\n{ground_truth}\n\n"
            f"TEAM: {team_context}\n"
            f"RESOURCES: {resource_context}\n\n"
            "STRUCTURED REVIEW CHECKLIST — address each item:\n\n"
            "1. GRAPH VALIDITY: Are there cycles? Orphan nodes? Missing dependencies?\n"
            "2. FEASIBILITY: Can the PI actually execute every task with the declared "
            "resources and team? Flag any task that requires instruments, data, or "
            "expertise the PI doesn't have. Suggest alternatives.\n"
            "3. RISK CREDIBILITY: Does every task have CONCRETE risks (not 'data "
            "quality issues' but 'sequencing coverage <30x reduces variant call "
            "confidence')? Are mitigations matched to risks?\n"
            "4. CHECKPOINT QUALITY: Does every CHECKPOINT node have a clear decision "
            "rule (proceed / refine / abort)? Is the decision criteria pre-registered?\n"
            "5. DATA_ACQUISITION VALIDITY: Are external data sources actually "
            "available? Is access confirmed or assumed?\n"
            "6. TASK JUSTIFICATION: Is every HUMAN task justified (can't be automated)? "
            "Is every AGENT task realistic (tools exist)?\n"
            "7. OVERKILL GUARD: Are there tasks that do more than the hypothesis "
            "requires? Flag unnecessary complexity.\n"
            "8. LAZY PLANNING GUARD: Are tasks too vague? 'Analyze data' is not a "
            "plan step; 'Run binned likelihood analysis on the stacked signal using "
            "scipy.optimize' is.\n"
            "9. TIMELINE REALISM: Do the target_month values form a realistic "
            "schedule given the team size and compute access?\n"
            "10. DELIVERABLE COVERAGE: Will the plan produce at least one "
            "reviewer-facing deliverable (publication, dataset, software release)?\n\n"
            "Produce output as JSON:\n"
            '{"summary": "...", "critical_issues": ["..."], '
            '"recommendations": ["..."], "checklist_pass": [true/false per item]}'
        )

    @staticmethod
    def _draft_task_description(hypothesis, team_context, resource_context, expertise_context):
        return (
            f"**CONSTRAINTS**: Team: {team_context}. Resources: {resource_context}. "
            f"Expertise: {expertise_context}.\n\n"
            f"Problem: {hypothesis}.\n"
            "Create a FULL RESEARCH PLAN as a DAG.\n"
            "For each node: Name, Executor (AGENT/HUMAN/HYBRID), Inputs, Outputs, Logic, Workflow.\n"
            "List Dependencies.\n\n"
            "Append **PROVENANCE** section with resources, decisions, and:\n"
            "```json_rejected\n[\"Method A (Rejected because X)\"]\n```"
        )

    @staticmethod
    def _json_conversion_prompt(plan_text):
        return (
            "Convert the research plan below into a STRICT JSON list of DAG Nodes.\n\n"
            f"PLAN:\n{plan_text}\n\n"
            "Output ONLY the JSON code block. Schema:\n"
            "[\n"
            '  {"name": "...", "description": "...",\n'
            '   "kind": "TASK" | "CHECKPOINT" | "DATA_ACQUISITION",\n'
            '   "inputs": [{"name": "...", "data_type": "File|Value|PhysicalSample|Protocol|Equation", "format": "CSV|FASTA|mg|μM|PDF|...", "source_node": "Node 1: ..."}],\n'
            '   "outputs": [{"name": "...", "data_type": "File|Value|PhysicalSample|Protocol|Equation", "format": "..."}],\n'
            '   "execution_spec": {\n'
            '     "executor_type": "AGENT" | "HYBRID" | "HUMAN",\n'
            '     "agent_role": "Data Scientist | Bench Biologist | PI | ...",\n'
            '     "script_path": "<path or null>",\n'
            '     "protocol_description": "<wet-lab protocol, field procedure, or null>",\n'
            '     "compute_spec": {\n'
            '       "cpu_hours": <number or null>,\n'
            '       "gpu_hours": <number or null>,\n'
            '       "gpu_type": <"A100"|"H100"|... or null>,\n'
            '       "storage_gb": <number or null>,\n'
            '       "memory_gb": <number or null>,\n'
            '       "human_hours": <number or null>,\n'
            '       "wall_clock_days": <number or null>,\n'
            '       "notes": "<free-text or empty>"\n'
            '     }\n'
            '   },\n'
            '   "dependencies": ["Node 1: ..."],\n'
            '   "justification": "<why this step exists>",\n'
            '   "workflow": "<step-by-step procedure>",\n'
            '   "risks": ["<specific risk 1>", "<specific risk 2>"],\n'
            '   "mitigations": ["<matched to risks>"],\n'
            '   "alternatives_considered": ["<alt 1: why rejected>", "<alt 2: why rejected>"],\n'
            '   "target_month": <integer, 0 = month 1 of project, or null>,\n'
            '   "is_deliverable": <true/false>,\n'
            '   "deliverable_kind": <"paper"|"dataset"|"artifact"|"knowledge"|"resource"|"method"|"software"|"protocol"|... or null>,\n'
            '   "deliverable_description": "<what the reviewer will see, or null>"\n'
            '  }\n'
            "]\n\n"
            "DEPENDENCY RULE: extract from inputs' source_node fields.\n"
            "COMPUTE_SPEC RULE: estimate REALISTIC numbers based on the resources "
            "the PI actually has. Use null for fields that don't apply (e.g. "
            "gpu_hours=null for a pure-CPU or wet-lab task, human_hours=null for "
            "a pure AGENT task). Do not invent GPU allocations the PI doesn't have.\n"
            "KIND RULE: use CHECKPOINT for go/no-go reviews and PI approvals (these "
            "produce decisions, not new artifacts). Use DATA_ACQUISITION for external "
            "datasets, specimens from collaborators, or instrument time that already "
            "exists — the project does not 'run' these steps during execution. "
            "Default to TASK for everything else.\n"
            "RISKS/MITIGATIONS RULE: always populate both. A risk without a mitigation "
            "is a red flag to reviewers. Be concrete — 'equipment failure' is weak, "
            "'cryo-EM detector failure delays 4-6 weeks' is strong.\n"
            "ARTIFACT RULE: use PhysicalSample for wet-lab reagents, cell lines, "
            "specimens, purified proteins; Protocol for written lab procedures; "
            "File for data/code; Value for scalar measurements; Equation for models."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _generate_summaries_and_abstract(self, state: ProjectState, verbose: bool):
        if not state.tasks:
            return
        if state.tasks[0].input_summary and state.project_abstract:
            return

        self.ui.log_status("    >> Generating readable summaries...")

        BATCH_SIZE = 10
        all_summaries = []

        for i in range(0, len(state.tasks), BATCH_SIZE):
            batch = state.tasks[i: i + BATCH_SIZE]
            tasks_text = "\n".join(
                f"TASK_ID: {t.id}\nNAME: {t.name}\n"
                f"RAW_INPUT: {t.input_requirements}\nRAW_OUTPUT: {t.expected_output}"
                for t in batch
            )

            self.ui.log_status(
                level="verbose",
                message=f"   [Summarizer] Batch {i // BATCH_SIZE + 1} ({len(batch)} tasks)...",
            )

            prompt = (
                "You are a Technical Writer. For each TASK_ID, provide a 1-sentence "
                "executive summary of Input and Output.\n\n"
                f"Tasks:\n{tasks_text}\n\n"
                'Return JSON list: [{"id": "...", "input_summary": "...", "output_summary": "..."}]'
            )

            try:
                tracker.start_timer(f"Stage 3: Summary Batch {i // BATCH_SIZE + 1}")
                raw = self.llm.run_text(prompt).content
                tracker.stop_timer(f"Stage 3: Summary Batch {i // BATCH_SIZE + 1}")

                parsed = JSONParser.extract_array(raw)
                if parsed:
                    all_summaries.extend(parsed)
            except Exception as e:
                self.ui.log_status(level="error", message=f"   [Warning] Summarization failed: {e}")

        if all_summaries:
            count = 0
            for item in all_summaries:
                t = state.get_task(item.get("id"))
                if t:
                    t.input_summary = item.get("input_summary")
                    t.output_summary = item.get("output_summary")
                    count += 1
            self.ui.log_status(f"    >> Summaries generated for {count} tasks.")

        # Abstract & title
        if not state.project_abstract or state.project_name.startswith("new-research-idea"):
            self.ui.log_status("    >> Generating Title & Abstract...")
            pi_name = state.researcher_profile.name if state.researcher_profile else "Researcher"
            hyp = state.refined_hypothesis or state.initial_shower_thought

            prompt = (
                f"Generate a snappy TITLE (max 10 words) and ABSTRACT (150-200 words) "
                f"for a research project.\n"
                f"PI: {pi_name}\nHypothesis: {hyp}\nPlan: {len(state.tasks)} steps.\n\n"
                'Return JSON: {"title": "...", "abstract": "..."}'
            )

            try:
                tracker.start_timer("Stage 3: Abstract")
                raw = self.llm.run_text(prompt).content
                tracker.stop_timer("Stage 3: Abstract")

                data = JSONParser.extract_object(raw)
                if data:
                    state.project_abstract = data.get("abstract")
                    new_title = data.get("title")
                    if new_title:
                        state.project_name = new_title
                        self.ui.log_status(level="verbose", message=f"   > New Title: {new_title}")
                    self.ui.log_status(level="verbose", message=">> Abstract generated.")
            except Exception as e:
                self.ui.log_status(level="error", message=f"   [Warning] Abstract failed: {e}")

    def _repair_sparse_nodes(self, dag: list, sparse_nodes: list) -> None:
        """Fill missing risks, mitigations, and deliverable descriptions
        via a single focused LLM call. Modifies the nodes in place.

        This is a repair pass, not a creation pass. The node already has
        a name, description, and workflow — this just fills gaps the JSON
        conversion left empty.
        """
        node_descriptions = []
        for n in sparse_nodes[:10]:  # cap to keep the prompt bounded
            node_descriptions.append(
                f"- Node: {n.name}\n"
                f"  Description: {n.description[:200]}\n"
                f"  Kind: {n.kind.value if hasattr(n.kind, 'value') else n.kind}\n"
                f"  Has risks: {bool(n.risks)}\n"
                f"  Has mitigations: {bool(n.mitigations)}\n"
                f"  Is deliverable: {n.is_deliverable}\n"
                f"  Has deliverable_description: {bool(n.deliverable_description)}"
            )

        prompt = (
            "You are a Research Plan Quality Assurer. The following DAG nodes "
            "have incomplete enrichment fields. For each, provide the missing "
            "fields ONLY. Do NOT change existing non-empty fields.\n\n"
            "Nodes needing repair:\n"
            + "\n".join(node_descriptions) + "\n\n"
            "Return a JSON array, one object per node, in the same order:\n"
            "[\n"
            '  {"name": "...",\n'
            '   "risks": ["concrete risk 1", "concrete risk 2"],\n'
            '   "mitigations": ["matched to risk 1", "matched to risk 2"],\n'
            '   "deliverable_description": "what the reviewer sees (or null if not a deliverable)"}\n'
            "]\n\n"
            "RULES:\n"
            "- Risks must be CONCRETE (not 'data quality issues' but 'sequencing "
            "coverage <30x reduces variant call confidence').\n"
            "- Every risk MUST have a matching mitigation.\n"
            "- Only populate deliverable_description if the node IS a deliverable.\n"
            "- Return ONLY the JSON array. No prose. No code fences."
        )

        try:
            response = self.llm.run_text(prompt)
            repairs = JSONParser.extract_array(response.content) or []
        except Exception:
            return

        # Apply repairs to matching nodes
        name_to_node = {n.name: n for n in sparse_nodes}
        for repair in repairs:
            if not isinstance(repair, dict):
                continue
            name = repair.get("name", "")
            node = name_to_node.get(name)
            if not node:
                continue
            if not node.risks and repair.get("risks"):
                node.risks = [str(r) for r in repair["risks"] if r]
            if not node.mitigations and repair.get("mitigations"):
                node.mitigations = [str(m) for m in repair["mitigations"] if m]
            if node.is_deliverable and not node.deliverable_description and repair.get("deliverable_description"):
                node.deliverable_description = str(repair["deliverable_description"])

    def _mock_decomposition(self, state: ProjectState) -> ProjectState:
        """Populate state.dag (canonical) + state.tasks (legacy view) with a
        realistic plan that exercises every node field — kind, risks,
        mitigations, alternatives, target_month, deliverables, compute_spec,
        typed artifacts including PhysicalSample.

        The mock is deliberately NOT pure-computational so it exercises the
        wet-lab and data-acquisition paths. Real runs use the LLM planner
        and produce domain-appropriate plans from state.research_domain.
        """
        self.ui.log_status("\n--- MOCK MODE: GENERATING INSTANT PLAN ---")
        simulated = [
            {"name": "Literature Review & Prior-Art Search",
             "description": "Review published methods in the target domain and identify the methodological gap this project addresses.",
             "type": "AGENT", "agent_role": "Research Scientist", "kind": "TASK",
             "inputs": [("User Hypothesis", "Value", "Text", None)],
             "outputs": [("Annotated bibliography", "File", "BIB"), ("Gap analysis", "File", "Markdown")],
             "workflow": "1. Query OpenAlex and Semantic Scholar.\n2. Filter by citation count and recency.\n3. Draft gap-analysis memo.",
             "justification": "Prevents duplication and sharpens the novelty claim for Stage 4.",
             "deps": [],
             "compute": {"cpu_hours": 4, "wall_clock_days": 3},
             "risks": ["Key recent preprint missed during search", "Gap analysis overstates novelty"],
             "mitigations": ["Cross-check against Google Scholar alerts", "Have a collaborator independently review the memo"],
             "alts": ["Rely on prior manual reading (rejected: biased toward PI's reading history)"],
             "target_month": 0,
             "is_deliverable": False},

            {"name": "Acquire reference dataset / samples",
             "description": "Secure access to the external reference material the project builds on (public dataset, collaborator specimens, or archival record).",
             "type": "HUMAN", "agent_role": "PI", "kind": "DATA_ACQUISITION",
             "inputs": [],
             "outputs": [("Reference material", "PhysicalSample", "sample-set")],
             "workflow": "1. Contact source (data portal / collaborator / archive).\n2. Sign any data-use or MTA agreements.\n3. Transfer and verify.",
             "justification": "Anchors the project in an external ground-truth resource that the project does not itself produce.",
             "deps": [],
             "compute": {"human_hours": 8, "wall_clock_days": 14},
             "risks": ["MTA negotiation delays", "Sample shipment lost in transit", "Dataset version mismatch after release"],
             "mitigations": ["Start MTA discussions at month 0", "Use tracked shipment with backup aliquots", "Pin to a specific versioned release and archive a local copy"],
             "alts": ["Generate equivalent material in-house (rejected: prohibitive cost and 6-month delay)"],
             "target_month": 0,
             "is_deliverable": False},

            {"name": "Pilot study",
             "description": "Run a small-scale version of the core methodology to validate feasibility before committing the full budget.",
             "type": "HYBRID", "agent_role": "Bench Scientist", "kind": "TASK",
             "inputs": [
                 ("Annotated bibliography", "File", "BIB", "Literature Review & Prior-Art Search"),
                 ("Reference material", "PhysicalSample", "sample-set", "Acquire reference dataset / samples"),
             ],
             "outputs": [("Pilot results", "File", "CSV"), ("Pilot protocol", "Protocol", "Markdown")],
             "workflow": "1. Run 10% of planned sample size through the full pipeline.\n2. Record protocol deviations.\n3. Generate preliminary effect-size estimate.",
             "justification": "A pilot catches problems before they become expensive. Standard practice in every scientific modality.",
             "deps": ["Literature Review & Prior-Art Search", "Acquire reference dataset / samples"],
             "compute": {"cpu_hours": 30, "human_hours": 40, "wall_clock_days": 14, "memory_gb": 8},
             "risks": ["Pilot effect size too small to justify the full study", "Protocol requires substantial revision"],
             "mitigations": ["Pre-register the effect-size decision rule", "Build a 2-week protocol-revision buffer into the timeline"],
             "alts": ["Skip directly to full study (rejected: high budgetary risk)"],
             "target_month": 1,
             "is_deliverable": False},

            {"name": "Go/no-go review",
             "description": "PI and co-investigator jointly review pilot results and decide whether to proceed to the full study, revise the protocol, or abort.",
             "type": "HUMAN", "agent_role": "PI + Co-I", "kind": "CHECKPOINT",
             "inputs": [("Pilot results", "File", "CSV", "Pilot study")],
             "outputs": [("Go/no-go decision memo", "File", "PDF")],
             "workflow": "1. Review effect size against pre-registered rule.\n2. Discuss protocol revisions needed.\n3. Record formal decision in the provenance ledger.",
             "justification": "Explicit human checkpoint before committing the remaining 90% of the budget.",
             "deps": ["Pilot study"],
             "compute": {"human_hours": 6, "wall_clock_days": 3},
             "risks": ["Decision is rushed under time pressure"],
             "mitigations": ["Schedule the review meeting at least 2 weeks before main-study kickoff deadline"],
             "alts": ["Automatic proceed based on p-value threshold (rejected: violates human-sovereignty principle)"],
             "target_month": 3,
             "is_deliverable": False},

            {"name": "Primary study execution",
             "description": "Run the full-scale version of the methodology validated by the pilot.",
             "type": "HYBRID", "agent_role": "Bench Scientist + Data Scientist", "kind": "TASK",
             "inputs": [
                 ("Pilot protocol", "Protocol", "Markdown", "Pilot study"),
                 ("Reference material", "PhysicalSample", "sample-set", "Acquire reference dataset / samples"),
             ],
             "outputs": [("Primary dataset", "File", "HDF5"), ("Characterized samples", "PhysicalSample", "batch")],
             "workflow": "1. Execute the validated protocol on the full sample size.\n2. Record all deviations and timestamps.\n3. Apply standard QC checks at each step.",
             "justification": "The core experiment. Its size is justified by the pilot's effect-size estimate.",
             "deps": ["Go/no-go review"],
             "compute": {"cpu_hours": 400, "gpu_hours": 40, "gpu_type": "A100", "human_hours": 160, "storage_gb": 120, "memory_gb": 32, "wall_clock_days": 90},
             "risks": ["Instrument downtime", "Batch effects between sample acquisition windows", "Staff turnover mid-study"],
             "mitigations": ["Reserve backup instrument time at partner institution", "Randomize sample order and include batch controls", "Cross-train two team members on every protocol step"],
             "alts": ["Single-batch execution (rejected: cannot fit within instrument availability window)"],
             "target_month": 4,
             "is_deliverable": True,
             "deliverable_kind": "dataset",
             "deliverable_desc": "Primary project dataset, fully QC'd and deposited in a discipline-appropriate repository"},

            {"name": "Analysis & interpretation",
             "description": "Analyze the primary dataset, validate findings against the pilot, and interpret the results in the context of prior literature.",
             "type": "AGENT", "agent_role": "Data Scientist", "kind": "TASK",
             "inputs": [("Primary dataset", "File", "HDF5", "Primary study execution")],
             "outputs": [("Results figures", "File", "PNG"), ("Analysis notebook", "File", "IPYNB")],
             "workflow": "1. Apply pre-registered analysis plan.\n2. Run sensitivity analyses.\n3. Generate publication-quality figures.\n4. Draft interpretation against literature gap.",
             "justification": "Converts raw measurements into reviewer-legible findings and claims.",
             "deps": ["Primary study execution"],
             "compute": {"cpu_hours": 60, "gpu_hours": 20, "gpu_type": "A100", "human_hours": 80, "memory_gb": 64, "wall_clock_days": 28},
             "risks": ["Analysis reveals unanticipated confounders", "Pre-registered plan is insufficient for observed data shape"],
             "mitigations": ["Budget 2 weeks for unregistered exploratory analyses, clearly labeled as such", "Consult a statistician if the data shape deviates from expectations"],
             "alts": ["Post-hoc-only analysis (rejected: weakens inferential claims)"],
             "target_month": 7,
             "is_deliverable": True,
             "deliverable_kind": "knowledge",
             "deliverable_desc": "Analysis report with figures, interpretation, and pre-registered vs exploratory labeling"},

            {"name": "Manuscript preparation and submission",
             "description": "Draft the primary paper from the analysis outputs, circulate to co-authors, and submit to the target venue.",
             "type": "HUMAN", "agent_role": "PI + Co-authors", "kind": "TASK",
             "inputs": [("Results figures", "File", "PNG", "Analysis & interpretation")],
             "outputs": [("Submitted manuscript", "File", "PDF")],
             "workflow": "1. Draft methods and results from the analysis notebook.\n2. Write introduction and discussion.\n3. Co-author review rounds.\n4. Submit to target journal.",
             "justification": "The primary publication is the terminal deliverable for most grants.",
             "deps": ["Analysis & interpretation"],
             "compute": {"human_hours": 120, "wall_clock_days": 45},
             "risks": ["Target venue changes scope", "Co-author delays", "Reviewer requests major revisions requiring new experiments"],
             "mitigations": ["Identify two backup venues at project start", "Set firm co-author deadlines with 1-week grace", "Budget 3 months post-submission for revision-driven work"],
             "alts": ["Preprint-only release (rejected: tenure/promotion considerations)"],
             "target_month": 9,
             "is_deliverable": True,
             "deliverable_kind": "paper",
             "deliverable_desc": "Peer-reviewed publication of the primary findings"},
        ]

        # First pass: create nodes and capture name→id
        type_map = {"AGENT": TaskType.AGENT, "HUMAN": TaskType.HUMAN, "HYBRID": TaskType.HYBRID}
        kind_map = {"TASK": NodeKind.TASK, "CHECKPOINT": NodeKind.CHECKPOINT, "DATA_ACQUISITION": NodeKind.DATA_ACQUISITION}
        name_to_id = {}
        new_dag = []
        for t in simulated:
            ttype = type_map.get(t["type"], TaskType.AGENT)
            compute_spec = ComputeRequirements(**t.get("compute", {})) if t.get("compute") else None
            node = DAGNode(
                name=t["name"],
                description=t["description"],
                inputs=[],  # filled in second pass (needs name→id resolution)
                outputs=[
                    Artifact(
                        name=o[0], data_type=o[1], format=o[2], source_node_id="SELF",
                    )
                    for o in t["outputs"]
                ],
                execution_spec=ExecutionSpec(
                    executor_type=ttype,
                    agent_role=t["agent_role"],
                    compute_spec=compute_spec,
                ),
                justification=t["justification"],
                workflow=t["workflow"],
                dependencies=[],  # filled in second pass
                kind=kind_map.get(t.get("kind", "TASK"), NodeKind.TASK),
                risks=list(t.get("risks", [])),
                mitigations=list(t.get("mitigations", [])),
                alternatives_considered=list(t.get("alts", [])),
                target_month=t.get("target_month"),
                is_deliverable=bool(t.get("is_deliverable", False)),
                deliverable_kind=t.get("deliverable_kind"),
                deliverable_description=t.get("deliverable_desc"),
            )
            name_to_id[t["name"]] = node.id
            new_dag.append(node)

        # Second pass: resolve dep names → ids, and wire inputs' source ids
        for node, t in zip(new_dag, simulated):
            node.dependencies = [name_to_id[d] for d in t["deps"] if d in name_to_id]
            node.inputs = [
                Artifact(
                    name=i[0], data_type=i[1], format=i[2],
                    source_node_id=name_to_id.get(i[3], "") if i[3] else "",
                )
                for i in t["inputs"]
            ]

        state.dag = new_dag

        # Legacy flat view for code that still reads state.tasks
        state.tasks = []
        for node in new_dag:
            legacy = ATHANORTask(
                id=node.id,
                name=node.name,
                description=node.description,
                assigned_to=node.execution_spec.agent_role or node.execution_spec.executor_type.value,
                task_type=node.execution_spec.executor_type,
                input_requirements=str([f"{i.name} ({i.format})" for i in node.inputs]),
                expected_output=str([f"{o.name} ({o.format})" for o in node.outputs]),
                justification=node.justification,
                suggested_workflow=node.workflow,
                dependencies=node.dependencies,
            )
            state.tasks.append(legacy)

        # Mock abstract so the Plan tab has something to show
        if not state.project_abstract:
            state.project_abstract = (
                "Mock abstract: This project decomposes the hypothesis into a 5-step "
                "pipeline spanning literature review, data contract design, simulation, "
                "statistical analysis, and human review of the final results."
            )

        return state
