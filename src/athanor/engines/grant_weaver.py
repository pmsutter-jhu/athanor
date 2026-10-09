"""
ATHANOR Grant Weaver (Stage 4).

Transforms a research plan into a structured grant proposal via
sequential PydanticLLMClient calls for RFP analysis, section drafting,
review, and quality assurance.
"""
import os
import json
import re
from typing import List, Dict, Optional, Literal, Any
from pydantic import BaseModel, Field

from ..core.config_loader import config
from ..core.tracker import tracker
from ..core.state import ProjectState, LedgerEntry
from ..core.llm_gateway import LLMGateway
from ..core.json_parser import JSONParser
from ..core.prompt_builder import ground_truth_block, provenance_instruction
from ..assistants.librarian import Librarian
from ..assistants.science_officer import ScienceOfficer
from ..assistants.bibliographer import Bibliographer


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class FeedbackItem(BaseModel):
    severity: Literal["BLOCKER", "NITPICK"]
    description: str
    location: str = Field(..., description="Section or paragraph identifier")


class JudgeVerdict(BaseModel):
    action: Literal["LOOP", "STOP"]
    filtered_feedback: List[FeedbackItem]
    reasoning: str


class GrantSection(BaseModel):
    section_id: str = Field(..., description="e.g., 'A.1', 'Methodology'")
    title: str
    content: str = ""
    constraints: List[str] = Field(default_factory=list)
    status: str = "Pending"


class ComplianceMatrix(BaseModel):
    rfp_title: str
    agency: str
    sections: List[GrantSection] = []
    formatting_requirements: Dict[str, str] = Field(default_factory=dict)


# =============================================================================
# GRANT WEAVER
# =============================================================================

# =============================================================================
# Standalone helpers (usable without the full GrantWeaver/Librarian setup)
# =============================================================================

def regenerate_proposal_section(
    state: ProjectState,
    section_idx: int,
    feedback: str,
    ui=None,
) -> str:
    """Rewrite a single proposal section to address human feedback.

    Returns the new content string. Does NOT mutate state — the caller
    assigns the returned string to ``state.proposal.sections[idx].content``.

    Runs a single LLM call; no Librarian/RFP reload needed. Used by the
    Proposal tab's per-section regenerate button so users can iterate on
    one paragraph without re-running the whole 7-call weave_proposal.

    In mock mode, returns the original content with a visible marker so
    tests stay deterministic.
    """
    if not state.proposal or section_idx >= len(state.proposal.sections):
        raise ValueError(f"No section at index {section_idx}")

    section = state.proposal.sections[section_idx]

    if config.llm_provider == "mock":
        return (
            (section.content or "") + f"\n\n[MOCK REGENERATED · feedback: {(feedback or '').strip()}]"
        )

    other_titles = [
        s.title for i, s in enumerate(state.proposal.sections) if i != section_idx
    ]

    # Re-use the same plan-derived context the original draft saw
    plan_narrative = state.plan_narrative_block()
    budget_block = state.format_compute_budget_block()

    parts: List[str] = [
        "You are revising ONE section of an existing grant proposal.",
        "Your job is to rewrite the section to address the human reviewer's "
        "feedback while preserving the voice and factual content of the rest.",
        "",
        f"=== SECTION: {section.title} ===",
        "CURRENT CONTENT:",
        section.content or "(empty)",
        "",
        "HUMAN REVIEWER FEEDBACK (must address as hard constraints):",
        (feedback or "").strip() or "(none — general polish)",
        "",
        f"OTHER SECTIONS IN THIS PROPOSAL (for coherence; do NOT duplicate their content):",
        ", ".join(other_titles) or "(none)",
        "",
    ]
    if plan_narrative:
        parts.append("=== PLAN-DERIVED NARRATIVE (from Stage 3 DAG) ===")
        parts.append(plan_narrative)
        parts.append("=== END ===")
        parts.append("")
    if budget_block:
        parts.append("=== PLAN-DERIVED COMPUTE BUDGET ===")
        parts.append(budget_block)
        parts.append("=== END ===")
        parts.append("")

    parts.append(
        "Output: the REVISED content for this section only. No section header. "
        "No commentary. No code fences. Just the new body text in Markdown."
    )
    prompt = "\n".join(parts)

    llm = LLMGateway.get_llm("GrantWeaverSectionRegenerator", verbose=False)
    if ui is not None:
        try:
            ui.log_status(f"    >> Regenerating section: {section.title}")
        except Exception:
            pass

    response = llm.run_text(prompt)
    new_content = (response.content or "").strip()

    # Strip common LLM boilerplate
    if new_content.startswith("```"):
        lines = new_content.split("\n")
        new_content = "\n".join(lines[1:]).strip()
        if new_content.endswith("```"):
            new_content = new_content[:-3].strip()

    return new_content


