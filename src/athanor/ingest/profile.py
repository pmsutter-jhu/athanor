"""
Multi-source researcher profile ingestion.

Accepts any combination of:
- research_url    (personal page, lab page, web CV)
- cv_path         (PDF/DOCX file)
- orcid           (16-char ORCID id, fetched via public API)
- github          (profile URL)
- scholar_url     (Google Scholar profile URL)
- bio_supplement  (free-text paragraph the user writes)
- extra_links     (any other URLs to scrape)

Merges all available fragments into a single ResearcherProfile via one
LLM call. The bio_supplement is treated as authoritative when sources
conflict — it comes directly from the researcher.

This replaces the old "scrape one URL per investigator" approach in
TeamProfiler. The intent is to support both traditional academics
(CV PDF) and non-traditional researchers (web page + bio paragraph).
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from ..core.json_parser import JSONParser
from ..core.llm_gateway import LLMGateway
from ..core.rag_tools import WebScrapeTool
from ..core.state import ResearcherProfile


class ProfileSource(BaseModel):
    """Inputs that contribute to a researcher profile.

    At least one source field (besides name/affiliation) must be set.
    """
    name: str
    affiliation: Optional[str] = None
    role: str = "Principal Investigator"

    research_url: Optional[str] = None
    cv_path: Optional[str] = None
    orcid: Optional[str] = None
    github: Optional[str] = None
    scholar_url: Optional[str] = None
    bio_supplement: Optional[str] = None
    extra_links: List[str] = Field(default_factory=list)

    def has_any_source(self) -> bool:
        return any([
            self.research_url,
            self.cv_path,
            self.orcid,
            self.github,
            self.scholar_url,
            self.bio_supplement,
            self.extra_links,
        ])


# Hard cap per source to keep prompts bounded
_MAX_FRAGMENT_CHARS = 8000


def _gather_fragments(source: ProfileSource) -> Dict[str, str]:
    """Pull raw text from each available input. Errors are logged, not raised."""
    fragments: Dict[str, str] = {}
    scraper = WebScrapeTool()

    if source.research_url:
        try:
            text = scraper._run(url=source.research_url)
            if text and not text.startswith("Error"):
                fragments["research_url"] = text[:_MAX_FRAGMENT_CHARS]
        except Exception:
            pass

    if source.cv_path and os.path.exists(source.cv_path):
        try:
            text = scraper._run(url=source.cv_path)
            if text and not text.startswith("Error"):
                fragments["cv"] = text[:_MAX_FRAGMENT_CHARS]
        except Exception:
            pass

    if source.orcid:
        try:
            import requests
            r = requests.get(
                f"https://pub.orcid.org/v3.0/{source.orcid}/record",
                headers={"Accept": "application/json"},
                timeout=10,
            )
            if r.status_code == 200:
                fragments["orcid"] = r.text[:_MAX_FRAGMENT_CHARS]
        except Exception:
            pass

    if source.github:
        try:
            text = scraper._run(url=source.github)
            if text and not text.startswith("Error"):
                fragments["github"] = text[:_MAX_FRAGMENT_CHARS]
        except Exception:
            pass

    if source.scholar_url:
        try:
            text = scraper._run(url=source.scholar_url)
            if text and not text.startswith("Error"):
                fragments["scholar"] = text[:_MAX_FRAGMENT_CHARS]
        except Exception:
            pass

    if source.bio_supplement:
        # Bio supplement is authoritative — pass through as-is
        fragments["bio_supplement"] = source.bio_supplement.strip()

    for url in source.extra_links:
        try:
            text = scraper._run(url=url)
            if text and not text.startswith("Error"):
                fragments[f"extra:{url}"] = text[:_MAX_FRAGMENT_CHARS]
        except Exception:
            pass

    return fragments


def _build_merge_prompt(source: ProfileSource, fragments: Dict[str, str]) -> str:
    sources_block = "\n\n".join(
        f"=== SOURCE: {kind} ===\n{text}"
        for kind, text in fragments.items()
    )

    ground_truth = ""
    try:
        from ..core.prompt_builder import ground_truth_block
        ground_truth = ground_truth_block()
    except Exception:
        pass

    return (
        f"You are an expert Research Profiler.\n"
        f"{ground_truth}\n"
        f"Synthesize a complete researcher profile by merging the sources below. "
        f"Be specific and concrete: use real names, dates, papers, institutions, "
        f"and grants. If sources conflict, prefer the bio_supplement — it comes "
        f"directly from the researcher.\n\n"
        f"Researcher: {source.name}\n"
        f"{f'Affiliation: {source.affiliation}' if source.affiliation else ''}\n"
        f"Role: {source.role}\n\n"
        f"=== SOURCES ===\n"
        f"{sources_block}\n"
        f"=== END SOURCES ===\n\n"
        f"Return a JSON object with these fields (all strings, all required):\n"
        f"- domain_expertise: 2-3 sentences on research interests, methods, key topics\n"
        f"- available_resources: institutional resources, compute, data access, collaborations, current projects\n"
        f"- publication_history: recent highlights, key papers, citation impact, books or non-traditional outputs\n"
        f"- important_connections: collaborators, institutions, professional networks, public reach\n"
        f"- persona_motivation: what drives this researcher's work, their philosophy and current focus\n\n"
        f"Constraints:\n"
        f"- Output MUST be valid JSON, no markdown fences.\n"
        f"- Do not invent grants, papers, or affiliations not present in the sources.\n"
        f"- If a section has no data, summarize what IS known rather than leaving it empty."
    )


def ingest_profile(source: ProfileSource) -> ResearcherProfile:
    """Pull from every available source, merge via one LLM call, return profile.

    After the LLM synthesis, runs structured extractors over ORCID JSON,
    GitHub API, and Semantic Scholar to populate the new structured fields
    (publications, h_index, career_timeline, funding_history, software_releases).

    Raises ValueError if no source is provided or all sources fail to load.
    """
    if not source.has_any_source():
        raise ValueError(
            "Athanor needs at least one profile source: research_url, cv_path, "
            "orcid, github, scholar_url, bio_supplement, or extra_links."
        )

    fragments = _gather_fragments(source)
    if not fragments:
        raise ValueError(
            "No profile sources could be loaded successfully. "
            "Check URLs/file paths and your network connection."
        )

    prompt = _build_merge_prompt(source, fragments)
    llm = LLMGateway.get_llm("ProfileIngestor", verbose=False)
    response = llm.run_text(prompt)

    data = JSONParser.extract_object(response.content) or {}

    contacts: List[str] = []
    for s in (source.research_url, source.cv_path, source.scholar_url, source.github):
        if s:
            contacts.append(s)
    contacts.extend(source.extra_links)

    profile = ResearcherProfile(
        name=source.name,
        affiliation=source.affiliation or "",
        role=source.role,
        contacts=contacts,
        domain_expertise=str(data.get("domain_expertise", "")),
        available_resources=str(data.get("available_resources", "")),
        publication_history=str(data.get("publication_history", "")),
        important_connections=str(data.get("important_connections", "")),
        persona_motivation=str(data.get("persona_motivation", "")),
    )

    # ── Structured extraction pass ──────────────────────────────
    # Run AFTER the LLM synthesis so the prose fields are already set.
    # These extractors populate the new structured fields from APIs and
    # parsed JSON — they never overwrite the prose fields.

    # ORCID: parse the JSON we already fetched for career timeline,
    # publications (DOIs), and funding history.
    if source.orcid and "orcid" in fragments:
        try:
            _enrich_from_orcid(profile, fragments["orcid"])
        except Exception:
            pass  # Degrade gracefully — prose fields still exist

    # GitHub: call the REST API for repo data (stars, languages)
    if source.github:
        try:
            _enrich_from_github(profile, source.github)
        except Exception:
            pass

    # Semantic Scholar: enrich publication list with citation counts,
    # compute h-index from the enriched data.
    if profile.publications:
        try:
            _enrich_from_semantic_scholar(profile)
        except Exception:
            pass

    return profile


# =============================================================================
# Structured extractors — parse what we already fetch
# =============================================================================

def _enrich_from_orcid(profile: ResearcherProfile, orcid_json_text: str) -> None:
    """Parse the ORCID v3.0 record JSON and populate structured fields.

    We already fetch this data in _gather_fragments — this function just
    extracts the structured fields instead of handing the raw JSON to the
    LLM.
    """
    import json as _json

    try:
        record = _json.loads(orcid_json_text)
    except (_json.JSONDecodeError, TypeError):
        return

    # ── Career timeline from employment + education ──
    timeline = {}

    # Education
    education_group = (
        record.get("activities-summary", {})
        .get("educations", {})
        .get("affiliation-group", [])
    )
    for group in education_group:
        for summary in group.get("summaries", []):
            edu = summary.get("education-summary", {})
            role_title = (edu.get("role-title") or "").lower()
            org_name = (edu.get("organization", {}).get("name") or "")
            end_date = edu.get("end-date")
            if end_date and end_date.get("year"):
                year = int(end_date["year"]["value"])
                if "phd" in role_title or "doctor" in role_title:
                    timeline["phd_year"] = year
                    timeline["phd_institution"] = org_name

    # Employment
    employment_group = (
        record.get("activities-summary", {})
        .get("employments", {})
        .get("affiliation-group", [])
    )
    employments = []
    for group in employment_group:
        for summary in group.get("summaries", []):
            emp = summary.get("employment-summary", {})
            org_name = (emp.get("organization", {}).get("name") or "")
            role_title = (emp.get("role-title") or "")
            start_date = emp.get("start-date")
            start_year = None
            if start_date and start_date.get("year"):
                start_year = int(start_date["year"]["value"])
            employments.append({
                "role": role_title,
                "institution": org_name,
                "start_year": start_year,
            })
            # Track current role (no end date = current)
            end_date = emp.get("end-date")
            if not end_date or not end_date.get("year"):
                if start_year:
                    timeline["current_role_start"] = start_year
                    timeline["current_institution"] = org_name

    # Postdoc detection
    for emp in employments:
        role = (emp.get("role") or "").lower()
        if "postdoc" in role or "post-doc" in role or "research associate" in role:
            if emp.get("start_year"):
                timeline["postdoc_start"] = emp["start_year"]
                timeline["postdoc_institution"] = emp.get("institution", "")
                break

    # Career stage heuristic
    phd_year = timeline.get("phd_year")
    if phd_year:
        from datetime import datetime as _dt
        years_since = _dt.now().year - phd_year
        if years_since <= 7:
            timeline["career_stage"] = "early-career"
        elif years_since <= 15:
            timeline["career_stage"] = "mid-career"
        else:
            timeline["career_stage"] = "established"

    if timeline:
        profile.career_timeline = timeline

    # ── Publications from works ──
    works_group = (
        record.get("activities-summary", {})
        .get("works", {})
        .get("group", [])
    )
    pubs = []
    for group in works_group:
        for summary in group.get("work-summary", []):
            title_obj = summary.get("title", {}).get("title", {})
            title = title_obj.get("value", "") if isinstance(title_obj, dict) else str(title_obj)
            if not title:
                continue
            pub_year = None
            pub_date = summary.get("publication-date")
            if pub_date and pub_date.get("year") and pub_date["year"].get("value"):
                try:
                    pub_year = int(pub_date["year"]["value"])
                except (ValueError, TypeError):
                    pass
            # Extract DOI from external-ids
            doi = None
            for eid in summary.get("external-ids", {}).get("external-id", []):
                if eid.get("external-id-type") == "doi":
                    doi = eid.get("external-id-value")
                    break
            venue = summary.get("journal-title", {})
            venue_name = venue.get("value", "") if isinstance(venue, dict) else str(venue or "")

            pubs.append({
                "title": title,
                "doi": doi,
                "year": pub_year,
                "venue": venue_name,
                "citations": None,  # filled by Semantic Scholar
            })

    if pubs:
        # Sort newest first, cap at 50 to keep state.json reasonable
        pubs.sort(key=lambda p: (p.get("year") or 0), reverse=True)
        profile.publications = pubs[:50]
        # Recent publication count (last 5 years)
        from datetime import datetime as _dt
        cutoff = _dt.now().year - 5
        profile.recent_publication_count = sum(
            1 for p in pubs if p.get("year") and p["year"] >= cutoff
        )

    # ── Funding history ──
    fundings_group = (
        record.get("activities-summary", {})
        .get("fundings", {})
        .get("group", [])
    )
    grants = []
    for group in fundings_group:
        for summary in group.get("funding-summary", []):
            title_obj = summary.get("title", {}).get("title", {})
            title = title_obj.get("value", "") if isinstance(title_obj, dict) else str(title_obj)
            org = summary.get("organization", {}).get("name", "")
            start_date = summary.get("start-date")
            end_date = summary.get("end-date")
            start_year = None
            end_year = None
            if start_date and start_date.get("year"):
                try:
                    start_year = int(start_date["year"]["value"])
                except (ValueError, TypeError):
                    pass
            if end_date and end_date.get("year"):
                try:
                    end_year = int(end_date["year"]["value"])
                except (ValueError, TypeError):
                    pass
            grants.append({
                "funder": org,
                "title": title,
                "start_year": start_year,
                "end_year": end_year,
                "role": summary.get("type", ""),
            })

    if grants:
        profile.funding_history = grants


def _enrich_from_github(profile: ResearcherProfile, github_url: str) -> None:
    """Call the GitHub REST API for public repos. Populates
    profile.software_releases with repo name, stars, language, desc.

    Accepts either a profile URL (github.com/username) or a username."""
    import requests

    # Extract username from URL
    username = github_url.rstrip("/").split("/")[-1]
    if not username:
        return

    try:
        resp = requests.get(
            f"https://api.github.com/users/{username}/repos",
            params={"sort": "stars", "per_page": 20},
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=10,
        )
        if resp.status_code != 200:
            return
        repos = resp.json()
    except Exception:
        return

    if not isinstance(repos, list):
        return

    releases = []
    for repo in repos:
        if repo.get("fork"):
            continue  # skip forks
        stars = repo.get("stargazers_count", 0)
        releases.append({
            "name": repo.get("name", ""),
            "url": repo.get("html_url", ""),
            "stars": stars,
            "language": repo.get("language") or "",
            "description": (repo.get("description") or "")[:200],
        })

    # Sort by stars descending, cap at 10
    releases.sort(key=lambda r: r.get("stars", 0), reverse=True)
    profile.software_releases = releases[:10]


def enrich_publications_from_titles(profile: ResearcherProfile, titles: List[str]) -> None:
    """Look up publication titles via Semantic Scholar search and populate
    profile.publications with DOIs + citation counts + h-index.

    Used when ORCID isn't available: the LLM-based auto-extractor finds
    publication titles in the bio/CV, and this function turns those titles
    into structured publication records. Only runs if profile.publications
    is currently empty (ORCID takes priority when available).
    """
    if profile.publications:
        return  # ORCID already populated; don't overwrite

    import requests
    import time

    pubs = []
    MAX_LOOKUPS = 8
    DELAY = 1.5  # Semantic Scholar free tier burst-limits aggressively

    for title in titles[:MAX_LOOKUPS]:
        if not title or len(title) < 10:
            continue
        try:
            resp = requests.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={"query": title, "limit": 1, "fields": "title,year,venue,citationCount,externalIds"},
                timeout=8,
            )
            # Retry once on 429 (Too Many Requests)
            if resp.status_code == 429:
                time.sleep(3.0)
                resp = requests.get(
                    "https://api.semanticscholar.org/graph/v1/paper/search",
                    params={"query": title, "limit": 1, "fields": "title,year,venue,citationCount,externalIds"},
                    timeout=8,
                )
            if resp.status_code != 200:
                time.sleep(DELAY)
                continue
            data = resp.json()
            papers = data.get("data") or []
            if not papers:
                time.sleep(DELAY)
                continue
            paper = papers[0]
            doi = None
            eids = paper.get("externalIds") or {}
            if isinstance(eids, dict):
                doi = eids.get("DOI")
            pubs.append({
                "title": paper.get("title", title),
                "doi": doi,
                "year": paper.get("year"),
                "venue": paper.get("venue", ""),
                "citations": paper.get("citationCount"),
            })
            time.sleep(DELAY)
        except Exception:
            time.sleep(DELAY)
            continue

    if pubs:
        pubs.sort(key=lambda p: (p.get("year") or 0), reverse=True)
        profile.publications = pubs

        # Compute h-index from the enriched data
        all_cites = [p["citations"] for p in pubs if p.get("citations") is not None]
        if all_cites:
            all_cites.sort(reverse=True)
            h = 0
            for i, c in enumerate(all_cites, 1):
                if c >= i:
                    h = i
                else:
                    break
            profile.h_index = h
            profile.total_citations = sum(all_cites)

        from datetime import datetime as _dt
        cutoff = _dt.now().year - 5
        profile.recent_publication_count = sum(
            1 for p in pubs if p.get("year") and p["year"] >= cutoff
        )


def _enrich_from_semantic_scholar(profile: ResearcherProfile) -> None:
    """For each publication with a DOI, fetch citation count from
    Semantic Scholar. Then compute h-index from the enriched list.

    Rate-limited: max 5 API calls to stay under the free-tier burst limit.
    Publications without a DOI keep citations=None."""
    import requests
    import time

    enriched = 0
    MAX_CALLS = 5
    citation_counts = []

    for pub in profile.publications:
        doi = pub.get("doi")
        if not doi:
            continue
        if enriched >= MAX_CALLS:
            break
        try:
            resp = requests.get(
                f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
                params={"fields": "citationCount"},
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                count = data.get("citationCount")
                if count is not None:
                    pub["citations"] = int(count)
                    citation_counts.append(int(count))
            enriched += 1
            time.sleep(1.5)  # Semantic Scholar free tier burst-limits aggressively
        except Exception:
            enriched += 1

    # Compute h-index from all publications that have citation data
    all_citations = [
        p["citations"] for p in profile.publications
        if p.get("citations") is not None
    ]
    if all_citations:
        all_citations.sort(reverse=True)
        h = 0
        for i, c in enumerate(all_citations, 1):
            if c >= i:
                h = i
            else:
                break
        profile.h_index = h
        profile.total_citations = sum(all_citations)
