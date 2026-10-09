"""
ATHANOR Paper Writer (Stage 6)

Stage 6 · Paper writing.

Drafts a structured research manuscript from the executed DAG, the
actual artifacts the human attached, and the deviation notes captured
during Stage 5. Unlike GrantWeaver (which writes prospectively from a
plan), PaperWriter writes retrospectively from what actually happened
— that's the Position 3 lifecycle payoff.

The manuscript uses the standard Abstract / Introduction / Methods /
Results / Discussion / Conclusion shape. Methods and Results are
grounded in the DAG: Methods is reconstructed from the workflow text
of each completed node plus any deviation notes, and Results is
assembled from attached artifacts + node output descriptions.

Like GrantWeaver, PaperWriter exposes:
  - draft_manuscript(state, feedback=None) — full pipeline
  - regenerate_manuscript_section(state, idx, feedback) — single section

Mock mode produces a deterministic 6-section canned manuscript so the
end-to-end pipeline runs in tests and demo mode without any API keys.
"""
from __future__ import annotations

from typing import List, Optional

from ..core.config_loader import config
from ..core.state import (
    ProjectState, Manuscript, ProposalSection, ExecutionStatus, LedgerEntry,
)
from ..core.llm_gateway import LLMGateway
from ..core.prompt_builder import ground_truth_block
from ..core.tracker import tracker


# =============================================================================
# Standalone helpers
# =============================================================================

def regenerate_manuscript_section(
    state: ProjectState,
    section_idx: int,
    feedback: str,
    ui=None,
) -> str:
    """Rewrite one manuscript section to address human feedback.

    Mirrors regenerate_proposal_section in grant_weaver.py: single LLM
    call, no Librarian, preserves voice and factual content of the rest.
    Returns the new content string; the caller assigns it.

    Mock mode returns the original content with a visible marker so
    tests stay deterministic.
    """
    if not state.manuscript or section_idx >= len(state.manuscript.sections):
        raise ValueError(f"No manuscript section at index {section_idx}")

    section = state.manuscript.sections[section_idx]

    if config.llm_provider == "mock":
        return (
            (section.content or "") + f"\n\n[MOCK PAPER REGENERATED · feedback: {(feedback or '').strip()}]"
        )

    other_titles = [
        s.title for i, s in enumerate(state.manuscript.sections) if i != section_idx
    ]

    # Ground the rewrite in the actual execution state
    methods_grounding = _build_methods_grounding(state)
    results_grounding = _build_results_grounding(state)

    parts: List[str] = [
        "You are revising ONE section of an in-progress research manuscript.",
        "Rewrite the section to address the author's feedback while preserving",
        "the voice and factual content of the rest of the paper.",
        "",
        f"=== SECTION: {section.title} ===",
        "CURRENT CONTENT:",
        section.content or "(empty)",
        "",
        "AUTHOR FEEDBACK (must address as hard constraints):",
        (feedback or "").strip() or "(none — general polish)",
        "",
        "OTHER SECTIONS IN THIS MANUSCRIPT (for coherence; do not duplicate):",
        ", ".join(other_titles) or "(none)",
        "",
    ]
    if methods_grounding:
        parts.append("=== WHAT WAS ACTUALLY DONE (from Stage 5 execution) ===")
        parts.append(methods_grounding)
        parts.append("=== END ===")
        parts.append("")
    if results_grounding:
        parts.append("=== ACTUAL ARTIFACTS PRODUCED ===")
        parts.append(results_grounding)
        parts.append("=== END ===")
        parts.append("")
    parts.append(
        "Output: the REVISED content for this section only. No section header. "
        "No commentary. No code fences. Just the new body text in Markdown."
    )

    llm = LLMGateway.get_llm("PaperWriterSectionRegenerator", verbose=False)
    if ui is not None:
        try:
            ui.log_status(f"    >> Regenerating manuscript section: {section.title}")
        except Exception:
            pass

    response = llm.run_text("\n".join(parts))
    new_content = (response.content or "").strip()
    if new_content.startswith("```"):
        lines = new_content.split("\n")
        new_content = "\n".join(lines[1:]).strip()
        if new_content.endswith("```"):
            new_content = new_content[:-3].strip()
    return new_content