class GrantWeaver:
    def __init__(self, rfp_path: str):
        self.rfp_path = rfp_path
        self.ui = None

        self.librarian = Librarian(doc_path=rfp_path, collection_name="rfp_rag_store")
        if not self.librarian.text_content or "Error" in (self.librarian.text_content[:20] or ""):
            reason = (self.librarian.text_content or "No content")[:200]
            raise RuntimeError(f"Could not load RFP at {rfp_path}. Reason: {reason}")

        self.science_officer = ScienceOfficer()
        self.llm = LLMGateway.get_llm(name="GrantWeaver")

    def set_ui(self, ui):
        self.ui = ui
        if self.librarian:
            self.librarian.ui = ui
        if self.science_officer:
            self.science_officer.ui = ui

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _smart_chunk(self, text: str, chunk_size: int = 15000) -> List[str]:
        chunks, current, current_len = [], [], 0
        for p in text.split("\n\n"):
            if current_len + len(p) < chunk_size:
                current.append(p)
                current_len += len(p) + 2
            else:
                if current:
                    chunks.append("\n\n".join(current))
                current, current_len = [p], len(p) + 2
        if current:
            chunks.append("\n\n".join(current))
        return chunks

    def _clean_proposal(self, content: str) -> str:
        if not content:
            return ""
        content = re.sub(r"<(thinking|thought)>.*?</\1>", "", content, flags=re.DOTALL | re.IGNORECASE)
        if "<thinking" in content.lower():
            content = content.split("<thinking")[0]
        content = content.strip()
        if content.startswith("```markdown"):
            content = content[11:].strip()
            if content.endswith("```"):
                content = content[:-3].strip()
        elif content.startswith("```"):
            lines = content.split("\n")
            if len(lines[0]) < 15:
                content = "\n".join(lines[1:]).strip()
                if content.endswith("```"):
                    content = content[:-3].strip()
        return content.strip()

    def _query_rfp(self, query: str) -> str:
        """Query the RFP document via the librarian."""
        return self.librarian.query(query)

    # ------------------------------------------------------------------
    # Main orchestration
    # ------------------------------------------------------------------

    def weave_proposal(
        self,
        state: ProjectState,
        verbose: bool = True,
        feedback: Optional[str] = None,
    ) -> ProjectState:
        """Run the full Stage 4 weave pipeline.

        Args:
            state: project state to update in place.
            verbose: verbose logging.
            feedback: optional free-text note from the human reviewer. When
                set (from the Proposal tab's "Re-run with feedback" button),
                it's injected into the draft prompt as a hard constraint
                block so the re-run addresses the specific concerns.
        """
        if config.llm_provider == "mock":
            return self._mock_proposal(state, feedback=feedback)

        from ..core.state_manager import StateManager
        from ..core.provenance_manager import ProvenanceManager
        from ..ingest import ExemplarGrant, merge_with_defaults

        manager = StateManager()
        project_dir = manager.get_project_dir(state)
        figures_dir = os.path.join(project_dir, "figures")
        os.makedirs(figures_dir, exist_ok=True)

        # -- Resolve grant context: exemplar (if any) merged with funder defaults --
        exemplar_obj = None
        if state.exemplar_grant:
            try:
                exemplar_obj = ExemplarGrant.model_validate(state.exemplar_grant)
            except Exception:
                exemplar_obj = None
        grant_ctx = merge_with_defaults(exemplar_obj, state.funder_short_name)
        self.ui.log_status(
            f"    >> Grant context: {grant_ctx.funder_name} | "
            f"cap=${grant_ctx.budget_cap_usd:,.0f} | "
            f"IDC={grant_ctx.indirect_cost_rate * 100:.0f}% | "
            f"voice={grant_ctx.voice}",
            level="verbose",
        )

        # -- Science Officer (evidence generation) --
        # Gated behind config.generate_demo_figures. Default OFF: real
        # grant runs should cite the PI's actual prior work and
        # preliminary results (see state.preliminary_work), not
        # fabricated plots. When OFF, we pass an empty figure_path and
        # the draft prompt instructs the writer not to reference a figure.
        figure_path = ""
        if config.generate_demo_figures:
            self.science_officer.work_dir = figures_dir
            figure_path = os.path.join("figures", "placeholder.png")
            self.ui.log_status(message="    >> Dispatching Science Officer (demo figure mode)...")
            abs_figure_path = self.science_officer.generate_evidence(state.refined_hypothesis)
            if os.path.isabs(abs_figure_path):
                figure_path = os.path.relpath(abs_figure_path, project_dir)
            else:
                figure_path = abs_figure_path.replace(project_dir + os.sep, "")
            self.ui.log_status(level="verbose", message=f"    >> Science Officer returned: {figure_path}")
        else:
            self.ui.log_status(
                level="verbose",
                message="    >> Demo figure generation disabled (grant.generate_demo_figures=false). Relying on PI's prior work + preliminary results for Stage 4.",
            )

        provenance = ProvenanceManager.get_provenance_instruction()
        ground_truth = ground_truth_block()
        # Read the plan via the DAG-first helper; falls back to state.tasks
        # only when state.dag is empty (legacy projects / mock-only runs).
        wbs_str = "\n".join(
            f"- {row['name']}: {row['description']}"
            for row in state.flat_plan_for_reports()
        )
        profile = state.researcher_profile

        # =================================================================
        # STEP 1: RFP Compliance Analysis
        # =================================================================
        self.ui.log_status("    >> Analyzing RFP requirements...")
        rfp_context = self._query_rfp("required sections budget timeline formatting")

        compliance_prompt = (
            "You are an RFP Shredder. Analyze the RFP and extract a Compliance Matrix.\n\n"
            f"RFP Context:\n{rfp_context}\n\n"
            "Extract: 1) Required Sections, 2) Budget Constraints, "
            "3) Timeline Requirements, 4) Formatting Rules, 5) Key Personnel.\n\n"
            "Return a JSON object with: sections, budget_limits, timeline_constraints, formatting_rules."
        )

        tracker.start_timer("RFP Analysis")
        compliance_raw = self.llm.run_text(compliance_prompt).content
        tracker.stop_timer("RFP Analysis")

        # =================================================================
        # STEP 2: Specialized Sections (parallel-style, sequential calls)
        # =================================================================
        self.ui.log_status("    >> Drafting specialized sections...")

        # Data Management Plan — enriched with prior data practices,
        # resources (storage, compute), and any prior dataset releases
        tracker.start_timer("DMP")
        dmp_context_parts = [
            f"Write the Data Management Plan for: {state.project_name}\n",
            f"WBS:\n{wbs_str}\n",
        ]
        # Prior data releases / datasets from the PI's track record
        data_items = [
            pw for pw in state.preliminary_work
            if pw.kind in ("dataset", "code", "software") and pw.role == "prior_work"
        ]
        if data_items:
            dmp_context_parts.append(
                "PI's prior data/software releases (cite as track record of responsible data stewardship):\n"
                + "\n".join(f"  - {item.title} ({item.date or '?'}): {item.description[:150]}" for item in data_items[:5])
                + "\n"
            )
        # Storage and compute resources available
        resources_block = ""
        if state.resources:
            try:
                from ..ingest import ResearchResources
                rr = ResearchResources.model_validate(state.resources)
                if rr.compute.storage_tb:
                    resources_block += f"Available storage: {rr.compute.storage_tb:.0f} TB\n"
                if rr.data.public_datasets:
                    resources_block += f"Public datasets in use: {', '.join(rr.data.public_datasets[:5])}\n"
                if rr.institutional.grants_office_support:
                    resources_block += "Institutional grants office support: Yes\n"
            except Exception:
                pass
        if resources_block:
            dmp_context_parts.append(f"Resources:\n{resources_block}")
        dmp_context_parts.append(f"Compliance:\n{compliance_raw}\n\n{provenance}")
        dmp_section = self.llm.run_text("".join(dmp_context_parts)).content
        tracker.stop_timer("DMP")

        # Biographical Sketches — uses both prose AND structured fields
        tracker.start_timer("Biosketches")

        # Build a structured publications block from the parsed data
        pub_block = ""
        if profile.publications:
            pub_lines = []
            for pub in profile.publications[:10]:
                title = pub.get("title", "")
                year = pub.get("year", "?")
                venue = pub.get("venue", "")
                cites = pub.get("citations")
                cite_str = f", {cites} citations" if cites is not None else ""
                doi = pub.get("doi")
                doi_str = f" [DOI:{doi}]" if doi else ""
                pub_lines.append(f"  - {title} ({year}, {venue}{cite_str}){doi_str}")
            pub_block = (
                "\n=== STRUCTURED PUBLICATION LIST (from ORCID/Semantic Scholar — CITE THESE BY TITLE) ===\n"
                + "\n".join(pub_lines)
                + "\n=== END ===\n"
            )

        # Career timeline block
        career_block = ""
        ct = profile.career_timeline
        if ct:
            career_parts = []
            if ct.get("phd_year") and ct.get("phd_institution"):
                career_parts.append(f"PhD: {ct['phd_institution']} ({ct['phd_year']})")
            if ct.get("postdoc_start") and ct.get("postdoc_institution"):
                career_parts.append(f"Postdoc: {ct['postdoc_institution']} ({ct['postdoc_start']})")
            if ct.get("current_role_start") and ct.get("current_institution"):
                career_parts.append(f"Current: {ct['current_institution']} (since {ct['current_role_start']})")
            if ct.get("career_stage"):
                career_parts.append(f"Career stage: {ct['career_stage']}")
            if career_parts:
                career_block = "Career: " + "; ".join(career_parts) + "\n"

        # Funding history block
        funding_block = ""
        if profile.funding_history:
            fund_lines = []
            for grant in profile.funding_history[:8]:
                funder = grant.get("funder", "")
                title = grant.get("title", "")
                years = ""
                if grant.get("start_year"):
                    years = f" ({grant['start_year']}"
                    if grant.get("end_year"):
                        years += f"-{grant['end_year']}"
                    years += ")"
                fund_lines.append(f"  - {funder}: {title}{years}")
            funding_block = (
                "\n=== FUNDING HISTORY (from ORCID — cite as track record) ===\n"
                + "\n".join(fund_lines)
                + "\n=== END ===\n"
            )

        # Metrics line
        metrics_line = ""
        if profile.h_index is not None:
            metrics_line = (
                f"Publication metrics: h-index {profile.h_index}, "
                f"{profile.total_citations or '?'} total citations, "
                f"{profile.recent_publication_count or '?'} publications in last 5 years.\n"
            )

        # Software/code releases
        sw_block = ""
        if profile.software_releases:
            sw_lines = [
                f"  - {r['name']} ({r.get('language', '')}, {r.get('stars', 0)} stars): {r.get('description', '')}"
                for r in profile.software_releases[:5]
            ]
            sw_block = (
                "\n=== SOFTWARE RELEASES (from GitHub) ===\n"
                + "\n".join(sw_lines)
                + "\n=== END ===\n"
            )

        bio_section = self.llm.run_text(
            f"Write Biographical Sketches for:\n"
            f"PI: {profile.name}, {profile.affiliation}\n"
            f"{career_block}"
            f"{metrics_line}"
            f"Expertise: {profile.domain_expertise}\n"
            f"Motivation: {profile.persona_motivation}\n"
            f"Connections: {profile.important_connections}\n"
            f"Publications (prose summary): {profile.publication_history}\n"
            f"{pub_block}"
            f"{funding_block}"
            f"{sw_block}"
            f"Resources: {profile.available_resources}\n"
            f"Team: {', '.join(profile.team_members)}\n\n"
            "IMPORTANT: Cite publications by their exact titles from the structured list. "
            "Cite funding by funder + title from the funding history. Do NOT invent "
            "publications, grants, or h-index values — only use what's listed above.\n"
            f"{provenance}"
        ).content
        tracker.stop_timer("Biosketches")

        # Budget Justification — uses real numbers from grant_ctx
        tracker.start_timer("Budget")
        budget_excerpt = grant_ctx.section_excerpts.get("Budget Justification", "")
        budget_excerpt_block = (
            f"\n=== EXEMPLAR (PI's previous funded budget — match this voice) ===\n"
            f"{budget_excerpt[:4000]}\n=== END EXEMPLAR ===\n"
            if budget_excerpt else ""
        )
        personnel_cost = grant_ctx.computed_personnel_cost()
        budget_section = self.llm.run_text(
            f"Write the Budget Justification.\n\n"
            f"FUNDER: {grant_ctx.funder_name}\n"
            f"BUDGET CAP (direct costs): ${grant_ctx.budget_cap_usd:,.0f}\n"
            f"INDIRECT COST RATE: {grant_ctx.indirect_cost_rate * 100:.0f}% MTDC\n"
            f"PI SALARY BASE: ${grant_ctx.salary_base_usd:,.0f}\n"
            f"PI FTE: {grant_ctx.pi_fte_percent * 100:.0f}%\n"
            f"FRINGE RATE: {grant_ctx.fringe_rate * 100:.0f}%\n"
            f"DURATION: {grant_ctx.duration_months} months\n"
            f"COMPUTED PI PERSONNEL COST: ${personnel_cost:,.0f}\n\n"
            f"WBS:\n{wbs_str}\n\n"
            f"Compliance:\n{compliance_raw}\n\n"
            "Use the exact numbers above. Compute totals: salary*FTE*(1+fringe)*years for personnel. "
            "Apply the IDC rate to direct costs. Justify every line item > $1,000 against a specific WBS task. "
            "Use NON-ROUND numbers — round numbers signal unrealistic budgets.\n"
            f"{budget_excerpt_block}"
            f"{provenance}"
        ).content
        tracker.stop_timer("Budget")

        # Facilities — combines structured ResearchResources, exemplar grant
        # institutional facts, and the unstructured profile blob
        tracker.start_timer("Facilities")
        facilities_block = ""
        if grant_ctx.facilities or grant_ctx.equipment:
            fac_str = ", ".join(grant_ctx.facilities) if grant_ctx.facilities else "(none listed)"
            eq_str = ", ".join(grant_ctx.equipment) if grant_ctx.equipment else "(none listed)"
            facilities_block = (
                f"\n=== INSTITUTIONAL FACILITIES (from PI's prior grant) ===\n"
                f"Facilities: {fac_str}\nEquipment: {eq_str}\n=== END ===\n"
            )

        # Structured resources (from state.resources) — the most reliable source
        resources_block = ""
        if state.resources:
            try:
                from ..ingest import ResearchResources
                rr = ResearchResources.model_validate(state.resources)
                resources_block = (
                    f"\n=== DECLARED RESEARCH RESOURCES (from PI) ===\n"
                    f"{rr.to_facilities_text()}\n=== END ===\n"
                )
            except Exception:
                pass

        # Plan-derived compute budget — Stage 3 DAG nodes with compute_spec
        # populated give us concrete totals (CPU-h, GPU-h, storage, human-h)
        # that the writer can reference instead of hand-waving.
        plan_budget_block = ""
        plan_budget = state.format_compute_budget_block()
        if plan_budget:
            plan_budget_block = f"\n=== {plan_budget}\n=== END ===\n"

        facilities_section = self.llm.run_text(
            f"Write the Facilities and Equipment section.\n"
            f"PI Affiliation: {profile.affiliation if profile else 'Unknown'}\n"
            f"PI Resources (free text): {profile.available_resources if profile else ''}\n"
            f"{resources_block}"
            f"{facilities_block}"
            f"{plan_budget_block}"
            f"WBS:\n{wbs_str}\n\n"
            "Use the DECLARED RESEARCH RESOURCES verbatim where possible — these are "
            "facts from the PI. Do not invent equipment or facilities not listed.\n"
            "When the PLAN-DERIVED COMPUTE BUDGET is present, cite the concrete "
            "numbers (CPU-h, GPU-h, storage, human-h) in the section so the "
            "reviewer can see how the plan matches the available infrastructure.\n"
            f"{provenance}"
        ).content
        tracker.stop_timer("Facilities")

        # =================================================================
        # STEP 3: Full Proposal Draft
        # =================================================================
        self.ui.log_status("    >> [Drafting] Generating full proposal...")

        # Build style guidance from grant_ctx
        style_block = (
            f"VOICE: {grant_ctx.voice}\n"
        )
        if grant_ctx.favored_phrases:
            style_block += f"USE these phrases naturally: {', '.join(grant_ctx.favored_phrases[:8])}\n"
        if grant_ctx.avoided_phrases:
            style_block += f"NEVER use: {', '.join(grant_ctx.avoided_phrases[:8])}\n"
        style_block += (
            "NEVER use these phrases (they signal AI-generated text to reviewers):\n"
            "  leverage, cutting-edge, groundbreaking, novel approach, paradigm shift,\n"
            "  robust framework, comprehensive solution, synergy, multifaceted,\n"
            "  holistic approach, transformative potential, innovative methodology,\n"
            "  unprecedented opportunity, state-of-the-art, in this proposal we,\n"
            "  the PI will leverage, the team brings complementary expertise,\n"
            "  this exciting research, at the forefront of.\n"
            "Instead: be SPECIFIC. Name the method, the dataset, the tool, the result.\n"
            "Use NON-ROUND budget numbers (e.g. $94,572 not $95,000) and concrete "
            "specifics (e.g. 'NVIDIA A100 80GB on a university HPC cluster').\n"
            "Do NOT start sections with 'In this proposal, we...' — start with the "
            "scientific question or the result.\n"
        )

        # Inject section excerpts as few-shot exemplars
        excerpt_block = ""
        if grant_ctx.section_excerpts:
            excerpt_pieces = []
            for sec_name, sec_text in grant_ctx.section_excerpts.items():
                if sec_text:
                    excerpt_pieces.append(
                        f"\n--- PI's previous '{sec_name}' (match this voice) ---\n{sec_text[:4000]}"
                    )
            if excerpt_pieces:
                excerpt_block = (
                    "\n=== STYLE EXEMPLARS FROM PI'S PRIOR FUNDED GRANT ===\n"
                    + "\n".join(excerpt_pieces)
                    + "\n=== END EXEMPLARS ===\n"
                )

        sections_to_include = ", ".join(grant_ctx.sections[:12])

        # PI's prior work (auto-extracted from profile + user-added) —
        # cite by name, never fabricate.
        prior_work_block = ""
        pw_narrative = state.prior_work_narrative_block()
        if pw_narrative:
            prior_work_block = (
                "\n=== PI'S PRIOR RELEVANT WORK (from profile + user additions) ===\n"
                + pw_narrative
                + "\n=== END ===\n"
                "Cite these in the Prior Work / Background / Biographical Sketch "
                "sections. Use the titles, dates, and citations VERBATIM. Do NOT "
                "fabricate publications, authors, venues, or results.\n"
            )

        # Preliminary results specific to this proposal (user-added +
        # promoted from DONE DAG nodes in Stage 5).
        prelim_results_block = ""
        pr_narrative = state.preliminary_results_narrative_block()
        if pr_narrative:
            prelim_results_block = (
                "\n=== PRELIMINARY RESULTS (specific to this proposal) ===\n"
                + pr_narrative
                + "\n=== END ===\n"
                "Cite these in the Preliminary Data / Feasibility section. "
                "Reference attached files by filename. Describe results honestly "
                "and do NOT extrapolate beyond what was actually observed. If a "
                "pilot was inconclusive, say so.\n"
            )

        # Relevant chunks from attached documents (project knowledge base)
        kb_block = ""
        try:
            from ..assistants.project_kb import ProjectKnowledgeBase
            project_dir = manager.get_project_dir(state)
            kb = ProjectKnowledgeBase(project_dir=project_dir, ui=self.ui)
            if kb.stats()["total_chunks"] > 0:
                # Query for both prior work and preliminary data context
                hits_prior = kb.query(
                    f"prior publications and work by {profile.name if profile else 'the PI'} relevant to {state.refined_hypothesis or ''}",
                    top_k=4,
                )
                hits_prelim = kb.query(
                    f"preliminary data, pilot results, and feasibility evidence for {state.refined_hypothesis or ''}",
                    top_k=4,
                )
                all_hits = hits_prior + hits_prelim
                # Dedupe on chunk text
                seen: set = set()
                unique_hits = []
                for h in all_hits:
                    key = h["chunk_text"][:100]
                    if key in seen:
                        continue
                    seen.add(key)
                    unique_hits.append(h)
                if unique_hits:
                    chunks_text_parts = []
                    for i, h in enumerate(unique_hits[:8], 1):
                        source_name = os.path.basename(h.get("source_path") or "unknown")
                        chunks_text_parts.append(
                            f"[Chunk {i} from {source_name}]\n{h['chunk_text'][:800]}"
                        )
                    kb_block = (
                        "\n=== RELEVANT CHUNKS FROM PI'S ATTACHED DOCUMENTS ===\n"
                        + "\n\n".join(chunks_text_parts)
                        + "\n=== END ===\n"
                        "These are verbatim excerpts from files the PI attached to "
                        "their prior work or preliminary results. Cite them by "
                        "filename where relevant — do NOT paraphrase as if they "
                        "were your own writing.\n"
                    )
        except Exception as e:
            # KB is optional — degrade silently if it fails
            self.ui.log_status(
                f"    [KB] Skipped knowledge base query: {e}",
                level="verbose",
            )

        # Plan-derived narrative enrichment: per-step risks, mitigations,
        # alternatives considered, timeline, and explicit deliverables.
        # Populated by the Stage 3 planner from DAGNode fields.
        plan_narrative = state.plan_narrative_block()
        plan_narrative_block_text = ""
        if plan_narrative:
            plan_narrative_block_text = (
                "\n=== PLAN-DERIVED NARRATIVE (from Stage 3 DAG) ===\n"
                + plan_narrative
                + "\n=== END ===\n"
                "Use the RISKS AND MITIGATIONS verbatim in the proposal's risk section. "
                "Use ALTERNATIVES CONSIDERED verbatim in the rationale section. Use the "
                "PROJECT TIMELINE as the basis for any Gantt chart or milestone list. "
                "Use PRODUCTS OF THE RESEARCH as the Products/Deliverables section.\n"
            )

        # Human reviewer feedback from the Proposal tab's "Re-run with feedback"
        # button. Injected into the draft prompt as a hard constraint block so
        # the re-run actually addresses the user's concerns.
        feedback_block = ""
        if feedback and feedback.strip():
            feedback_block = (
                "\n=== HUMAN REVIEWER FEEDBACK (hard constraints) ===\n"
                f"{feedback.strip()}\n"
                "=== END ===\n"
                "The reviewer's concerns override your defaults. Treat every item "
                "above as a non-negotiable constraint — restructure the draft to "
                "accommodate them rather than dismissing them.\n"
            )

        # Evidence slot: either a real figure path, or explicit no-figure
        # instructions when demo-figure generation is disabled.
        if figure_path:
            evidence_block = f"[EVIDENCE] {figure_path}\n\n"
        else:
            evidence_block = (
                "[EVIDENCE] No figure generated for this run. Do NOT reference a "
                "'Figure 1' or 'the plot' or 'the chart below' — there is no "
                "figure. Build the Preliminary Data / Background story from the "
                "PI's prior work + preliminary results blocks above. If neither "
                "is populated, state honestly that feasibility will be "
                "demonstrated during the funded project's pilot phase, and lean "
                "on the PI's domain expertise from the profile.\n\n"
            )

        # Pre-compute conditional blocks.
        # Unresolved issues from Stage 2 debate — the grant writer should
        # acknowledge these and explain how the proposal addresses them.
        _unresolved_issues_block = ""
        if state.scorecard and state.scorecard.unresolved_issues:
            issues_text = "\n".join(
                f"  - {issue}" for issue in state.scorecard.unresolved_issues[:8]
            )
            _unresolved_issues_block = (
                "\n=== UNRESOLVED SCIENTIFIC ISSUES (from Stage 2 debate) ===\n"
                f"{issues_text}\n"
                "=== END ===\n"
                "Address each of these in the proposal. Show the reviewer you are "
                "aware of these concerns and explain how the proposed work will "
                "resolve or mitigate them. This is a strength signal — reviewers "
                "trust PIs who acknowledge weaknesses.\n"
            )

        _pi_metrics_line = (
            f"h-index: {profile.h_index}, citations: {profile.total_citations}, "
            f"recent pubs (5yr): {profile.recent_publication_count}\n"
        ) if profile.h_index is not None else ""
        _pi_career_line = (
            f"Career: {'; '.join(f'{k}={v}' for k,v in profile.career_timeline.items())}\n"
        ) if profile.career_timeline else ""

        draft_prompt = (
            f"You are a Senior Scientist writing a major grant proposal for {grant_ctx.funder_name}.\n"
            f"{ground_truth}\n"
            f"=== STYLE ===\n{style_block}\n"
            f"=== PROJECT BACKBONE ===\n"
            f"[TITLE] {state.project_name}\n"
            f"[HYPOTHESIS] {state.refined_hypothesis}\n"
            f"[ABSTRACT] {state.project_abstract}\n"
            f"[PI] {profile.name}, {profile.affiliation}\n"
            f"Team: {', '.join(profile.team_members)}\n"
            f"Expertise: {profile.domain_expertise}\n"
            f"Motivation: {profile.persona_motivation}\n"
            f"Connections: {profile.important_connections}\n"
            f"Resources: {profile.available_resources}\n"
            f"{_pi_metrics_line}"
            f"{_pi_career_line}"
            f"[WBS]\n{wbs_str}\n"
            f"{prior_work_block}"
            f"{prelim_results_block}"
            f"{kb_block}"
            f"{plan_narrative_block_text}"
            f"{_unresolved_issues_block}"
            f"{feedback_block}"
            f"{evidence_block}"
            f"COMPLIANCE:\n{compliance_raw}\n\n"
            f"REQUIRED SECTIONS for {grant_ctx.funder_name}: {sections_to_include}\n"
            + ("PRELIMINARY DATA: Required — include a section.\n" if grant_ctx.expects_preliminary_data else "")
            + ("INNOVATION: Required as separate section.\n" if grant_ctx.expects_innovation_section else "")
            + f"{excerpt_block}\n"
            "Write a COMPLETE grant proposal. INCORPORATE the specialized sections below verbatim:\n\n"
            f"--- DATA MANAGEMENT PLAN ---\n{dmp_section}\n\n"
            f"--- BIOGRAPHICAL SKETCHES ---\n{bio_section}\n\n"
            f"--- BUDGET JUSTIFICATION ---\n{budget_section}\n\n"
            f"--- FACILITIES ---\n{facilities_section}\n\n"
            f"{provenance}\n"
            "Output: Complete Markdown document with ALL sections in the order listed above."
        )

        tracker.start_timer("Proposal Drafting")
        current_draft = self.llm.run_text(draft_prompt, timeout=600).content
        tracker.stop_timer("Proposal Drafting")
        current_draft = self._clean_proposal(current_draft)

        if not current_draft or len(current_draft) < 200:
            raise RuntimeError("Failed to generate initial proposal draft.")

        # =================================================================
        # STEP 4: Review Loop
        # =================================================================
        MAX_REVISIONS = config.weaver_max_revisions
        outstanding_issues = []

        for revision in range(MAX_REVISIONS):
            self.ui.log_status(f"    >> Revision {revision + 1}/{MAX_REVISIONS}")

            # Combined QA: citations + compliance
            self.ui.log_status(message="    [QA] Citation + compliance review...")
            qa_chunks = self._smart_chunk(current_draft, chunk_size=15000)
            annotated_chunks = []
            all_issues = []

            for ci, chunk in enumerate(qa_chunks):
                self.ui.log_status(level="verbose", message=f"     > Fragment {ci + 1}/{len(qa_chunks)}...")
                qa_prompt = (
                    "COMBINED QA REVIEW:\n\n"
                    f"{chunk}\n\n"
                    "PART 1 - CITATIONS: Add missing [Source: URL]. Flag [[LACKING PROVENANCE]].\n"
                    "PART 2 - COMPLIANCE: Check RFP constraints.\n\n"
                    'Return JSON: {"annotated_text": "...", "issues": [{"issue":"...", "severity":"BLOCKER|NITPICK", "location":"..."}]}'
                )
                tracker.start_timer("Combined QA")
                qa_raw = self.llm.run_text(qa_prompt).content
                tracker.stop_timer("Combined QA")

                qa_data = JSONParser.extract_object(qa_raw)
                if qa_data:
                    annotated_chunks.append(qa_data.get("annotated_text", chunk))
                    all_issues.extend(qa_data.get("issues", []))
                else:
                    annotated_chunks.append(qa_raw)

            current_draft = "\n\n".join(annotated_chunks)

            # Judge verdict
            critique_text = json.dumps(all_issues, indent=2)
            judge_prompt = (
                f"Evaluate reviewer critique. Revision: {revision}.\n"
                f"{ground_truth}\n"
                f"CRITIQUE:\n{critique_text}\n\n"
                f"IF revision >= {MAX_REVISIONS - 1}: FORCE STOP.\n"
                "IF no BLOCKERS: STOP. ELSE: LOOP.\n\n"
                'Return JSON: {"action": "LOOP|STOP", "filtered_feedback": [...], "reasoning": "..."}'
            )

            self.ui.log_status("    [Judge] Evaluating...")
            tracker.start_timer("Convergence Judge")
            judge_raw = self.llm.run_text(judge_prompt).content
            tracker.stop_timer("Convergence Judge")

            action = "STOP"
            feedback_str = ""
            try:
                v_data = JSONParser.extract_object(judge_raw)
                if v_data:
                    action = v_data.get("action", "STOP")
                    feedback_items = v_data.get("filtered_feedback", [])
                    outstanding_issues = feedback_items
                    feedback_str = "\n".join(
                        f"- {i.get('description', i.get('issue', '?'))} ({i.get('location', '?')})"
                        for i in feedback_items if isinstance(i, dict)
                    )
                    self.ui.log_status(
                        level="verbose",
                        message=f"    [Judge] {action}: {v_data.get('reasoning', '')[:100]}",
                    )
            except Exception as e:
                self.ui.log_status(level="error", message=f"    [Judge] Parse error: {e}")

            if action == "STOP":
                self.ui.log_status("    >> Judge signaled STOP.")
                break

            # Revise
            self.ui.log_status(f"    >> Revising ({len(outstanding_issues)} blockers)...")
            revise_prompt = (
                f"REVISE the proposal to fix ONLY the sections affected by these BLOCKER issues:\n"
                f"{feedback_str}\n\n"
                f"ORIGINAL DRAFT:\n{current_draft}\n\n"
                "IMPORTANT: Preserve sections that are NOT flagged — copy them VERBATIM. "
                "Only rewrite the specific sections and paragraphs that the feedback targets. "
                "Do NOT change the overall structure, voice, or length of unaffected sections.\n"
                "Output the FULL REVISED MARKDOWN proposal (with unflagged sections unchanged)."
            )
            tracker.start_timer("Proposal Revision")
            current_draft = self.llm.run_text(revise_prompt, timeout=600).content
            tracker.stop_timer("Proposal Revision")
            current_draft = self._clean_proposal(current_draft)

        # =================================================================
        # STEP 5: Structure into JSON sections
        # =================================================================
        self.ui.log_status("    >> Structuring final proposal...")

        struct_prompt = (
            "Convert this Grant Proposal into a JSON list of sections.\n\n"
            f"PROPOSAL:\n{current_draft}\n\n"
            'Output ONLY: [{"title": "...", "content": "..."}]'
        )
        struct_raw = self.llm.run_text(struct_prompt).content

        sections_data = JSONParser.extract_array(struct_raw)
        if not sections_data or len(sections_data) < 3:
            raise RuntimeError(
                f"Proposal structuring failed: got {len(sections_data or [])} sections (need >=3). "
                f"Raw: {struct_raw[:200]}"
            )

        # Update state
        from ..core.state import GrantProposal, ProposalSection
        state.proposal = GrantProposal(
            rfp_title=state.project_name,
            sections=[
                ProposalSection(title=s.get("title", "Untitled"), content=s.get("content", ""))
                for s in sections_data
            ],
        )

        compliance_obj = JSONParser.extract_object(compliance_raw)
        if compliance_obj:
            state.proposal.compliance_matrix = compliance_obj

        # ── Compliance validation: check all required sections are present ──
        if compliance_obj and isinstance(compliance_obj, dict):
            required = compliance_obj.get("sections_required") or compliance_obj.get("sections") or []
            if required:
                present_titles = {s.get("title", "").lower() for s in sections_data}
                missing = []
                for req in required:
                    req_name = str(req) if not isinstance(req, dict) else (req.get("title") or req.get("section_id") or "")
                    if not any(req_name.lower() in pt for pt in present_titles):
                        missing.append(req_name)
                if missing:
                    self.ui.log_status(
                        f"    [!] Compliance gap: {len(missing)} required section(s) not found in proposal: "
                        f"{', '.join(missing[:5])}",
                        level="error",
                    )
                    # Store the gap in red_team_issues so the Proposal tab shows it
                    state.red_team_issues.append({
                        "severity": "BLOCKER",
                        "description": f"Required sections missing from proposal: {', '.join(missing)}",
                        "location": "Compliance Matrix",
                    })
                else:
                    self.ui.log_status(
                        f"    >> Compliance check: all {len(required)} required sections present.",
                        level="verbose",
                    )

        if outstanding_issues and revision >= MAX_REVISIONS:
            state.red_team_issues = [
                issue.dict() if hasattr(issue, "dict") else issue
                for issue in outstanding_issues
            ]

        state.add_ledger_entry(LedgerEntry(
            stage="Stage 4: Grant Weaver",
            agent="GrantWeaver",
            description="Generated structured grant proposal sections",
        ))

        return state

    def _mock_proposal(self, state: ProjectState, feedback: Optional[str] = None) -> ProjectState:
        """Generate a mock proposal for testing without LLM calls."""
        self.ui.log_status("    MOCK MODE: Generating instant grant proposal...")

        from ..core.state import GrantProposal, ProposalSection
        plan_rows = state.flat_plan_for_reports()
        wbs_str = "\n".join(f"- {row['name']}: {row['description']}" for row in plan_rows)
        feedback_tag = f" [FEEDBACK: {feedback.strip()}]" if feedback and feedback.strip() else ""

        state.proposal = GrantProposal(
            rfp_title=state.project_name,
            sections=[
                ProposalSection(
                    title="Abstract",
                    content=f"MOCK MODE:This proposal investigates: {state.refined_hypothesis or state.initial_shower_thought}{feedback_tag}",
                ),
                ProposalSection(
                    title="Research Plan",
                    content=f"MOCK MODE:The research plan consists of {len(plan_rows)} tasks:\n{wbs_str}",
                ),
                ProposalSection(
                    title="Broader Impacts",
                    content="MOCK MODE:This research will advance scientific understanding and train the next generation.",
                ),
                ProposalSection(
                    title="Budget Justification",
                    content="MOCK MODE:Personnel: $100,000. Computing: $25,000. Travel: $5,000. Total: $130,000.",
                ),
                ProposalSection(
                    title="Data Management Plan",
                    content="MOCK MODE:All data will be archived on Zenodo under CC-BY-4.0 license.",
                ),
            ],
        )

        # Populate a minimal compliance matrix so the Proposal tab's compliance
        # panel has something to render in mock mode / tests.
        state.proposal.compliance_matrix = {
            "rfp_title": state.project_name,
            "sections_required": ["Abstract", "Research Plan", "Broader Impacts", "Budget", "Data Management Plan"],
            "sections_covered": ["Abstract", "Research Plan", "Broader Impacts", "Budget", "Data Management Plan"],
            "missing": [],
            "formatting_requirements": {
                "page_limit": "15 pages (mock)",
                "font": "Arial 11pt (mock)",
            },
        }

        state.add_ledger_entry(LedgerEntry(
            stage="Stage 4: Grant Weaver",
            agent="GrantWeaver",
            description="MOCK MODE:Generated mock grant proposal sections",
        ))
        return state
