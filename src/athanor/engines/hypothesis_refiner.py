"""
ATHANOR Hypothesis Refiner Agent (Stage 2)

Multi-agent debate via sequential PydanticLLMClient calls.
Three roles -- Proponent, Antagonist, Judge -- debate in rounds.
The Judge emits a structured JSON verdict at the end.
"""
from typing import List, Optional, Tuple

from ..core.llm_gateway import LLMGateway
from ..core.state import ProjectState, HypothesisScorecard
from ..core.config_loader import config
from ..core.tracker import tracker
from ..core.json_parser import JSONParser
from ..core.prompt_builder import ground_truth_block, citation_instruction, provenance_instruction


# Shared evaluation rubric injected into every agent's system message.
# Updated to include calibration examples and a 6th scored dimension
# (Reviewer Appeal) directly relevant to Athanor's grant-writing purpose.
_EVAL_CRITERIA = """
You are a member of a panel of scientists debating the merits of a proposed hypothesis.
Evaluate based on the following criteria:

1. Novelty & Prior Art Gap (1-5)
   - 1 (Derivative): Rephrasing existing work, reiterating a known fact, or proposing what others have already done.
   - 3 (Incremental): Applies a known method to a new domain or dataset, with some novelty in the combination.
   - 5 (Pioneering): Cross-domain synthesis with no close precedent. The core insight is genuinely new.

2. Plausibility (1-5)
   - 1 (Fantasy): Violates known physical, chemical, or biological laws. Relies on mechanisms with no evidence.
   - 3 (Plausible but speculative): Consistent with known laws but relies on untested assumptions.
   - 5 (Grounded): Based on well-established principles with strong theoretical or empirical support.

3. Falsifiability (1-5)
   - 1 (Unfalsifiable): No experiment or observation could disprove the claim.
   - 3 (Testable with effort): A valid test exists but requires non-trivial design or multi-year data collection.
   - 5 (Readily testable): A straightforward experiment with existing methods and accessible data could confirm or refute within one funding cycle.

4. Executability (1-5) — BASED ON THE PI'S ACTUAL RESOURCES
   - Score this against what the PI actually has (see the Profile section), NOT against what any well-funded lab could do.
   - 1 (Infeasible): Requires instruments, datasets, or expertise the PI demonstrably lacks.
   - 3 (Feasible with effort): Achievable but requires acquiring new resources, collaborations, or pilot data.
   - 5 (Ready to go): The PI already has the compute, data, instruments, and team to execute within the proposed timeline.

5. Impact (1-5)
   - 1 (Trivial): Confirms what is already known. Minimal interest outside a narrow subfield.
   - 3 (Significant): Would change practice or understanding in one subfield if successful.
   - 5 (Transformative): Would change textbook claims, open a new subfield, or have broad societal/policy implications.

6. Reviewer Appeal (1-5) — will a grant reviewer fund this?
   - 1 (Unfundable): Unclear motivation, no urgency, no broader impact story.
   - 3 (Competitive): Well-motivated with a clear research question, but doesn't stand out from the pool.
   - 5 (Must-fund): Compelling narrative, obvious broader impacts, timely, fills a gap reviewers have been waiting for.

7. Safety (Binary Pass/Fail)
   - Fail: Dual-use risk (pathogens, weapons, mass surveillance, gain-of-function without oversight).
   - Pass: No significant dual-use concerns.
"""


