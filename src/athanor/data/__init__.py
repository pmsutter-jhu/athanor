"""
Bundled data files for Athanor.

Currently contains:
  - mock_rfp.md  -- a fictional RFP used as a default fallback when the
    user runs Stage 4 without a real funder in mind, and as a test
    fixture for the grant weaver pipeline.
"""
from __future__ import annotations

from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent

MOCK_RFP_PATH: str = str(_DATA_DIR / "mock_rfp.md")
MOCK_RFP_NAME: str = "Mock RFP — Athanor Research Foundation Frontier Science Grants 2026"
MOCK_RFP_FUNDER_SHORT_NAME: str = "generic"


def mock_rfp_path() -> str:
    """Absolute path to the bundled mock RFP markdown file."""
    return MOCK_RFP_PATH
