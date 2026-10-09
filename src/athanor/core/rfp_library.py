"""
ATHANOR RFP Library — a user-global bank of saved RFPs.

The same research idea often gets submitted to multiple funders. A PI
might take "Athanor: AI for Trustworthy Science" and throw it at NSF,
NIH, a foundation, and a philanthropic RFP in succession, producing
four different proposals from the same underlying plan. Athanor stores
the list of RFPs they care about as a user-global bank (lives in
~/.athanor/rfp_bank.json, NOT per-project), so switching targets is
a one-click affair.

Not tied to any specific project. Not stored inside the repo. Survives
clones, worktrees, and project resets.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


DEFAULT_BANK_PATH = os.path.join(os.path.expanduser("~"), ".athanor", "rfp_bank.json")


class SavedRFP(BaseModel):
    """One entry in the user's RFP bank."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str  # Short human-readable label — e.g. "NSF AI for Science 2026"
    url_or_path: str  # The actual RFP target (URL or local file path)
    funder_short_name: Optional[str] = None  # Matches ingest.funder_profiles
    description: str = ""
    deadline: Optional[str] = None  # Loose string: "2026-10-15", "rolling", "quarterly"
    notes: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    last_used_at: Optional[datetime] = None


class RFPLibrary:
    """User-global bank of saved RFPs with CRUD + persistence."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or DEFAULT_BANK_PATH
        self._rfps: List[SavedRFP] = []
        self._load()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list(self) -> List[SavedRFP]:
        """Return all saved RFPs, newest first (by last_used then created)."""
        def sort_key(rfp: SavedRFP):
            return (rfp.last_used_at or rfp.created_at)
        return sorted(self._rfps, key=sort_key, reverse=True)

    def get(self, rfp_id: str) -> Optional[SavedRFP]:
        for rfp in self._rfps:
            if rfp.id == rfp_id:
                return rfp
        return None

    def find_by_name(self, name: str) -> Optional[SavedRFP]:
        for rfp in self._rfps:
            if rfp.name == name:
                return rfp
        return None

    def add(
        self,
        name: str,
        url_or_path: str,
        *,
        funder_short_name: Optional[str] = None,
        description: str = "",
        deadline: Optional[str] = None,
        notes: str = "",
    ) -> SavedRFP:
        """Append a new RFP. Does NOT dedupe on name or URL — callers
        that want dedupe should check find_by_name first."""
        if not name or not name.strip():
            raise ValueError("RFP name is required")
        if not url_or_path or not url_or_path.strip():
            raise ValueError("RFP url_or_path is required")
        rfp = SavedRFP(
            name=name.strip(),
            url_or_path=url_or_path.strip(),
            funder_short_name=funder_short_name,
            description=description or "",
            deadline=deadline,
            notes=notes or "",
        )
        self._rfps.append(rfp)
        self._save()
        return rfp

    def update(self, rfp_id: str, **kwargs: Any) -> Optional[SavedRFP]:
        """Update selected fields on an existing RFP. Unknown fields are
        silently ignored. Returns the updated RFP or None if not found."""
        allowed = {
            "name", "url_or_path", "funder_short_name",
            "description", "deadline", "notes",
        }
        target = self.get(rfp_id)
        if target is None:
            return None
        for k, v in kwargs.items():
            if k in allowed:
                setattr(target, k, v)
        self._save()
        return target

    def remove(self, rfp_id: str) -> bool:
        before = len(self._rfps)
        self._rfps = [r for r in self._rfps if r.id != rfp_id]
        changed = len(self._rfps) != before
        if changed:
            self._save()
        return changed

    def mark_used(self, rfp_id: str) -> Optional[SavedRFP]:
        """Bump last_used_at on an RFP, so the sort order in list()
        reflects recent usage."""
        target = self.get(rfp_id)
        if target is None:
            return None
        target.last_used_at = datetime.now()
        self._save()
        return target

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self.path):
            self._rfps = []
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                raw = raw.get("rfps", [])
            if not isinstance(raw, list):
                raw = []
            self._rfps = [SavedRFP.model_validate(item) for item in raw]
        except Exception:
            # Corrupt file — start fresh rather than crash. The user can
            # restore from backup if they care.
            self._rfps = []

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        payload = {
            "version": 1,
            "rfps": [rfp.model_dump(mode="json") for rfp in self._rfps],
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        os.replace(tmp, self.path)
