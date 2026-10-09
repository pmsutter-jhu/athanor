"""Shared types for the Athanor LLM layer."""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class LLMTextResponse:
    """Standard text response from an LLM run."""
    content: str
    usage_metadata: Optional[Dict[str, int]] = None
