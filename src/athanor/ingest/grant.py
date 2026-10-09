"""
Athanor exemplar-grant ingestion.

The user uploads a previously-funded grant proposal (theirs or a colleague's).
We extract:

  1. Hard facts about the researcher's situation (salary, FTE, IDC, facilities)
  2. Structural facts about successful grants in this user's domain
  3. Style/voice fingerprint (vocabulary, sentence rhythm, framings)
  4. Raw section excerpts to use as few-shot exemplars in the writer prompts

When the user has no exemplar, the GrantWeaver falls back to FunderProfile
defaults from funder_profiles.py.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from ..core.json_parser import JSONParser
from ..core.llm_gateway import LLMGateway
from ..core.rag_tools import WebScrapeTool
from .funder_profiles import FunderProfile, get_profile


# ============================================================================
# Schema
# ============================================================================

class GrantBudget(BaseModel):
    """Hard budget facts extracted from the exemplar."""
    total_direct_costs_usd: Optional[float] = None
    total_indirect_costs_usd: Optional[float] = None
    indirect_cost_rate: Optional[float] = None  # decimal, e.g. 0.65
    pi_salary_base_usd: Optional[float] = None
    pi_fte_percent: Optional[float] = None  # decimal, e.g. 0.25 = 25%
    fringe_rate: Optional[float] = None
    duration_months: Optional[int] = None
    personnel_count: Optional[int] = None
    notable_line_items: List[str] = Field(default_factory=list)


class GrantStructure(BaseModel):
    """Structural inventory of the exemplar."""
    title: Optional[str] = None
    funding_agency: Optional[str] = None
    program: Optional[str] = None
    sections_used: List[str] = Field(default_factory=list)
    page_allocations: Dict[str, int] = Field(default_factory=dict)


class GrantStyle(BaseModel):
    """Voice fingerprint for style imitation."""
    voice: str = "formal academic"
    sentence_length: str = "medium"  # short | medium | long
    citation_format: Optional[str] = None  # numeric | author-year | footnotes
    favored_phrases: List[str] = Field(default_factory=list)
    avoided_phrases: List[str] = Field(default_factory=list)
    uses_first_person: bool = False
    uses_active_voice: bool = True


class GrantInstitutional(BaseModel):
    """Researcher / institution facts that persist across projects."""
    pi_name: Optional[str] = None
    institution: Optional[str] = None
    department: Optional[str] = None
    facilities: List[str] = Field(default_factory=list)
    equipment: List[str] = Field(default_factory=list)
    collaborators: List[str] = Field(default_factory=list)
    prior_funding_history: List[str] = Field(default_factory=list)


class ExemplarGrant(BaseModel):
    """A parsed exemplar grant + style fingerprint."""
    source_path: Optional[str] = None
    budget: GrantBudget = Field(default_factory=GrantBudget)
    structure: GrantStructure = Field(default_factory=GrantStructure)
    style: GrantStyle = Field(default_factory=GrantStyle)
    institutional: GrantInstitutional = Field(default_factory=GrantInstitutional)

    # raw_excerpts: section_name -> first ~1500 chars of that section.
    # Used as few-shot exemplars in writer prompts.
    raw_excerpts: Dict[str, str] = Field(default_factory=dict)

    # Which funder profile this exemplar matches (computed at merge time)
    funder_short_name: Optional[str] = None

    def merge_with_funder_defaults(self, profile: FunderProfile) -> "MergedGrantContext":
        """Combine the exemplar's extracted values with funder defaults.
        Exemplar wins where present; profile fills the gaps."""
        return MergedGrantContext(
            # Budget: exemplar wins, then funder defaults
            budget_cap_usd=(
                self.budget.total_direct_costs_usd
                or profile.typical_budget_cap_usd
            ),
            indirect_cost_rate=(
                self.budget.indirect_cost_rate
                if self.budget.indirect_cost_rate is not None
                else profile.typical_idc_rate
            ),
            salary_base_usd=(
                self.budget.pi_salary_base_usd
                or (profile.salary_cap_usd or 200_000)
            ),
            pi_fte_percent=self.budget.pi_fte_percent or 0.25,
            fringe_rate=self.budget.fringe_rate or profile.fringe_rate,
            duration_months=self.budget.duration_months or profile.typical_duration_months,
            # Structure: exemplar's sections if present, else funder's required list
            sections=(
                self.structure.sections_used
                if self.structure.sections_used
                else profile.required_sections
            ),
            page_limits=(
                {**profile.page_limits, **self.structure.page_allocations}
                if self.structure.page_allocations
                else profile.page_limits
            ),
            # Style: exemplar wins
            voice=self.style.voice or profile.voice_register,
            favored_phrases=list(self.style.favored_phrases),
            avoided_phrases=list(self.style.avoided_phrases),
            # Institutional context
            facilities=list(self.institutional.facilities),
            equipment=list(self.institutional.equipment),
            # Few-shot excerpts
            section_excerpts=dict(self.raw_excerpts),
            # Funder metadata
            funder_name=profile.name,
            expects_preliminary_data=profile.expects_preliminary_data,
            expects_innovation_section=profile.expects_innovation_section,
        )


class MergedGrantContext(BaseModel):
    """Final values used by GrantWeaver — exemplar + funder defaults merged."""
    # Budget
    budget_cap_usd: float
    indirect_cost_rate: float
    salary_base_usd: float
    pi_fte_percent: float
    fringe_rate: float
    duration_months: int

    # Structure
    sections: List[str]
    page_limits: Dict[str, int]

    # Style
    voice: str
    favored_phrases: List[str]
    avoided_phrases: List[str]

    # Institutional context
    facilities: List[str]
    equipment: List[str]

    # Few-shot excerpts (section_name -> raw text)
    section_excerpts: Dict[str, str]

    # Funder metadata
    funder_name: str
    expects_preliminary_data: bool
    expects_innovation_section: bool

    def computed_personnel_cost(self) -> float:
        """Estimated PI personnel cost: salary * FTE * (1 + fringe) * duration_years."""
        years = self.duration_months / 12
        return self.salary_base_usd * self.pi_fte_percent * (1 + self.fringe_rate) * years

    def computed_total_with_idc(self, direct_costs: float) -> float:
        return direct_costs * (1 + self.indirect_cost_rate)


# ============================================================================
# Ingestor
# ============================================================================

_MAX_GRANT_CHARS = 60_000  # cap for the LLM input — most grants fit


def _load_grant_text(path: str) -> str:
    """Load a grant from a local file (PDF/DOCX/TXT) or URL via WebScrapeTool."""
    scraper = WebScrapeTool()
    text = scraper._run(url=path)
    if text and not text.startswith("Error"):
        return text[:_MAX_GRANT_CHARS]
    raise ValueError(f"Could not load grant from {path}: {text[:200] if text else 'empty'}")


_EXTRACT_PROMPT = """You are an expert grant analyst. Extract structured data from this previously-funded grant proposal.

