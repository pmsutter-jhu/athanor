"""
Athanor research resources ingestion.

Captures what the researcher actually has access to: compute, data,
instruments, personnel, software, institutional infrastructure. This
deeply influences:

  - Stage 3 (planner): what tasks are feasible given the resources
  - Stage 4 (grant):   the Facilities & Equipment section, the budget
                       (don't ask for what you have)

Inputs (any combination):
  - free_text: a paragraph the user writes describing their setup
  - extracted from bio_supplement (optional, fuzzy)
  - extracted from exemplar grant (most reliable when available)
  - structured field overrides (for picky users)

Defaults: a generic-academic baseline that fills gaps. The user's
explicit values always override defaults.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .funder_profiles import FunderProfile, get_profile


# ============================================================================
# Schema
# ============================================================================

class ComputeResources(BaseModel):
    """What the researcher can actually compute on."""
    hpc_cluster_name: Optional[str] = None  # e.g. "University HPC cluster"
    gpu_count: Optional[int] = None
    gpu_type: Optional[str] = None  # "A100", "H100", "RTX 4090"
    cpu_cores: Optional[int] = None
    storage_tb: Optional[float] = None
    cloud_credits_usd: Optional[float] = None
    compute_hours_year: Optional[int] = None
    notes: str = ""


class DataResources(BaseModel):
    """Data the researcher has access to."""
    public_datasets: List[str] = Field(default_factory=list)
    proprietary_data: List[str] = Field(default_factory=list)
    data_sharing_agreements: List[str] = Field(default_factory=list)
    notes: str = ""


class InstrumentResources(BaseModel):
    """Physical instruments and observational facilities."""
    telescopes_observatories: List[str] = Field(default_factory=list)
    lab_equipment: List[str] = Field(default_factory=list)
    core_facilities: List[str] = Field(default_factory=list)
    notes: str = ""


class PersonnelResources(BaseModel):
    """People who can do work on the project."""
    students_available: int = 0
    postdocs_available: int = 0
    technical_staff: int = 0
    named_collaborators: List[str] = Field(default_factory=list)
    notes: str = ""

    @property
    def total_team_size(self) -> int:
        return self.students_available + self.postdocs_available + self.technical_staff


class SoftwareResources(BaseModel):
    """Software the researcher already has."""
    licenses: List[str] = Field(default_factory=list)  # MATLAB, Mathematica, etc.
    internal_tools: List[str] = Field(default_factory=list)  # in-house code
    notes: str = ""


class InstitutionalResources(BaseModel):
    """Institutional infrastructure beyond physical resources."""
    irb_status: Optional[str] = None  # "approved" | "pending" | "n/a"
    iacuc_status: Optional[str] = None
    grants_office_support: bool = True
    institutional_review_offices: List[str] = Field(default_factory=list)
    other_support: List[str] = Field(default_factory=list)
    notes: str = ""


class ResearchResources(BaseModel):
    """The complete inventory of what the researcher has available."""
    compute: ComputeResources = Field(default_factory=ComputeResources)
    data: DataResources = Field(default_factory=DataResources)
    instruments: InstrumentResources = Field(default_factory=InstrumentResources)
    personnel: PersonnelResources = Field(default_factory=PersonnelResources)
    software: SoftwareResources = Field(default_factory=SoftwareResources)
    institutional: InstitutionalResources = Field(default_factory=InstitutionalResources)

    # Free-text gap-filler — the easiest entry point for users.
    # Anything the user writes here is appended verbatim to engine prompts.
    free_text_supplement: str = ""

    def to_planner_constraints(self) -> str:
        """Format as a constraint block for the Stage 3 planner system prompt.

        The planner uses this to decide what tasks are feasible. Keep it
        terse and concrete — bullet points the LLM can act on.
        """
        lines: List[str] = []

        # Compute
        c = self.compute
        if c.hpc_cluster_name or c.gpu_count or c.compute_hours_year:
            parts = []
            if c.hpc_cluster_name:
                parts.append(c.hpc_cluster_name)
            if c.gpu_count and c.gpu_type:
                parts.append(f"{c.gpu_count}× {c.gpu_type}")
            elif c.gpu_count:
                parts.append(f"{c.gpu_count} GPUs")
            if c.compute_hours_year:
                parts.append(f"~{c.compute_hours_year:,} CPU-h/yr")
            if c.cloud_credits_usd:
                parts.append(f"${c.cloud_credits_usd:,.0f} cloud credits")
            lines.append(f"COMPUTE: {' · '.join(parts)}")
        elif c.notes:
            lines.append(f"COMPUTE: {c.notes}")

        # Data
        d = self.data
        all_data = d.public_datasets + d.proprietary_data
        if all_data:
            lines.append(f"DATA ACCESS: {', '.join(all_data[:6])}")

        # Instruments
        inst = self.instruments
        all_instruments = inst.telescopes_observatories + inst.lab_equipment + inst.core_facilities
        if all_instruments:
            lines.append(f"INSTRUMENTS: {', '.join(all_instruments[:6])}")

        # Personnel
        p = self.personnel
        if p.total_team_size > 0 or p.named_collaborators:
            parts = []
            if p.students_available:
                parts.append(f"{p.students_available} student(s)")
            if p.postdocs_available:
                parts.append(f"{p.postdocs_available} postdoc(s)")
            if p.technical_staff:
                parts.append(f"{p.technical_staff} tech staff")
            if p.named_collaborators:
                parts.append(f"collaborators: {', '.join(p.named_collaborators[:4])}")
            lines.append(f"PERSONNEL: {' · '.join(parts)}")
        else:
            lines.append("PERSONNEL: PI only (no team)")

        # Software
        s = self.software
        if s.licenses or s.internal_tools:
            sw = s.licenses + s.internal_tools
            lines.append(f"SOFTWARE: {', '.join(sw[:6])}")

        # Institutional
        ii = self.institutional
        flags = []
        if ii.irb_status:
            flags.append(f"IRB {ii.irb_status}")
        if ii.iacuc_status:
            flags.append(f"IACUC {ii.iacuc_status}")
        if flags:
            lines.append(f"INSTITUTIONAL: {' · '.join(flags)}")

        # Free text supplement always appended
        if self.free_text_supplement.strip():
            lines.append(f"NOTES: {self.free_text_supplement.strip()}")

        if not lines:
            return "RESOURCES: standard academic baseline (no specific resources declared)"

        return "RESOURCES AVAILABLE:\n" + "\n".join(f"- {l}" for l in lines)

    def to_facilities_text(self) -> str:
        """Format as raw text for the Stage 4 Facilities & Equipment section.

        More verbose than to_planner_constraints — this gets injected into
        the writer prompt as a fact-source.
        """
        sections: List[str] = []

        c = self.compute
        if any([c.hpc_cluster_name, c.gpu_count, c.cpu_cores, c.storage_tb,
                c.cloud_credits_usd, c.compute_hours_year, c.notes]):
            parts = []
            if c.hpc_cluster_name:
                parts.append(f"High-performance computing access via {c.hpc_cluster_name}.")
            if c.gpu_count:
                gpu_desc = f"{c.gpu_count} GPUs"
                if c.gpu_type:
                    gpu_desc = f"{c.gpu_count}× {c.gpu_type} GPUs"
                parts.append(f"GPU resources: {gpu_desc}.")
            if c.cpu_cores:
                parts.append(f"{c.cpu_cores:,} CPU cores available.")
            if c.storage_tb:
                parts.append(f"{c.storage_tb:.1f} TB of storage.")
            if c.compute_hours_year:
                parts.append(f"Annual allocation of approximately {c.compute_hours_year:,} CPU-hours.")
            if c.cloud_credits_usd:
                parts.append(f"${c.cloud_credits_usd:,.0f} in cloud computing credits.")
            if c.notes:
                parts.append(c.notes)
            sections.append("Computing Resources: " + " ".join(parts))

        d = self.data
        all_data = d.public_datasets + d.proprietary_data
        if all_data or d.data_sharing_agreements:
            parts = []
            if all_data:
                parts.append(f"Data access: {', '.join(all_data)}.")
            if d.data_sharing_agreements:
                parts.append(f"Active data sharing agreements: {', '.join(d.data_sharing_agreements)}.")
            if d.notes:
                parts.append(d.notes)
            sections.append("Data Resources: " + " ".join(parts))

        inst = self.instruments
        if inst.telescopes_observatories or inst.lab_equipment or inst.core_facilities:
            parts = []
            if inst.telescopes_observatories:
                parts.append(f"Observational facilities: {', '.join(inst.telescopes_observatories)}.")
            if inst.lab_equipment:
                parts.append(f"Laboratory equipment: {', '.join(inst.lab_equipment)}.")
            if inst.core_facilities:
                parts.append(f"Core facilities: {', '.join(inst.core_facilities)}.")
            if inst.notes:
                parts.append(inst.notes)
            sections.append("Instruments and Facilities: " + " ".join(parts))

        p = self.personnel
        if p.total_team_size > 0 or p.named_collaborators:
            parts = []
            roster = []
            if p.students_available:
                roster.append(f"{p.students_available} graduate student(s)")
            if p.postdocs_available:
                roster.append(f"{p.postdocs_available} postdoctoral researcher(s)")
            if p.technical_staff:
                roster.append(f"{p.technical_staff} technical staff")
            if roster:
                parts.append(f"The PI's team includes {', '.join(roster)}.")
            if p.named_collaborators:
                parts.append(f"Active collaborators: {', '.join(p.named_collaborators)}.")
            if p.notes:
                parts.append(p.notes)
            sections.append("Personnel: " + " ".join(parts))

        s = self.software
        if s.licenses or s.internal_tools:
            parts = []
            if s.licenses:
                parts.append(f"Software licenses: {', '.join(s.licenses)}.")
            if s.internal_tools:
                parts.append(f"In-house software: {', '.join(s.internal_tools)}.")
            sections.append("Software Resources: " + " ".join(parts))

        ii = self.institutional
        if ii.irb_status or ii.iacuc_status or ii.other_support:
            parts = []
            if ii.irb_status:
                parts.append(f"IRB approval status: {ii.irb_status}.")
            if ii.iacuc_status:
                parts.append(f"IACUC status: {ii.iacuc_status}.")
            if ii.grants_office_support:
                parts.append("Full institutional grants office support.")
            if ii.other_support:
                parts.append(f"Additional support: {', '.join(ii.other_support)}.")
            sections.append("Institutional Support: " + " ".join(parts))

        if self.free_text_supplement.strip():
            sections.append("Additional Resources: " + self.free_text_supplement.strip())

        if not sections:
            return "Standard academic research environment."

        return "\n\n".join(sections)


# ============================================================================
# Domain-specific defaults
# ============================================================================

# Research domain enum (used by config + GUI dropdown)
RESEARCH_DOMAINS = [
    "general",
    "biomedical",
    "ai_ml",
    "observational_astro",
    "physical_sciences",
    "lab_chemistry",
    "social_sciences",
    "engineering",
]


def default_academic_resources() -> ResearchResources:
    """A conservative generic-academic baseline. The fallback when no domain
    is specified. Fills nothing assertive — the user can override anything."""
    return ResearchResources(
        compute=ComputeResources(
            notes="Institutional HPC cluster access (typical academic baseline).",
        ),
        institutional=InstitutionalResources(
            grants_office_support=True,
            other_support=["University grants office", "Departmental administrative support"],
        ),
    )


def default_biomedical_resources() -> ResearchResources:
    """NIH-style biomedical defaults: animal facilities, IRB, wet lab equipment."""
    return ResearchResources(
        compute=ComputeResources(
            notes="Institutional HPC for bioinformatics workflows; modest GPU access.",
        ),
        instruments=InstrumentResources(
            core_facilities=[
                "Institutional core imaging facility",
                "Genomics core",
                "Proteomics core",
                "Flow cytometry core",
            ],
            lab_equipment=[
                "Standard wet-lab bench equipment (centrifuges, incubators, freezers)",
                "PCR and qPCR machines",
                "Cell culture facilities (BSL-2)",
            ],
            notes="Subject to institutional core facility scheduling and rates.",
        ),
        institutional=InstitutionalResources(
            irb_status="approved",
            iacuc_status="approved",
            grants_office_support=True,
            other_support=[
                "IRB / Human Subjects Office",
                "IACUC / Animal Welfare",
                "Biosafety Office",
                "Office of Sponsored Programs",
                "Clinical Research Coordinator",
            ],
        ),
    )


def default_ai_ml_resources() -> ResearchResources:
    """AI/ML defaults: GPU compute, cloud credits, dataset access. No wet lab."""
    return ResearchResources(
        compute=ComputeResources(
            hpc_cluster_name="Institutional GPU cluster",
            gpu_count=4,
            gpu_type="A100 or equivalent",
            cpu_cores=128,
            storage_tb=20.0,
            notes="GPU allocation via institutional cluster; cloud credits via "
                  "researcher accounts (AWS/GCP/Azure) as needed.",
        ),
        software=SoftwareResources(
            internal_tools=["Python ML stack (PyTorch, JAX, HuggingFace)", "git/GitHub"],
            notes="Standard open-source ML toolchain.",
        ),
        institutional=InstitutionalResources(
            grants_office_support=True,
            other_support=[
                "University grants office",
                "AI/data science research center (if applicable)",
            ],
        ),
    )


def default_observational_astro_resources() -> ResearchResources:
    """Observational astronomy: telescope access, archives, simulation HPC."""
    return ResearchResources(
        compute=ComputeResources(
            notes="Institutional HPC for cosmological simulations and "
                  "observational data reduction; modest GPU access.",
        ),
        data=DataResources(
            public_datasets=[
                "SDSS data releases",
                "Planck public data",
                "Pan-STARRS",
                "DES public release",
                "Gaia DR3",
            ],
            notes="Access to standard public astronomical archives via "
                  "institutional subscriptions.",
        ),
        instruments=InstrumentResources(
            telescopes_observatories=[
                "Time-allocation access through institutional partnerships",
            ],
            notes="Telescope time competitive; major facility GO time via standard TAC process.",
        ),
        software=SoftwareResources(
            internal_tools=["Python astronomy stack (astropy, scipy, healpy)"],
        ),
        institutional=InstitutionalResources(
            grants_office_support=True,
            other_support=["University grants office", "Department of Physics & Astronomy"],
        ),
    )


def default_physical_sciences_resources() -> ResearchResources:
    """Physics / chemistry / materials: lab + compute."""
    return ResearchResources(
        compute=ComputeResources(
            notes="Institutional HPC for simulations and data analysis.",
        ),
        instruments=InstrumentResources(
            core_facilities=[
                "Institutional materials characterization core",
                "X-ray diffraction facility",
                "Electron microscopy facility",
            ],
            lab_equipment=["Standard physics/chemistry lab benchtop equipment"],
        ),
        institutional=InstitutionalResources(
            grants_office_support=True,
            other_support=[
                "University grants office",
                "Environmental Health and Safety",
            ],
        ),
    )


def default_lab_chemistry_resources() -> ResearchResources:
    """Synthetic / analytical chemistry: bench + characterization cores."""
    return ResearchResources(
        instruments=InstrumentResources(
            core_facilities=[
                "NMR facility (multi-nuclear, 400-800 MHz)",
                "Mass spectrometry facility",
                "X-ray crystallography facility",
                "Materials characterization (SEM/TEM)",
            ],
            lab_equipment=[
                "Synthetic chemistry bench (fume hoods, glove boxes, rotovaps)",
                "Standard analytical instrumentation (UV-Vis, FTIR, HPLC, GC-MS)",
            ],
        ),
        institutional=InstitutionalResources(
            grants_office_support=True,
            other_support=[
                "University grants office",
                "Chemical Hygiene Office",
                "Environmental Health and Safety",
            ],
        ),
    )


def default_social_sciences_resources() -> ResearchResources:
    """Social science: IRB-heavy, survey/recruitment infrastructure."""
    return ResearchResources(
        compute=ComputeResources(
            notes="Institutional cluster for statistical analysis; modest needs.",
        ),
        software=SoftwareResources(
            licenses=["Stata or SPSS (institutional license)", "Qualtrics survey platform"],
            internal_tools=["R, Python statistical stack"],
        ),
        institutional=InstitutionalResources(
            irb_status="approved",
            grants_office_support=True,
            other_support=[
                "IRB / Human Subjects Office",
                "Survey Research Center",
                "University grants office",
            ],
        ),
    )


def default_engineering_resources() -> ResearchResources:
    """Engineering: fab facilities, machine shops, instrumentation."""
    return ResearchResources(
        compute=ComputeResources(
            notes="Institutional HPC for simulations (FEA, CFD); GPU access for ML-augmented design.",
        ),
        instruments=InstrumentResources(
            core_facilities=[
                "Institutional machine shop",
                "Cleanroom (Class 1000 or better)",
                "3D printing / additive manufacturing",
                "Materials testing facility",
            ],
            lab_equipment=[
                "CAD/CAM workstations",
                "Standard electronics bench (oscilloscopes, signal generators, power supplies)",
            ],
        ),
        institutional=InstitutionalResources(
            grants_office_support=True,
            other_support=[
                "University grants office",
                "Tech Transfer Office",
                "Environmental Health and Safety",
            ],
        ),
    )


# Lookup table
DOMAIN_DEFAULTS = {
    "general": default_academic_resources,
    "biomedical": default_biomedical_resources,
    "ai_ml": default_ai_ml_resources,
    "observational_astro": default_observational_astro_resources,
    "physical_sciences": default_physical_sciences_resources,
    "lab_chemistry": default_lab_chemistry_resources,
    "social_sciences": default_social_sciences_resources,
    "engineering": default_engineering_resources,
}


def get_domain_defaults(domain: Optional[str]) -> ResearchResources:
    """Look up default resources for a research domain. Falls back to general."""
    if not domain:
        return default_academic_resources()
    factory = DOMAIN_DEFAULTS.get(domain.lower(), default_academic_resources)
    return factory()


# ============================================================================
# Ingestor
# ============================================================================

_RESOURCES_EXTRACT_PROMPT = """You are extracting research resource information from a researcher's free-text description.

