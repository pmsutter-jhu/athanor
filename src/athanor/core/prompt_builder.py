"""Shared prompt fragments used across Athanor engines."""

from .config_loader import config
from .provenance_manager import ProvenanceManager


def ground_truth_block() -> str:
    """Return the GROUND TRUTH directive if configured, else empty string."""
    if config.ground_truth:
        return f"GROUND TRUTH: {config.ground_truth}\n"
    return ""


def citation_instruction() -> str:
    """Standard citation format directive."""
    return (
        "CRITICAL: Use the citation format `[Category: Name](URL)` exactly. "
        "Do NOT use (Author, Year) style."
    )


def provenance_instruction() -> str:
    """Provenance protocol from ProvenanceManager."""
    return ProvenanceManager.get_provenance_instruction()