class HypothesisRefiner:
    def __init__(self):
        self.ui = None

    def set_ui(self, ui):
        self.ui = ui

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run_stress_test(
        self,
        state: ProjectState,
        verbose: bool = True,
        hypothesis_override: Optional[str] = None,
        feedback: Optional[str] = None,
    ) -> ProjectState:
        """Run a multi-agent debate and update *state* with the verdict.

        Args:
            state: the project state to update in place.
            verbose: verbose logging.
            hypothesis_override: if provided, the debate uses this text as
                the starting hypothesis instead of state.initial_shower_thought.
                Used by the Hypothesis tab's Re-debate and Optimize flows so
                the original spark stays pinned while the user iterates.
            feedback: if provided, a free-text note from the human reviewer
                that is injected into the Proponent's system prompt as a
                hard constraint. Used by the Edit + Re-debate flow so the
                human's intent guides the next round.
        """
        starting_text = hypothesis_override or state.initial_shower_thought

        # Mock path — varied scores + synthetic rationales so tests exercise
        # the scorecard rendering path with realistic (not all-5s) data.
        if config.llm_provider == "mock":
            self.ui.log_status(level="verbose", message="    [Mock] Skipping Debate.")
            state.scorecard = HypothesisScorecard(
                novelty_score=4,
                novelty_rationale="Mock: The hypothesis applies an established methodology to a new domain, which is novel but not pioneering.",
                plausibility_score=4,
                plausibility_rationale="Mock: Consistent with known physical laws; relies on one untested assumption about signal scaling.",
                falsifiability_score=3,
                is_falsifiable=True,
                falsifiability_rationale="Mock: A valid test exists but requires multi-year data collection from upcoming surveys.",
                executability_score=4,
                executability_rationale="Mock: PI has the required compute and data access; collaboration with an instrumentalist would strengthen the approach.",
                impact_score=3,
                impact_rationale="Mock: Would change practice in one subfield if successful; broader impact depends on replication.",
                reviewer_appeal_score=4,
                reviewer_appeal_rationale="Mock: Timely topic, clear narrative, fills a gap reviewers have noted. Competitive but not a guaranteed must-fund.",
                is_safe=True,
                safety_rationale="Mock: No dual-use concerns identified.",
                unresolved_issues=[
                    "Signal-to-noise ratio at the predicted level has not been independently verified.",
                    "The proposed timeline assumes survey data release on schedule — any delay shifts the project by 6+ months.",
                ],
                final_score_avg=3.7,
                recommendation="PROCEED",
                verdict_summary=(
                    "Mock verdict: The hypothesis is scientifically grounded and addresses a real gap in the literature. "
                    "The PI has the expertise and resources to execute, though the timeline depends on external data availability. "
                    "The main risk is signal-to-noise — a pilot with existing data should be conducted before committing to the full analysis."
                ),
                feedback_summary="Mock Pass.",
            )
            state.refined_hypothesis = starting_text
            state.scorecard_stale = False
            return state

        try:
            return self._run_debate(state, verbose, starting_text=starting_text, feedback=feedback)
        except Exception as e:
            self.ui.log_status(level="error", message=f"    [Error] Debate Failed: {e}")
            return state

    # ------------------------------------------------------------------
    # Debate implementation
    # ------------------------------------------------------------------

    def _run_debate(
        self,
        state: ProjectState,
        verbose: bool,
        starting_text: Optional[str] = None,
        feedback: Optional[str] = None,
    ) -> ProjectState:
        shower_thought = starting_text or state.initial_shower_thought
        profile_parts = [
            f"{state.researcher_profile.name}, {state.researcher_profile.affiliation}.",
            f"Expertise: {state.researcher_profile.domain_expertise}",
        ]
        # Feed structured publication data into the debate so the
        # Proponent can cite specific prior work by the PI.
        p = state.researcher_profile
        if p.h_index is not None:
            profile_parts.append(f"h-index: {p.h_index}, total citations: {p.total_citations or 'unknown'}")
        if p.publications:
            top = [pub for pub in p.publications[:5] if pub.get("title")]
            if top:
                cites = [
                    f'"{pub["title"]}" ({pub.get("year", "?")}' +
                    (f', {pub["citations"]} cites' if pub.get("citations") else '') + ')'
                    for pub in top
                ]
                profile_parts.append(f"Key publications: {'; '.join(cites)}")
        if p.persona_motivation:
            profile_parts.append(f"Research philosophy: {p.persona_motivation}")
        profile_summary = " ".join(profile_parts)

        # Feedback-as-constraint: injected into ALL THREE agents during
        # re-debate so the full panel addresses the human's concerns, not
        # just the Proponent.
        feedback_block = ""
        if feedback and feedback.strip():
            feedback_block = (
                "\n\nHUMAN REVIEWER FEEDBACK (MUST address — this is a hard constraint):\n"
                f"{feedback.strip()}\n"
                "The reviewer's concerns override your defaults. Every agent in "
                "this debate MUST address these points directly."
            )

        ground_truth = ground_truth_block()
        search_inst = LLMGateway.get_search_instruction()
        prov_inst = provenance_instruction()
        cite = citation_instruction()

        # ── System prompts ────────────────────────────────────────
        # Each role gets a differentiated system prompt. The generic
        # _role_system helper from before is replaced with role-specific
        # prompts that give each agent a clear job description.

        shared_preamble = (
            f"{ground_truth}{search_inst}\n{prov_inst}\n"
            "MANDATORY: All arguments must be backed by evidence.\n"
            f"{cite}\n"
            f"{_EVAL_CRITERIA}"
        )

        proponent_system = (
            "You are the PROPONENT — the researcher's champion.\n"
            f"Your job: mount the strongest possible defense of the hypothesis.\n\n"
            f"Hypothesis: \"{shower_thought}\"\n\n"
            f"Researcher profile:\n{profile_summary}\n\n"
            "Strategy:\n"
            "- Cite specific prior publications by title and year to establish credibility.\n"
            "- Show how the PI's resources and expertise make execution feasible.\n"
            "- Anticipate and pre-empt the Antagonist's likely objections.\n"
            "- Frame the hypothesis's significance in terms a grant reviewer would find compelling.\n"
            f"{shared_preamble}"
            f"{feedback_block}"
        )

        antagonist_system = (
            "You are the ANTAGONIST — the rigorous scientific critic.\n"
            "Your job: find genuine flaws, not just offer polite suggestions.\n\n"
            "Structured critique checklist (address each):\n"
            "1. PRIOR ART: Has this or something very close already been done? "
            "Search your knowledge for published work that covers this ground. "
            "Name specific papers or groups if you can identify them.\n"
            "2. FEASIBILITY: Given the PI's actual resources (see the Proponent's "
            "profile claims), is this realistically achievable within a typical "
            "3-year funding cycle? Flag specific resource gaps.\n"
            "3. ASSUMPTIONS: What unstated assumptions does the hypothesis rely on? "
            "Flag each and ask how the Proponent justifies them.\n"
            "4. ALTERNATIVE EXPLANATIONS: If the predicted result is observed, "
            "could it be explained by something other than the hypothesis? "
            "Identify at least one confounding factor.\n"
            "5. HALLUCINATION CHECK: Does the Proponent cite papers, datasets, "
            "or tools that you cannot verify? Flag any that sound invented.\n\n"
            "Be adversarial but fair. Your goal is to strengthen the hypothesis "
            "by exposing its weaknesses, not to kill it.\n"
            f"{shared_preamble}"
            f"{feedback_block}"
        )

        judge_system = (
            "You are the JUDGE — the impartial evaluator.\n"
            f"{ground_truth}\n"
            "Evaluate the debate using the Shared Criteria below.\n"
            f"{_EVAL_CRITERIA}\n\n"
            "=== RESEARCHER PROFILE (for calibrating Executability) ===\n"
            f"{profile_summary}\n"
            "=== END PROFILE ===\n\n"
            "IMPORTANT: Your message MUST be a valid JSON block ONLY.\n"
            "Evaluate all criteria (1-5) based on the debate AND the researcher's "
            "actual profile. Provide DETAILED reasoning (2-3 sentences) for each "
            "score in the 'rationale' fields.\n"
            "MANDATORY: Preserve citations `[Category: Name](URL)` from the "
            "debate in your rationales. Do NOT strip them.\n\n"
            "SCORING CALIBRATION:\n"
            "- A 3 is the baseline for a competent, well-motivated proposal. "
            "Most hypotheses from experienced researchers should land around 3-4.\n"
            "- Reserve 5 for genuinely exceptional cases (truly novel, transformative, "
            "must-fund). A panel of 5s is as suspicious as a panel of 1s.\n"
            "- A 1 or 2 means a serious problem the Proponent failed to address.\n"
            "- Executability MUST be scored against the PI's real profile (above), "
            "not against an idealized well-funded lab.\n\n"
            'The "refined_hypothesis" should be a single, technically dense '
            "paragraph synthesizing the initial idea with the strongest defenses.\n"
            'The "unresolved_issues" list is MANDATORY. Each issue is a single '
            "sentence naming a specific scientific concern. If genuinely none "
            "remain, return [].\n"
            'The "verdict_summary" MUST be a one-paragraph synthesis explaining '
            "WHY you reached this decision.\n\n"
            "REFERENCE VERDICTS (for calibration — these are what well-calibrated\n"
            "panels produced for real proposals; use them as anchors):\n\n"
            "STRONG proposal (experienced PI, feasible, timely, funded):\n"
            "  novelty=4, plausibility=4, falsifiability=4, executability=5,\n"
            "  impact=4, reviewer_appeal=4 → PROCEED (avg 4.2)\n\n"
            "AVERAGE proposal (solid but incremental, some resource gaps):\n"
            "  novelty=3, plausibility=3, falsifiability=3, executability=3,\n"
            "  impact=3, reviewer_appeal=3 → PROCEED (avg 3.0)\n\n"
            "WEAK proposal (derivative, untested assumptions, unclear impact):\n"
            "  novelty=2, plausibility=2, falsifiability=2, executability=2,\n"
            "  impact=2, reviewer_appeal=2 → REFINE (avg 2.0)\n\n"
            "Return the verdict in this EXACT JSON format:\n"
            "{\n"
            '    "novelty_score": 4, "novelty_rationale": "...",\n'
            '    "plausibility_score": 4, "plausibility_rationale": "...",\n'
            '    "falsifiability_score": 3, "falsifiability_rationale": "...",\n'
            '    "executability_score": 4, "executability_rationale": "...",\n'
            '    "impact_score": 3, "impact_rationale": "...",\n'
            '    "reviewer_appeal_score": 4, "reviewer_appeal_rationale": "...",\n'
            '    "is_safe": true, "safety_rationale": "...",\n'
            '    "recommendation": "PROCEED",\n'
            '    "verdict_summary": "...",\n'
            '    "refined_hypothesis": "...",\n'
            '    "unresolved_issues": ["Issue 1", "Issue 2"]\n'
            "}"
            f"{feedback_block}"
        )

        # Run debate -------------------------------------------------------

        llm = LLMGateway.get_llm("HypothesisRefiner", verbose=verbose)
        # Use a separate model for the antagonist if configured
        antagonist_model = config.antagonist_model
        antag_llm = (
            LLMGateway.get_llm("Antagonist", verbose=verbose, model_name=antagonist_model)
            if antagonist_model != config.llm_model
            else llm
        )

        self.ui.log_status("    >> Running Debate...")
        tracker.start_timer("Stage 2: Debate")

        # debate_max_turns is total turns; each round has 3 turns
        rounds = max(1, config.debate_max_turns // 3)
        transcript: List[Tuple[str, str]] = []

        for rnd in range(rounds):
            # -- Proponent --
            proponent_prompt = self._build_turn_prompt(
                proponent_system, shower_thought, transcript, "Proponent"
            )
            prop_resp = llm.run_text(proponent_prompt)
            transcript.append(("Proponent", prop_resp.content))
            self._log_turn("Proponent", prop_resp.content, verbose)

            # -- Antagonist --
            antagonist_prompt = self._build_turn_prompt(
                antagonist_system, shower_thought, transcript, "Antagonist"
            )
            antag_resp = antag_llm.run_text(antagonist_prompt)
            transcript.append(("Antagonist", antag_resp.content))
            self._log_turn("Antagonist", antag_resp.content, verbose)

            # -- Judge (skip per-round judge on non-final rounds;
            #    the multi-judge panel runs after the last round) --
            if rnd < rounds - 1:
                # Mid-round: single judge for transcript continuity
                judge_prompt = self._build_turn_prompt(
                    judge_system, shower_thought, transcript, "Judge"
                )
                judge_resp = llm.run_text(judge_prompt)
                transcript.append(("Judge", judge_resp.content))
                self._log_turn("Judge", judge_resp.content, verbose)

        # =================================================================
        # MULTI-JUDGE PANEL — run the Judge N times independently on the
        # final transcript, then average scores and take majority vote on
        # the recommendation. This is the primary variance reducer.
        # =================================================================
        N_JUDGES = 3
        judge_prompt = self._build_turn_prompt(
            judge_system, shower_thought, transcript, "Judge"
        )

        self.ui.log_status(f"    >> Empaneling {N_JUDGES} judges (parallel)...")
        all_verdicts = []

        # Run judges in parallel — each gets its own LLM instance with
        # its own asyncio event loop, so there are no thread-safety
        # concerns. 3 concurrent Gemini calls is well under any rate limit.
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _run_one_judge(prompt_text: str):
            j_llm = LLMGateway.get_llm("Judge", verbose=False)
            resp = j_llm.run_text(prompt_text)
            return JSONParser.extract_object(resp.content)

        with ThreadPoolExecutor(max_workers=N_JUDGES) as pool:
            futures = {
                pool.submit(_run_one_judge, judge_prompt): j
                for j in range(N_JUDGES)
            }
            for future in as_completed(futures):
                j = futures[future]
                try:
                    verdict = future.result()
                    if verdict:
                        all_verdicts.append(verdict)
                        self.ui.log_status(
                            f"    >> Judge {j + 1}/{N_JUDGES}: "
                            f"avg={(sum(_safe_int(verdict, d) for d in _SCORE_DIMS) / len(_SCORE_DIMS)):.1f}",
                            level="verbose",
                        )
                except Exception as e:
                    self.ui.log_status(
                        f"    [!] Judge {j + 1} failed: {e}",
                        level="error",
                    )

        # Use the best transcript-continuity judge for the transcript log
        if all_verdicts:
            transcript.append(("Judge (panel)", str(all_verdicts[0])))

        tracker.stop_timer("Stage 2: Debate")

        # Average the panel's scores ----------------------------------------
        verdict_data = self._average_panel(all_verdicts) if all_verdicts else self._extract_verdict(transcript)

        if verdict_data:
            try:
                def _int(key, default=0):
                    try:
                        return int(verdict_data.get(key, default))
                    except (TypeError, ValueError):
                        return default

                nov = _int("novelty_score")
                pla = _int("plausibility_score")
                fal = _int("falsifiability_score")
                exe = _int("executability_score")
                imp = _int("impact_score")
                rev = _int("reviewer_appeal_score")

                scored = [nov, pla, fal, exe, imp, rev]
                avg = sum(scored) / len(scored) if scored else 0.0

                # ── Consistency check ────────────────────────────────
                # Override the recommendation if the scores don't support it.
                rec = str(verdict_data.get("recommendation", "REFINE")).upper().strip()
                if rec not in ("PROCEED", "REFINE", "REJECT"):
                    rec = "REFINE"

                # Rule 1: any dimension ≤ 2 → can't be PROCEED
                if rec == "PROCEED" and any(s <= 2 for s in scored):
                    rec = "REFINE"
                # Rule 2: average < 3.0 → can't be PROCEED
                if rec == "PROCEED" and avg < 3.0:
                    rec = "REFINE"

                state.scorecard = HypothesisScorecard(
                    novelty_score=nov,
                    novelty_rationale=str(verdict_data.get("novelty_rationale", "")),
                    plausibility_score=pla,
                    plausibility_rationale=str(verdict_data.get("plausibility_rationale", "")),
                    falsifiability_score=fal,
                    is_falsifiable=(fal >= 3),
                    falsifiability_rationale=str(verdict_data.get("falsifiability_rationale", "")),
                    executability_score=exe,
                    executability_rationale=str(verdict_data.get("executability_rationale", "")),
                    impact_score=imp,
                    impact_rationale=str(verdict_data.get("impact_rationale", "")),
                    reviewer_appeal_score=rev,
                    reviewer_appeal_rationale=str(verdict_data.get("reviewer_appeal_rationale", "")),
                    is_safe=bool(verdict_data.get("is_safe", True)),
                    safety_rationale=str(verdict_data.get("safety_rationale", "")),
                    recommendation=rec,
                    verdict_summary=str(verdict_data.get("verdict_summary", "")),
                    unresolved_issues=verdict_data.get("unresolved_issues") or [],
                    final_score_avg=round(avg, 2),
                )
                state.refined_hypothesis = verdict_data.get(
                    "refined_hypothesis", shower_thought
                )
                state.scorecard_stale = False
            except Exception as e:
                self.ui.log_status(
                    level="error", message=f"    [Error] Applying Scorecard: {e}"
                )
        else:
            self.ui.log_status(
                level="error",
                message="    [Warning] No valid JSON verdict found in debate transcript.",
            )

        return state

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_turn_prompt(
        system: str,
        hypothesis: str,
        transcript: List[Tuple[str, str]],
        role: str,
    ) -> str:
        parts = [system, "", f"Hypothesis under debate: {hypothesis}"]
        if transcript:
            parts.append("\n--- Debate transcript so far ---")
            for speaker, text in transcript:
                parts.append(f"\n[{speaker}]:\n{text}")
            parts.append("\n--- End of transcript ---")
        parts.append(f"\nNow respond as the {role}.")
        return "\n".join(parts)

    def _log_turn(self, role: str, content: str, verbose: bool) -> None:
        if verbose:
            preview = content[:60].replace("\n", " ") + "..." if len(content) > 60 else content
            self.ui.log_status(message=f"   [{role}] {preview}")
        else:
            self.ui.log_status(message=f"   {role} has spoken.")

    @staticmethod
    def _extract_verdict(transcript: List[Tuple[str, str]]):
        """Search the transcript (newest first) for a JSON verdict block."""
        for _speaker, content in reversed(transcript):
            data = JSONParser.extract_object(content)
            if data:
                return data
        return None

    @staticmethod
    def _average_panel(verdicts: list) -> dict:
        """Average scores from N independent judge verdicts.

        Numeric scores are rounded to the nearest integer after averaging.
        Rationale text is taken from the verdict whose scores are closest
        to the panel average (the "median judge"). Recommendation is
        majority vote. Unresolved issues are deduplicated and merged.
        """
        if not verdicts:
            return {}
        if len(verdicts) == 1:
            return verdicts[0]

        # Average numeric scores
        averaged: dict = {}
        for dim in _SCORE_DIMS:
            values = [_safe_int(v, dim) for v in verdicts]
            averaged[dim] = round(sum(values) / len(values))

        # Find the "median judge" — the verdict whose scores are closest
        # to the panel average. Take its rationale text and other fields.
        best_idx = 0
        best_dist = float("inf")
        for i, v in enumerate(verdicts):
            dist = sum(abs(_safe_int(v, d) - averaged[d]) for d in _SCORE_DIMS)
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        median = verdicts[best_idx]

        # Build the merged verdict
        result = dict(median)  # start with the median judge's full dict
        # Override scores with the averages
        for dim in _SCORE_DIMS:
            result[dim] = averaged[dim]

        # Majority vote on recommendation
        recs = [str(v.get("recommendation", "REFINE")).upper().strip() for v in verdicts]
        for candidate in ("PROCEED", "REFINE", "REJECT"):
            if recs.count(candidate) > len(verdicts) // 2:
                result["recommendation"] = candidate
                break

        # Merge + deduplicate unresolved issues
        all_issues = []
        seen: set = set()
        for v in verdicts:
            for issue in (v.get("unresolved_issues") or []):
                key = issue.strip().lower()[:80]
                if key not in seen:
                    seen.add(key)
                    all_issues.append(issue)
        result["unresolved_issues"] = all_issues

        # is_safe: conservative — if ANY judge says unsafe, the panel
        # verdict is unsafe
        result["is_safe"] = all(v.get("is_safe", True) for v in verdicts)

        return result


# Score dimension keys used by the multi-judge panel averaging
_SCORE_DIMS = [
    "novelty_score", "plausibility_score", "falsifiability_score",
    "executability_score", "impact_score", "reviewer_appeal_score",
]


def _safe_int(d: dict, key: str, default: int = 0) -> int:
    """Extract an int from a dict, coercing gracefully."""
    try:
        return int(d.get(key, default))
    except (TypeError, ValueError):
        return default
