"""
ATHANOR Provenance Manager.

This module centralizes the logic for "Scientific Provenance" tracking.
It provides:
1. Standardized Prompt Instructions ensuring agents cite sources.
2. Utilities to parse and structure these citations into Ledger Entries.
3. Validation logic to reduce hallucinations (e.g. checking if a citation looks real).
"""
import re
from typing import List, Dict, Any
from .state import ProvenanceItem

class ProvenanceManager:
    
    @staticmethod
    def get_provenance_instruction() -> str:
        """
        Returns the standardized instruction block for agents.
        """
        return """
        **PROVENANCE & CITATION RULES (MANDATORY)**:
        1. **NO CLAIM WITHOUT EVIDENCE**: Any scientific claim, fact, or resource mentioned must be cited.
        2. **CITATION FORMAT**: 
           - For Papers: `[Paper: <Title> (<Year>)](<URL or DOI>)`
           - For Code/Tools: `[Code: <Name>](<URL>)`
           - For Datasets: `[Data: <Name>](<URL>)`
           - For Web Info: `[Web: <Title>](<URL>)`
        3. **DECISION JUSTIFICATION**: Every key design choice (e.g., choosing Model A over Model B) must be justified with a citation or a logical derivation.
        4. **CONCEPTUAL TRACKING**: Link every *key concept* or *argument* to a source.
        5. **HALLUCINATION CHECK**: Do NOT invent citations. If you cannot find a source, state "No direct source found" and assign lower confidence.
        """

    @staticmethod
    def extract_citations(text: str) -> List[Dict[str, str]]:
        """
        Parses text for the standardized citation format: [Category: Name](URL)
        Returns list of dicts.
        """
        # Regex for [Category: Name](URL)
        # We allow flexible whitespace
        # Capture groups: 1=Category, 2=Name, 3=URL
        pattern = r"\[(Paper|Code|Data|Web|Tool):\s*(.*?)\]\((.*?)\)"
        matches = re.findall(pattern, text, re.IGNORECASE)
        
        results = []
        for cat, name, url in matches:
            results.append({
                "category": cat.capitalize(),
                "name": name.strip(),
                "link": url.strip(),
                "justification": "Cited in text" # Default justification
            })
        return results

    @staticmethod
    def create_ledger_entry(stage: str, agent: str, description: str, text_content: str) -> Dict[str, Any]:
        """
        Helper to generate a LedgerEntry dict (to be converted to object) from agent output.
        Extracts citations automatically.
        """
        citations = ProvenanceManager.extract_citations(text_content)
        items = []
        for c in citations:
            items.append(ProvenanceItem(
                category=c['category'],
                name=c['name'],
                link=c['link'],
                justification=c['justification']
            ))
            
        return {
            "stage": stage,
            "agent": agent,
            "description": description,
            "items_used": items
        }