def _build_methods_grounding(state: ProjectState) -> str:
    """Build a grounded methods summary from DONE DAG nodes.

    This is the core Position 3 move: the Methods section isn't
    invented by the LLM, it's reconstructed from the workflow text the
    user captured during Stage 3 + deviation notes from Stage 5.
    """
    if not state.dag:
        return ""
    lines: List[str] = []
    for node in state.topo_sorted_dag():
        if node.status not in (ExecutionStatus.DONE, ExecutionStatus.IN_PROGRESS):
            continue
        lines.append(f"- {node.name} ({node.execution_spec.executor_type.value if node.execution_spec else 'unknown'})")
        if node.description:
            lines.append(f"  Purpose: {node.description}")
        if node.workflow:
            lines.append(f"  Procedure: {node.workflow}")
        if node.deviation_notes:
            lines.append(f"  Deviation from plan: {node.deviation_notes}")
        if node.actual_wall_clock_days is not None:
            lines.append(f"  Actual duration: {node.actual_wall_clock_days:.1f} days")
    return "\n".join(lines)


def _build_results_grounding(state: ProjectState) -> str:
    """Build a results summary from attached artifacts + outputs."""
    if not state.dag:
        return ""
    lines: List[str] = []
    for node in state.topo_sorted_dag():
        if node.status != ExecutionStatus.DONE:
            continue
        if not (node.outputs or node.attached_artifact_paths):
            continue
        lines.append(f"- {node.name} produced:")
        for out in node.outputs:
            lines.append(f"    · {out.name} ({out.format})")
        for path in node.attached_artifact_paths:
            lines.append(f"    · [file] {path}")
    return "\n".join(lines)


# =============================================================================
# Paper Writer
# =============================================================================