Be conservative: only fill fields the text clearly supports. NEVER invent specific numbers, dataset names, or equipment.

Researcher description:
---
{text}
---

Return a JSON object with this structure (omit any field where the text gives no signal):

{{
  "compute": {{
    "hpc_cluster_name": <e.g. "University HPC cluster" or null>,
    "gpu_count": <integer or null>,
    "gpu_type": <e.g. "A100" or null>,
    "cpu_cores": <integer or null>,
    "storage_tb": <number or null>,
    "compute_hours_year": <integer or null>,
    "notes": <free-text summary, or empty string>
  }},
  "data": {{
    "public_datasets": [<named datasets mentioned>],
    "proprietary_data": [<named proprietary datasets>],
    "notes": <free-text or empty>
  }},
  "instruments": {{
    "telescopes_observatories": [<named instruments>],
    "lab_equipment": [<named equipment>],
    "core_facilities": [<named cores>],
    "notes": <free-text or empty>
  }},
  "personnel": {{
    "students_available": <integer or 0>,
    "postdocs_available": <integer or 0>,
    "technical_staff": <integer or 0>,
    "named_collaborators": [<named people>],
    "notes": <free-text or empty>
  }},
  "software": {{
    "licenses": [<named licensed software>],
    "internal_tools": [<named in-house tools>],
    "notes": <free-text or empty>
  }},
  "institutional": {{
    "irb_status": <"approved"|"pending"|"n/a"|null>,
    "iacuc_status": <"approved"|"pending"|"n/a"|null>,
    "other_support": [<named offices/supports>],
    "notes": <free-text or empty>
  }}
}}

