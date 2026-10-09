"""
ATHANOR Core State Definitions.

This module defines the fundamental data models used to persist the state of a research project
throughout its lifecycle in the ATHANOR pipeline. It uses Pydantic for robust type validation and serialization.

Key Models:
- `ProjectState`: The root object containing the entire state of a project (Hypothesis, Plan, Ledger, Context).
- `Task`: Represents a single unit of work in the research plan (assigned to Agents or Humans).
- `LedgerEntry`: A record in the Provenance Ledger, tracking decisions, resources used, and alternatives rejected.
- `TaskType`: Enum distinguishing between AGENT (automated), HUMAN (manual), and HYBRID (oversight) tasks.
"""
from typing import List, Optional, Dict, Any, Set, Union, Literal
from enum import Enum
from pydantic import BaseModel, Field
from datetime import datetime
import uuid

class TaskType(str, Enum):
    AGENT = "AGENT"
    HUMAN = "HUMAN"
    HYBRID = "HYBRID"

class Artifact(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    data_type: str = Field(..., description="File, Value, PhysicalSample, Equation") 
    format: str = Field(..., description="CSV, FITS, FrozenTissue")
    source_node_id: str

class ExecutionStatus(str, Enum):
    """Lifecycle state of a DAG node as the human executes the research.

    This is Athanor's Position 3 hook: we don't execute anything, but we
    track the human's progress so downstream stages (paper writing) know
    what actually happened vs. what was planned.
    """
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    ABANDONED = "ABANDONED"


class NodeKind(str, Enum):
    """Semantic classification of a DAG node — orthogonal to executor_type.

    - TASK: the default. A unit of work that produces an artifact.
    - CHECKPOINT: a human-judgment gate. Produces a decision (proceed /
      refine / abort) rather than a new artifact. Always HUMAN executor.
    - DATA_ACQUISITION: consumes an external source (public dataset,
      specimen from a collaborator, observation time on a shared
      instrument). Doesn't "run" during the project — it's already
      available, or obtained through a separate process.
    """
    TASK = "TASK"
    CHECKPOINT = "CHECKPOINT"
    DATA_ACQUISITION = "DATA_ACQUISITION"


class ComputeRequirements(BaseModel):
    """Structured resource estimate for a single DAG node.

    All fields are optional and additive — any field set to None or 0 is
    treated as "not applicable" when the GUI renders it and when Stage 4
    aggregates totals across the DAG.
    """
    cpu_hours: Optional[float] = None  # Estimated CPU-core-hours
    gpu_hours: Optional[float] = None  # Estimated GPU-hours
    gpu_type: Optional[str] = None  # e.g. "A100", "H100"
    storage_gb: Optional[float] = None  # Storage required for this step
    memory_gb: Optional[float] = None  # Peak memory for this step
    human_hours: Optional[float] = None  # Wall-clock human effort
    wall_clock_days: Optional[float] = None  # Calendar days from start to finish
    notes: str = ""  # Free-text qualifiers ("requires cleanroom", "off-peak only")


class ExecutionSpec(BaseModel):
    executor_type: TaskType
    agent_role: Optional[str] = None  # specialty (e.g. Data Scientist)
    # AI Fields
    docker_image: Optional[str] = None
    script_path: Optional[str] = None
    compute_requirements: Optional[str] = None  # Legacy free-text blob (kept for backward compat)
    compute_spec: Optional[ComputeRequirements] = None  # Structured budget used by Stage 4 F&E
    # Human Fields
    protocol_description: Optional[str] = None
    skill_level_required: Optional[str] = None

class DAGNode(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    inputs: List[Artifact] = Field(default_factory=list)
    outputs: List[Artifact] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)  # List of depending Node IDs
    execution_spec: Optional[ExecutionSpec] = None
    scatter_strategy: Optional[str] = None
    justification: Optional[str] = None
    workflow: Optional[str] = None

    # ── Semantic enrichment (A+B) ──────────────────────────────────
    # Default is TASK so old projects round-trip unchanged.
    kind: NodeKind = NodeKind.TASK

    # Reviewer-facing fields that the grant proposal uses directly.
    # All are optional and start empty — the planner fills them.
    risks: List[str] = Field(default_factory=list)
    mitigations: List[str] = Field(default_factory=list)
    alternatives_considered: List[str] = Field(default_factory=list)

    # Time anchoring for the project timeline / Gantt-style view.
    # Integer months since project start (0 = month 1).
    target_month: Optional[int] = None

    # Deliverable flag — set when this node's output is a reviewer-
    # facing product (paper, dataset, specimen, software release,
    # characterized material, etc). Kind is an open string with
    # suggested values so it works for every scientific modality:
    # "artifact" | "knowledge" | "resource" | "method" | "paper"
    # but users can write anything.
    is_deliverable: bool = False
    deliverable_kind: Optional[str] = None
    deliverable_description: Optional[str] = None

    # ── Execution state (Stage 5) ──────────────────
    # Lifecycle tracking. Fields default to "unstarted" so projects that
    # never touch Stage 5 round-trip cleanly.
    status: ExecutionStatus = ExecutionStatus.PLANNED
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    actual_wall_clock_days: Optional[float] = None
    actual_compute_used: Optional["ComputeRequirements"] = None
    attached_artifact_paths: List[str] = Field(default_factory=list)
    deviation_notes: str = ""

class TaskStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"

class ResearcherProfile(BaseModel):
    name: str
    affiliation: Optional[str] = None
    role: str = "Principal Investigator"
    team_members: List[str] = Field(default_factory=list)

    # ── Prose fields (LLM-synthesized from multi-source fragments) ──
    domain_expertise: str = ""
    available_resources: str = ""
    publication_history: str = ""
    important_connections: str = ""
    persona_motivation: str = ""

    # ── Structured fields (parsed from ORCID / Semantic Scholar / GitHub) ──
    # These are populated by the structured extractors in ingest/profile.py
    # and feed directly into downstream prompts that need concrete data
    # (bio sketch, budget justification, feasibility arguments).

    publications: List[Dict[str, Any]] = Field(default_factory=list)
    # [{title, doi, year, venue, citations, authors}, ...]
    # Populated from ORCID works + Semantic Scholar citation enrichment.

    h_index: Optional[int] = None
    total_citations: Optional[int] = None
    recent_publication_count: Optional[int] = None  # last 5 years

    career_timeline: Dict[str, Any] = Field(default_factory=dict)
    # {phd_year, phd_institution, postdoc_start, postdoc_institution,
    #  current_role_start, current_institution, career_stage}
    # career_stage: "early-career" | "mid-career" | "established"

    funding_history: List[Dict[str, Any]] = Field(default_factory=list)
    # [{funder, title, start_year, end_year, amount_usd, role}, ...]
    # Populated from ORCID fundings section.

    software_releases: List[Dict[str, Any]] = Field(default_factory=list)
    # [{name, url, stars, language, description}, ...]
    # Populated from GitHub API.

    collaborators: List[Dict[str, Any]] = Field(default_factory=list)
    # [{name, institution, expertise, relationship}, ...]
    # Parsed from important_connections prose + ORCID co-authors.

    contacts: List[str] = Field(default_factory=list)
    interests: List[str] = Field(default_factory=list)

class ProposalSection(BaseModel):
    title: str
    content: str # Markdown within the section

class GrantProposal(BaseModel):
    rfp_title: str
    target_agency: Optional[str] = None
    sections: List[ProposalSection] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.now)
    compliance_matrix: Dict[str, Any] = Field(default_factory=dict)


