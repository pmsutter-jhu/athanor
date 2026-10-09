"""
ATHANOR Execution Tracker (Stage 5)

Stage 5 · Execution tracking.

Stage 5 is deliberately NOT an execution engine. Athanor does not run
code, run wet-lab protocols, or schedule cluster jobs. That is what
CWL / Nextflow / Snakemake / Airflow / Galaxy are for. Position 3
(research lifecycle companion) says we track what the human actually
did, notice deviations from the plan, and drive re-planning or paper-
writing based on that ground truth.

This engine is therefore thin: it reads the per-node execution state
already populated by the GUI (via mark_node_started / mark_node_done
/ mark_node_blocked), produces a text progress summary for the trace
log, and emits a structured summary into state.context for use by
Stage 6 and by the Scribe reports.

When the GUI drives Stage 5 interactively, this engine is invoked by
the "run all" flow to refresh the summary before advancing to Stage 6.
It is non-LLM and has no mock vs. real distinction.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..core.state import ProjectState, ExecutionStatus


class ExecutionTracker:
    """Thin bookkeeping engine for Stage 5."""

    def __init__(self) -> None:
        self.ui = None

    def set_ui(self, ui) -> None:
        self.ui = ui

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, state: ProjectState, verbose: bool = True) -> ProjectState:
        """Refresh Stage 5 summary in state.context and log progress.

        This method is safe to call on a project that has done zero
        execution work (everything still PLANNED) — it just reports
        0% complete.
        """
        if self.ui is not None:
            self.ui.log_status("    >> Execution Tracker: summarizing plan vs. actual...")

        progress = state.execution_progress()
        summary_text = self.summarize(state)
        blockers = self.check_for_blockers(state)

        # Persist the summary into state.context so downstream code
        # (Stage 6, Scribe) can pull a consistent snapshot.
        state.context["execution_summary"] = {
            "progress": progress,
            "summary_text": summary_text,
            "blockers": blockers,
        }

        if self.ui is not None:
            pct = int(round(progress["percent_complete"] * 100))
            self.ui.log_status(
                f"    >> {pct}% complete — "
                f"{progress['done_count']}/{progress['total_count']} done, "
                f"{progress['in_progress_count']} in progress, "
                f"{progress['blocked_count']} blocked"
            )
            if blockers:
                self.ui.log_status(
                    f"    [!] {len(blockers)} blocker(s):",
                    level="verbose",
                )
                for b in blockers[:5]:
                    self.ui.log_status(f"    - {b}", level="verbose")
            if progress.get("schedule_delta_days") is not None:
                delta = progress["schedule_delta_days"]
                direction = "over" if delta > 0 else "under"
                self.ui.log_status(
                    f"    >> Schedule: {abs(delta):.1f} days {direction} plan (on DONE nodes)",
                    level="verbose",
                )

        return state

    # ------------------------------------------------------------------
    # Pure functions — also used by the GUI for progress panels
    # ------------------------------------------------------------------

    @staticmethod
    def summarize(state: ProjectState) -> str:
        """Produce a human-readable progress report. Used by the Scribe
        assistant to include an execution snapshot in reports."""
        if not state.dag:
            return "No plan DAG — nothing to track."

        p = state.execution_progress()
        lines: List[str] = []
        pct = int(round(p["percent_complete"] * 100))
        lines.append(
            f"Execution progress: {p['done_count']}/{p['total_count']} nodes complete ({pct}%)"
        )
        if p["in_progress_count"]:
            lines.append(f"In progress: {', '.join(p['in_progress_nodes'])}")
        if p["blocked_count"]:
            lines.append(f"Blocked: {', '.join(p['blocked_nodes'])}")
        if p.get("schedule_delta_days") is not None:
            delta = p["schedule_delta_days"]
            if delta > 0:
                lines.append(f"Schedule: {delta:.1f} days over plan (on completed nodes)")
            elif delta < 0:
                lines.append(f"Schedule: {abs(delta):.1f} days under plan (on completed nodes)")
            else:
                lines.append("Schedule: on plan")

        # Deviation notes from completed nodes — the "what really happened"
        deviations: List[str] = []
        for node in state.dag:
            if (node.status == ExecutionStatus.DONE or node.status == ExecutionStatus.BLOCKED) \
                    and node.deviation_notes:
                deviations.append(f"  · {node.name}: {node.deviation_notes}")
        if deviations:
            lines.append("Deviations from plan:")
            lines.extend(deviations)

        return "\n".join(lines)

    @staticmethod
    def check_for_blockers(state: ProjectState) -> List[str]:
        """Return human-readable blocker descriptions."""
        out: List[str] = []
        for node in state.dag:
            if node.status == ExecutionStatus.BLOCKED:
                note = node.deviation_notes or "(no reason given)"
                out.append(f"{node.name}: {note}")
        return out

    @staticmethod
    def should_trigger_replan(state: ProjectState) -> bool:
        """Heuristic: suggest a re-plan if more than 1 node is blocked OR
        the schedule delta exceeds 30% of planned wall-clock on completed
        nodes. The UI uses this to nudge the user toward the Re-plan
        button."""
        p = state.execution_progress()
        if p["blocked_count"] >= 2:
            return True
        delta = p.get("schedule_delta_days")
        if delta is None:
            return False
        # Only meaningful if we have actuals
        total_planned = sum(
            (n.execution_spec.compute_spec.wall_clock_days or 0.0)
            for n in state.dag
            if n.status == ExecutionStatus.DONE
            and n.execution_spec and n.execution_spec.compute_spec
        )
        if total_planned <= 0:
            return False
        return delta / total_planned > 0.3

    @staticmethod
    def build_replan_feedback(state: ProjectState) -> str:
        """Build a feedback string for rerun_plan based on deviation notes,
        so Stage 3 can regenerate downstream with the blockers baked in."""
        progress = state.execution_progress()
        parts: List[str] = []
        if progress["blocked_count"]:
            parts.append("The following steps are BLOCKED and the downstream plan needs to route around them:")
            for node in state.dag:
                if node.status == ExecutionStatus.BLOCKED:
                    note = node.deviation_notes or "(no reason given)"
                    parts.append(f"  - {node.name}: {note}")
        if progress.get("schedule_delta_days") and progress["schedule_delta_days"] > 0:
            parts.append(
                f"Completed steps ran {progress['schedule_delta_days']:.0f} days "
                f"over plan — tighten subsequent wall-clock estimates accordingly."
            )
        # Pull in any deviation notes from DONE steps that might matter
        done_deviations: List[str] = []
        for node in state.dag:
            if node.status == ExecutionStatus.DONE and node.deviation_notes:
                done_deviations.append(f"  - {node.name}: {node.deviation_notes}")
        if done_deviations:
            parts.append("Completed steps had these deviations from the original plan, which may affect downstream:")
            parts.extend(done_deviations)
        return "\n".join(parts) if parts else ""
