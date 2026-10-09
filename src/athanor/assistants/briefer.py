"""
ATHANOR Briefer — handoff brief generator.

Given a DAG node, render a self-contained Markdown document that
anyone (another AI agent, a postdoc, a collaborator, a ticket system,
or the PI themselves on a Monday morning) can act on without needing
to open Athanor first. This is Athanor's preferred alternative to
built-in execution: we don't run the work, we hand it off cleanly.

The brief is a pure presentation layer over what the DAG already
contains — no new state, no new LLM calls. Everything comes from the
node's fields plus the surrounding neighborhood in state.dag.

Two modes:
- Local neighborhood (default): just the target node plus its direct
  upstream dependencies and direct downstream consumers. Keeps the
  brief focused and short enough to paste into a chat window.
- Full plan: adds a numbered list of every plan step at the bottom
  so the recipient can see where their work sits in the big picture.
  Use when the delegate hasn't seen Athanor before and needs
  orientation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.state import (
    ProjectState,
    DAGNode,
    ExecutionStatus,
    NodeKind,
    TaskType,
)


# =============================================================================
# Public entry point
# =============================================================================

def build_handoff_brief(
    state: ProjectState,
    node_id: str,
    *,
    full_plan: bool = False,
) -> str:
    """Build a self-contained Markdown handoff brief for a DAG node.

    Args:
        state: the project state (must have a non-empty DAG and a
            refined hypothesis).
        node_id: the id of the target node to brief.
        full_plan: if True, append the full ordered plan at the bottom
            so the recipient can orient themselves. If False (default),
            only the target node's local neighborhood is shown.

    Returns:
        A Markdown string suitable for saving to .md, copying to a
        chat window, emailing, or pasting into a ticket system.

    Raises:
        ValueError: if node_id is not present in state.dag.
    """
    node = _find_node(state, node_id)
    if node is None:
        raise ValueError(f"No DAG node with id={node_id!r} in state")

    index = _node_index(state, node_id)
    total = len(state.dag)

    lines: List[str] = []
    lines.append(f"# Handoff: {_format_title(node, index)}")
    lines.append("")
    tagline = _tagline(node)
    if tagline:
        lines.append(f"*{tagline}*")
        lines.append("")

    # ── Context ──
    lines.append("## Context")
    lines.append("")
    project_name = state.project_name or "Untitled project"
    lines.append(f"**Project**: {project_name}")

    overall = _overall_goal(state)
    if overall:
        lines.append(f"**Overall goal**: {overall}")

    kind_label = _kind_label(node.kind)
    exec_label = _executor_label(node)
    if total > 0:
        lines.append(f"**Your step**: {index + 1} of {total}  ·  {kind_label}  ·  {exec_label}")
    else:
        lines.append(f"**Your step**: {kind_label}  ·  {exec_label}")

    if node.target_month is not None:
        lines.append(f"**Target start**: Month {node.target_month + 1} of the project")

    if node.is_deliverable:
        kind_str = (node.deliverable_kind or "deliverable").lower()
        desc = (node.deliverable_description or node.description or node.name).strip()
        lines.append(f"**This is a deliverable** ({kind_str}): {desc}")

    # Execution-state awareness: if the node has been started / is blocked,
    # surface that at the top so the recipient knows the history.
    status_line = _status_summary(node)
    if status_line:
        lines.append(f"**Current status**: {status_line}")

    lines.append("")

    # ── What you're doing ──
    lines.append("## What you're doing")
    lines.append("")
    if node.description:
        lines.append(node.description.strip())
        lines.append("")
    if node.justification:
        lines.append(f"**Why this matters**: {node.justification.strip()}")
        lines.append("")

    # ── Inputs ──
    lines.append("## Inputs you need")
    lines.append("")
    if not node.inputs:
        if node.kind == NodeKind.DATA_ACQUISITION:
            lines.append(
                "*This step consumes an external source — there are no upstream "
                "plan steps that produce its inputs. Source the material from its "
                "origin (public dataset, collaborator, archive, instrument time).*"
            )
        elif _is_root(state, node):
            lines.append("*This is a starting step — no inputs from upstream plan steps.*")
        else:
            lines.append("*(No typed inputs declared. See the workflow below for context.)*")
    else:
        id_to_name = {n.id: n.name for n in state.dag}
        for inp in node.inputs:
            name = inp.name or "(unnamed)"
            dtype = inp.data_type or "File"
            fmt = inp.format or "Any"
            source_line = ""
            if inp.source_node_id and inp.source_node_id in id_to_name:
                source_line = f"  *From: {id_to_name[inp.source_node_id]}*"
            elif inp.source_node_id:
                source_line = "  *From: (external)*"
            lines.append(f"- **{name}** ({dtype}, {fmt}){source_line}")
    lines.append("")

    # ── How to do it ──
    lines.append("## How to do it")
    lines.append("")
    workflow = (node.workflow or "").strip()
    if workflow:
        lines.append(workflow)
    else:
        lines.append(
            "*(No workflow specified. Use the description and inputs/outputs above "
            "to infer the procedure, or ask the PI for clarification.)*"
        )
    lines.append("")

    # Execution-type-specific guidance
    agent_role = (node.execution_spec.agent_role or "").strip() if node.execution_spec else ""
    exec_type_value = ""
    if node.execution_spec and node.execution_spec.executor_type:
        exec_type_value = (
            node.execution_spec.executor_type.value
            if hasattr(node.execution_spec.executor_type, "value")
            else str(node.execution_spec.executor_type)
        )
    if agent_role:
        lines.append(f"**Suggested role**: {agent_role}")
        lines.append("")

    # ── What to produce ──
    lines.append("## What to produce")
    lines.append("")
    if node.kind == NodeKind.CHECKPOINT:
        lines.append(
            "This is a **checkpoint** — you're producing a decision, not an artifact. "
            "Record your verdict (proceed / refine / abort) and the reasoning behind "
            "it. The decision feeds downstream planning."
        )
    elif not node.outputs:
        lines.append("*(No typed outputs declared. See the description for the expected result.)*")
    else:
        # Who consumes each output?
        consumers: Dict[str, List[str]] = {}
        for other in state.dag:
            if node.id in other.dependencies and other.id != node.id:
                for inp in other.inputs:
                    if inp.source_node_id == node.id:
                        consumers.setdefault(inp.name, []).append(other.name)
                if other.id not in [c for lst in consumers.values() for c in lst]:
                    # Catch downstream nodes that depend on us but don't
                    # tag their input source explicitly — they still care
                    # about our outputs generally.
                    for o in node.outputs:
                        if other.name not in consumers.get(o.name, []):
                            consumers.setdefault(o.name, []).append(other.name)

        for out in node.outputs:
            name = out.name or "(unnamed)"
            dtype = out.data_type or "File"
            fmt = out.format or "Any"
            consumed_by = consumers.get(name) or []
            # Dedupe while preserving order
            seen: set = set()
            ordered: List[str] = []
            for c in consumed_by:
                if c not in seen:
                    seen.add(c)
                    ordered.append(c)
            consumer_line = ""
            if ordered:
                consumer_line = f"  *Consumed by: {', '.join(ordered[:4])}*"
            lines.append(f"- **{name}** ({dtype}, {fmt}){consumer_line}")
    lines.append("")

    # ── Watch for (risks) ──
    if node.risks:
        lines.append("## Watch for")
        lines.append("")
        for risk in node.risks:
            lines.append(f"- ⚠ {risk.strip()}")
        lines.append("")

    # ── Ensure you (mitigations) ──
    if node.mitigations:
        lines.append("## Ensure you")
        lines.append("")
        for mit in node.mitigations:
            lines.append(f"- ✓ {mit.strip()}")
        lines.append("")

    # ── Alternatives already considered ──
    if node.alternatives_considered:
        lines.append("## Alternatives already considered (don't revisit)")
        lines.append("")
        for alt in node.alternatives_considered:
            lines.append(f"- {alt.strip()}")
        lines.append("")

    # ── Effort estimate ──
    effort_line = _effort_line(node)
    if effort_line:
        lines.append("## Effort estimate")
        lines.append("")
        lines.append(effort_line)
        spec_notes = _spec_notes(node)
        if spec_notes:
            lines.append("")
            lines.append(f"*{spec_notes}*")
        lines.append("")

    # ── Downstream — what this unlocks ──
    downstream = _downstream_names(state, node)
    if downstream:
        lines.append("## What your work unlocks")
        lines.append("")
        lines.append("When this step is done, the following downstream steps can proceed:")
        lines.append("")
        for d in downstream[:8]:
            lines.append(f"- {d}")
        if len(downstream) > 8:
            lines.append(f"- *(and {len(downstream) - 8} more)*")
        lines.append("")

    # ── Closing instructions ──
    lines.append("## When you finish")
    lines.append("")
    lines.append(
        "Return to Athanor's **Execution** tab and mark this step **DONE**. "
        "Capture any deviations from this brief in the notes field — they "
        "flow into the paper's Methods section later, so be specific."
    )
    lines.append("")
    lines.append(
        "Attach any artifacts you produced (file paths, repository URLs, "
        "dataset DOIs, photo of a wet-lab notebook page, whatever fits) so "
        "downstream steps can find them."
    )
    lines.append("")

    # ── Full plan appendix ──
    if full_plan and total > 1:
        lines.append("---")
        lines.append("")
        lines.append("## Full plan context")
        lines.append("")
        lines.append("This step sits inside a larger plan. The full ordered sequence:")
        lines.append("")
        for i, n in enumerate(state.topo_sorted_dag(), 1):
            marker = "**← your step**" if n.id == node.id else ""
            kind_tag = ""
            if n.kind != NodeKind.TASK:
                kind_tag = f" *[{n.kind.value.lower()}]*"
            line = f"{i}. {n.name}{kind_tag}"
            if marker:
                line += f"  {marker}"
            lines.append(line)
        lines.append("")

    # ── Footer ──
    lines.append("---")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    short_id = node.id.replace("-", "")[:8]
    lines.append(
        f"*Generated by Athanor · {ts} · Node {short_id} · {project_name}*"
    )

    return "\n".join(lines).rstrip() + "\n"


# =============================================================================
# Helpers
# =============================================================================

def _find_node(state: ProjectState, node_id: str) -> Optional[DAGNode]:
    for n in state.dag:
        if n.id == node_id:
            return n
    return None


def _node_index(state: ProjectState, node_id: str) -> int:
    """Return the 0-based topo-sorted position of the node."""
    try:
        topo = state.topo_sorted_dag()
    except Exception:
        topo = state.dag
    for i, n in enumerate(topo):
        if n.id == node_id:
            return i
    return 0


def _is_root(state: ProjectState, node: DAGNode) -> bool:
    return not node.dependencies


def _overall_goal(state: ProjectState) -> str:
    """Best available one-sentence summary of the project."""
    if state.project_abstract:
        # Take the first sentence
        first = state.project_abstract.strip().split(". ")[0]
        if len(first) > 10:
            return first.rstrip(".") + "."
    if state.refined_hypothesis:
        text = state.refined_hypothesis.strip()
        if len(text) > 280:
            text = text[:277] + "..."
        return text
    if state.initial_shower_thought:
        text = state.initial_shower_thought.strip()
        if len(text) > 280:
            text = text[:277] + "..."
        return text
    return ""


def _format_title(node: DAGNode, index: int) -> str:
    name = (node.name or "Unnamed step").strip()
    return f"{index + 1}. {name}"


def _tagline(node: DAGNode) -> str:
    """One-sentence summary for the very top of the brief."""
    desc = (node.description or "").strip()
    if not desc:
        return ""
    first = desc.split(". ")[0]
    if len(first) > 200:
        first = first[:197] + "..."
    return first.rstrip(".") + "."


def _kind_label(kind: NodeKind) -> str:
    return {
        NodeKind.TASK: "task",
        NodeKind.CHECKPOINT: "checkpoint (decision, not artifact)",
        NodeKind.DATA_ACQUISITION: "data acquisition (external source)",
    }.get(kind, "task")


def _executor_label(node: DAGNode) -> str:
    if not node.execution_spec or not node.execution_spec.executor_type:
        return "unknown executor"
    t = node.execution_spec.executor_type
    t_value = t.value if hasattr(t, "value") else str(t)
    human = {
        "AGENT": "automated / computational",
        "HUMAN": "human execution",
        "HYBRID": "human-supervised automation",
    }.get(t_value, t_value.lower())
    return human


def _status_summary(node: DAGNode) -> str:
    """Surface execution state if the node is past PLANNED."""
    status = node.status
    if status == ExecutionStatus.PLANNED:
        return ""
    if status == ExecutionStatus.IN_PROGRESS:
        started = node.started_at.isoformat() if node.started_at else "(no timestamp)"
        return f"IN PROGRESS since {started}"
    if status == ExecutionStatus.DONE:
        return "DONE — this step has already been executed"
    if status == ExecutionStatus.BLOCKED:
        return f"BLOCKED — {node.deviation_notes or '(no reason given)'}"
    if status == ExecutionStatus.ABANDONED:
        return "ABANDONED"
    return ""


def _effort_line(node: DAGNode) -> str:
    """Format compute_spec as a terse bullet line."""
    if not node.execution_spec or not node.execution_spec.compute_spec:
        return ""
    cs = node.execution_spec.compute_spec
    parts: List[str] = []
    if cs.cpu_hours:
        parts.append(f"{cs.cpu_hours:,.0f} CPU-h")
    if cs.gpu_hours:
        gpu = f"{cs.gpu_hours:,.0f} GPU-h"
        if cs.gpu_type:
            gpu += f" ({cs.gpu_type})"
        parts.append(gpu)
    if cs.memory_gb:
        parts.append(f"{cs.memory_gb:,.0f} GB RAM")
    if cs.storage_gb:
        parts.append(f"{cs.storage_gb:,.0f} GB storage")
    if cs.human_hours:
        parts.append(f"{cs.human_hours:,.0f} human-h")
    if cs.wall_clock_days:
        parts.append(f"~{cs.wall_clock_days:,.0f} day{'s' if cs.wall_clock_days != 1 else ''} wall clock")
    return " · ".join(parts) if parts else ""


def _spec_notes(node: DAGNode) -> str:
    if not node.execution_spec or not node.execution_spec.compute_spec:
        return ""
    return (node.execution_spec.compute_spec.notes or "").strip()


def _downstream_names(state: ProjectState, node: DAGNode) -> List[str]:
    """Direct downstream consumers of the given node, in topo order."""
    topo = state.topo_sorted_dag()
    out: List[str] = []
    for other in topo:
        if node.id in other.dependencies:
            out.append(other.name)
    return out
