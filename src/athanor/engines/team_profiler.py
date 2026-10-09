"""
ATHANOR Profiler Agent Module.

This module is responsible for Stage 1: Researcher Profiling.
It reads `config/profile_config.toml` and allows a specialized "Profile Builder" agent
to robustly research the PI using both provided links (WebScrape) and general search.
"""
import json
import os
from typing import Dict, List

from ..core.state import ProjectState, ResearcherProfile, LedgerEntry
from ..core.config_loader import config
from ..core.tracker import tracker
from ..core.llm_gateway import LLMGateway
from ..core.rag_tools import WebScrapeTool
from ..core.json_parser import JSONParser
from ..core.prompt_builder import ground_truth_block
from ..ingest import (
    ProfileSource,
    ingest_profile,
    ingest_resources,
    merge_resources_with_defaults,
)


class TeamProfiler:
    def __init__(self):
        self.llm_name = config.llm_model or "gemini-2.5-flash"

        # Initialize LLM using LLMGateway
        self.llm = LLMGateway.get_llm(name="TeamProfiler")
        
        # UI Provider
        self.ui = None

    def set_ui(self, ui):
        self.ui = ui

    def load_config(self) -> Dict:
        """Returns the global active configuration."""
        return config.all_configs

    
    def build_profile(self, state: ProjectState, verbose: bool = True) -> ProjectState:
        prof_cfg = self.load_config()
        if not prof_cfg:
             self.ui.log_status(level='verbose', message="    [!] No profile config found. Skipping.")
             return state

        tracker.start_timer("Stage 1: Profiling")

        # 1. Process Principal Investigator
        pi_cfg = prof_cfg.get('pi', {})
        pi_name = pi_cfg.get('name', 'Unknown Researcher')
        pi_affiliation = pi_cfg.get('affiliation', 'Unknown Affiliation')
        pi_links = pi_cfg.get('links', [])

        # Merge global resources into PI links (Context for the Lead)
        resource_links = prof_cfg.get('resources', {}).get('links', [])
        for link in resource_links:
            if link not in pi_links:
                pi_links.append(link)

        # New: structured PI source fields (bio_supplement is the killer feature
        # for non-traditional researchers without a formal CV)
        pi_source = ProfileSource(
            name=pi_name,
            affiliation=pi_affiliation,
            role="Principal Investigator",
            research_url=pi_cfg.get('research_url') or (pi_links[0] if pi_links else None),
            cv_path=pi_cfg.get('cv_path'),
            orcid=pi_cfg.get('orcid'),
            github=pi_cfg.get('github'),
            scholar_url=pi_cfg.get('scholar_url'),
            bio_supplement=pi_cfg.get('bio_supplement'),
            extra_links=[l for l in pi_links[1:]] if len(pi_links) > 1 else [],
        )

        self.ui.log_status(message=f"   > PI: {pi_name}")
        pi_profile = self._synthesize_investigator_from_source(pi_source, verbose=verbose)
        
        # Team members (global list)
        pi_profile.team_members = prof_cfg.get('team', {}).get('members', [])
        state.researcher_profile = pi_profile

        # Auto-extract structured resources from the PI's bio_supplement
        # if no resources are already declared. This lets a researcher who
        # only filled in the free-text bio still benefit from a structured
        # resources object downstream (planner constraints + facilities text).
        if state.resources is None and pi_source.bio_supplement and pi_source.bio_supplement.strip():
            try:
                self.ui.log_status(
                    "     - Extracting research resources from bio_supplement...",
                    level="verbose",
                )
                extracted = ingest_resources(pi_source.bio_supplement)
                # Apply domain-specific defaults to fill any gaps
                domain = config.research_domain
                merged = merge_resources_with_defaults(extracted, domain=domain)
                state.resources = merged.model_dump()
                state.research_domain = domain
                self.ui.log_status(
                    f"     - Resources extracted (domain: {domain})",
                    level="verbose",
                )
            except Exception as e:
                self.ui.log_status(
                    f"    [!] Resource extraction from bio_supplement failed: {e}",
                    level="error",
                )

        # Auto-extract prior work (publications, code, datasets, etc.)
        # from the same PI profile sources. Runs AFTER ingest_profile
        # so the text sources are already loaded. Items are marked
        # auto_extracted=True so a Stage 1 re-run can cleanly replace
        # them without clobbering anything the user added manually.
        self._auto_extract_prior_work(state, pi_source)

        # If ORCID didn't populate structured publications but the LLM
        # auto-extractor found publication titles, look them up via
        # Semantic Scholar by title to get DOIs + citation counts + h-index.
        if pi_profile and not pi_profile.publications:
            pub_titles = [
                pw.title for pw in state.preliminary_work
                if pw.role == "prior_work" and pw.kind == "publication" and pw.title
            ]
            if pub_titles:
                try:
                    from ..ingest.profile import enrich_publications_from_titles
                    self.ui.log_status(
                        f"     - Looking up {len(pub_titles)} publication titles via Semantic Scholar...",
                        level="verbose",
                    )
                    enrich_publications_from_titles(pi_profile, pub_titles)
                    if pi_profile.h_index is not None:
                        self.ui.log_status(
                            f"     - Found h-index={pi_profile.h_index}, "
                            f"{pi_profile.total_citations} citations, "
                            f"{len(pi_profile.publications)} publications",
                            level="verbose",
                        )
                except Exception as e:
                    self.ui.log_status(
                        f"    [!] Publication lookup failed: {e}",
                        level="error",
                    )

        # 2. Process Co-Investigators
        co_is = prof_cfg.get('co_investigators', [])
        state.co_investigators = []
        for coi in co_is:
            name = coi.get('name', '').strip()
            # Skip if name is empty or a common placeholder
            if not name or "Unknown" in name or "Dr. Jane Smith" in name:
                continue

            coi_links = coi.get('links', [])
            coi_source = ProfileSource(
                name=name,
                affiliation=coi.get('affiliation', 'Unknown Affiliation'),
                role=coi.get('role', 'Co-Investigator'),
                research_url=coi.get('research_url') or (coi_links[0] if coi_links else None),
                cv_path=coi.get('cv_path'),
                orcid=coi.get('orcid'),
                bio_supplement=coi.get('bio_supplement'),
                extra_links=coi_links[1:] if len(coi_links) > 1 else [],
            )

            self.ui.log_status(message=f"   > {coi_source.role}: {name}")
            coi_profile = self._synthesize_investigator_from_source(coi_source, verbose=verbose)
            state.co_investigators.append(coi_profile)

        # 3. Synthesize Team Summary
        self.ui.log_status(level='verbose', message="   > Synthesizing High-Level Team Overview...")
        state.team_summary = self._synthesize_team_overview(state, verbose=verbose)

        self.ui.log_status(level='verbose', message=f"   > Profile synthesis complete for {1 + len(state.co_investigators)} investigators.")
        tracker.stop_timer("Stage 1: Profiling")
        return state

    def _synthesize_team_overview(self, state: ProjectState, verbose: bool = True) -> str:
        """Synthesizes a single paragraph overview of the entire team's expertise."""
        profiles = []
        if state.researcher_profile:
            profiles.append(f"PI {state.researcher_profile.name}: {state.researcher_profile.domain_expertise}. (Resources: {state.researcher_profile.available_resources})")
        for coi in state.co_investigators:
            profiles.append(f"{coi.role} {coi.name}: {coi.domain_expertise}")
            
        if not profiles: return "No team profile data available."
        
        team_blob = "\n\n".join(profiles)
        
        prompt = f"""
        You are a Research Strategist.
        {ground_truth_block()}
        
        Task: Provide a single, high-impact paragraph (3-5 sentences) that synthesizes the expertise of the following research team.
        The goal is to show how their combined skills and resources make them uniquely qualified for this project.
        
        Team Profiles:
        -------------------------
        {team_blob}
        -------------------------
        
        Constraint: Return ONLY the paragraph. No headers, no intro text.
        """
        
        try:
            summary = self.llm.run_text(prompt).content
            return summary.strip()
        except Exception as e:
            self.ui.log_status(level='error', message=f"    [!] Team synthesis failed: {e}")
            return "A multi-disciplinary team with expertise in the required research domains."

    def _synthesize_investigator_from_source(self, source: ProfileSource, verbose: bool = True) -> ResearcherProfile:
        """Build a profile from a multi-source ProfileSource via the ingest module."""
        if verbose:
            tracker.start_timer(f"Profiling: {source.name}")
        try:
            self.ui.log_status(
                f"     - Ingesting profile for {source.name} from {len(_count_sources(source))} source(s)...",
                level="verbose",
            )
            profile = ingest_profile(source)
            return profile
        except ValueError as e:
            # No sources at all — fall back to a minimal stub so the engine
            # doesn't crash, and warn the user.
            self.ui.log_status(
                f"    [!] No profile sources for {source.name}: {e}",
                level="error",
            )
            return ResearcherProfile(
                name=source.name,
                affiliation=source.affiliation or "",
                role=source.role,
                domain_expertise=f"(No sources provided for {source.name}.)",
            )
        except Exception as e:
            self.ui.log_status(
                f"\n    [!] Profile ingestion failed for {source.name}: {e}",
                level="error",
            )
            return ResearcherProfile(
                name=source.name,
                affiliation=source.affiliation or "",
                role=source.role,
            )
        finally:
            if verbose:
                tracker.stop_timer(f"Profiling: {source.name}")

    def _auto_extract_prior_work(self, state, pi_source: ProfileSource) -> None:
        """Parse the PI's profile sources into structured PreliminaryWork
        entries with role="prior_work".

        The extractor runs ONE LLM call over the already-scraped profile
        sources (research_url content, ORCID, bio_supplement, etc.) and
        asks for a JSON list of work items. Items marked auto_extracted=True
        so prune_from_stage(1) can cleanly replace them on re-run.

        Mock mode: populates a small canned list from the PI's name so
        tests can verify the flow without LLM calls.
        """
        from ..core.state import PreliminaryWork

        # Skip if the user already has auto-extracted items (Stage 1 rerun)
        # — prune handles clearing, but be defensive.
        existing_auto = [pw for pw in state.preliminary_work if pw.auto_extracted]
        if existing_auto:
            return

        # Mock path — small canned list keyed to the PI's name
        if config.llm_provider == "mock":
            state.preliminary_work.extend([
                PreliminaryWork(
                    role="prior_work",
                    title=f"{pi_source.name}'s prior publication (mock)",
                    kind="publication",
                    date="2023",
                    description="Mock-extracted publication for testing.",
                    relevance="Establishes PI track record in the domain.",
                    auto_extracted=True,
                ),
                PreliminaryWork(
                    role="prior_work",
                    title=f"{pi_source.name}'s research code repository (mock)",
                    kind="code",
                    date="2024",
                    description="Mock-extracted code repo for testing.",
                    auto_extracted=True,
                ),
            ])
            self.ui.log_status(
                f"     - Mock prior work extracted: 2 items",
                level="verbose",
            )
            return

        # Real path — feed the profile sources to an LLM and ask for JSON
        # Gather all available source text
        source_text_parts: List[str] = []
        if pi_source.bio_supplement:
            source_text_parts.append(f"=== BIO ===\n{pi_source.bio_supplement}")
        if state.researcher_profile and state.researcher_profile.publication_history:
            source_text_parts.append(
                f"=== EXTRACTED PUBLICATION HISTORY ===\n{state.researcher_profile.publication_history}"
            )
        if state.researcher_profile and state.researcher_profile.domain_expertise:
            source_text_parts.append(
                f"=== DOMAIN EXPERTISE ===\n{state.researcher_profile.domain_expertise}"
            )
        if state.researcher_profile and state.researcher_profile.important_connections:
            source_text_parts.append(
                f"=== CONNECTIONS ===\n{state.researcher_profile.important_connections}"
            )
        if pi_source.research_url:
            source_text_parts.append(f"=== RESEARCH URL ===\n{pi_source.research_url}")
        if pi_source.orcid:
            source_text_parts.append(f"=== ORCID ===\n{pi_source.orcid}")
        if pi_source.github:
            source_text_parts.append(f"=== GITHUB ===\n{pi_source.github}")

        if not source_text_parts:
            return

        source_text = "\n\n".join(source_text_parts)

        prompt = (
            "You are extracting the PI's prior academic/research work from their "
            "profile sources, for use as 'prior relevant work' in a grant proposal.\n\n"
            "Extract a structured list of notable items. Be conservative — only "
            "list items clearly supported by the source text. Do NOT invent "
            "publication titles, dates, or results. If the source is thin, return "
            "a short list or an empty list.\n\n"
            "For each item, choose the most appropriate KIND from: publication, "
            "dataset, code, software, protocol, specimen, instrument, grant, "
            "talk, book, unpublished_data.\n\n"
            "Sources:\n"
            "---\n"
            f"{source_text[:12000]}\n"
            "---\n\n"
            "Return ONLY a JSON array of objects (no prose, no code fences). Schema:\n"
            "[\n"
            '  {\n'
            '    "title": "<full title>",\n'
            '    "kind": "<publication|dataset|code|software|protocol|specimen|instrument|grant|talk|book|unpublished_data>",\n'
            '    "date": "<year or \'unknown\'>",\n'
            '    "description": "<1-2 sentence summary>",\n'
            '    "relevance": "<1 sentence on how this connects to the PI\'s research trajectory>",\n'
            '    "link": "<URL or DOI or null>",\n'
            '    "citation": "<formatted citation string, or null>"\n'
            '  }\n'
            "]\n\n"
            "Return at most 15 items, prioritizing the most recent and most "
            "cited/impactful work. If you can't identify clear items, return []."
        )

        try:
            from ..core.llm_gateway import LLMGateway
            from ..core.json_parser import JSONParser
            llm = LLMGateway.get_llm("PriorWorkExtractor", verbose=False)
            self.ui.log_status(
                "     - Auto-extracting prior work from profile sources...",
                level="verbose",
            )
            response = llm.run_text(prompt)
            items_data = JSONParser.extract_array(response.content) or []
        except Exception as e:
            self.ui.log_status(
                f"    [!] Prior work extraction failed: {e}",
                level="error",
            )
            return

        if not items_data:
            self.ui.log_status(
                "     - No prior work items extracted",
                level="verbose",
            )
            return

        # Build PreliminaryWork objects, being defensive about types
        def _s(d, k, default=None):
            v = d.get(k) if isinstance(d, dict) else None
            return v if v is not None else default

        added = 0
        for item in items_data:
            if not isinstance(item, dict):
                continue
            title = _s(item, "title", "").strip()
            if not title:
                continue
            kind = _s(item, "kind", "publication")
            date = _s(item, "date")
            if date and date.lower() == "unknown":
                date = None
            state.preliminary_work.append(
                PreliminaryWork(
                    role="prior_work",
                    title=title,
                    kind=kind,
                    date=date,
                    description=_s(item, "description", "") or "",
                    relevance=_s(item, "relevance", "") or "",
                    link=_s(item, "link"),
                    citation=_s(item, "citation"),
                    auto_extracted=True,
                )
            )
            added += 1

        self.ui.log_status(
            f"     - Extracted {added} prior work item(s) from profile",
            level="verbose",
        )


def _count_sources(source: ProfileSource) -> List[str]:
    """Helper for verbose logging — list which source kinds are populated."""
    kinds = []
    if source.research_url: kinds.append("url")
    if source.cv_path: kinds.append("cv")
    if source.orcid: kinds.append("orcid")
    if source.github: kinds.append("github")
    if source.scholar_url: kinds.append("scholar")
    if source.bio_supplement: kinds.append("bio")
    if source.extra_links: kinds.append(f"+{len(source.extra_links)} extra")
    return kinds

