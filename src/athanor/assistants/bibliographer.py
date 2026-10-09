"""
ATHANOR Bibliographer Assistant.

Responsible for citation audit, fact-checking, and ensuring scientific
provenance across research plans and proposals.
"""
from ..core.config_loader import config
from ..core.llm_gateway import LLMGateway
from ..core.provenance_manager import ProvenanceManager


class Bibliographer:
    """Citation auditing and fact-checking via direct LLM calls."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.llm = LLMGateway.get_llm("Scientific Bibliographer", verbose=verbose)

    def audit(self, content_to_audit: str) -> str:
        """Run a citation audit on the given text. Returns the corrected text."""
        provenance = ProvenanceManager.get_provenance_instruction()

        prompt = f"""# IDENTITY
You are a meticulous Editorial Assistant and fact-checker. Your only job is provenance.

# CORE DIRECTIVES
1. **Scan for Claims:** Read the text line-by-line. Identify every scientific claim, data resource, software tool, or method.
2. **Verify Citation:** Check if it has a standardized citation [Source: URL] or [File: Path].
3. **Fix Missing:** If a claim is missing a citation, INSERT a plausible citation immediately after the claim.
4. **Fix Incorrect** citations: If a citation is present but incorrect, replace it with the correct citation.
5. **Flag Failures:** If you CANNOT find a reliable source, modify the text to include the inline flag: `[[LACKING PROVENANCE]]`.

{provenance}

# TASK
AUDIT the following text for scientific provenance and citations:

---
{content_to_audit}
---

Return the FULL original text with all necessary citations added and provenance flags inserted.
Do not summarize. Return the COMPLETE text with your fixes."""

        resp = self.llm.run_text(prompt)
        return resp.content