class PaperWriter:
    """Drafts a manuscript from the executed DAG."""

    def __init__(self) -> None:
        self.ui = None
        self.llm = LLMGateway.get_llm(name="PaperWriter")

    def set_ui(self, ui) -> None:
        self.ui = ui

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def draft_manuscript(
        self,
        state: ProjectState,
        verbose: bool = True,
        feedback: Optional[str] = None,
    ) -> ProjectState:
        """Populate state.manuscript with a drafted paper.

        Args:
            state: project state, must have a non-empty DAG and a refined
                hypothesis. Execution state (Stage 5) is optional — a paper
                can be drafted from a plan that hasn't been executed, but
                the Methods/Results sections will be thinner.
            verbose: verbose logging.
            feedback: optional author feedback. When set, injected into the
                draft prompt as a hard constraint block.
        """
        if not state.dag:
            raise RuntimeError(
                "Paper drafting requires a DAG (Stage 3 must have run). "
                "No plan → nothing to write a methods section about."
            )

        if config.llm_provider == "mock":
            return self._mock_draft(state, feedback=feedback)

        try:
            return self._real_draft(state, verbose, feedback)
        except Exception as e:
            if self.ui is not None:
                self.ui.log_status(f"    [Error] Paper drafting failed: {e}", level="error")
            raise

    # ------------------------------------------------------------------
    # Real LLM draft
    # ------------------------------------------------------------------

    def _real_draft(
        self,
        state: ProjectState,
        verbose: bool,
        feedback: Optional[str],
    ) -> ProjectState:
        if self.ui is not None:
            self.ui.log_status("    >> Drafting manuscript sections...")

        ground_truth = ground_truth_block()

        profile = state.researcher_profile
        pi_line = f"{profile.name}, {profile.affiliation}" if profile else "Unknown"
        hypothesis = state.refined_hypothesis or state.initial_shower_thought or ""
        abstract_seed = state.project_abstract or ""

        # Build a PI context block with structured data for the
        # Introduction (prior work references) and Discussion (career
        # context, significance framing)
        pi_context_parts = [f"PI: {pi_line}"]
        if profile:
            if profile.domain_expertise:
                pi_context_parts.append(f"Expertise: {profile.domain_expertise}")
            if profile.h_index is not None:
                pi_context_parts.append(f"h-index: {profile.h_index}, citations: {profile.total_citations}")
            if profile.publications:
                top = [p for p in profile.publications[:5] if p.get("title")]
                if top:
                    pi_context_parts.append(
                        "Key prior publications: " + "; ".join(
                            f'"{p["title"]}" ({p.get("year", "?")})' for p in top
                        )
                    )
        pi_context = "\n".join(pi_context_parts)

        methods_grounding = _build_methods_grounding(state)
        results_grounding = _build_results_grounding(state)
        execution_summary = state.context.get("execution_summary", {}).get("summary_text", "")

        feedback_block = ""
        if feedback and feedback.strip():
            feedback_block = (
                "\n\n=== AUTHOR FEEDBACK (hard constraints) ===\n"
                f"{feedback.strip()}\n"
                "=== END ===\n"
                "Treat the feedback as non-negotiable. Restructure sections to "
                "accommodate it rather than dismissing.\n"
            )

        # ---- Draft each section with a focused prompt. We run a few LLM
        # calls rather than one monolithic one so each section gets enough
        # attention and the failure mode on any single section is isolated.

        section_specs = [
            ("Abstract",
             "Write a 150-250 word abstract summarizing the study's question, "
             "method, key findings, and significance. Past tense where the "
             "findings are real, present tense for claims."),
            ("Introduction",
             "Write the Introduction (3-6 paragraphs). Motivate the question, "
             "situate it in prior work, articulate the gap, and state the "
             "specific hypothesis tested. Do NOT pad with generalities."),
            ("Methods",
             "Write the Methods section strictly from the WHAT WAS ACTUALLY "
             "DONE block. Cover each step in the order it was executed. Use "
             "past tense. Include deviation-from-plan notes where relevant. "
             "Do NOT invent procedures that are not in the block."),
            ("Results",
             "Write the Results section strictly from the ACTUAL ARTIFACTS "
             "PRODUCED block. Describe findings in past tense. Reference "
             "figures by the filename listed in the block. Do NOT invent "
             "numerical results."),
            ("Discussion",
             "Write the Discussion (4-6 paragraphs). Interpret the findings "
             "against the hypothesis. Acknowledge limitations — especially "
             "any deviations from the original plan. Connect back to the "
             "literature gap identified in the Introduction."),
            ("Conclusion",
             "Write a short (1-2 paragraph) Conclusion that states the "
             "take-home finding and points to one concrete next step."),
        ]

        sections: List[ProposalSection] = []
        for idx, (title, instruction) in enumerate(section_specs):
            if self.ui is not None:
                self.ui.log_status(f"    >> [Paper] {idx + 1}/{len(section_specs)}: {title}")
            prompt = (
                f"You are a senior scientist drafting the {title} section of a "
                f"research manuscript.\n"
                f"{ground_truth}\n\n"
                f"{pi_context}\n"
                f"Hypothesis: {hypothesis}\n"
                f"Seed abstract: {abstract_seed}\n\n"
                + (
                    f"=== WHAT WAS ACTUALLY DONE (from Stage 5 execution) ===\n"
                    f"{methods_grounding}\n=== END ===\n\n"
                    if methods_grounding else ""
                )
                + (
                    f"=== ACTUAL ARTIFACTS PRODUCED ===\n"
                    f"{results_grounding}\n=== END ===\n\n"
                    if results_grounding else ""
                )
                + (
                    f"=== EXECUTION SUMMARY ===\n{execution_summary}\n=== END ===\n\n"
                    if execution_summary else ""
                )
                + f"SECTION TO WRITE: {title}\n"
                + f"INSTRUCTION: {instruction}\n"
                + feedback_block
                + "\nOutput: Markdown body text only. No section header. No commentary."
            )

            tracker.start_timer(f"Stage 6: {title}")
            try:
                response = self.llm.run_text(prompt)
                content = (response.content or "").strip()
                if content.startswith("```"):
                    lines = content.split("\n")
                    content = "\n".join(lines[1:]).strip()
                    if content.endswith("```"):
                        content = content[:-3].strip()
            except Exception as e:
                if self.ui is not None:
                    self.ui.log_status(f"    [Warning] {title} section failed: {e}", level="error")
                content = f"(Draft failed for {title} — retry with feedback)"
            finally:
                tracker.stop_timer(f"Stage 6: {title}")

            sections.append(ProposalSection(title=title, content=content))

        # Collect figure paths from attached artifacts
        figures: List[str] = []
        for node in state.dag:
            figures.extend(node.attached_artifact_paths)

        # Generate a title — use the project name if it's not the default
        title = state.project_name
        if not title or title.startswith("new-research-idea") or title == "New-Research-Idea":
            title = f"Manuscript: {hypothesis[:80]}"

        state.manuscript = Manuscript(
            title=title,
            target_venue=None,
            abstract=sections[0].content if sections else "",
            sections=sections,
            figures=figures,
        )

        state.add_ledger_entry(LedgerEntry(
            stage="Stage 6: Paper Writer",
            agent="PaperWriter",
            description=f"Drafted manuscript with {len(sections)} sections",
        ))

        if self.ui is not None:
            self.ui.log_status(f"    >> Manuscript drafted: {len(sections)} sections")

        return state

    # ------------------------------------------------------------------
    # Mock draft
    # ------------------------------------------------------------------

    def _mock_draft(self, state: ProjectState, feedback: Optional[str]) -> ProjectState:
        if self.ui is not None:
            self.ui.log_status("    MOCK MODE: drafting canned manuscript...")

        feedback_tag = f" [FEEDBACK: {feedback.strip()}]" if feedback and feedback.strip() else ""
        hypothesis = state.refined_hypothesis or state.initial_shower_thought or "(no hypothesis)"
        pi = state.researcher_profile.name if state.researcher_profile else "Unknown PI"

        methods_bits = _build_methods_grounding(state) or "(no execution state — using planned workflows)"
        results_bits = _build_results_grounding(state) or "(no attached artifacts yet)"

        sections = [
            ProposalSection(
                title="Abstract",
                content=(
                    f"MOCK MODE abstract. This study investigates: {hypothesis}. "
                    f"Drafted by Athanor PaperWriter for {pi}.{feedback_tag}"
                ),
            ),
            ProposalSection(
                title="Introduction",
                content=(
                    "MOCK MODE introduction. In real use this section motivates the "
                    "question, situates it in prior work, and states the hypothesis."
                ),
            ),
            ProposalSection(
                title="Methods",
                content=(
                    "MOCK MODE methods, reconstructed from the DAG:\n\n"
                    + methods_bits
                ),
            ),
            ProposalSection(
                title="Results",
                content=(
                    "MOCK MODE results, assembled from attached artifacts:\n\n"
                    + results_bits
                ),
            ),
            ProposalSection(
                title="Discussion",
                content=(
                    "MOCK MODE discussion. In real use this section interprets findings, "
                    "acknowledges limitations, and connects back to the literature gap."
                ),
            ),
            ProposalSection(
                title="Conclusion",
                content="MOCK MODE conclusion. The take-home and the next step go here.",
            ),
        ]

        figures: List[str] = []
        for node in state.dag:
            figures.extend(node.attached_artifact_paths)

        title = state.project_name
        if not title or title.startswith("new-research-idea") or title == "New-Research-Idea":
            title = f"Manuscript: {hypothesis[:80]}"

        state.manuscript = Manuscript(
            title=title,
            abstract=sections[0].content,
            sections=sections,
            figures=figures,
        )

        state.add_ledger_entry(LedgerEntry(
            stage="Stage 6: Paper Writer",
            agent="PaperWriter",
            description="MOCK MODE: drafted canned manuscript",
        ))

        return state
