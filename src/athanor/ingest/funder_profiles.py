"""
Athanor funder profiles — defaults for common grant programs.

Used when the user has no exemplar grant to upload, or when fields are
missing from a partial exemplar. The merge precedence is:

    exemplar grant fields > funder profile defaults > generic defaults

These numbers are calibrated to 2024-2025 norms and should be updated
periodically. They're conservative-but-defensible: not minimums, not
maximums, just plausible values that wouldn't get a reviewer's eyebrow up.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class FunderProfile(BaseModel):
    """Defaults for a specific grant program / funder type."""

    name: str
    short_name: str  # e.g. "nih_r01", "nsf_standard"
    description: str

    # Budget
    typical_budget_cap_usd: float  # direct costs, per year
    typical_idc_rate: float  # as decimal, e.g. 0.65 = 65%
    salary_cap_usd: Optional[float] = None  # NIH cap, etc; None = no cap
    fringe_rate: float = 0.30  # fringe benefits as fraction of salary
    typical_duration_months: int = 36

    # Structure
    required_sections: List[str] = Field(default_factory=list)
    page_limits: Dict[str, int] = Field(default_factory=dict)

    # Voice / framing
    expects_preliminary_data: bool = True
    expects_innovation_section: bool = False
    voice_register: str = "formal academic"  # narrative | formal academic | technical | accessible


# ============================================================================
# Preset profiles
# ============================================================================

NIH_R01 = FunderProfile(
    name="NIH R01 (Research Project Grant)",
    short_name="nih_r01",
    description="Standalone NIH research project. Typical mid-career PI grant.",
    typical_budget_cap_usd=500_000,  # direct costs/year, NIH modular
    typical_idc_rate=0.65,           # federally negotiated rate average
    salary_cap_usd=212_100,          # NIH 2024 salary cap (Executive Level II)
    fringe_rate=0.30,
    typical_duration_months=60,
    required_sections=[
        "Project Summary/Abstract",
        "Project Narrative",
        "Specific Aims",
        "Research Strategy",         # Significance + Innovation + Approach
        "Bibliography & References Cited",
        "Vertebrate Animals",         # if applicable
        "Biographical Sketches",
        "Other Support",
        "Resources",                  # Facilities & Equipment
        "Budget Justification",
        "Data Management & Sharing Plan",
    ],
    page_limits={
        "Specific Aims": 1,
        "Research Strategy": 12,
        "Project Summary/Abstract": 1,
        "Project Narrative": 1,
    },
    expects_preliminary_data=True,
    expects_innovation_section=True,
    voice_register="formal academic",
)


NIH_R21 = FunderProfile(
    name="NIH R21 (Exploratory/Developmental)",
    short_name="nih_r21",
    description="High-risk/high-reward exploratory NIH grant. No preliminary data required.",
    typical_budget_cap_usd=275_000,  # total over 2 years
    typical_idc_rate=0.65,
    salary_cap_usd=212_100,
    fringe_rate=0.30,
    typical_duration_months=24,
    required_sections=[
        "Project Summary/Abstract",
        "Specific Aims",
        "Research Strategy",
        "Biographical Sketches",
        "Budget Justification",
        "Data Management & Sharing Plan",
    ],
    page_limits={
        "Specific Aims": 1,
        "Research Strategy": 6,
    },
    expects_preliminary_data=False,
    expects_innovation_section=True,
    voice_register="formal academic",
)


NSF_STANDARD = FunderProfile(
    name="NSF Standard Research Grant",
    short_name="nsf_standard",
    description="Standard NSF research grant. Two-criterion review: Intellectual Merit + Broader Impacts.",
    typical_budget_cap_usd=500_000,  # total over duration
    typical_idc_rate=0.55,           # NSF average
    salary_cap_usd=None,             # NSF has 2-month summer rule, not a hard cap
    fringe_rate=0.28,
    typical_duration_months=36,
    required_sections=[
        "Project Summary",            # 1 page, 3 boxes: Overview, IM, BI
        "Project Description",        # the main narrative
        "References Cited",
        "Biographical Sketches",
        "Budget Justification",
        "Current and Pending Support",
        "Facilities, Equipment, and Other Resources",
        "Data Management Plan",
        "Postdoc Mentoring Plan",     # if postdocs included
    ],
    page_limits={
        "Project Summary": 1,
        "Project Description": 15,
        "Data Management Plan": 2,
        "Postdoc Mentoring Plan": 1,
    },
    expects_preliminary_data=True,
    expects_innovation_section=False,  # NSF folds into Intellectual Merit
    voice_register="formal academic",
)


NSF_CAREER = FunderProfile(
    name="NSF CAREER Award",
    short_name="nsf_career",
    description="Early-career faculty grant. Integrates research and education.",
    typical_budget_cap_usd=400_000,  # over 5 years; varies by directorate
    typical_idc_rate=0.55,
    salary_cap_usd=None,
    fringe_rate=0.28,
    typical_duration_months=60,
    required_sections=[
        "Project Summary",
        "Project Description",        # research + education plan integrated
        "References Cited",
        "Biographical Sketches",
        "Budget Justification",
        "Current and Pending Support",
        "Facilities and Resources",
        "Data Management Plan",
        "Letter of Department Support",
    ],
    page_limits={
        "Project Summary": 1,
        "Project Description": 15,
    },
    expects_preliminary_data=True,
    expects_innovation_section=False,
    voice_register="formal academic",
)


FOUNDATION = FunderProfile(
    name="Private Foundation Grant",
    short_name="foundation",
    description="Generic private foundation grant. Lower IDC, shorter format, more accessible voice.",
    typical_budget_cap_usd=250_000,
    typical_idc_rate=0.15,           # foundations cap IDC much lower than federal
    salary_cap_usd=None,
    fringe_rate=0.28,
    typical_duration_months=24,
    required_sections=[
        "Executive Summary",
        "Statement of Need",
        "Project Description",
        "Goals and Objectives",
        "Methodology",
        "Timeline",
        "Evaluation Plan",
        "Budget and Budget Narrative",
        "Organizational Background",
        "Sustainability",
    ],
    page_limits={
        "Executive Summary": 1,
        "Project Description": 8,
    },
    expects_preliminary_data=False,
    expects_innovation_section=False,
    voice_register="accessible",   # foundations want plain English
)


DARPA = FunderProfile(
    name="DARPA Research Proposal",
    short_name="darpa",
    description="DARPA / DoD research grant. Heisenberg-scale ambition expected.",
    typical_budget_cap_usd=2_000_000,  # programs vary widely
    typical_idc_rate=0.60,
    salary_cap_usd=None,
    fringe_rate=0.28,
    typical_duration_months=36,
    required_sections=[
        "Executive Summary",
        "Goals and Impact",
        "Technical Plan",
        "Management Plan",
        "Capabilities",
        "Statement of Work",
        "Cost Proposal",
        "Schedule and Milestones",
    ],
    page_limits={
        "Executive Summary": 2,
        "Technical Plan": 20,
    },
    expects_preliminary_data=True,
    expects_innovation_section=True,
    voice_register="technical",
)


# ============================================================================
# Generic defaults — used when no funder profile is selected
# ============================================================================

GENERIC = FunderProfile(
    name="Generic Research Grant",
    short_name="generic",
    description="Conservative defaults for an unknown funder.",
    typical_budget_cap_usd=300_000,
    typical_idc_rate=0.50,
    salary_cap_usd=212_100,  # NIH cap as a safe ceiling
    fringe_rate=0.28,
    typical_duration_months=36,
    required_sections=[
        "Executive Summary",
        "Specific Aims",
        "Background and Significance",
        "Research Plan",
        "Timeline and Milestones",
        "Budget Justification",
        "Biographical Sketches",
        "Facilities and Resources",
        "Data Management Plan",
        "References",
    ],
    page_limits={
        "Executive Summary": 1,
        "Research Plan": 10,
    },
    expects_preliminary_data=True,
    expects_innovation_section=False,
    voice_register="formal academic",
)


# ============================================================================
# Lookup table
# ============================================================================

PROFILES: Dict[str, FunderProfile] = {
    "nih_r01": NIH_R01,
    "nih_r21": NIH_R21,
    "nsf_standard": NSF_STANDARD,
    "nsf_career": NSF_CAREER,
    "foundation": FOUNDATION,
    "darpa": DARPA,
    "generic": GENERIC,
}


def get_profile(short_name: Optional[str]) -> FunderProfile:
    """Look up a profile by short_name. Returns GENERIC if unknown or None."""
    if not short_name:
        return GENERIC
    return PROFILES.get(short_name.lower(), GENERIC)


def list_profiles() -> List[str]:
    """Return all available profile short_names for UI dropdowns."""
    return sorted(PROFILES.keys())
