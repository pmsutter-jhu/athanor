"""
Athanor lifetime tally — persistent token/cost/run accumulator.

Stores totals across all sessions in ~/.athanor/tally.json so users
can see how much they've spent over time, not just per-session.
"""
import json
import os
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Dict, Any

_TALLY_PATH = Path.home() / ".athanor" / "tally.json"
_TALLY_LOCK = Lock()


def _ensure_dir() -> None:
    _TALLY_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_tally() -> Dict[str, Any]:
    """Load the lifetime tally from disk. Returns empty defaults if missing."""
    if not _TALLY_PATH.exists():
        return {
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_cost": 0.0,
            "total_runs": 0,
            "total_seconds": 0.0,
            "first_run": None,
            "last_run": None,
        }
    try:
        with _TALLY_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_cost": 0.0,
            "total_runs": 0,
            "total_seconds": 0.0,
            "first_run": None,
            "last_run": None,
        }


def record_session(
    *,
    tokens: int,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    seconds: float,
) -> Dict[str, Any]:
    """Add a finished session to the lifetime tally. Returns the updated tally."""
    with _TALLY_LOCK:
        _ensure_dir()
        tally = load_tally()

        now = datetime.now().isoformat(timespec="seconds")
        if not tally.get("first_run"):
            tally["first_run"] = now
        tally["last_run"] = now

        tally["total_tokens"] = int(tally.get("total_tokens", 0)) + int(tokens or 0)
        tally["prompt_tokens"] = int(tally.get("prompt_tokens", 0)) + int(prompt_tokens or 0)
        tally["completion_tokens"] = int(tally.get("completion_tokens", 0)) + int(completion_tokens or 0)
        tally["total_cost"] = float(tally.get("total_cost", 0.0)) + float(cost or 0.0)
        tally["total_runs"] = int(tally.get("total_runs", 0)) + 1
        tally["total_seconds"] = float(tally.get("total_seconds", 0.0)) + float(seconds or 0.0)

        try:
            with _TALLY_PATH.open("w", encoding="utf-8") as f:
                json.dump(tally, f, indent=2)
        except Exception:
            pass

        return tally


def reset_tally() -> None:
    """Wipe the lifetime tally (for testing or fresh starts)."""
    with _TALLY_LOCK:
        if _TALLY_PATH.exists():
            _TALLY_PATH.unlink()
