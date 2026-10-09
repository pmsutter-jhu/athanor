"""
ATHANOR Hypothesis Optimizer

Given a current hypothesis and its scorecard, generate a variant that
maximizes novelty and impact — the "competitive proposal" mode.

Lives alongside HypothesisRefiner because the two are complementary:
the Refiner *evaluates* a hypothesis, the Optimizer *reformulates* one.

Usage:
    opt = HypothesisOptimizer()
    opt.set_ui(ui_provider)
    variant = opt.generate_variant(current_hypothesis, scorecard, profile_summary)
    # variant.text           — the reformulated hypothesis
    # variant.rationale      — one-paragraph explanation of the changes
    # variant.targeted_scores — optimizer's predicted scores (dict of str->int)

The user sees the variant in a preview dialog before it goes to debate,
so they can edit or cancel. That's the "human sovereignty" pass through
the optimize flow.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from ..core.llm_gateway import LLMGateway
from ..core.state import HypothesisScorecard
from ..core.config_loader import config
from ..core.json_parser import JSONParser
from ..core.prompt_builder import ground_truth_block


class OptimizedVariant(BaseModel):
    """Container for the optimizer's output."""

    text: str = Field(..., description="The reformulated hypothesis text")
    rationale: str = Field("", description="Why the optimizer made these changes")
    targeted_scores: Dict[str, int] = Field(
        default_factory=dict,
        description="Optimizer's predicted scores for novelty/plausibility/falsifiability/executability/impact",
    )


_OPTIMIZER_SYSTEM = """You are a Research Strategist optimizing a hypothesis for a competitive grant proposal.

{ground_truth}

Your single job: take the current hypothesis and reformulate it to maximize NOVELTY and IMPACT while staying scientifically plausible and executable with the researcher's available resources.

Your optimization MUST respect these constraints:
1. PLAUSIBILITY IS NON-NEGOTIABLE. Never propose something that violates physics, chemistry, or biology.
2. EXECUTABILITY MATTERS. The researcher must be able to actually do this with their resources. Do not propose experiments that need instruments or data they don't have.
3. FALSIFIABILITY IS REQUIRED. The hypothesis must make a concrete prediction that could be tested.
4. SAFETY. No dual-use hazards, no weapons, no pathogen gain-of-function.

Techniques you may use to raise Novelty and Impact:
- Widen the regime: apply the core idea to a broader or more ambitious domain
- Cross-domain synthesis: connect to a methodology or dataset from an adjacent field
- Tie to a near-term observation or measurement that will become available
- Reframe as an enabling infrastructure ("if X works, a whole class of Y becomes possible")
- Sharpen the prediction: make the expected signal more specific and measurable
- Elevate stakes: show how the result would change a textbook claim

Techniques you must NOT use:
- Magical thinking or dark-matter-of-the-gaps handwaving
- Handwaving away resource constraints ("assume we get JWST GO time")
- Generic buzzwords ("using AI", "quantum-inspired") without a concrete mechanism
- Dropping falsifiability for ambition
"""


_OPTIMIZER_USER_TEMPLATE = """Current hypothesis:
---
{current}
---

Current scorecard (out of 5):
- Novelty:       {novelty}/5 — {novelty_rat}
- Plausibility:  {plausibility}/5 — {plausibility_rat}
- Falsifiability:{falsifiability}/5 — {falsifiability_rat}
- Executability: {executability}/5 — {executability_rat}
- Impact:        {impact}/5 — {impact_rat}

Researcher profile: {profile_summary}

Your task: produce a reformulated version of the hypothesis that you predict will score HIGHER on Novelty and Impact while staying at least as high on Plausibility, Falsifiability, and Executability.

Return ONLY a JSON object in this exact shape. No markdown fences. No commentary.

{{
  "text": "<the reformulated hypothesis, 2-4 sentences, technically dense>",
  "rationale": "<one paragraph explaining what you changed and why>",
  "targeted_scores": {{
    "novelty": <integer 1-5>,
    "plausibility": <integer 1-5>,
    "falsifiability": <integer 1-5>,
    "executability": <integer 1-5>,
    "impact": <integer 1-5>
  }}
}}"""