class PreliminaryWork(BaseModel):
    """A piece of work the PI brings into the proposal.

    Two roles:
      - prior_work: published / public work that establishes the PI's
        track record and capability. Feeds the "Prior Relevant Work" /
        "Results from Prior Support" / "Biographical Sketch" sections
        of a grant. Usually auto-extracted from the PI's profile sources
        (research URL, ORCID, CV).
      - preliminary_result: pilot work, proof-of-concept, or preliminary
        data specific to THIS proposal. Feeds the "Preliminary Data" /
        "Feasibility" / "Proof of Concept" section. Either manually
        added by the user or promoted automatically from a DONE DAG node
        in Stage 5.

    All other fields are optional and used as available. Attached paths
    point at files stored under <project_dir>/attachments/, and when the
    ProjectKnowledgeBase is active, those files are also indexed for
    retrieval during Stage 4 grant writing.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: Literal["prior_work", "preliminary_result"] = "prior_work"
    title: str
    kind: Optional[str] = None  # "publication" | "dataset" | "code" | "figure" | "pilot_experiment" | "proof_of_concept" | "specimen" | "protocol" | "talk" | "grant" | "unpublished_data" | ...
    date: Optional[str] = None  # loose — "2022", "2023-03", "unpublished"
    description: str = ""
    relevance: str = ""  # how this connects to the proposed work
    link: Optional[str] = None  # URL / DOI / repo / wherever it lives
    citation: Optional[str] = None  # formatted citation string (for publications)
    attached_paths: List[str] = Field(default_factory=list)  # local files
    auto_extracted: bool = False  # True if TeamProfiler created this; False if user-added
    created_at: datetime = Field(default_factory=datetime.now)


class Manuscript(BaseModel):
    """Stage 6 output — a structured paper drafted from the executed DAG.

    Sections are the usual Abstract / Introduction / Methods / Results /
    Discussion / Conclusion / References shape. Mirrors GrantProposal so
    the GUI can render both with the same section widget. `figures` is
    a list of paths pulled from DAGNode.attached_artifact_paths so the
    paper can reference what actually got produced during execution.
    """
    title: str
    target_venue: Optional[str] = None
    abstract: str = ""
    sections: List[ProposalSection] = Field(default_factory=list)
    figures: List[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.now)

class HypothesisScorecard(BaseModel):
    # 1. Novelty & Prior Art Gap (1-5)
    novelty_score: int = 0
    novelty_rationale: str = ""

    # 2. Biological/Physical Plausibility (1-5)
    plausibility_score: int = 0
    plausibility_rationale: str = ""

    # 3. Falsifiability (1-5 Scale & Binary)
    falsifiability_score: int = 0
    is_falsifiable: bool = False  # True if score >= 3
    falsifiability_rationale: str = ""

    # 4. Executability (1-5)
    executability_score: int = 0
    executability_rationale: str = ""

    # 5. Potential Impact (1-5)
    impact_score: int = 0
    impact_rationale: str = ""

    # 6. Reviewer Appeal (1-5) — will a grant reviewer find this
    # compelling and well-motivated? Directly relevant to Athanor's
    # purpose as a grant-writing tool.
    reviewer_appeal_score: int = 0
    reviewer_appeal_rationale: str = ""

    # 7. Safety & Ethical Guardrails (Binary)
    is_safe: bool = True
    safety_rationale: str = ""

    unresolved_issues: List[str] = Field(default_factory=list)

    # Final Outcome — computed as the mean of the 6 scored dimensions
    final_score_avg: float = 0.0
    recommendation: str = "PENDING"  # "PROCEED" | "REFINE" | "REJECT"
    verdict_summary: str = ""
    feedback_summary: str = ""

class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    assigned_to: str # Agent name or "Researcher"
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    dependencies: List[str] = Field(default_factory=list) # List of Task IDs
    
    # Detailed Execution Requirements
    input_requirements: Optional[str] = None # e.g. "CSV file with columns A, B; API Key"
    expected_output: Optional[str] = None    # e.g. "Cleaned dataframe, PNG Plot"
    suggested_workflow: Optional[str] = None # e.g. "Use pandas to read csv, then matplotlib to plot"
    justification: Optional[str] = None      # e.g. "Agent assigned because task is computational."
    
    output: Optional[str] = None # The actual result content
    
    # UI Summaries (for PDF/Markdown)
    input_summary: Optional[str] = None
    output_summary: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None

class ProvenanceItem(BaseModel):
    category: str # "Tool", "Library", "Dataset", "Paper", "Decision"
    name: str
    link: Optional[str] = None
    justification: str

class AlternativeItem(BaseModel):
    category: str
    name: str
    reason_rejected: str

class LedgerEntry(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.now)
    stage: str # e.g. "Stage 3"
    agent: str 
    description: str
    items_used: List[ProvenanceItem] = Field(default_factory=list)
    alternatives_rejected: List[AlternativeItem] = Field(default_factory=list)


class ProjectState(BaseModel):
    project_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_name: str
    project_slug: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    researcher_profile: Optional[ResearcherProfile] = None
    co_investigators: List[ResearcherProfile] = Field(default_factory=list)
    team_summary: Optional[str] = None
    initial_shower_thought: str = ""
    refined_hypothesis: Optional[str] = None
    rejected_alternatives: List[str] = Field(default_factory=list)
    project_abstract: Optional[str] = None
    
    scorecard: Optional[HypothesisScorecard] = None

    # Previous-version snapshot for the Hypothesis tab's three-slot UI.
    # The GUI sets these when the user edits+redebates or optimizes, so the
    # pre-change version is one click away (Revert to previous). The original
    # spark lives in initial_shower_thought and is never overwritten.
    previous_hypothesis: Optional[str] = None
    previous_scorecard: Optional[HypothesisScorecard] = None

    # True when the user has edited refined_hypothesis but hasn't yet re-run
    # the debate, so the scorecard above is no longer in sync with the text.
    # Cleared when a fresh debate completes.
    scorecard_stale: bool = False
    
    # The Work Breakdown Structure
    # The Work Breakdown Structure
    tasks: List[Task] = Field(default_factory=list)
    
    # The Executable DAG
    dag: List[DAGNode] = Field(default_factory=list)
    
    # Provenance Ledger
    ledger: List[LedgerEntry] = Field(default_factory=list)
    
    # Global context/memory
    context: Dict[str, Any] = Field(default_factory=dict)
    
    # Progress Tracking
    current_stage: int = 0
    
    # Grant Proposal Results
    proposal: Optional[GrantProposal] = None
    red_team_issues: List[Dict[str, Any]] = Field(default_factory=list)

    # Exemplar grant — if the user provided one, this holds the parsed
    # extraction. If None, GrantWeaver falls back to FunderProfile defaults.
    # Stored as a dict so the state.json round-trip stays simple; the
    # ExemplarGrant Pydantic schema lives in src/athanor/ingest/grant.py.
    exemplar_grant: Optional[Dict[str, Any]] = None
    funder_short_name: Optional[str] = None  # e.g. "nih_r01", "foundation"

    # Research resources — what the PI actually has access to (compute,
    # data, instruments, personnel, software, institutional). Influences
    # Stage 3 (what tasks are feasible) and Stage 4 (Facilities & Equipment,
    # budget realism). Stored as dict; schema lives in ingest/resources.py.
    resources: Optional[Dict[str, Any]] = None

    # Research domain (e.g. "ai_ml", "biomedical", "observational_astro").
    # Selects the domain-specific defaults baseline used to fill missing
    # resource fields. See RESEARCH_DOMAINS in ingest/resources.py.
    research_domain: Optional[str] = None

    # Stage 6 output — the drafted manuscript. Populated by PaperWriter
    # from the executed DAG (DONE nodes + attached artifacts + deviation
    # notes) when the human advances to Stage 6.
    manuscript: Optional[Manuscript] = None

    # Prior work + preliminary results the PI brings into the proposal.
    # Populated by TeamProfiler auto-extraction (prior work from CV /
    # ORCID / research URL) and by manual user entry (preliminary results
    # specific to this grant). Consumed by GrantWeaver as structured
    # context for the Background and Preliminary Data sections.
    preliminary_work: List[PreliminaryWork] = Field(default_factory=list)

    def add_ledger_entry(self, entry: LedgerEntry):
        self.ledger.append(entry)
        self.updated_at = datetime.now()

    def add_task(self, task: Task):
        self.tasks.append(task)
        self.updated_at = datetime.now()

    def add_node(self, node: DAGNode):
        self.dag.append(node)
        self.updated_at = datetime.now()

    def get_task(self, task_id: str) -> Optional[Task]:
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def prune_from_stage(self, start_stage: int):
        """
        Clears all data generated from `start_stage` onwards to allow a clean restart.
        The engine's [PURGE] log covers the user-facing notification.
        """
        # If restarting Stage 1 (Profile)
        if start_stage <= 1:
            self.researcher_profile = None
            self.co_investigators = []
            self.team_summary = None
            self.current_stage = 0
            self.context = {} # Clear all global context for fresh start
            # Clear AUTO-EXTRACTED preliminary_work items only; preserve
            # anything the user added manually so they don't lose work
            # on a Stage 1 re-run.
            self.preliminary_work = [
                pw for pw in self.preliminary_work if not pw.auto_extracted
            ]
            
        # If restarting Stage 2 (Hypothesis)
        if start_stage <= 2:
            self.refined_hypothesis = None
            self.scorecard = None
            self.previous_hypothesis = None
            self.previous_scorecard = None
            self.scorecard_stale = False
            self.current_stage = min(self.current_stage, 1)
            
        # If restarting Stage 3 (Plan)
        if start_stage <= 3:
            self.tasks = []
            self.dag = []
            self.red_team_issues = []
            self.rejected_alternatives = []
            self.project_abstract = None
            self.current_stage = min(self.current_stage, 2)
            
        # If restarting Stage 4 (Proposal)
        if start_stage <= 4:
            self.proposal = None
            self.current_stage = min(self.current_stage, 3)

        # If restarting Stage 5 (Execution) — clear all execution state
        # on the DAG nodes so they go back to PLANNED. Preserves the plan
        # itself; only the execution bookkeeping is reset.
        if start_stage <= 5:
            for node in self.dag:
                node.status = ExecutionStatus.PLANNED
                node.started_at = None
                node.completed_at = None
                node.actual_wall_clock_days = None
                node.actual_compute_used = None
                node.attached_artifact_paths = []
                node.deviation_notes = ""
            self.current_stage = min(self.current_stage, 4)

        # If restarting Stage 6 (Paper) — wipe the manuscript but keep
        # execution state so the draft can be regenerated without redoing
        # the work.
        if start_stage <= 6:
            self.manuscript = None
            self.current_stage = min(self.current_stage, 5)
            
        # Prune Ledger (remove entries from this stage onwards)
        # Note: This is an approximation based on stage definition
        # Stage 1: Profiling, Stage 2: Hypothesis, Stage 3: Planning, Stage 4: Proposal
        cutoff_map = {1: ["Stage 1"], 2: ["Stage 2"], 3: ["Stage 3"], 4: ["Stage 4"]}
        forbidden_stages = []
        for s in range(start_stage, 5):
            forbidden_stages.extend(cutoff_map.get(s, []))
            
        self.ledger = [entry for entry in self.ledger if not any(fs in entry.stage for fs in forbidden_stages)]
        self.updated_at = datetime.now()

    def update_task_status(self, task_id: str, status: TaskStatus, output: Optional[str] = None):
        task = self.get_task(task_id)
        if task:
            task.status = status
            if output:
                task.output = output
            if status == TaskStatus.COMPLETED:
                task.completed_at = datetime.now()
            self.updated_at = datetime.now()

    # ------------------------------------------------------------------
    # DAG helpers — make state.dag the canonical source of the plan
    # ------------------------------------------------------------------

    def dag_validation_errors(self) -> List[str]:
        """Run GraphValidator on the current DAG. Empty list means valid."""
        if not self.dag:
            return []
        # Local import to avoid a circular dep at module load
        from .graph_validator import GraphValidator
        return GraphValidator.validate(self.dag)

    def is_dag_valid(self) -> bool:
        """True when the DAG is non-empty and passes all validator checks."""
        return bool(self.dag) and not self.dag_validation_errors()

    def topo_sorted_dag(self) -> List["DAGNode"]:
        """Return DAG nodes in a stable topological order.

        Uses Kahn's algorithm. If the graph has a cycle, the returned list
        may be shorter than self.dag — callers should also check
        is_dag_valid() if they care about completeness. Nodes with no
        dependencies come first; ties broken by original insertion order.
        """
        if not self.dag:
            return []

        node_map = {n.id: n for n in self.dag}
        # Remaining in-degree per node
        in_degree = {n.id: 0 for n in self.dag}
        for n in self.dag:
            for dep in n.dependencies:
                if dep in node_map:
                    in_degree[n.id] += 1

        # Preserve original order among nodes with same in-degree
        order = {n.id: i for i, n in enumerate(self.dag)}
        ready = sorted(
            [nid for nid, deg in in_degree.items() if deg == 0],
            key=lambda nid: order[nid],
        )

        result: List["DAGNode"] = []
        seen: Set[str] = set()
        while ready:
            nid = ready.pop(0)
            if nid in seen:
                continue
            seen.add(nid)
            node = node_map[nid]
            result.append(node)
            # For each node that lists `nid` as a dep, decrement
            for other in self.dag:
                if nid in other.dependencies and other.id not in seen:
                    in_degree[other.id] -= 1
                    if in_degree[other.id] == 0:
                        ready.append(other.id)
            ready.sort(key=lambda x: order[x])

        return result

    def dag_depth_map(self) -> Dict[str, int]:
        """Return {node_id: depth} where depth = longest dep chain to a root.

        Used by the GUI to render indented tree view. Nodes with no deps
        have depth 0; a node that depends on a depth-0 node has depth 1.
        Cycles are handled by returning depth 0 for cycle members.
        """
        if not self.dag:
            return {}
        node_map = {n.id: n for n in self.dag}
        depth: Dict[str, int] = {}

        def _depth(nid: str, stack: Optional[Set[str]] = None) -> int:
            if nid in depth:
                return depth[nid]
            stack = stack or set()
            if nid in stack:
                # Cycle — treat as root
                return 0
            node = node_map.get(nid)
            if not node or not node.dependencies:
                depth[nid] = 0
                return 0
            stack.add(nid)
            max_parent = 0
            for dep in node.dependencies:
                if dep in node_map:
                    max_parent = max(max_parent, _depth(dep, stack) + 1)
            stack.remove(nid)
            depth[nid] = max_parent
            return max_parent

        for n in self.dag:
            _depth(n.id)
        return depth

    def aggregate_compute_budget(self) -> Dict[str, Any]:
        """Sum compute_spec fields across all DAG nodes that set them.

        Returns a dict with keys: cpu_hours, gpu_hours, storage_gb,
        memory_gb_peak, human_hours, wall_clock_days, nodes_with_gpu,
        gpu_types, notes (list). Fields are None when no node reports
        that field. Used by GrantWeaver's facilities prompt to surface
        concrete compute numbers derived from the plan.
        """
        totals: Dict[str, Any] = {
            "cpu_hours": None,
            "gpu_hours": None,
            "storage_gb": None,
            "memory_gb_peak": None,
            "human_hours": None,
            "wall_clock_days": None,
            "nodes_with_gpu": 0,
            "gpu_types": [],
            "notes": [],
        }
        gpu_type_set: Set[str] = set()

        def _add(key: str, value: Optional[float]) -> None:
            if value is None:
                return
            if totals[key] is None:
                totals[key] = 0.0
            totals[key] += float(value)

        for node in self.dag:
            spec = node.execution_spec.compute_spec if node.execution_spec else None
            if spec is None:
                continue
            _add("cpu_hours", spec.cpu_hours)
            _add("gpu_hours", spec.gpu_hours)
            _add("storage_gb", spec.storage_gb)
            _add("human_hours", spec.human_hours)
            _add("wall_clock_days", spec.wall_clock_days)
            if spec.memory_gb is not None:
                if totals["memory_gb_peak"] is None:
                    totals["memory_gb_peak"] = 0.0
                totals["memory_gb_peak"] = max(totals["memory_gb_peak"], spec.memory_gb)
            if spec.gpu_hours and spec.gpu_hours > 0:
                totals["nodes_with_gpu"] += 1
            if spec.gpu_type:
                gpu_type_set.add(spec.gpu_type)
            if spec.notes:
                totals["notes"].append(f"{node.name}: {spec.notes}")

        totals["gpu_types"] = sorted(gpu_type_set)
        return totals

    def format_compute_budget_block(self) -> str:
        """Human-readable version of aggregate_compute_budget for injection
        into LLM prompts (GrantWeaver facilities and budget sections)."""
        b = self.aggregate_compute_budget()
        lines: List[str] = []
        if b["cpu_hours"] is not None:
            lines.append(f"- Total CPU-hours (estimated): {b['cpu_hours']:,.0f}")
        if b["gpu_hours"] is not None:
            gpu_descr = f"{b['gpu_hours']:,.0f}"
            if b["gpu_types"]:
                gpu_descr += f" ({', '.join(b['gpu_types'])})"
            lines.append(f"- Total GPU-hours (estimated): {gpu_descr}")
        if b["storage_gb"] is not None:
            lines.append(f"- Total storage: {b['storage_gb']:,.0f} GB")
        if b["memory_gb_peak"] is not None:
            lines.append(f"- Peak memory per step: {b['memory_gb_peak']:,.0f} GB")
        if b["human_hours"] is not None:
            lines.append(f"- Total human-hours (estimated): {b['human_hours']:,.0f}")
        if b["wall_clock_days"] is not None:
            lines.append(f"- Total wall-clock days (sequential): {b['wall_clock_days']:,.0f}")
        if not lines:
            return ""
        header = "PLAN-DERIVED COMPUTE BUDGET (aggregated from Stage 3 DAG):"
        body = "\n".join(lines)
        notes = ""
        if b["notes"]:
            notes = "\nPer-node notes:\n" + "\n".join(f"  · {n}" for n in b["notes"][:6])
        return f"{header}\n{body}{notes}"

    def execution_progress(self) -> Dict[str, Any]:
        """Return a snapshot of Stage 5 execution state across the DAG.

        Keys:
          - done_count / in_progress_count / blocked_count / abandoned_count / planned_count
          - total_count
          - percent_complete (float 0-1, fraction of DONE nodes)
          - schedule_delta_days (float or None — sum of actual_wall_clock_days
            for DONE nodes minus their compute_spec.wall_clock_days estimates;
            positive means over-budget, negative means under)
          - done_nodes / in_progress_nodes / blocked_nodes (lists of names)
        """
        total = len(self.dag)
        counts = {"PLANNED": 0, "IN_PROGRESS": 0, "DONE": 0, "BLOCKED": 0, "ABANDONED": 0}
        planned_days = 0.0
        actual_days = 0.0
        any_actuals = False
        by_status: Dict[str, List[str]] = {k: [] for k in counts}

        for node in self.dag:
            status = node.status.value if hasattr(node.status, "value") else str(node.status)
            if status not in counts:
                status = "PLANNED"
            counts[status] += 1
            by_status[status].append(node.name)
            if status == "DONE":
                if node.actual_wall_clock_days is not None:
                    actual_days += float(node.actual_wall_clock_days)
                    any_actuals = True
                if (node.execution_spec and node.execution_spec.compute_spec
                        and node.execution_spec.compute_spec.wall_clock_days):
                    planned_days += float(node.execution_spec.compute_spec.wall_clock_days)

        schedule_delta = None
        if any_actuals and planned_days > 0:
            schedule_delta = actual_days - planned_days

        percent = (counts["DONE"] / total) if total > 0 else 0.0

        return {
            "total_count": total,
            "planned_count": counts["PLANNED"],
            "in_progress_count": counts["IN_PROGRESS"],
            "done_count": counts["DONE"],
            "blocked_count": counts["BLOCKED"],
            "abandoned_count": counts["ABANDONED"],
            "percent_complete": percent,
            "schedule_delta_days": schedule_delta,
            "done_nodes": by_status["DONE"],
            "in_progress_nodes": by_status["IN_PROGRESS"],
            "blocked_nodes": by_status["BLOCKED"],
        }

    def plan_narrative_block(self) -> str:
        """Build a grant-proposal-ready narrative view of the DAG.

        Unlike flat_plan_for_reports() (a minimal WBS string), this walks
        the DAG and emits the reviewer-facing enrichment: risks,
        mitigations, alternatives, milestones, deliverables. The returned
        text is injected into the Stage 4 draft prompt so the writer can
        cite concrete per-step risks and products instead of inventing
        them.

        Falls back to an empty string when the DAG is empty or no nodes
        have enrichment populated.
        """
        if not self.dag:
            return ""
        nodes = self.topo_sorted_dag()
        any_enriched = any(
            n.risks or n.mitigations or n.alternatives_considered
            or n.is_deliverable or n.target_month is not None
            for n in nodes
        )
        if not any_enriched:
            return ""

        lines: List[str] = []
        # Section 1: Timeline (only nodes with target_month)
        timeline_nodes = sorted(
            [n for n in nodes if n.target_month is not None],
            key=lambda n: (n.target_month or 0),
        )
        if timeline_nodes:
            lines.append("PROJECT TIMELINE (months since start):")
            for n in timeline_nodes:
                m = n.target_month or 0
                kind_tag = f" [{n.kind.value.lower()}]" if n.kind.value != "TASK" else ""
                lines.append(f"  M{m}+ {n.name}{kind_tag}")
            lines.append("")

        # Section 2: Risks & mitigations (only nodes with risks)
        risk_nodes = [n for n in nodes if n.risks]
        if risk_nodes:
            lines.append("RISKS AND MITIGATIONS (per plan step):")
            for n in risk_nodes:
                lines.append(f"  {n.name}:")
                max_len = max(len(n.risks), len(n.mitigations))
                for i in range(max_len):
                    r = n.risks[i] if i < len(n.risks) else ""
                    m = n.mitigations[i] if i < len(n.mitigations) else ""
                    if r:
                        lines.append(f"    - Risk: {r}")
                    if m:
                        lines.append(f"      Mitigation: {m}")
            lines.append("")

        # Section 3: Alternatives considered
        alt_nodes = [n for n in nodes if n.alternatives_considered]
        if alt_nodes:
            lines.append("ALTERNATIVES CONSIDERED (per plan step):")
            for n in alt_nodes:
                lines.append(f"  {n.name}:")
                for alt in n.alternatives_considered:
                    lines.append(f"    - {alt}")
            lines.append("")

        # Section 4: Deliverables
        deliverable_nodes = [n for n in nodes if n.is_deliverable]
        if deliverable_nodes:
            lines.append("PRODUCTS OF THE RESEARCH (explicit deliverables):")
            for n in deliverable_nodes:
                kind = n.deliverable_kind or "deliverable"
                desc = n.deliverable_description or n.description or n.name
                tm = f" (target: month {n.target_month + 1})" if n.target_month is not None else ""
                lines.append(f"  - {kind.upper()}: {desc}{tm}")
            lines.append("")

        return "\n".join(lines).rstrip()

    def prior_work_narrative_block(self) -> str:
        """Format the PI's prior work as a grant-proposal-ready context
        block for Stage 4. Returns empty string if nothing to render."""
        items = [pw for pw in self.preliminary_work if pw.role == "prior_work"]
        if not items:
            return ""
        lines: List[str] = []
        lines.append("PI'S PRIOR RELEVANT WORK (cite by name; DO NOT fabricate titles, authors, or results):")
        # Group by kind for readability
        by_kind: Dict[str, List[PreliminaryWork]] = {}
        for pw in items:
            k = (pw.kind or "other").upper()
            by_kind.setdefault(k, []).append(pw)
        kind_order = ["PUBLICATION", "DATASET", "CODE", "SOFTWARE", "PROTOCOL",
                      "SPECIMEN", "INSTRUMENT", "GRANT", "TALK", "BOOK",
                      "UNPUBLISHED_DATA", "OTHER"]
        seen_kinds: set = set()
        for kind in kind_order:
            if kind not in by_kind:
                continue
            seen_kinds.add(kind)
            lines.append("")
            lines.append(f"  {kind}:")
            for pw in by_kind[kind]:
                date = f" ({pw.date})" if pw.date else ""
                link = f" — {pw.link}" if pw.link else ""
                lines.append(f"    - {pw.title}{date}{link}")
                if pw.description:
                    lines.append(f"      {pw.description.strip()}")
                if pw.relevance:
                    lines.append(f"      Relevance: {pw.relevance.strip()}")
                if pw.citation:
                    lines.append(f"      Citation: {pw.citation.strip()}")
                if pw.attached_paths:
                    lines.append(f"      Attached files: {', '.join(pw.attached_paths)}")
        # Any remaining kinds not in the ordered list
        for kind, kind_items in by_kind.items():
            if kind in seen_kinds:
                continue
            lines.append("")
            lines.append(f"  {kind}:")
            for pw in kind_items:
                date = f" ({pw.date})" if pw.date else ""
                lines.append(f"    - {pw.title}{date}")
                if pw.description:
                    lines.append(f"      {pw.description.strip()}")
        return "\n".join(lines)

    def preliminary_results_narrative_block(self) -> str:
        """Format the PI's preliminary results (pilot work, proof-of-
        concept, early-stage evidence specific to this proposal) as a
        grant-proposal-ready context block for Stage 4. Returns empty
        string if nothing to render."""
        items = [pw for pw in self.preliminary_work if pw.role == "preliminary_result"]
        if not items:
            return ""
        lines: List[str] = []
        lines.append("PRELIMINARY RESULTS SPECIFIC TO THIS PROJECT (cite as pilot/feasibility evidence; DO NOT extrapolate beyond what was actually observed):")
        lines.append("")
        for pw in items:
            kind = (pw.kind or "preliminary result").lower()
            date = f" · {pw.date}" if pw.date else ""
            lines.append(f"  - {pw.title}  [{kind}{date}]")
            if pw.description:
                lines.append(f"    {pw.description.strip()}")
            if pw.relevance:
                lines.append(f"    Implications: {pw.relevance.strip()}")
            if pw.attached_paths:
                lines.append(f"    Attached files: {', '.join(pw.attached_paths)}")
            if pw.link:
                lines.append(f"    Link: {pw.link}")
        return "\n".join(lines)

    def flat_plan_for_reports(self) -> List[Dict[str, str]]:
        """Build a minimal task-list view from the DAG for downstream code
        (GrantWeaver, reports) that just needs name + description.

        Falls back to state.tasks if the DAG is empty, so legacy projects
        and the mock path keep working. New code should prefer this
        helper over state.tasks directly.
        """
        if self.dag:
            return [
                {"name": n.name, "description": n.description}
                for n in self.topo_sorted_dag()
            ]
        return [{"name": t.name, "description": t.description} for t in self.tasks]