Be conservative: only fill fields when the source clearly supports them. Leave fields null/empty if uncertain. NEVER invent numbers.

GRANT TEXT:
{text}

Return a JSON object with this exact structure:

{{
  "budget": {{
    "total_direct_costs_usd": <number or null>,
    "total_indirect_costs_usd": <number or null>,
    "indirect_cost_rate": <decimal e.g. 0.65, or null>,
    "pi_salary_base_usd": <number or null>,
    "pi_fte_percent": <decimal e.g. 0.25, or null>,
    "fringe_rate": <decimal or null>,
    "duration_months": <integer or null>,
    "personnel_count": <integer or null>,
    "notable_line_items": [<strings, e.g. "$50k HPC compute", "2 grad students">]
  }},
  "structure": {{
    "title": <string or null>,
    "funding_agency": <"NIH"|"NSF"|"DOE"|"Foundation"|"DARPA"|other or null>,
    "program": <e.g. "R01", "CAREER", "AI for Science", or null>,
    "sections_used": [<list of section heading names found in the document>],
    "page_allocations": {{<section_name>: <approx pages, integer>, ...}}
  }},
  "style": {{
    "voice": <"formal academic"|"narrative"|"technical"|"accessible">,
    "sentence_length": <"short"|"medium"|"long">,
    "citation_format": <"numeric"|"author-year"|"footnotes" or null>,
    "favored_phrases": [<phrases the author uses repeatedly>],
    "avoided_phrases": [<phrases conspicuously absent — e.g. "leverage", "cutting-edge">],
    "uses_first_person": <true|false>,
    "uses_active_voice": <true|false>
  }},
  "institutional": {{
    "pi_name": <string or null>,
    "institution": <string or null>,
    "department": <string or null>,
    "facilities": [<named facilities, e.g. "University HPC cluster">],
    "equipment": [<specific instruments mentioned>],
    "collaborators": [<named collaborators>],
    "prior_funding_history": [<grant numbers or programs mentioned>]
  }},
  "raw_excerpts": {{
    "Specific Aims": <first 1500 chars of that section, or empty>,
    "Approach": <first 1500 chars of approach/methods, or empty>,
    "Budget Justification": <first 1500 chars, or empty>
  }}
}}

Output ONLY the JSON object. No markdown fences. No commentary."""


def ingest_grant(path: str, funder_short_name: Optional[str] = None) -> ExemplarGrant:
    """Parse an exemplar grant from a file path or URL. Returns ExemplarGrant.

    Raises ValueError on load failure. Returns a partially-empty ExemplarGrant
    if the LLM can't parse some sections — defaults will fill the gaps later.
    """
    text = _load_grant_text(path)

    prompt = _EXTRACT_PROMPT.format(text=text)
    llm = LLMGateway.get_llm("GrantIngestor", verbose=False)
    response = llm.run_text(prompt)

    data = JSONParser.extract_object(response.content) or {}

    # Coerce nested dicts into the schema. Pydantic will ignore unknown fields.
    try:
        exemplar = ExemplarGrant(
            source_path=path,
            budget=GrantBudget(**(data.get("budget") or {})),
            structure=GrantStructure(**(data.get("structure") or {})),
            style=GrantStyle(**(data.get("style") or {})),
            institutional=GrantInstitutional(**(data.get("institutional") or {})),
            raw_excerpts=data.get("raw_excerpts") or {},
            funder_short_name=funder_short_name,
        )
    except Exception:
        # If construction fails for any reason, return an empty exemplar.
        # The defaults will fill everything.
        exemplar = ExemplarGrant(source_path=path, funder_short_name=funder_short_name)

    return exemplar


def merge_with_defaults(
    exemplar: Optional[ExemplarGrant],
    funder_short_name: Optional[str] = None,
) -> MergedGrantContext:
    """Combine an (optional) exemplar with funder defaults to produce final context.

    If no exemplar, returns the funder profile's defaults wrapped as MergedGrantContext.
    """
    profile = get_profile(funder_short_name or (exemplar.funder_short_name if exemplar else None))
    if exemplar is None:
        exemplar = ExemplarGrant(funder_short_name=funder_short_name)
    return exemplar.merge_with_funder_defaults(profile)