Output ONLY the JSON object. No markdown fences. No commentary."""


def ingest_resources(free_text: str) -> ResearchResources:
    """Parse a free-text description into structured ResearchResources.

    Defaults fill any gaps. The free_text is preserved verbatim in
    free_text_supplement for downstream prompts.
    """
    if not free_text or not free_text.strip():
        return default_academic_resources()

    from ..core.json_parser import JSONParser
    from ..core.llm_gateway import LLMGateway

    prompt = _RESOURCES_EXTRACT_PROMPT.format(text=free_text)
    llm = LLMGateway.get_llm("ResourcesIngestor", verbose=False)
    response = llm.run_text(prompt)

    data = JSONParser.extract_object(response.content) or {}

    try:
        resources = ResearchResources(
            compute=ComputeResources(**(data.get("compute") or {})),
            data=DataResources(**(data.get("data") or {})),
            instruments=InstrumentResources(**(data.get("instruments") or {})),
            personnel=PersonnelResources(**(data.get("personnel") or {})),
            software=SoftwareResources(**(data.get("software") or {})),
            institutional=InstitutionalResources(**(data.get("institutional") or {})),
            free_text_supplement=free_text.strip(),
        )
    except Exception:
        # If construction fails, fall back to defaults plus the raw text
        resources = default_academic_resources()
        resources.free_text_supplement = free_text.strip()

    return resources


def merge_resources_with_defaults(
    user: Optional[ResearchResources],
    funder_profile: Optional[FunderProfile] = None,
    domain: Optional[str] = None,
) -> ResearchResources:
    """Combine user resources with domain-specific defaults.

    User-provided values always win. Defaults fill ONLY where the user
    left a field empty. Pass `domain` (e.g. "ai_ml", "biomedical") to
    select a domain-aware baseline.
    """
    defaults = get_domain_defaults(domain)

    if user is None:
        return defaults

    # Compute: user wins, defaults fill notes if empty
    if not user.compute.notes and not any([
        user.compute.hpc_cluster_name, user.compute.gpu_count,
        user.compute.compute_hours_year, user.compute.cloud_credits_usd,
    ]):
        user.compute.notes = defaults.compute.notes
        # Also pull in defaults' specific compute fields if user has none
        if not user.compute.hpc_cluster_name:
            user.compute.hpc_cluster_name = defaults.compute.hpc_cluster_name
        if not user.compute.gpu_count:
            user.compute.gpu_count = defaults.compute.gpu_count
            user.compute.gpu_type = defaults.compute.gpu_type

    # Data: extend with domain defaults if user has none
    if not user.data.public_datasets and defaults.data.public_datasets:
        user.data.public_datasets = list(defaults.data.public_datasets)
    if not user.data.notes and defaults.data.notes:
        user.data.notes = defaults.data.notes

    # Instruments: extend with defaults if user has none
    if not user.instruments.telescopes_observatories and defaults.instruments.telescopes_observatories:
        user.instruments.telescopes_observatories = list(defaults.instruments.telescopes_observatories)
    if not user.instruments.lab_equipment and defaults.instruments.lab_equipment:
        user.instruments.lab_equipment = list(defaults.instruments.lab_equipment)
    if not user.instruments.core_facilities and defaults.instruments.core_facilities:
        user.instruments.core_facilities = list(defaults.instruments.core_facilities)

    # Software: extend if user has none
    if not user.software.licenses and defaults.software.licenses:
        user.software.licenses = list(defaults.software.licenses)
    if not user.software.internal_tools and defaults.software.internal_tools:
        user.software.internal_tools = list(defaults.software.internal_tools)

    # Institutional: pull in IRB/IACUC status, grants office, other support
    if user.institutional.irb_status is None and defaults.institutional.irb_status:
        user.institutional.irb_status = defaults.institutional.irb_status
    if user.institutional.iacuc_status is None and defaults.institutional.iacuc_status:
        user.institutional.iacuc_status = defaults.institutional.iacuc_status
    if not user.institutional.other_support:
        user.institutional.other_support = list(defaults.institutional.other_support)

    return user


def merge_exemplar_facilities_into_resources(
    resources: Optional[ResearchResources],
    exemplar_institutional_dict: Dict[str, Any],
) -> ResearchResources:
    """Merge facilities/equipment/collaborators from a parsed exemplar grant
    into the resources object. Never overwrites user-provided values; only
    fills empty fields.

    `exemplar_institutional_dict` is the dict form of GrantInstitutional
    from src/athanor/ingest/grant.py.
    """
    if resources is None:
        resources = default_academic_resources()

    facilities = exemplar_institutional_dict.get("facilities") or []
    equipment = exemplar_institutional_dict.get("equipment") or []
    collaborators = exemplar_institutional_dict.get("collaborators") or []

    # Facilities → instruments.core_facilities (additive, deduplicated)
    if facilities:
        existing = set(resources.instruments.core_facilities)
        for f in facilities:
            if f and f not in existing:
                resources.instruments.core_facilities.append(f)
                existing.add(f)

    # Equipment → instruments.lab_equipment
    if equipment:
        existing = set(resources.instruments.lab_equipment)
        for e in equipment:
            if e and e not in existing:
                resources.instruments.lab_equipment.append(e)
                existing.add(e)

    # Collaborators → personnel.named_collaborators
    if collaborators:
        existing = set(resources.personnel.named_collaborators)
        for c in collaborators:
            if c and c not in existing:
                resources.personnel.named_collaborators.append(c)
                existing.add(c)

    return resources
