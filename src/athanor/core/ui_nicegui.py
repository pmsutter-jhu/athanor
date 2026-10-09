"""
Athanor NiceGUI Provider.

Bridges the engine's synchronous UIProvider contract to NiceGUI's
reactive event loop using a thread-safe event queue. The engine
runs in a worker thread; UI updates are enqueued and drained by a
ui.timer running on the main async loop.

Cross-thread direction:
  - Engine thread -> GUI:  enqueue event, GUI timer drains
  - GUI thread -> Engine:  threading.Event for blocking input
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any, Dict, List, Optional

from .ui import UIProvider


class NiceGUIProvider(UIProvider):
    """Engine UI bridge for the NiceGUI desktop app."""

    @property
    def is_web(self) -> bool:
        return True

    def __init__(self):
        super().__init__()

        # Cross-thread event queue. Each item is a dict with a "kind" field:
        #   {"kind": "log", "msg": ..., "level": ..., "ts": ...}
        #   {"kind": "input", "prompt": ..., "default": ...}
        #   {"kind": "approval", "original": ..., "refined": ...}
        #   {"kind": "choice", "message": ..., "options": [...], "default": ...}
        #   {"kind": "metrics", "data": {...}}
        #   {"kind": "artifacts", "state": <ProjectState>}
        #   {"kind": "stage_finished"}
        self.events: "queue.Queue[Dict[str, Any]]" = queue.Queue()

        # All logs ever (for full history if a fresh client connects)
        self.logs: List[Dict[str, Any]] = []
        self._logs_lock = threading.Lock()

        # Blocking input bridges (engine thread waits, GUI thread sets)
        self._input_event = threading.Event()
        self._input_value: Any = None

        self._approval_event = threading.Event()
        self._approval_value: Optional[str] = None

        self._choice_event = threading.Event()
        self._choice_value: Optional[str] = None

        # Cancellation
        self._cancel_event = threading.Event()

    # ------------------------------------------------------------------
    # Logging — engine thread enqueues, GUI timer drains
    # ------------------------------------------------------------------

    def log_status(self, message: str, level: str = "info") -> None:
        self._write_to_log_file(message, level)

        # Strip CLI artifacts for cleaner GUI display
        clean = message.strip()
        while clean.startswith(">"):
            clean = clean.lstrip(">").strip()

        entry = {"kind": "log", "msg": clean, "level": level, "ts": time.time()}

        with self._logs_lock:
            self.logs.append(entry)

        try:
            self.events.put_nowait(entry)
        except Exception:
            pass

    def drain_events(self, max_items: int = 50) -> List[Dict[str, Any]]:
        """Called from the main GUI thread (via ui.timer) to drain pending events."""
        items = []
        for _ in range(max_items):
            try:
                items.append(self.events.get_nowait())
            except queue.Empty:
                break
        return items

    # ------------------------------------------------------------------
    # Blocking input requests — engine thread waits, GUI thread submits
    # ------------------------------------------------------------------

    def get_input(self, prompt: str, default: Optional[str] = None) -> str:
        self._input_event.clear()
        self._input_value = None
        try:
            self.events.put_nowait({"kind": "input", "prompt": prompt, "default": default})
        except Exception:
            pass

        while not self._input_event.wait(timeout=0.5):
            if self._cancel_event.is_set():
                raise KeyboardInterrupt("User cancelled run")
        return self._input_value if self._input_value is not None else (default or "")

    def submit_input(self, value: str) -> None:
        """Called by GUI thread to deliver user input to the engine."""
        self._input_value = value
        self._input_event.set()

    def get_choice(self, message: str, options: List[str], default: Optional[str] = None) -> str:
        self._choice_event.clear()
        self._choice_value = None
        try:
            self.events.put_nowait({
                "kind": "choice",
                "message": message,
                "options": options,
                "default": default,
            })
        except Exception:
            pass

        while not self._choice_event.wait(timeout=0.5):
            if self._cancel_event.is_set():
                raise KeyboardInterrupt("User cancelled run")
        return self._choice_value if self._choice_value is not None else (default or (options[0] if options else ""))

    def submit_choice(self, value: str) -> None:
        self._choice_value = value
        self._choice_event.set()

    def get_initial_spark(self, default: str) -> str:
        # Check config first
        from .config_loader import config
        if config.initial_spark and config.initial_spark.strip():
            return config.initial_spark
        return self.get_input("Initial research spark", default=default)

    def request_approval(self, original: str, refined: str) -> str:
        self._approval_event.clear()
        self._approval_value = None
        try:
            self.events.put_nowait({"kind": "approval", "original": original, "refined": refined})
        except Exception:
            pass

        while not self._approval_event.wait(timeout=0.5):
            if self._cancel_event.is_set():
                raise KeyboardInterrupt("User cancelled run")
        return self._approval_value or "b"  # default: accept refined

    def submit_approval(self, value: str) -> None:
        self._approval_value = value
        self._approval_event.set()

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Signal the engine to stop at the next checkpoint."""
        self._cancel_event.set()

    def reset_cancel(self) -> None:
        self._cancel_event.clear()

    def check_interruption(self) -> None:
        if self._cancel_event.is_set():
            raise KeyboardInterrupt("User cancelled run")

    # ------------------------------------------------------------------
    # Artifacts and metrics — engine thread enqueues
    # ------------------------------------------------------------------

    def display_report(self, file_path: str, title: str) -> None:
        self.log_status(f"[Artifact] {title}: {file_path}", level="success")

    def display_header(self) -> None:
        # Header is rendered by the GUI shell, not the engine
        pass

    def display_metrics(self, metrics_data: Dict[str, Any]) -> None:
        try:
            self.events.put_nowait({"kind": "metrics", "data": metrics_data})
        except Exception:
            pass

    def refresh_artifacts(self, state: Any = None) -> None:
        try:
            self.events.put_nowait({"kind": "artifacts", "state": state})
        except Exception:
            pass

    def signal_finished(self) -> None:
        """Called from worker thread when the pipeline run completes."""
        try:
            self.events.put_nowait({"kind": "finished"})
        except Exception:
            pass