class HypothesisOptimizer:
    """Generates an optimized variant of a hypothesis."""

    def __init__(self):
        self.ui = None

    def set_ui(self, ui):
        self.ui = ui

    def generate_variant(
        self,
        current_hypothesis: str,
        scorecard: Optional[HypothesisScorecard],
        profile_summary: str = "",
    ) -> OptimizedVariant:
        """Return an optimized variant of the given hypothesis.

        Uses mock output when config.llm_provider == "mock" so tests and
        demo mode stay deterministic and offline.
        """
        if config.llm_provider == "mock":
            return self._mock_variant(current_hypothesis, scorecard)

        try:
            return self._llm_variant(current_hypothesis, scorecard, profile_summary)
        except Exception as e:
            if self.ui is not None:
                self.ui.log_status(
                    level="error",
                    message=f"    [Error] Optimizer failed: {e}",
                )
            # Graceful fallback — return the original text with a flag
            return OptimizedVariant(
                text=current_hypothesis,
                rationale=f"(Optimizer error: {e}. Returning original hypothesis unchanged.)",
                targeted_scores={},
            )

    # ------------------------------------------------------------------
    # Implementation
    # ------------------------------------------------------------------

    def _llm_variant(
        self,
        current_hypothesis: str,
        scorecard: Optional[HypothesisScorecard],
        profile_summary: str,
    ) -> OptimizedVariant:
        sc = scorecard or HypothesisScorecard()
        user_prompt = _OPTIMIZER_USER_TEMPLATE.format(
            current=current_hypothesis,
            novelty=sc.novelty_score,
            novelty_rat=sc.novelty_rationale or "(no rationale)",
            plausibility=sc.plausibility_score,
            plausibility_rat=sc.plausibility_rationale or "(no rationale)",
            falsifiability=sc.falsifiability_score,
            falsifiability_rat=sc.falsifiability_rationale or "(no rationale)",
            executability=sc.executability_score,
            executability_rat=sc.executability_rationale or "(no rationale)",
            impact=sc.impact_score,
            impact_rat=sc.impact_rationale or "(no rationale)",
            profile_summary=profile_summary or "(no profile)",
        )
        system = _OPTIMIZER_SYSTEM.format(ground_truth=ground_truth_block())
        full_prompt = system + "\n\n" + user_prompt

        llm = LLMGateway.get_llm("HypothesisOptimizer", verbose=False)
        response = llm.run_text(full_prompt)

        data = JSONParser.extract_object(response.content) or {}
        text = (data.get("text") or "").strip()
        if not text:
            # If the LLM didn't produce usable text, return the original
            return OptimizedVariant(
                text=current_hypothesis,
                rationale="(Optimizer returned empty text. Returning original hypothesis unchanged.)",
                targeted_scores={},
            )

        return OptimizedVariant(
            text=text,
            rationale=(data.get("rationale") or "").strip(),
            targeted_scores=self._coerce_scores(data.get("targeted_scores") or {}),
        )

    def _mock_variant(
        self,
        current_hypothesis: str,
        scorecard: Optional[HypothesisScorecard],
    ) -> OptimizedVariant:
        """Canned output for tests and demo mode."""
        return OptimizedVariant(
            text=(
                f"[OPTIMIZED] {current_hypothesis} "
                "Furthermore, this reformulation extends the core claim to a "
                "wider regime and ties the prediction to near-term survey data."
            ),
            rationale=(
                "Mock optimizer: widened the scope, tied the prediction to a "
                "near-term observable, and sharpened the falsifiable signal."
            ),
            targeted_scores={
                "novelty": 5,
                "plausibility": 4,
                "falsifiability": 4,
                "executability": 4,
                "impact": 5,
            },
        )

    @staticmethod
    def _coerce_scores(raw: Dict[str, Any]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for k in ("novelty", "plausibility", "falsifiability", "executability", "impact"):
            v = raw.get(k)
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                out[k] = 0
        return out
