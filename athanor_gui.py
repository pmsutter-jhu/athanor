#!/usr/bin/env python3
"""
Athanor NiceGUI Desktop Application.

The primary interface for Athanor.  Launches a desktop window with
tabs for each pipeline stage, drag-and-drop file slots, editable
outputs, and per-stage knobs.

Launch with:
    uv run python athanor_gui.py            # desktop window (PyWebView)
    uv run python athanor_gui.py --web      # browser mode
"""
from __future__ import annotations

import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Load environment variables before anything else
from dotenv import load_dotenv
load_dotenv(dotenv_path="config/.env")
load_dotenv(dotenv_path=".env")

# Normalize Gemini API key (Athanor convention)
if "GOOGLE_API_KEY" in os.environ:
    if "GEMINI_API_KEY" not in os.environ:
        os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]
    del os.environ["GOOGLE_API_KEY"]

from nicegui import ui, app

from src.athanor.core.engine import AthanorEngine
from src.athanor.core.ui_nicegui import NiceGUIProvider
from src.athanor.core.state_manager import StateManager
from src.athanor.core.config_loader import config
from src.athanor.core.state import ProjectState


# ============================================================================
# Theme
# ============================================================================

_THEME_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;700&family=Inter:wght@300;400;500;600&display=swap');

:root {
    --ath-bg:           #0f1014;
    --ath-surface:      #1a1b22;
    --ath-surface-alt:  #23242e;
    --ath-border:       rgba(108, 196, 142, 0.18);
    --ath-border-hover: rgba(108, 196, 142, 0.40);
    --ath-emerald:      #6CC48E;
    --ath-emerald-dim:  #4a9b6a;
    --ath-gold:         #c9a44b;
    --ath-parchment:    #e6d6b3;
    --ath-muted:        #7a7c8a;
    --ath-text:         #d8d9e0;
    --ath-error:        #d96a6a;
}

body, .q-page, .nicegui-content {
    background-color: var(--ath-bg) !important;
    color: var(--ath-text) !important;
    font-family: 'Inter', sans-serif !important;
}

.q-card {
    background-color: var(--ath-surface) !important;
    border: 1px solid var(--ath-border) !important;
    border-radius: 6px !important;
    transition: border-color 0.25s ease;
    box-shadow: none !important;
    padding: 8px 12px !important;
}
.q-card:hover { border-color: var(--ath-border-hover) !important; }

/* NiceGUI wraps card contents in a nicegui-column with its own gap.
   Tighten it so sidebar cards don't waste vertical space. */
.ath-sidebar .q-card > .nicegui-column,
.ath-sidebar .q-card {
    gap: 4px !important;
}

.ath-section-title {
    font-family: 'Cinzel', serif !important;
    color: var(--ath-parchment) !important;
    font-size: 0.92rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.04em;
    line-height: 1.2;
}
.ath-section-subtitle {
    color: var(--ath-gold) !important;
    font-size: 0.7rem !important;
    font-style: italic;
    letter-spacing: 0.04em;
    margin-top: -1px;
    line-height: 1.2;
}
.ath-section-rule {
    height: 1px;
    background: linear-gradient(90deg, var(--ath-gold), transparent);
    margin: 4px 0 6px 0;
}

.q-field__control {
    background-color: var(--ath-surface-alt) !important;
    color: var(--ath-text) !important;
}
.q-field__label, .q-field__native, .q-field__input {
    color: var(--ath-text) !important;
}
.q-field--focused .q-field__control { border-color: var(--ath-emerald) !important; }
.q-field__bottom { color: var(--ath-muted) !important; }
textarea, input { color: var(--ath-text) !important; }
::placeholder { color: var(--ath-muted) !important; opacity: 0.7; }

.q-tabs {
    background-color: var(--ath-surface) !important;
    border: 1px solid var(--ath-border) !important;
    border-radius: 8px !important;
}
.q-tab {
    color: var(--ath-muted) !important;
    font-family: 'Cinzel', serif !important;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    font-size: 0.78rem !important;
}
.q-tab--active { color: var(--ath-parchment) !important; }
.q-tab__indicator { background: var(--ath-emerald) !important; height: 2px !important; }

.q-menu, .q-virtual-scroll__content {
    background-color: var(--ath-surface) !important;
    color: var(--ath-text) !important;
    border: 1px solid var(--ath-border) !important;
}
.q-item { color: var(--ath-text) !important; }
.q-item--active, .q-item:hover {
    background-color: var(--ath-surface-alt) !important;
    color: var(--ath-parchment) !important;
}

.q-expansion-item__container {
    background-color: var(--ath-surface) !important;
    border: 1px solid var(--ath-border) !important;
    border-radius: 8px !important;
}
.q-expansion-item .q-item__label {
    color: var(--ath-parchment) !important;
    font-family: 'Cinzel', serif !important;
    letter-spacing: 0.04em;
}

.ath-run-btn {
    background: linear-gradient(135deg, var(--ath-emerald), var(--ath-emerald-dim)) !important;
    color: #0f1014 !important;
    font-family: 'Cinzel', serif !important;
    font-weight: 700 !important;
    letter-spacing: 0.10em;
    text-transform: uppercase !important;
    border: none !important;
    transition: box-shadow 0.3s ease, transform 0.15s ease;
}
.ath-run-btn:hover {
    box-shadow: 0 0 24px rgba(108, 196, 142, 0.45) !important;
    transform: translateY(-1px);
}
.ath-run-btn.disabled { opacity: 0.4 !important; }

.ath-secondary-btn {
    background-color: var(--ath-surface-alt) !important;
    color: var(--ath-parchment) !important;
    border: 1px solid var(--ath-border) !important;
    font-family: 'Inter', sans-serif !important;
    text-transform: none !important;
}

.ath-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 20px;
    border-bottom: 1px solid var(--ath-border);
    background-color: var(--ath-surface);
    min-height: 42px;
    position: sticky;
    top: 0;
    z-index: 50;
}
.ath-banner-title {
    font-family: 'Cinzel', serif !important;
    font-size: 1.15rem !important;
    font-weight: 700 !important;
    color: var(--ath-parchment) !important;
    letter-spacing: 0.18em;
    line-height: 1;
}
.ath-banner-subtitle {
    font-family: 'Inter', sans-serif !important;
    font-size: 0.68rem !important;
    color: var(--ath-gold) !important;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    line-height: 1;
}

.ath-sidebar {
    background-color: var(--ath-surface) !important;
    border-right: 1px solid var(--ath-border) !important;
    position: sticky;
    top: 42px;
    height: calc(100vh - 82px);
    overflow-y: auto;
}
.ath-status-line {
    color: var(--ath-muted);
    font-size: 0.8rem;
    padding: 1px 0;
    line-height: 1.35;
    min-height: unset;
}
.ath-status-done { color: var(--ath-emerald) !important; }
.ath-status-active {
    color: var(--ath-gold) !important;
    animation: ath-sidebar-pulse 1.6s ease-in-out infinite;
}
.ath-status-active::before {
    content: '';
    display: inline-block;
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--ath-gold);
    margin-right: 4px;
    vertical-align: middle;
    animation: ath-dot-pulse 1.2s ease-in-out infinite;
}
@keyframes ath-sidebar-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.6; }
}
@keyframes ath-dot-pulse {
    0%, 100% { transform: scale(1); opacity: 1; }
    50% { transform: scale(1.5); opacity: 0.5; }
}

/* Pulsing glow on the active tab when a stage is running */
.ath-tab-running {
    position: relative;
    color: var(--ath-emerald) !important;
    animation: ath-tab-glow 1.6s ease-in-out infinite;
}
@keyframes ath-tab-glow {
    0%, 100% { text-shadow: 0 0 6px rgba(108, 196, 142, 0.3); }
    50% { text-shadow: 0 0 14px rgba(108, 196, 142, 0.7); }
}

.ath-log {
    background-color: #0a0b0f !important;
    border: 1px solid var(--ath-border) !important;
    border-radius: 6px;
    padding: 8px 10px;
    font-family: 'JetBrains Mono', 'SF Mono', monospace !important;
    font-size: 0.76rem !important;
    color: #a8a9b3;
    max-height: 200px;
    overflow-y: auto;
}
.ath-log-info { color: #a8a9b3; }
.ath-log-verbose { color: #6a6b75; font-style: italic; }
.ath-log-success { color: var(--ath-emerald); }
.ath-log-error { color: var(--ath-error); }

.q-uploader {
    background-color: var(--ath-surface-alt) !important;
    color: var(--ath-text) !important;
    border: 1px dashed var(--ath-border) !important;
    border-radius: 6px !important;
}
.q-uploader__header { background-color: var(--ath-surface) !important; }

.q-separator { background-color: var(--ath-border) !important; }

::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: var(--ath-bg); }
::-webkit-scrollbar-thumb { background: var(--ath-border); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--ath-emerald-dim); }

.q-notification {
    background-color: var(--ath-surface) !important;
    color: var(--ath-text) !important;
    border: 1px solid var(--ath-border) !important;
}

/* ── Status bar (fixed footer) ───────────────────── */
.ath-statusbar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    z-index: 100;
    background-color: var(--ath-surface) !important;
    border-top: 1px solid var(--ath-border) !important;
    padding: 6px 16px;
    display: flex;
    align-items: center;
    gap: 24px;
    font-family: 'JetBrains Mono', 'SF Mono', monospace;
    font-size: 0.78rem;
}
.ath-statusbar-section {
    display: flex;
    align-items: center;
    gap: 6px;
}
.ath-statusbar-label {
    color: var(--ath-muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-size: 0.68rem;
}
.ath-statusbar-value {
    color: var(--ath-parchment);
    font-weight: 500;
}
.ath-statusbar-divider {
    width: 1px;
    height: 18px;
    background: var(--ath-border);
}
.ath-statusbar-emerald { color: var(--ath-emerald) !important; }
.ath-statusbar-gold { color: var(--ath-gold) !important; }
.ath-statusbar-running .ath-statusbar-emerald {
    animation: ath-pulse 1.4s ease-in-out infinite;
}
@keyframes ath-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

/* Add bottom padding to body so content isn't hidden under the fixed bar */
body { padding-bottom: 40px; }

.ath-output-card {
    background-color: var(--ath-surface) !important;
    border: 1px solid var(--ath-border) !important;
    border-radius: 6px;
    padding: 10px 12px;
    max-height: none;
    overflow-y: visible;
}
.ath-readout {
    color: var(--ath-text);
    font-size: 0.88rem;
    line-height: 1.7;
    white-space: pre-wrap;
}
.ath-readout p { margin: 0.6em 0; }
.ath-readout h1, .ath-readout h2, .ath-readout h3 {
    color: var(--ath-parchment);
    font-family: 'Cinzel', serif;
    margin: 1em 0 0.3em;
}
.ath-readout ul, .ath-readout ol { padding-left: 1.5em; margin: 0.4em 0; }
.ath-readout a { color: var(--ath-emerald); text-decoration: underline; }
.ath-readout-key {
    color: var(--ath-gold);
    font-family: 'Cinzel', serif;
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-top: 8px;
}
.ath-score-bar {
    background-color: var(--ath-surface-alt);
    border-radius: 3px;
    height: 6px;
    overflow: hidden;
}
.ath-score-fill {
    background: linear-gradient(90deg, var(--ath-emerald-dim), var(--ath-emerald));
    height: 100%;
}
"""


# ============================================================================
# Application State (per-session)
# ============================================================================

class AppState:
    """Per-session state holding the engine, UI provider, project state, and UI refs."""

    def __init__(self):
        self.ui_provider: NiceGUIProvider = NiceGUIProvider()
        self.manager: StateManager = StateManager()
        self.engine: Optional[AthanorEngine] = None
        self.worker: Optional[threading.Thread] = None
        self.is_running: bool = False
        self.running_stage: Optional[int] = None
        self.error_count: int = 0

        # Loaded project state (None until a project is loaded or created)
        self.project_state: Optional[ProjectState] = None
        self.current_project_id: Optional[str] = None

        # UI refs (set during page build)
        self.status_labels: Dict[int, Any] = {}
        self.project_picker = None
        self.stage_panels: Dict[int, Any] = {}  # tab num -> refresh function
        self.stage_tabs: Dict[int, Any] = {}     # tab num -> ui.tab element
        self.stage_run_btns: Dict[int, Any] = {} # tab num -> Run button element
        self.log_ticker = None                    # single-line status label
        self.log_entries: list = []               # all log entries for trace dialog

        # Cost tracker (sidebar tally)
        self.cost_label = None
        self.token_label = None

        # Status bar refs (fixed footer)
        self.sb_session_tokens = None
        self.sb_session_cost = None
        self.sb_session_time = None
        self.sb_lifetime_tokens = None
        self.sb_lifetime_cost = None
        self.sb_lifetime_runs = None
        self.sb_status = None
        self.sb_root = None

        # First-run banner + RUN ALL guard refs (Setup tab)
        self.first_run_banner = None
        self.first_run_banner_label = None
        self.run_all_button = None
        self.spark_input = None
        self.identity_name_input = None

        # API key card refs
        self.api_key_status_label = None
        self.api_key_input = None

        # Project KB cache — lazy opened on first access
        self._kb_cache: Optional[Any] = None

        # RFP library cache — user-global, loaded lazily
        self._rfp_lib_cache: Optional[Any] = None

        # Refresh registries for widgets that live outside the stage tabs.
        # stage_panels[N] only refreshes Stage N's readout; these registries
        # cover the Setup tab and the knobs panels, which also need to
        # re-render when state mutates (e.g. adding a prior work item,
        # applying an RFP from the bank).
        self.preliminary_work_refreshers: List[Any] = []
        self.rfp_target_input: Optional[Any] = None

    def start_engine(self, start_stage: Optional[int] = None, end_stage: Optional[int] = None) -> None:
        """Launch the engine in a worker thread, optionally restricted to a stage range."""
        if self.is_running:
            ui.notify("Pipeline already running", type="warning")
            return

        # Confirm if re-running over existing state for this stage
        if start_stage is not None and self._stage_has_output(start_stage):
            self._confirm_rerun(start_stage, end_stage)
            return

        self._do_start(start_stage, end_stage)

    def _stage_has_output(self, stage: int) -> bool:
        s = self.project_state
        if not s:
            return False
        if stage == 1:
            return s.researcher_profile is not None and bool(s.researcher_profile.name)
        if stage == 2:
            return s.scorecard is not None
        if stage == 3:
            return bool(s.dag) or bool(s.tasks)
        if stage == 4:
            return s.proposal is not None
        if stage == 5:
            # Stage 5 is bookkeeping — "has output" iff any node has moved
            # past PLANNED (in progress, done, blocked, or abandoned)
            if not s.dag:
                return False
            from src.athanor.core.state import ExecutionStatus
            return any(n.status != ExecutionStatus.PLANNED for n in s.dag)
        if stage == 6:
            return s.manuscript is not None
        return False

    def _confirm_rerun(self, start_stage: Optional[int], end_stage: Optional[int]) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label("Confirm re-run").classes("ath-section-title")
            ui.label("Will overwrite existing output").classes("ath-section-subtitle")
            ui.element("div").classes("ath-section-rule")
            ui.label(f"Stage {start_stage} already has output. Re-running will replace it.").classes("text-sm")
            ui.label("Any edits you've saved will be lost.").classes("text-xs mt-1").style("color: var(--ath-muted)")
            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("Cancel", on_click=dialog.close).classes("ath-secondary-btn")
                ui.button("Re-run anyway", on_click=lambda: (self._do_start(start_stage, end_stage), dialog.close())).classes("ath-run-btn")
        dialog.open()

    def _do_start(self, start_stage: Optional[int], end_stage: Optional[int]) -> None:
        # Apply stage range overrides
        if start_stage is not None:
            config.start_stage = start_stage
        if end_stage is not None:
            config.end_stage = end_stage

        self.ui_provider.reset_cancel()
        self.engine = AthanorEngine(
            ui=self.ui_provider,
            manager=self.manager,
            project_state=self.project_state,
        )
        self.is_running = True
        self.running_stage = start_stage
        self.error_count = 0

        def _run():
            try:
                self.engine.run(verbose=True)
                self.project_state = self.engine.state
            except KeyboardInterrupt:
                self.ui_provider.log_status("Pipeline cancelled.", level="error")
            except Exception as e:
                self.ui_provider.log_status(f"Pipeline error: {e}", level="error")
                import traceback
                self.ui_provider.log_status(traceback.format_exc(), level="verbose")
            finally:
                self.is_running = False
                self.running_stage = None
                self.ui_provider.signal_finished()

        self.worker = threading.Thread(target=_run, daemon=True)
        self.worker.start()
        ui.notify("Pipeline started", type="positive")

    def cancel_engine(self) -> None:
        if self.is_running:
            self.ui_provider.cancel()
            ui.notify("Cancelling at next checkpoint...", type="warning")

    def rerun_debate(
        self,
        edited_hypothesis: str,
        feedback: str,
        *,
        source: str = "Re-debate",
    ) -> None:
        """Run HypothesisRefiner directly on the edited hypothesis + feedback.

        Snapshots the current hypothesis+scorecard into the previous-version
        slot so the user can revert with one click. Runs in a worker thread
        so the GUI stays responsive.
        """
        if self.is_running:
            ui.notify("Pipeline already running", type="warning")
            return
        if not self.project_state:
            ui.notify("No project loaded", type="warning")
            return
        if not (edited_hypothesis or "").strip():
            ui.notify("Hypothesis is empty — nothing to debate", type="warning")
            return

        s = self.project_state
        # Snapshot previous BEFORE the refiner overwrites anything
        s.previous_hypothesis = s.refined_hypothesis
        s.previous_scorecard = s.scorecard.model_copy() if s.scorecard else None

        # Pin the edited text as the current refined_hypothesis so the
        # refiner picks it up via its override parameter
        s.refined_hypothesis = edited_hypothesis
        s.scorecard_stale = False

        from src.athanor.engines.hypothesis_refiner import HypothesisRefiner
        refiner = HypothesisRefiner()
        refiner.set_ui(self.ui_provider)

        self.ui_provider.reset_cancel()
        self.is_running = True
        self.ui_provider.log_status(f"    >> {source}: starting debate loop...")

        def _run():
            try:
                self.project_state = refiner.run_stress_test(
                    self.project_state,
                    verbose=True,
                    hypothesis_override=edited_hypothesis,
                    feedback=feedback or None,
                )
                try:
                    self.manager.save_state(self.project_state, filename="state.json")
                except Exception:
                    pass
            except Exception as e:
                self.ui_provider.log_status(f"{source} error: {e}", level="error")
                import traceback
                self.ui_provider.log_status(traceback.format_exc(), level="verbose")
            finally:
                self.is_running = False
                self.ui_provider.signal_finished()

        self.worker = threading.Thread(target=_run, daemon=True)
        self.worker.start()
        ui.notify(f"{source} started", type="positive")

    # ── Preliminary work mutations ──

    def _project_kb(self):
        """Return the project's ProjectKnowledgeBase, lazy-created.

        Lives under <project_dir>/knowledge_base/. Cached on the
        AppState to avoid reopening the SQLite file on every call."""
        if not self.project_state:
            return None
        if getattr(self, "_kb_cache", None) is not None:
            return self._kb_cache
        try:
            from src.athanor.assistants.project_kb import ProjectKnowledgeBase
            project_dir = self.manager.get_project_dir(self.project_state)
            self._kb_cache = ProjectKnowledgeBase(project_dir=project_dir, ui=self.ui_provider)
            return self._kb_cache
        except Exception as e:
            _safe_notify(f"Could not open knowledge base: {e}", type="negative")
            return None

    def add_preliminary_work(self, role: str, title: str, **kwargs) -> Optional[str]:
        """Append a new PreliminaryWork item. Returns its id."""
        if not self.project_state:
            return None
        from src.athanor.core.state import PreliminaryWork
        if not title or not title.strip():
            _safe_notify("Title is required", type="warning")
            return None
        try:
            pw = PreliminaryWork(role=role, title=title.strip(), **kwargs)
            self.project_state.preliminary_work.append(pw)
            self.save_project_state()
            self._refresh_all_panels()
            _safe_notify(f"Added: {pw.title}", type="positive")
            return pw.id
        except Exception as e:
            _safe_notify(f"Failed to add: {e}", type="negative")
            return None

    def update_preliminary_work_field(self, item_id: str, field: str, value) -> None:
        if not self.project_state:
            return
        for pw in self.project_state.preliminary_work:
            if pw.id == item_id:
                setattr(pw, field, value)
                break
        self.save_project_state()

    def remove_preliminary_work(self, item_id: str) -> None:
        if not self.project_state:
            return
        # Remove any attached files from the KB first
        kb = self._project_kb()
        if kb is not None:
            try:
                kb.remove_source(item_id)
            except Exception:
                pass
        self.project_state.preliminary_work = [
            pw for pw in self.project_state.preliminary_work if pw.id != item_id
        ]
        self.save_project_state()
        self._refresh_all_panels()
        _safe_notify("Removed", type="info")

    def attach_file_to_preliminary_work(self, item_id: str, src_path: str) -> bool:
        """Copy a file into the project's attachments dir and index it.

        Returns True on success. The file is copied (not moved), added
        to the item's attached_paths, and ingested into the KB.
        """
        if not self.project_state or not src_path:
            return False
        src = src_path.strip()
        if not os.path.exists(src):
            _safe_notify(f"File not found: {src}", type="negative")
            return False

        import shutil
        project_dir = self.manager.get_project_dir(self.project_state)
        attachments_dir = os.path.join(project_dir, "attachments")
        os.makedirs(attachments_dir, exist_ok=True)
        dest = os.path.join(attachments_dir, os.path.basename(src))
        # Avoid overwriting — append counter if needed
        if os.path.exists(dest):
            base, ext = os.path.splitext(dest)
            i = 1
            while os.path.exists(f"{base}_{i}{ext}"):
                i += 1
            dest = f"{base}_{i}{ext}"
        try:
            shutil.copyfile(src, dest)
        except Exception as e:
            _safe_notify(f"Copy failed: {e}", type="negative")
            return False

        # Find the item and add the path
        target = None
        for pw in self.project_state.preliminary_work:
            if pw.id == item_id:
                pw.attached_paths.append(dest)
                target = pw
                break
        if target is None:
            _safe_notify("Item not found", type="warning")
            return False

        # Ingest into the KB
        kb = self._project_kb()
        if kb is not None:
            try:
                kb.ingest_file(
                    dest,
                    source_id=item_id,
                    metadata={
                        "title": target.title,
                        "role": target.role,
                        "kind": target.kind or "",
                    },
                )
            except Exception as e:
                _safe_notify(f"Ingest failed (file still attached): {e}", type="warning")

        self.save_project_state()
        self._refresh_all_panels()
        _safe_notify(f"Attached {os.path.basename(dest)}", type="positive")
        return True

    def promote_node_to_preliminary_result(self, node_id: str) -> Optional[str]:
        """Create a PreliminaryWork(role="preliminary_result") entry from
        a completed DAG node.

        Pulls the node's name, description, workflow, deviation notes,
        and attached_artifact_paths into a fresh PreliminaryWork item.
        The attached artifacts are copied to the project's attachments/
        directory and indexed into the KB so Stage 4 can cite them.
        Returns the new PreliminaryWork id, or None if the node wasn't
        found.
        """
        if not self.project_state:
            return None
        target = None
        for n in self.project_state.dag:
            if n.id == node_id:
                target = n
                break
        if target is None:
            return None

        from src.athanor.core.state import PreliminaryWork
        description_parts = [target.description or ""]
        if target.workflow:
            description_parts.append(f"Procedure: {target.workflow}")
        if target.deviation_notes:
            description_parts.append(f"Deviation from plan: {target.deviation_notes}")
        if target.actual_wall_clock_days is not None:
            description_parts.append(f"Actual duration: {target.actual_wall_clock_days:.1f} days")

        pw = PreliminaryWork(
            role="preliminary_result",
            title=target.name,
            kind="pilot_experiment",
            description="\n\n".join(p for p in description_parts if p.strip()),
            relevance=f"Completed as part of this project's execution. Deliverables: {', '.join(o.name for o in target.outputs) or 'see workflow'}.",
            auto_extracted=False,
        )
        self.project_state.preliminary_work.append(pw)

        # Attach any artifacts + index them
        for path in target.attached_artifact_paths:
            if os.path.exists(path):
                self.attach_file_to_preliminary_work(pw.id, path)
            else:
                # Path doesn't resolve — just record it without ingesting
                pw.attached_paths.append(path)

        # Also index the node's description/workflow/deviation text so
        # keyword + semantic retrieval can find the step even without
        # files.
        kb = self._project_kb()
        if kb is not None and "\n".join(description_parts).strip():
            try:
                kb.ingest_text(
                    "\n\n".join(description_parts),
                    source_id=pw.id,
                    source_label=f"node:{target.name}",
                    metadata={"role": "preliminary_result", "kind": "pilot_experiment"},
                )
            except Exception:
                pass

        self.save_project_state()
        self._refresh_all_panels()
        _safe_notify(f"Promoted '{target.name}' to preliminary results", type="positive")
        return pw.id

    def upload_file_to_preliminary_work(self, item_id: str, filename: str, content: bytes) -> bool:
        """Handle a drag-and-drop upload — write bytes to disk, then
        delegate to attach_file_to_preliminary_work."""
        if not self.project_state or not filename:
            return False
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix="_" + filename) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            # attach_file_to_preliminary_work copies the file to the
            # attachments dir, so we can clean up the temp after.
            ok = self.attach_file_to_preliminary_work(item_id, tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return ok

    def remove_attachment_from_preliminary_work(self, item_id: str, attachment_path: str) -> None:
        if not self.project_state:
            return
        for pw in self.project_state.preliminary_work:
            if pw.id == item_id:
                pw.attached_paths = [p for p in pw.attached_paths if p != attachment_path]
                break
        # Note: we don't re-ingest the remaining files on removal; the
        # simpler path is to remove ALL chunks with this source_id and
        # let the user re-attach anything they want indexed again.
        # Keeping the chunks would be more efficient but risks stale
        # entries pointing to a deleted file.
        kb = self._project_kb()
        if kb is not None:
            try:
                kb.remove_source(item_id)
                # Re-ingest surviving files
                for pw in self.project_state.preliminary_work:
                    if pw.id == item_id:
                        for path in pw.attached_paths:
                            try:
                                kb.ingest_file(
                                    path,
                                    source_id=item_id,
                                    metadata={"title": pw.title, "role": pw.role, "kind": pw.kind or ""},
                                )
                            except Exception:
                                pass
                        break
            except Exception:
                pass
        self.save_project_state()
        self._refresh_all_panels()
        _safe_notify("Attachment removed", type="info")

    # ── Stage 5 mutations (execution bookkeeping, no worker threads) ──

    def mark_node_started(self, node_id: str) -> None:
        if not self.project_state:
            return
        from src.athanor.core.state import ExecutionStatus
        from datetime import datetime as _dt
        for node in self.project_state.dag:
            if node.id == node_id:
                node.status = ExecutionStatus.IN_PROGRESS
                if node.started_at is None:
                    node.started_at = _dt.now()
                break
        self.save_project_state()
        self._refresh_all_panels()
        _safe_notify("Marked as in progress", type="positive")

    def mark_node_done(self, node_id: str, actual_days: Optional[float], deviation_notes: str) -> None:
        if not self.project_state:
            return
        from src.athanor.core.state import ExecutionStatus
        from datetime import datetime as _dt
        for node in self.project_state.dag:
            if node.id == node_id:
                node.status = ExecutionStatus.DONE
                node.completed_at = _dt.now()
                if node.started_at is None:
                    node.started_at = node.completed_at
                if actual_days is not None:
                    node.actual_wall_clock_days = float(actual_days)
                if deviation_notes:
                    node.deviation_notes = deviation_notes.strip()
                break
        self.save_project_state()
        self._refresh_all_panels()
        _safe_notify("Marked as done", type="positive")

    def mark_node_blocked(self, node_id: str, reason: str) -> None:
        if not self.project_state:
            return
        from src.athanor.core.state import ExecutionStatus
        for node in self.project_state.dag:
            if node.id == node_id:
                node.status = ExecutionStatus.BLOCKED
                if reason:
                    node.deviation_notes = reason.strip()
                break
        self.save_project_state()
        self._refresh_all_panels()
        _safe_notify("Marked as blocked", type="warning")

    def mark_node_planned(self, node_id: str) -> None:
        """Revert a node to PLANNED — clears execution state for that node."""
        if not self.project_state:
            return
        from src.athanor.core.state import ExecutionStatus
        for node in self.project_state.dag:
            if node.id == node_id:
                node.status = ExecutionStatus.PLANNED
                node.started_at = None
                node.completed_at = None
                node.actual_wall_clock_days = None
                node.deviation_notes = ""
                break
        self.save_project_state()
        self._refresh_all_panels()
        _safe_notify("Reset to planned", type="info")

    def attach_artifact(self, node_id: str, path: str, description: str = "") -> None:
        if not self.project_state or not path:
            return
        for node in self.project_state.dag:
            if node.id == node_id:
                if path not in node.attached_artifact_paths:
                    node.attached_artifact_paths.append(path)
                break
        self.save_project_state()
        self._refresh_all_panels()
        _safe_notify("Artifact attached", type="positive")

    # ── Stage 6 worker-thread methods ──

    def rerun_paper_section(self, section_idx: int, feedback: str) -> None:
        """Regenerate a single manuscript section with author feedback."""
        if self.is_running:
            ui.notify("Pipeline already running", type="warning")
            return
        if not self.project_state or not self.project_state.manuscript:
            ui.notify("No manuscript loaded", type="warning")
            return
        if section_idx < 0 or section_idx >= len(self.project_state.manuscript.sections):
            ui.notify(f"Invalid section index {section_idx}", type="warning")
            return

        from src.athanor.engines.paper_writer import regenerate_manuscript_section

        self.ui_provider.reset_cancel()
        self.is_running = True
        section_title = self.project_state.manuscript.sections[section_idx].title
        self.ui_provider.log_status(f"    >> Regenerating paper section: {section_title}")

        def _run():
            try:
                new_content = regenerate_manuscript_section(
                    self.project_state,
                    section_idx,
                    feedback,
                    ui=self.ui_provider,
                )
                self.project_state.manuscript.sections[section_idx].content = new_content
                try:
                    self.manager.save_state(self.project_state, filename="state.json")
                except Exception:
                    pass
            except Exception as e:
                self.ui_provider.log_status(f"Section regenerate error: {e}", level="error")
                import traceback
                self.ui_provider.log_status(traceback.format_exc(), level="verbose")
            finally:
                self.is_running = False
                self.ui_provider.signal_finished()

        self.worker = threading.Thread(target=_run, daemon=True)
        self.worker.start()
        ui.notify(f"Regenerating {section_title}…", type="positive")

    def rerun_full_paper(self, feedback: str) -> None:
        """Re-run the full Stage 6 manuscript draft with feedback."""
        if self.is_running:
            ui.notify("Pipeline already running", type="warning")
            return
        if not self.project_state:
            ui.notify("No project loaded", type="warning")
            return

        from src.athanor.engines.paper_writer import PaperWriter
        writer = PaperWriter()
        writer.set_ui(self.ui_provider)

        self.ui_provider.reset_cancel()
        self.is_running = True
        self.ui_provider.log_status("    >> Re-running full manuscript draft with feedback…")

        def _run():
            try:
                self.project_state = writer.draft_manuscript(
                    self.project_state,
                    verbose=True,
                    feedback=feedback or None,
                )
                try:
                    self.manager.save_state(self.project_state, filename="state.json")
                except Exception:
                    pass
            except Exception as e:
                self.ui_provider.log_status(f"Paper re-run error: {e}", level="error")
                import traceback
                self.ui_provider.log_status(traceback.format_exc(), level="verbose")
            finally:
                self.is_running = False
                self.ui_provider.signal_finished()

        self.worker = threading.Thread(target=_run, daemon=True)
        self.worker.start()
        ui.notify("Full manuscript re-draft started", type="positive")

    def rerun_proposal_section(self, section_idx: int, feedback: str) -> None:
        """Regenerate a single proposal section with human feedback.

        Uses the standalone regenerate_proposal_section helper so no
        Librarian/RFP reload is needed — just a single LLM call in a
        worker thread. Mirrors rerun_debate / rerun_plan.
        """
        if self.is_running:
            ui.notify("Pipeline already running", type="warning")
            return
        if not self.project_state or not self.project_state.proposal:
            ui.notify("No proposal loaded", type="warning")
            return
        if section_idx < 0 or section_idx >= len(self.project_state.proposal.sections):
            ui.notify(f"Invalid section index {section_idx}", type="warning")
            return

        from src.athanor.engines.grant_weaver import regenerate_proposal_section

        self.ui_provider.reset_cancel()
        self.is_running = True
        section_title = self.project_state.proposal.sections[section_idx].title
        self.ui_provider.log_status(f"    >> Regenerating section: {section_title}")

        def _run():
            try:
                new_content = regenerate_proposal_section(
                    self.project_state,
                    section_idx,
                    feedback,
                    ui=self.ui_provider,
                )
                self.project_state.proposal.sections[section_idx].content = new_content
                try:
                    self.manager.save_state(self.project_state, filename="state.json")
                except Exception:
                    pass
                self.ui_provider.log_status(f"    >> Section regenerated: {section_title}", level="verbose")
            except Exception as e:
                self.ui_provider.log_status(f"Section regenerate error: {e}", level="error")
                import traceback
                self.ui_provider.log_status(traceback.format_exc(), level="verbose")
            finally:
                self.is_running = False
                self.ui_provider.signal_finished()

        self.worker = threading.Thread(target=_run, daemon=True)
        self.worker.start()
        ui.notify(f"Regenerating {section_title}…", type="positive")

    def rerun_full_proposal(self, feedback: str) -> None:
        """Re-run the full Stage 4 pipeline with feedback injected into
        the draft prompt. Expensive (~7 LLM calls) but comprehensive —
        use for overall tone / structure changes where per-section
        regenerate isn't enough."""
        if self.is_running:
            ui.notify("Pipeline already running", type="warning")
            return
        if not self.project_state:
            ui.notify("No project loaded", type="warning")
            return

        rfp_target = config.rfp_target
        if not rfp_target:
            ui.notify("RFP path required — set it in the Settings panel", type="warning")
            return

        from src.athanor.engines.grant_weaver import GrantWeaver
        try:
            weaver = GrantWeaver(rfp_path=rfp_target)
        except Exception as e:
            ui.notify(f"Could not load RFP: {e}", type="negative")
            return
        weaver.set_ui(self.ui_provider)

        self.ui_provider.reset_cancel()
        self.is_running = True
        self.ui_provider.log_status("    >> Re-running full proposal with feedback…")

        def _run():
            try:
                self.project_state = weaver.weave_proposal(
                    self.project_state,
                    verbose=True,
                    feedback=feedback or None,
                )
                try:
                    self.manager.save_state(self.project_state, filename="state.json")
                except Exception:
                    pass
            except Exception as e:
                self.ui_provider.log_status(f"Proposal re-run error: {e}", level="error")
                import traceback
                self.ui_provider.log_status(traceback.format_exc(), level="verbose")
            finally:
                self.is_running = False
                self.ui_provider.signal_finished()

        self.worker = threading.Thread(target=_run, daemon=True)
        self.worker.start()
        ui.notify("Full proposal re-run started", type="positive")

    def rerun_plan(self, feedback: str, *, source: str = "Re-plan") -> None:
        """Run ResearchPlanner directly with human feedback injected into
        the Drafter's system prompt. Mirrors rerun_debate — worker thread,
        no engine wrapper, no prune."""
        if self.is_running:
            ui.notify("Pipeline already running", type="warning")
            return
        if not self.project_state:
            ui.notify("No project loaded", type="warning")
            return
        if not self.project_state.refined_hypothesis and not self.project_state.initial_shower_thought:
            ui.notify("No hypothesis to plan from", type="warning")
            return

        from src.athanor.engines.research_planner import ResearchPlanner
        planner = ResearchPlanner()
        planner.set_ui(self.ui_provider)

        self.ui_provider.reset_cancel()
        self.is_running = True
        self.ui_provider.log_status(f"    >> {source}: regenerating plan...")

        def _run():
            try:
                self.project_state = planner.decompose_hypothesis(
                    self.project_state,
                    verbose=True,
                    feedback=feedback or None,
                )
                try:
                    self.manager.save_state(self.project_state, filename="state.json")
                except Exception:
                    pass
            except Exception as e:
                self.ui_provider.log_status(f"{source} error: {e}", level="error")
                import traceback
                self.ui_provider.log_status(traceback.format_exc(), level="verbose")
            finally:
                self.is_running = False
                self.ui_provider.signal_finished()

        self.worker = threading.Thread(target=_run, daemon=True)
        self.worker.start()
        ui.notify(f"{source} started", type="positive")

    def optimize_hypothesis(self) -> Optional["OptimizedVariant"]:
        """Call the optimizer agent and return the variant (no debate yet).

        Runs synchronously on the caller's thread — the optimizer is a
        single LLM call so this is cheap. Returns None on failure.
        The caller (GUI) then shows the preview dialog with the result.
        """
        if not self.project_state or not self.project_state.refined_hypothesis:
            ui.notify("No hypothesis to optimize yet — run Stage 2 first", type="warning")
            return None

        from src.athanor.engines.hypothesis_optimizer import HypothesisOptimizer
        s = self.project_state
        profile_summary = ""
        if s.researcher_profile:
            profile_summary = (
                f"{s.researcher_profile.name}, {s.researcher_profile.affiliation}. "
                f"Expertise: {s.researcher_profile.domain_expertise}"
            )
        opt = HypothesisOptimizer()
        opt.set_ui(self.ui_provider)
        try:
            variant = opt.generate_variant(
                s.refined_hypothesis,
                s.scorecard,
                profile_summary,
            )
            return variant
        except Exception as e:
            ui.notify(f"Optimizer failed: {e}", type="negative")
            return None

    def load_project(self, project_id: str) -> None:
        try:
            self.project_state = self.manager.load_state(project_id)
            self.project_state.project_slug = project_id
            self.current_project_id = project_id
            self._kb_cache = None
            ui.notify(f"Loaded: {project_id}", type="positive")
        except Exception as e:
            ui.notify(f"Load failed: {e}", type="negative")
            return
        self._refresh_all_panels()

    def list_projects_detailed(self) -> List[Dict[str, Any]]:
        """Return a list of projects with pretty name, stage, mtime.

        Each entry: {id, name, stage, mtime_display}. The id is the slug
        that load_project takes; the name is state.project_name for
        display. Sort: most-recently-modified first."""
        try:
            slugs = self.manager.list_projects()
        except Exception:
            slugs = []
        entries: List[Dict[str, Any]] = []
        for slug in slugs:
            if isinstance(slug, str) and slug.startswith("(Fixed)"):
                entries.append({
                    "id": slug,
                    "name": slug,
                    "stage": None,
                    "mtime": 0,
                    "mtime_display": "",
                })
                continue
            try:
                project_dir = os.path.join(self.manager.data_dir, slug)
                state_path = os.path.join(project_dir, "state.json")
                mtime = os.path.getmtime(state_path) if os.path.exists(state_path) else 0
                name = slug
                stage = None
                if os.path.exists(state_path):
                    try:
                        with open(state_path, "r") as f:
                            data = _json_load(f)
                        name = data.get("project_name") or slug
                        stage = data.get("current_stage")
                    except Exception:
                        pass
                entries.append({
                    "id": slug,
                    "name": name,
                    "stage": stage,
                    "mtime": mtime,
                    "mtime_display": _format_mtime(mtime),
                })
            except Exception:
                continue
        entries.sort(key=lambda x: x.get("mtime") or 0, reverse=True)
        return entries

    def delete_project(self, project_id: str) -> bool:
        """Delete a project's directory. Returns True on success.

        If the deleted project is the currently-loaded one, the in-memory
        state is also cleared."""
        import shutil
        if not project_id or project_id.startswith("(Fixed)"):
            return False
        try:
            project_dir = os.path.join(self.manager.data_dir, project_id)
            if os.path.exists(project_dir):
                shutil.rmtree(project_dir)
        except Exception as e:
            _safe_notify(f"Delete failed: {e}", type="negative")
            return False
        if self.current_project_id == project_id:
            self.project_state = None
            self.current_project_id = None
            self._kb_cache = None
            self._refresh_all_panels()
        _safe_notify(f"Deleted project: {project_id}", type="info")
        return True

    # ── RFP library accessors ──

    def rfp_library(self):
        """Return the user-global RFPLibrary, cached on the AppState."""
        if getattr(self, "_rfp_lib_cache", None) is not None:
            return self._rfp_lib_cache
        from src.athanor.core.rfp_library import RFPLibrary
        self._rfp_lib_cache = RFPLibrary()
        return self._rfp_lib_cache

    def apply_rfp_from_bank(self, rfp_id: str) -> None:
        """Apply a saved RFP as the current proposal target.

        Sets config.rfp_target, the funder_short_name, and bumps the
        RFP's last_used_at. If a project is loaded, also stores the
        funder on the ProjectState. Pushes the new target directly
        into the Proposal settings input so the user sees it immediately."""
        lib = self.rfp_library()
        rfp = lib.get(rfp_id)
        if rfp is None:
            _safe_notify("RFP not found", type="warning")
            return
        _set_config(["grant", "rfp_target"], rfp.url_or_path)
        if rfp.funder_short_name:
            _set_config(["grant", "funder_short_name"], rfp.funder_short_name)
            if self.project_state:
                self.project_state.funder_short_name = rfp.funder_short_name
        lib.mark_used(rfp_id)
        if self.project_state:
            self.save_project_state()
        # Sync the visible RFP input field in the Proposal settings
        if self.rfp_target_input is not None:
            try:
                self.rfp_target_input.value = rfp.url_or_path
            except Exception:
                pass
        self._refresh_all_panels()
        _safe_notify(f"Applied: {rfp.name}", type="positive")

    def new_project(self) -> None:
        self.project_state = None
        self.current_project_id = None
        ui.notify("New project. Set the spark and run Stage 1.", type="info")
        self._refresh_all_panels()

    def _refresh_all_panels(self) -> None:
        for fn in self.stage_panels.values():
            try:
                fn()
            except Exception:
                pass
        if self.refresh_status:
            try:
                self.refresh_status()
            except Exception:
                pass
        self._refresh_preliminary_work_widgets()

    def _refresh_preliminary_work_widgets(self) -> None:
        """Re-render every PreliminaryWork list widget registered on
        this AppState. Called after any mutation that changes the
        underlying state.preliminary_work collection (add, remove,
        update, promote, file attach/detach)."""
        for fn in self.preliminary_work_refreshers:
            try:
                fn()
            except Exception:
                pass

    refresh_status = None  # set after page build

    def save_project_state(self) -> None:
        """Persist the current project state back to disk via StateManager."""
        if not self.project_state:
            return
        try:
            self.manager.save_state(self.project_state, filename="state.json")
        except Exception as e:
            ui.notify(f"Save failed: {e}", type="negative")


# ============================================================================
# Main Page
# ============================================================================

@ui.page("/")
def index():
    state = AppState()
    ui.add_head_html(f"<style>{_THEME_CSS}</style>")

    # ── Compact header bar ───────────────────────────────────
    with ui.element("div").classes("w-full ath-banner"):
        ui.label("ATHANOR").classes("ath-banner-title")
        ui.label("AI Research Engine").classes("ath-banner-subtitle")

    # ── Two-column layout: sidebar + main ───────────────────
    with ui.row().classes("w-full items-stretch gap-0").style("min-height: calc(100vh - 82px);"):

        # ════════════════════════════════════════════════════
        #  LEFT SIDEBAR
        # ════════════════════════════════════════════════════
        with ui.column().classes("ath-sidebar p-2 gap-2").style("width: 256px; flex-shrink: 0;"):

            # Project Picker — sidebar select + Switch dialog
            with ui.card().classes("w-full"):
                ui.label("Project").classes("ath-section-title")
                ui.element("div").classes("ath-section-rule")

                # Build a dict: slug id → display label
                # NiceGUI ui.select with dict: keys = values, dict-values = labels
                def _project_options() -> Dict[str, str]:
                    options: Dict[str, str] = {"(new)": "(new)"}
                    seen_labels: set = set()
                    for entry in state.list_projects_detailed():
                        stage = entry.get("stage")
                        stage_tag = f" · S{stage}" if stage else ""
                        label = f"{entry['name'][:40]}{stage_tag}"
                        base_label = label
                        i = 2
                        while label in seen_labels:
                            label = f"{base_label} ({i})"
                            i += 1
                        seen_labels.add(label)
                        options[entry["id"]] = label
                    return options

                current_options = _project_options()
                project_select = ui.select(
                    options=current_options,
                    value="(new)",
                    label="Active project",
                ).classes("w-full").props("dense outlined")

                def _on_project_change(e):
                    val = e.value
                    if val == "(new)" or not val:
                        state.new_project()
                    else:
                        state.load_project(val)

                project_select.on_value_change(_on_project_change)
                state.project_picker = project_select

                def _refresh_projects():
                    fresh = _project_options()
                    project_select.options = fresh
                    project_select.update()

                with ui.row().classes("w-full gap-1 mt-1"):
                    ui.button(
                        "Switch…",
                        icon="swap_horiz",
                        on_click=lambda: _show_project_switcher_dialog(state, _refresh_projects),
                    ).classes("ath-run-btn flex-1").props("size=sm").tooltip(
                        "Open a full list of saved projects with stage, last-modified, and delete actions."
                    )
                    ui.button(
                        icon="refresh",
                        on_click=_refresh_projects,
                    ).props("flat round size=sm").tooltip("Refresh project list")

            # PI + API key (compact)
            with ui.card().classes("w-full"):
                ui.label("Investigator").classes("ath-section-title")
                ui.element("div").classes("ath-section-rule")
                ui.label(config.pi_name or "(unset)").classes("text-sm")
                ui.label(_get_pi_affiliation()).classes("text-xs").style("color: var(--ath-muted)")
                ui.button(
                    "API key…",
                    icon="key",
                    on_click=lambda: _show_api_key_dialog(state),
                ).classes("ath-secondary-btn w-full mt-2").props("size=sm dense")

            # Stage Status
            with ui.card().classes("w-full"):
                ui.label("Pipeline").classes("ath-section-title")
                ui.element("div").classes("ath-section-rule")

                stages_info = [
                    (1, "Profile"),
                    (2, "Hypothesis"),
                    (3, "Plan"),
                    (4, "Proposal"),
                    (5, "Execution"),
                    (6, "Paper"),
                ]
                with ui.column().classes("w-full gap-0"):
                    for num, label_text in stages_info:
                        label = ui.label(f"○ {num}. {label_text}").classes("ath-status-line")
                        state.status_labels[num] = label

                def _refresh_status():
                    s = state.project_state
                    cur_stage = s.current_stage if s else 0
                    for num, label_text in stages_info:
                        lbl = state.status_labels[num]
                        if num <= cur_stage:
                            lbl.text = f"✓ {num}. {label_text}"
                            lbl.classes(remove="ath-status-active", add="ath-status-done")
                        elif num == cur_stage + 1 and state.is_running:
                            lbl.text = f"⏵ {num}. {label_text}"
                            lbl.classes(remove="ath-status-done", add="ath-status-active")
                        else:
                            lbl.text = f"○ {num}. {label_text}"
                            lbl.classes(remove="ath-status-done ath-status-active")

                state.refresh_status = _refresh_status

            # Cost / Tokens (compact inline)
            with ui.card().classes("w-full"):
                ui.label("Spend").classes("ath-section-title")
                ui.element("div").classes("ath-section-rule")
                with ui.row().classes("w-full items-baseline gap-2"):
                    state.cost_label = ui.label("$0.00").classes("text-sm").style("color: var(--ath-parchment);")
                    state.token_label = ui.label("0 tokens").classes("text-xs").style("color: var(--ath-muted)")

        # ════════════════════════════════════════════════════
        #  MAIN AREA
        # ════════════════════════════════════════════════════
        with ui.column().classes("flex-1 p-2 gap-2").style("min-width: 0;"):

            with ui.tabs().classes("w-full") as tabs:
                tab_setup = ui.tab("Setup", icon="settings")
                tab_profile = ui.tab("1. Profile", icon="science")
                tab_hypothesis = ui.tab("2. Hypothesis", icon="local_fire_department")
                tab_plan = ui.tab("3. Plan", icon="account_tree")
                tab_proposal = ui.tab("4. Proposal", icon="auto_stories")
                tab_execution = ui.tab("5. Execution", icon="build_circle")
                tab_paper = ui.tab("6. Paper", icon="menu_book")
                tab_output = ui.tab("Output", icon="picture_as_pdf")

                state.stage_tabs = {
                    1: tab_profile, 2: tab_hypothesis, 3: tab_plan,
                    4: tab_proposal, 5: tab_execution, 6: tab_paper,
                }

            with ui.tab_panels(tabs, value=tab_setup).classes("w-full").style("background: transparent;"):

                with ui.tab_panel(tab_setup):
                    _build_setup_tab(state)

                with ui.tab_panel(tab_profile):
                    state.stage_panels[1] = _build_stage_tab(
                        state, num=1, name="Researcher Profile",
                        motto="Stage 1 · Build the researcher's professional profile",
                        knobs_builder=_profile_knobs,
                        readout_builder=_profile_readout,
                    )

                with ui.tab_panel(tab_hypothesis):
                    state.stage_panels[2] = _build_stage_tab(
                        state, num=2, name="Hypothesis Debate",
                        motto="Stage 2 · Stress-test the hypothesis through debate",
                        knobs_builder=_hypothesis_knobs,
                        readout_builder=_hypothesis_readout,
                    )

                with ui.tab_panel(tab_plan):
                    state.stage_panels[3] = _build_stage_tab(
                        state, num=3, name="Research Plan",
                        motto="Stage 3 · Decompose into a DAG-based research plan",
                        knobs_builder=_plan_knobs,
                        readout_builder=_plan_readout,
                    )

                with ui.tab_panel(tab_proposal):
                    state.stage_panels[4] = _build_stage_tab(
                        state, num=4, name="Grant Proposal",
                        motto="Stage 4 · Weave a grant proposal from plan and RFP",
                        knobs_builder=_proposal_knobs,
                        readout_builder=_proposal_readout,
                        promoted_builder=_proposal_promoted,
                    )

                with ui.tab_panel(tab_execution):
                    state.stage_panels[5] = _build_stage_tab(
                        state, num=5, name="Execution",
                        motto="Stage 5 · Track research execution progress",
                        knobs_builder=_execution_knobs,
                        readout_builder=_execution_readout,
                    )

                with ui.tab_panel(tab_paper):
                    state.stage_panels[6] = _build_stage_tab(
                        state, num=6, name="Paper",
                        motto="Stage 6 · Draft the final paper",
                        knobs_builder=_paper_knobs,
                        readout_builder=_paper_readout,
                        promoted_builder=_paper_promoted,
                    )

                with ui.tab_panel(tab_output):
                    output_container = ui.column().classes("w-full")

                    def _refresh_output():
                        output_container.clear()
                        with output_container:
                            _build_output_tab(state)

                    _refresh_output()
                    state.stage_panels["output"] = _refresh_output

            # (ticker is in the status bar now — no separate element needed here)

            # Drain provider events on the main thread (NiceGUI 3.x is not
            # thread-safe; the worker enqueues events and this timer dispatches).
            def _drain():
                events = state.ui_provider.drain_events(max_items=100)
                for evt in events:
                    kind = evt.get("kind")
                    try:
                        if kind == "log":
                            _render_log_entry(state, evt)
                        elif kind == "input":
                            _show_input_dialog(state, "text", evt["prompt"], evt.get("default"))
                        elif kind == "approval":
                            _show_approval_dialog(state, evt["original"], evt["refined"])
                        elif kind == "choice":
                            _show_choice_dialog(state, evt["message"], evt["options"], evt.get("default"))
                        elif kind == "metrics":
                            _update_metrics(state, evt["data"])
                        elif kind == "artifacts":
                            # Refresh status badges; project_state may have updated
                            if state.refresh_status:
                                state.refresh_status()
                        elif kind == "finished":
                            state._refresh_all_panels()
                    except Exception as e:
                        # Don't let one bad event break the timer loop
                        try:
                            ui.notify(f"Event dispatch error: {e}", type="negative")
                        except Exception:
                            pass
                # Live-poll the tracker for status bar updates (every tick)
                _refresh_status_bar(state)
                _refresh_running_indicators(state)

            ui.timer(0.2, _drain)

    # ── Status bar (fixed footer, outside the main row) ─────
    _build_status_bar(state)


# ============================================================================
# Setup tab — first-run helpers
# ============================================================================

# Where the GEMINI_API_KEY lives between launches.
ENV_FILE = "config/.env"


def _get_gemini_key() -> str:
    """Read the active Gemini key from process env (set by .env on launch)."""
    return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()


def _mask_key(key: str) -> str:
    """Return a masked preview safe to render in the UI."""
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}…{key[-4:]}"


def _persist_gemini_key(key: str, env_file: str = ENV_FILE) -> bool:
    """Write GEMINI_API_KEY to the .env file and update the current process env.

    - Empty `key` removes any existing GEMINI_API_KEY/GOOGLE_API_KEY lines.
    - Existing lines for either key are replaced (not duplicated).
    - Other lines in .env are preserved verbatim.

    Returns True on success, False on any I/O error.
    """
    try:
        os.makedirs(os.path.dirname(env_file) or ".", exist_ok=True)

        existing_lines: List[str] = []
        if os.path.exists(env_file):
            with open(env_file, "r") as f:
                existing_lines = f.readlines()

        new_lines: List[str] = []
        replaced = False
        for line in existing_lines:
            stripped = line.strip()
            if stripped.startswith("GEMINI_API_KEY=") or stripped.startswith("GOOGLE_API_KEY="):
                if key and not replaced:
                    new_lines.append(f"GEMINI_API_KEY={key}\n")
                    replaced = True
                # else: drop the line (either we're clearing, or already wrote it)
            else:
                new_lines.append(line)

        if key and not replaced:
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines[-1] = new_lines[-1] + "\n"
            new_lines.append(f"GEMINI_API_KEY={key}\n")

        with open(env_file, "w") as f:
            f.writelines(new_lines)

        # Update current process env so the next pipeline run picks it up
        # without requiring a restart.
        if key:
            os.environ["GEMINI_API_KEY"] = key
            os.environ.pop("GOOGLE_API_KEY", None)
        else:
            os.environ.pop("GEMINI_API_KEY", None)
            os.environ.pop("GOOGLE_API_KEY", None)
        return True
    except Exception as e:
        try:
            ui.notify(f"Failed to save key: {e}", type="negative")
        except Exception:
            pass
        return False


def _persist_config_to_disk() -> None:
    """Save the current in-memory config back to athanor_config.toml.

    Used by the Setup tab so identity edits stick across launches.
    Silent on success; toasts on failure.
    """
    try:
        config.save_config("config/athanor_config.toml")
    except Exception as e:
        ui.notify(f"Config save failed: {e}", type="negative")


def _is_unconfigured() -> List[str]:
    """Return a list of human-readable reasons why the project isn't ready
    to run. Empty list means RUN ALL is safe to enable."""
    reasons: List[str] = []
    pi = config._config.get("pi", {}) if config._config else {}
    name = (pi.get("name") or "").strip()
    if not name:
        reasons.append("your name")
    spark = (config.initial_spark or "").strip()
    if not spark:
        reasons.append("a research spark")
    # API key only required if using a real provider — mock mode doesn't
    # need one.
    provider = (config.llm_provider or "gemini").lower()
    if provider != "mock" and not _get_gemini_key():
        reasons.append("a Gemini API key")
    return reasons


def _refresh_api_key_status(state: AppState) -> None:
    """Update the Clavis card status line in place."""
    if state.api_key_status_label is None:
        return
    k = _get_gemini_key()
    if k:
        state.api_key_status_label.text = f"✓ Key configured ({_mask_key(k)})"
        state.api_key_status_label.style("color: var(--ath-emerald);")
    else:
        state.api_key_status_label.text = "⚠ No API key set — required for real Gemini calls"
        state.api_key_status_label.style("color: var(--ath-gold);")


def _refresh_first_run_ui(state: AppState) -> None:
    """Re-evaluate the first-run state and update banner + RUN ALL button."""
    reasons = _is_unconfigured()
    if state.first_run_banner is not None:
        state.first_run_banner.set_visibility(bool(reasons))
        if reasons and state.first_run_banner_label is not None:
            state.first_run_banner_label.text = (
                f"Before you can run the pipeline, set: {', '.join(reasons)}."
            )
    if state.run_all_button is not None:
        if reasons:
            state.run_all_button.props("disable")
        else:
            state.run_all_button.props(remove="disable")


# ============================================================================
# Setup tab
# ============================================================================

# A small canned demo so first-time users see the full pipeline animate
# in mock mode without configuring anything.
_DEMO_SPARK = (
    "Cosmic voids may carry an imprint of dark energy that we can detect "
    "by stacking weak-lensing measurements around them in upcoming "
    "wide-field surveys."
)
_DEMO_NAME = "Demo Researcher"
_DEMO_AFFILIATION = "Example University"


def _start_demo_run(state: AppState) -> None:
    """Flip into mock mode, fill demo identity + spark, run all 4 stages."""
    # Force mock provider — no API key required
    _set_config(["llm", "provider"], "mock")

    # Fill PI identity if it's still blank, but never overwrite real values
    pi = config._config.setdefault("pi", {})
    if not (pi.get("name") or "").strip():
        pi["name"] = _DEMO_NAME
    if not (pi.get("affiliation") or "").strip():
        pi["affiliation"] = _DEMO_AFFILIATION
    if not pi.get("bio_supplement"):
        pi["bio_supplement"] = (
            "Demo profile auto-generated for the Try Demo Run button. "
            "Replace with your real bio in the Identity card."
        )

    # Fill spark if blank
    if not (config.initial_spark or "").strip():
        _set_config(["workflow", "initial_spark"], _DEMO_SPARK)
        if state.spark_input is not None:
            state.spark_input.value = _DEMO_SPARK

    # Reflect the new state in any visible inputs
    if state.identity_name_input is not None and state.identity_name_input.value != pi["name"]:
        state.identity_name_input.value = pi["name"]

    _refresh_first_run_ui(state)
    ui.notify("Demo run starting (mock mode, no API key)", type="positive")
    state.start_engine(start_stage=1, end_stage=6)


def _build_api_key_card(state: AppState):
    """Paste-the-key card. Lives in the Setup tab.

    Always renders. The status line tells the user whether a key is set
    (and shows a masked preview). Pasting a new key into the input and
    blurring (or clicking Save) writes it to config/.env and updates
    os.environ for the current process.
    """
    with ui.card().classes("w-full"):
        ui.label("API Key").classes("ath-section-title")
        ui.label("Google AI Studio").classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")

        # Status line — refreshed via _refresh_api_key_status
        state.api_key_status_label = ui.label("").classes("text-sm")
        _refresh_api_key_status(state)

        ui.label("Paste a key to set or change:").classes("text-xs mt-2").style("color: var(--ath-muted)")
        key_inp = ui.input(placeholder="AIza...").props("type=password dense outlined").classes("w-full")
        state.api_key_input = key_inp

        def _save():
            v = (key_inp.value or "").strip()
            if not v:
                ui.notify("Paste a key first (use Clear to remove)", type="warning")
                return
            if _persist_gemini_key(v):
                key_inp.value = ""  # don't leave the secret sitting in the input
                _refresh_api_key_status(state)
                _refresh_first_run_ui(state)
                ui.notify("API key saved", type="positive")

        def _clear():
            if _persist_gemini_key(""):
                key_inp.value = ""
                _refresh_api_key_status(state)
                _refresh_first_run_ui(state)
                ui.notify("API key cleared", type="info")

        key_inp.on("blur", lambda e=None: _save() if (key_inp.value or "").strip() else None)

        with ui.row().classes("w-full gap-2 mt-2 items-center"):
            ui.button("Save", icon="save", on_click=_save).classes("ath-secondary-btn")
            ui.button("Clear", icon="delete_outline", on_click=_clear).classes("ath-secondary-btn")
            ui.link(
                "Get a free key →",
                "https://aistudio.google.com/apikey",
                new_tab=True,
            ).classes("text-xs ml-2").style("color: var(--ath-emerald);")
            ui.label("(stored in config/.env, gitignored)").classes("text-xs ml-2").style("color: var(--ath-muted)")


def _show_api_key_dialog(state: AppState) -> None:
    """Modal version of the Clavis card. Opened from the sidebar so the
    user can change their key from any tab without scrolling."""
    with ui.dialog() as dialog, ui.card().style("min-width: 480px;"):
        ui.label("Change API Key").classes("ath-section-title")
        ui.label("Google API Key").classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")

        current = _get_gemini_key()
        if current:
            ui.label(f"Currently set ({_mask_key(current)})").classes("text-sm").style("color: var(--ath-emerald);")
        else:
            ui.label("No key currently set").classes("text-sm").style("color: var(--ath-gold);")

        ui.label("Paste a new key:").classes("text-xs mt-2").style("color: var(--ath-muted)")
        new_inp = ui.input(placeholder="AIza...").props("type=password dense outlined autofocus").classes("w-full")

        def _save_and_close():
            v = (new_inp.value or "").strip()
            if not v:
                ui.notify("Paste a key first", type="warning")
                return
            if _persist_gemini_key(v):
                _refresh_api_key_status(state)
                _refresh_first_run_ui(state)
                ui.notify("API key saved", type="positive")
                dialog.close()

        def _clear_and_close():
            if _persist_gemini_key(""):
                _refresh_api_key_status(state)
                _refresh_first_run_ui(state)
                ui.notify("API key cleared", type="info")
                dialog.close()

        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Cancel", on_click=dialog.close).classes("ath-secondary-btn")
            ui.button("Clear key", icon="delete_outline", on_click=_clear_and_close).classes("ath-secondary-btn")
            ui.button("Save", icon="save", on_click=_save_and_close).classes("ath-run-btn")

        ui.link(
            "Get a free key →",
            "https://aistudio.google.com/apikey",
            new_tab=True,
        ).classes("text-xs mt-2").style("color: var(--ath-emerald);")

    dialog.open()


def _build_preliminary_work_list(
    state: AppState,
    role: str,
    *,
    heading: str,
    subtitle: str,
    empty_hint: str,
    add_button_label: str = "+ Add item",
) -> None:
    """Render an editable list of PreliminaryWork items filtered by role.

    Used in two places:
      - Setup tab's About You card (role="prior_work")
      - Proposal tab's knobs (role="preliminary_result")

    Registers a rebuild closure on state.preliminary_work_refreshers so
    mutations (add/remove/update) trigger an immediate re-render without
    requiring a page reload.
    """
    ui.label(heading).classes("ath-readout-key mt-3")
    ui.label(subtitle).classes("text-xs").style("color: var(--ath-muted)")

    # Container that holds the dynamic item list. Cleared + rebuilt on
    # every refresh; static intro text above stays in place.
    items_container = ui.column().classes("w-full")

    def _render_items():
        items_container.clear()
        with items_container:
            items = (
                [pw for pw in state.project_state.preliminary_work if pw.role == role]
                if state.project_state else []
            )
            if not items:
                ui.label(empty_hint).classes("text-xs mt-2").style("color: var(--ath-muted); font-style: italic;")
            for pw in items:
                _render_preliminary_work_item(state, pw)

    _render_items()
    state.preliminary_work_refreshers.append(_render_items)

    # Add new item button — outside the refreshable container so it
    # stays visible even when the list is empty
    ui.button(
        add_button_label,
        icon="add",
        on_click=lambda: _show_add_preliminary_work_dialog(state, role),
    ).classes("ath-secondary-btn mt-3").props("size=sm")


def _render_preliminary_work_item(state: AppState, pw) -> None:
    """Render one PreliminaryWork card.

    Pulled into its own helper so _render_items (inside
    _build_preliminary_work_list) can call it in a loop without deep
    nesting, and so the structure stays testable in isolation.
    """
    header_bits = [pw.title or "(untitled)"]
    if pw.kind:
        header_bits.append(f"[{pw.kind}]")
    if pw.date:
        header_bits.append(f"{pw.date}")

    with ui.expansion().classes("w-full mt-2 ath-readout").props("dense") as exp:
        with exp.add_slot("header"):
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(" · ".join(header_bits)).classes("text-sm flex-1").style("color: var(--ath-parchment);")
                if pw.auto_extracted:
                    ui.label("auto").classes("text-xs").style(
                        "color: var(--ath-emerald); "
                        "border: 1px solid var(--ath-emerald); "
                        "padding: 1px 6px; border-radius: 3px;"
                    )
                if pw.attached_paths:
                    ui.label(f"📎 {len(pw.attached_paths)}").classes("text-xs").style("color: var(--ath-gold);")

        # Editable fields
        ui.label("Title").classes("text-xs mt-1").style("color: var(--ath-muted)")
        title_inp = ui.input(value=pw.title).classes("w-full").props("dense outlined")
        title_inp.on("blur", lambda e=title_inp, pid=pw.id: state.update_preliminary_work_field(pid, "title", e.sender.value))

        with ui.grid(columns=3).classes("w-full gap-2 mt-2"):
            with ui.column():
                ui.label("Kind").classes("text-xs").style("color: var(--ath-muted)")
                kind_inp = ui.input(value=pw.kind or "").classes("w-full").props("dense outlined")
                kind_inp.on("blur", lambda e=kind_inp, pid=pw.id: state.update_preliminary_work_field(pid, "kind", e.sender.value or None))
            with ui.column():
                ui.label("Date").classes("text-xs").style("color: var(--ath-muted)")
                date_inp = ui.input(value=pw.date or "").classes("w-full").props("dense outlined")
                date_inp.on("blur", lambda e=date_inp, pid=pw.id: state.update_preliminary_work_field(pid, "date", e.sender.value or None))
            with ui.column():
                ui.label("Link / DOI").classes("text-xs").style("color: var(--ath-muted)")
                link_inp = ui.input(value=pw.link or "").classes("w-full").props("dense outlined")
                link_inp.on("blur", lambda e=link_inp, pid=pw.id: state.update_preliminary_work_field(pid, "link", e.sender.value or None))

        ui.label("Description").classes("text-xs mt-2").style("color: var(--ath-muted)")
        desc_inp = ui.textarea(value=pw.description or "").classes("w-full").props("rows=2 dense outlined")
        desc_inp.on("blur", lambda e=desc_inp, pid=pw.id: state.update_preliminary_work_field(pid, "description", e.sender.value))

        ui.label("Relevance to this proposal").classes("text-xs mt-2").style("color: var(--ath-muted)")
        rel_inp = ui.textarea(value=pw.relevance or "").classes("w-full").props("rows=2 dense outlined")
        rel_inp.on("blur", lambda e=rel_inp, pid=pw.id: state.update_preliminary_work_field(pid, "relevance", e.sender.value))

        if pw.citation:
            ui.label("Citation").classes("text-xs mt-2").style("color: var(--ath-muted)")
            ui.label(pw.citation).classes("text-xs").style("color: var(--ath-muted); font-style: italic;")

        # Attached files
        if pw.attached_paths:
            ui.label("Attached files").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
            for path in pw.attached_paths:
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(f"📄 {os.path.basename(path)}").classes("text-xs flex-1").style("color: var(--ath-text);")
                    ui.button(
                        icon="delete_outline",
                        on_click=lambda pid=pw.id, p=path: state.remove_attachment_from_preliminary_work(pid, p),
                    ).props("flat round size=sm").style("color: var(--ath-error);")

        # Upload new attachment
        ui.label("Attach a file (drag-and-drop or click to browse)").classes("text-xs mt-3").style("color: var(--ath-muted)")
        def _on_upload(e, pid=pw.id):
            try:
                content = e.content.read() if hasattr(e.content, "read") else e.content
                state.upload_file_to_preliminary_work(pid, e.name, content)
            except Exception as ex:
                _safe_notify(f"Upload failed: {ex}", type="negative")
        ui.upload(
            auto_upload=True,
            multiple=False,
            on_upload=_on_upload,
        ).props("accept=.pdf,.docx,.txt,.md,.csv,.json,.ipynb").classes("w-full")

        # Delete item
        with ui.row().classes("w-full mt-3"):
            ui.button(
                "Delete this item",
                icon="delete_outline",
                on_click=lambda pid=pw.id: state.remove_preliminary_work(pid),
            ).classes("ath-secondary-btn").props("size=sm").style("color: var(--ath-error);")


def _show_add_preliminary_work_dialog(state: AppState, role: str) -> None:
    """Modal for manually adding a new PreliminaryWork item."""
    role_label = "prior work" if role == "prior_work" else "preliminary result"
    with ui.dialog() as dialog, ui.card().style("min-width: 520px;"):
        ui.label(f"Add {role_label}").classes("ath-section-title")
        ui.element("div").classes("ath-section-rule")

        ui.label("Title (required)").classes("text-xs mt-2").style("color: var(--ath-muted)")
        title_inp = ui.input(placeholder="e.g. Smith et al. 2022, Title of Paper").classes("w-full").props("dense outlined autofocus")

        with ui.grid(columns=2).classes("w-full gap-2 mt-2"):
            with ui.column():
                ui.label("Kind").classes("text-xs").style("color: var(--ath-muted)")
                if role == "prior_work":
                    kind_options = ["publication", "dataset", "code", "software", "protocol", "specimen", "instrument", "grant", "talk", "book", "unpublished_data"]
                else:
                    kind_options = ["pilot_experiment", "proof_of_concept", "simulation", "feasibility_study", "prototype", "preliminary_data"]
                kind_inp = ui.select(options=kind_options, value=kind_options[0]).classes("w-full").props("dense outlined")
            with ui.column():
                ui.label("Date").classes("text-xs").style("color: var(--ath-muted)")
                date_inp = ui.input(placeholder="2024 or 2024-03").classes("w-full").props("dense outlined")

        ui.label("Link / DOI (optional)").classes("text-xs mt-2").style("color: var(--ath-muted)")
        link_inp = ui.input(placeholder="https://... or 10.xxx/yyy").classes("w-full").props("dense outlined")

        ui.label("Description").classes("text-xs mt-2").style("color: var(--ath-muted)")
        desc_inp = ui.textarea(placeholder="1-2 sentence summary").classes("w-full").props("rows=2 dense outlined")

        ui.label("Relevance to this proposal").classes("text-xs mt-2").style("color: var(--ath-muted)")
        rel_inp = ui.textarea(placeholder="How this connects to the proposed work").classes("w-full").props("rows=2 dense outlined")

        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Cancel", on_click=dialog.close).classes("ath-secondary-btn")
            def _do_add():
                state.add_preliminary_work(
                    role=role,
                    title=title_inp.value or "",
                    kind=kind_inp.value or None,
                    date=date_inp.value or None,
                    link=link_inp.value or None,
                    description=desc_inp.value or "",
                    relevance=rel_inp.value or "",
                    auto_extracted=False,
                )
                dialog.close()
            ui.button("Add", icon="add", on_click=_do_add).classes("ath-run-btn")
    dialog.open()


def _build_identity_card(state: AppState):
    """PI identity editor — name, affiliation, research_url, bio_supplement.

    Edits persist to athanor_config.toml on blur so the user only ever has
    to fill this in once.
    """
    pi = config._config.get("pi", {}) if config._config else {}

    with ui.card().classes("w-full"):
        ui.label("About You").classes("ath-section-title")
        ui.label("Tell Athanor about yourself").classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")

        with ui.grid(columns=2).classes("w-full gap-3"):
            with ui.column():
                ui.label("Name").classes("text-xs").style("color: var(--ath-muted)")
                name_inp = ui.input(
                    value=pi.get("name") or "",
                    placeholder="Dr. Jane Smith",
                ).classes("w-full")
                state.identity_name_input = name_inp

                def _save_name(e=None):
                    config._config.setdefault("pi", {})["name"] = name_inp.value
                    _persist_config_to_disk()
                    _refresh_first_run_ui(state)
                name_inp.on("blur", _save_name)

            with ui.column():
                ui.label("Affiliation").classes("text-xs").style("color: var(--ath-muted)")
                aff_inp = ui.input(
                    value=pi.get("affiliation") or "",
                    placeholder="Example University",
                ).classes("w-full")

                def _save_aff(e=None):
                    config._config.setdefault("pi", {})["affiliation"] = aff_inp.value
                    _persist_config_to_disk()
                aff_inp.on("blur", _save_aff)

        ui.label("Research URL (web CV, lab page, personal site)").classes("text-xs mt-3").style("color: var(--ath-muted)")
        url_inp = ui.input(
            value=pi.get("research_url") or "",
            placeholder="https://your-lab.example.edu/",
        ).classes("w-full")

        def _save_url(e=None):
            config._config.setdefault("pi", {})["research_url"] = url_inp.value
            _persist_config_to_disk()
        url_inp.on("blur", _save_url)

        ui.label("Bio (optional — fill in what a CV scrape would miss)").classes("text-xs mt-3").style("color: var(--ath-muted)")
        bio_inp = ui.textarea(
            value=pi.get("bio_supplement") or "",
            placeholder=(
                "A few sentences about your background, current focus, and "
                "anything an automated CV scrape would miss. Use this if you "
                "don't have a formal CV online."
            ),
        ).classes("w-full").props("rows=4")

        def _save_bio(e=None):
            config._config.setdefault("pi", {})["bio_supplement"] = bio_inp.value
            _persist_config_to_disk()
        bio_inp.on("blur", _save_bio)

        # ── Prior Work list ──
        # Auto-populated by TeamProfiler in Stage 1 from the profile sources;
        # user can edit, delete, add, and attach files. Feeds Stage 4's
        # Background / Prior Relevant Work sections.
        _build_preliminary_work_list(
            state,
            role="prior_work",
            heading="Prior work",
            subtitle="Publications, datasets, code, and other prior work that establishes your track record. Auto-filled from your profile; add or edit anything that matters for this specific proposal.",
            empty_hint="(None yet — run Stage 1 to auto-extract from your profile, or add manually.)",
            add_button_label="+ Add prior work item",
        )


def _build_setup_tab(state: AppState):
    """Setup tab — first-run banner, run controls at top, then config grid."""

    # ── First-run banner (only visible when name/spark are missing) ──
    banner = ui.card().classes("w-full").style(
        "border-left: 4px solid var(--ath-gold) !important; "
        "background-color: rgba(201, 164, 75, 0.08) !important;"
    )
    state.first_run_banner = banner
    with banner:
        ui.label("Welcome to Athanor").classes("ath-section-title").style("color: var(--ath-gold);")
        state.first_run_banner_label = ui.label("").classes("text-sm")
        ui.label(
            "Or click TRY DEMO RUN below to see the full pipeline in mock mode "
            "(no API key, ~10 seconds)."
        ).classes("text-xs mt-1").style("color: var(--ath-muted)")

    # ── Run Pipeline (at top so it's above the fold) ─────────
    with ui.card().classes("w-full"):
        ui.label("Run Pipeline").classes("ath-section-title")
        ui.element("div").classes("ath-section-rule")
        with ui.row().classes("w-full gap-2 items-center"):
            run_all_btn = ui.button(
                "RUN ALL",
                icon="play_arrow",
                on_click=lambda: state.start_engine(start_stage=1, end_stage=6),
            ).classes("ath-run-btn").props("size=md")
            state.run_all_button = run_all_btn
            ui.button(
                "Cancel",
                icon="stop",
                on_click=lambda: state.cancel_engine(),
            ).classes("ath-secondary-btn").props("dense")
            ui.button(
                "Try demo",
                icon="science",
                on_click=lambda: _start_demo_run(state),
            ).classes("ath-secondary-btn").props("dense").tooltip(
                "Mock mode · no API key · ~10 seconds"
            )
            ui.label("Or run individual stages from the pipeline tabs").classes(
                "text-xs ml-2"
            ).style("color: var(--ath-muted)")

    # ── Research idea — full width, the most important field ────
    with ui.card().classes("w-full"):
        ui.label("Research Idea").classes("ath-section-title")
        ui.label("Initial hypothesis or idea").classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")

        spark = ui.textarea(
            label="Initial research idea",
            value=config.initial_spark or "",
            placeholder="Describe your hypothesis in 1-3 sentences...",
        ).classes("w-full").props("rows=3 dense outlined")
        state.spark_input = spark

        def _save_spark():
            try:
                if "workflow" not in config._config:
                    config._config["workflow"] = {}
                config._config["workflow"]["initial_spark"] = spark.value
                _persist_config_to_disk()
                _refresh_first_run_ui(state)
                ui.notify("Spark saved", type="positive")
            except Exception as e:
                ui.notify(f"Save failed: {e}", type="negative")

        spark.on("blur", lambda e=None: _save_spark())
        ui.button("Save", icon="save", on_click=_save_spark).classes("ath-secondary-btn mt-1").props("dense size=sm")

    # ── 2-col grid: Identity + API key, then Pipeline + Resources ─
    with ui.grid(columns=2).classes("w-full gap-2"):
        _build_identity_card(state)
        _build_api_key_card(state)

    with ui.card().classes("w-full"):
        ui.label("Pipeline Settings").classes("ath-section-title")
        ui.label("LLM & Search").classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")

        with ui.row().classes("w-full gap-3"):
            with ui.column().classes("flex-1 gap-0"):
                ui.label("LLM Provider").classes("text-xs").style("color: var(--ath-muted)")
                ui.select(
                    options=["gemini", "mock"],
                    value=config.llm_provider or "gemini",
                ).classes("w-full").props("dense outlined").on_value_change(
                    lambda e: _set_config(["llm", "provider"], e.value)
                )
            with ui.column().classes("flex-1 gap-0"):
                ui.label("Model").classes("text-xs").style("color: var(--ath-muted)")
                ui.input(value=config.llm_model or "gemini-2.5-flash").classes("w-full").props("dense outlined").on_value_change(
                    lambda e: _set_config(["llm", "primary_model"], e.value)
                )

    # ── Research Resources card (expansion by default)──
    _build_resources_card(state)

    _refresh_first_run_ui(state)


# ============================================================================
# Resources card (Setup tab)
# ============================================================================

def _build_resources_card(state: AppState):
    """A card with free-text + structured editors for ResearchResources."""
    from src.athanor.ingest import (
        ResearchResources, ComputeResources, DataResources, InstrumentResources,
        PersonnelResources, SoftwareResources, InstitutionalResources,
        default_academic_resources,
        get_domain_defaults,
        merge_resources_with_defaults,
        RESEARCH_DOMAINS,
    )
    from src.athanor.core.config_loader import config as _cfg

    with ui.card().classes("w-full"):
        ui.label("Research Resources").classes("ath-section-title")
        ui.label("What You Have to Work With").classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")

        # ── Research domain selector ────────────────────────────
        # Domain choice drives the defaults baseline that fills any
        # gaps in the resource fields downstream.
        current_domain = (
            (state.project_state.research_domain if state.project_state else None)
            or _cfg.research_domain
            or "general"
        )
        ui.label("Research domain (selects defaults baseline)").classes("text-xs").style("color: var(--ath-muted)")

        def _on_domain_change(e):
            new_domain = e.value or "general"
            if not state.project_state:
                from src.athanor.core.state import ProjectState
                state.project_state = ProjectState(project_name="New Project")
            state.project_state.research_domain = new_domain
            # Re-merge any existing resources against the new baseline
            existing = None
            if state.project_state.resources:
                try:
                    existing = ResearchResources.model_validate(state.project_state.resources)
                except Exception:
                    existing = None
            merged = merge_resources_with_defaults(existing, domain=new_domain)
            state.project_state.resources = merged.model_dump()
            state.save_project_state()
            ui.notify(f"Domain set to {new_domain}", type="positive", position="bottom-right", timeout=1000)

        ui.select(
            options=RESEARCH_DOMAINS,
            value=current_domain,
            on_change=_on_domain_change,
        ).classes("w-full mb-2").props("dense outlined")

        # Load current resources from state, or fall back to defaults
        def _current_resources() -> ResearchResources:
            if state.project_state and state.project_state.resources:
                try:
                    return ResearchResources.model_validate(state.project_state.resources)
                except Exception:
                    pass
            return default_academic_resources()

        rr = _current_resources()

        def _ensure_state_resources() -> dict:
            if not state.project_state:
                from src.athanor.core.state import ProjectState
                state.project_state = ProjectState(project_name="New Project")
            if not state.project_state.resources:
                state.project_state.resources = default_academic_resources().model_dump()
            return state.project_state.resources

        def _save_field(category: str, key: str, value):
            d = _ensure_state_resources()
            if category not in d or d[category] is None:
                d[category] = {}
            d[category][key] = value
            state.save_project_state()

        # ── Free text supplement (the easy entry point) ────────
        ui.label("Quick description (one paragraph — fastest way to fill all categories)").classes("text-xs").style("color: var(--ath-muted)")
        free_text = ui.textarea(
            value=rr.free_text_supplement or "",
            placeholder=(
                "e.g. University HPC cluster with 4 A100 GPUs. "
                "Member of IceCube collaboration with full data access. "
                "1 grad student (Smith) and 1 postdoc working with me. "
                "Standard university grants office support."
            ),
        ).classes("w-full").props("rows=4")

        def _save_free_text():
            d = _ensure_state_resources()
            d["free_text_supplement"] = free_text.value
            state.save_project_state()
            ui.notify("Saved", type="positive", position="bottom-right", timeout=800)

        free_text.on("blur", _save_free_text)

        # ── Structured detail (collapsed by default) ───────────
        with ui.expansion("Structured details (optional overrides)", icon="tune").classes("w-full mt-2"):

            # Compute
            ui.label("Compute").classes("ath-readout-key")
            with ui.grid(columns=2).classes("w-full gap-2"):
                with ui.column():
                    ui.label("HPC cluster name").classes("text-xs").style("color: var(--ath-muted)")
                    inp = ui.input(value=rr.compute.hpc_cluster_name or "").classes("w-full")
                    inp.on("blur", lambda e=inp: _save_field("compute", "hpc_cluster_name", e.sender.value))
                with ui.column():
                    ui.label("GPU count / type").classes("text-xs").style("color: var(--ath-muted)")
                    with ui.row().classes("w-full gap-1"):
                        gpu_n = ui.number(value=rr.compute.gpu_count or 0, min=0, format="%.0f").classes("flex-1")
                        gpu_n.on_value_change(lambda e: _save_field("compute", "gpu_count", int(e.value or 0) or None))
                        gpu_t = ui.input(value=rr.compute.gpu_type or "", placeholder="A100").classes("flex-1")
                        gpu_t.on("blur", lambda e=gpu_t: _save_field("compute", "gpu_type", e.sender.value or None))
                with ui.column():
                    ui.label("Compute hours / year").classes("text-xs").style("color: var(--ath-muted)")
                    n = ui.number(value=rr.compute.compute_hours_year or 0, min=0, format="%.0f").classes("w-full")
                    n.on_value_change(lambda e: _save_field("compute", "compute_hours_year", int(e.value or 0) or None))
                with ui.column():
                    ui.label("Cloud credits ($)").classes("text-xs").style("color: var(--ath-muted)")
                    n = ui.number(value=rr.compute.cloud_credits_usd or 0, min=0, format="%.0f").classes("w-full")
                    n.on_value_change(lambda e: _save_field("compute", "cloud_credits_usd", float(e.value or 0) or None))

            # Personnel
            ui.label("Personnel").classes("ath-readout-key mt-3")
            with ui.grid(columns=3).classes("w-full gap-2"):
                with ui.column():
                    ui.label("Students").classes("text-xs").style("color: var(--ath-muted)")
                    n = ui.number(value=rr.personnel.students_available, min=0, format="%.0f").classes("w-full")
                    n.on_value_change(lambda e: _save_field("personnel", "students_available", int(e.value or 0)))
                with ui.column():
                    ui.label("Postdocs").classes("text-xs").style("color: var(--ath-muted)")
                    n = ui.number(value=rr.personnel.postdocs_available, min=0, format="%.0f").classes("w-full")
                    n.on_value_change(lambda e: _save_field("personnel", "postdocs_available", int(e.value or 0)))
                with ui.column():
                    ui.label("Tech staff").classes("text-xs").style("color: var(--ath-muted)")
                    n = ui.number(value=rr.personnel.technical_staff, min=0, format="%.0f").classes("w-full")
                    n.on_value_change(lambda e: _save_field("personnel", "technical_staff", int(e.value or 0)))

            # Data — comma-separated lists for simplicity
            ui.label("Data Access").classes("ath-readout-key mt-3")
            ui.label("Public datasets (comma-separated)").classes("text-xs").style("color: var(--ath-muted)")
            ds_inp = ui.input(value=", ".join(rr.data.public_datasets)).classes("w-full")

            def _save_datasets():
                items = [s.strip() for s in (ds_inp.value or "").split(",") if s.strip()]
                _save_field("data", "public_datasets", items)
            ds_inp.on("blur", _save_datasets)

            # Instruments
            ui.label("Instruments / Facilities (comma-separated)").classes("text-xs mt-2").style("color: var(--ath-muted)")
            inst_inp = ui.input(
                value=", ".join(rr.instruments.telescopes_observatories + rr.instruments.lab_equipment),
                placeholder="e.g. IceCube, JWST GO time, campus NMR core",
            ).classes("w-full")

            def _save_instruments():
                items = [s.strip() for s in (inst_inp.value or "").split(",") if s.strip()]
                _save_field("instruments", "lab_equipment", items)
            inst_inp.on("blur", _save_instruments)

            # Software
            ui.label("Licensed software (comma-separated)").classes("text-xs mt-2").style("color: var(--ath-muted)")
            sw_inp = ui.input(
                value=", ".join(rr.software.licenses),
                placeholder="e.g. MATLAB, Mathematica, IDL",
            ).classes("w-full")

            def _save_software():
                items = [s.strip() for s in (sw_inp.value or "").split(",") if s.strip()]
                _save_field("software", "licenses", items)
            sw_inp.on("blur", _save_software)


# ============================================================================
# Generic stage tab builder
# ============================================================================

STAGE_TITLES = {
    1: "Researcher Profile",
    2: "Hypothesis Debate",
    3: "Research Plan",
    4: "Grant Proposal",
    5: "Execution",
    6: "Paper",
}


def _show_stage_settings_dialog(state: AppState, num: int, knobs_builder) -> None:
    title = STAGE_TITLES.get(num, f"Stage {num}")
    with ui.dialog() as dialog, ui.card().style(
        "min-width: 520px; max-width: 680px; max-height: 85vh;"
    ):
        with ui.row().classes("w-full items-center"):
            ui.label(f"Stage {num} Settings").classes("ath-section-title flex-1")
            ui.button(icon="close", on_click=dialog.close).props("flat round size=sm")
        ui.label(title).classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")
        with ui.scroll_area().classes("w-full").style("max-height: 70vh;"):
            knobs_builder(state)
    dialog.open()


def _build_stage_tab(state: AppState, num: int, name: str, motto: str,
                     knobs_builder, readout_builder, promoted_builder=None):
    """Build a stage tab: compact header row (title + run buttons),
    optional promoted content (always visible), collapsible Settings,
    then a flex-grown output panel.

    Returns a refresh function that re-renders the readout from current state.
    """
    # Header strip — title on left, run controls + settings gear on right
    with ui.card().classes("w-full"):
        with ui.row().classes("w-full items-center gap-3"):
            with ui.column().classes("gap-0 flex-1"):
                ui.label(name).classes("ath-section-title")
                ui.label(motto).classes("ath-section-subtitle")
            with ui.row().classes("gap-2 items-center"):
                run_btn = ui.button(
                    f"Run Stage {num}",
                    icon="play_arrow",
                    on_click=lambda: state.start_engine(start_stage=num, end_stage=num),
                ).classes("ath-run-btn").props("dense")
                state.stage_run_btns[num] = run_btn
                ui.button(
                    icon="refresh",
                    on_click=lambda: state.start_engine(start_stage=num, end_stage=num),
                ).classes("ath-secondary-btn").props("dense round size=sm").tooltip(f"Re-run Stage {num}")

                def _open_settings(s=state, n=num, kb=knobs_builder):
                    _show_stage_settings_dialog(s, n, kb)
                ui.button(
                    icon="tune",
                    on_click=_open_settings,
                ).props("flat round size=sm").style("color: var(--ath-muted)").tooltip("Stage settings")

        if promoted_builder:
            ui.element("div").classes("ath-section-rule")
            promoted_builder(state)

    # Output readout panel — flex-grows to fill remaining space
    with ui.card().classes("w-full").style("flex: 1 1 auto; min-height: 0;"):
        ui.label("Output").classes("ath-section-title")
        ui.element("div").classes("ath-section-rule")
        readout_container = ui.column().classes("w-full ath-output-card")

    def _refresh():
        readout_container.clear()
        with readout_container:
            readout_builder(state)

    _refresh()
    return _refresh


# ============================================================================
# Stage 1 — Researcher Profile
# ============================================================================

def _profile_knobs(state: AppState):
    ui.label("Re-scrape from a new URL or PDF (Phase 3)").classes("text-xs").style("color: var(--ath-muted)")
    ui.label("Add a co-investigator (Phase 3)").classes("text-xs").style("color: var(--ath-muted)")


def _profile_readout(state: AppState):
    s = state.project_state
    if not s or not s.researcher_profile:
        ui.label("No profile yet. Run Stage 1.").classes("text-sm").style("color: var(--ath-muted)")
        return

    p = s.researcher_profile
    ui.label(p.name or "(unnamed)").classes("text-lg").style("color: var(--ath-parchment); font-family: 'Cinzel', serif;")
    if p.affiliation:
        ui.label(p.affiliation).classes("text-xs").style("color: var(--ath-muted)")

    if p.team_members:
        ui.label("Team").classes("ath-readout-key")
        ui.label(", ".join(p.team_members)).classes("ath-readout text-sm")

    for key, label in [
        ("domain_expertise", "Domain Expertise"),
        ("available_resources", "Available Resources"),
        ("publication_history", "Publication History"),
        ("important_connections", "Important Connections"),
        ("persona_motivation", "Motivation"),
    ]:
        val = getattr(p, key, "") or ""
        ui.label(label).classes("ath-readout-key")
        _editable_text(state, val, lambda v, k=key: _set_profile_field(state, k, v))


def _set_profile_field(state: AppState, key: str, value: str) -> None:
    if state.project_state and state.project_state.researcher_profile:
        setattr(state.project_state.researcher_profile, key, value)
        state.save_project_state()


def _set_dag_node_field(state: AppState, node_id: str, field: str, value: str) -> None:
    """Mutate a single field on a DAG node and persist state."""
    s = state.project_state
    if not s:
        return
    for node in s.dag:
        if node.id == node_id:
            setattr(node, field, value)
            break
    state.save_project_state()


def _delete_dag_node(state: AppState, node_id: str) -> None:
    """Remove a node from state.dag and clean up all references to it.

    This also removes the node's id from every other node's `dependencies`
    list and clears any Artifact.source_node_id that pointed at it. Missing
    producers for inputs become validation errors (surfaced in the panel)
    so the user notices and re-plans or re-wires.
    """
    s = state.project_state
    if not s:
        return
    s.dag = [n for n in s.dag if n.id != node_id]
    for node in s.dag:
        node.dependencies = [d for d in node.dependencies if d != node_id]
        for inp in node.inputs:
            if inp.source_node_id == node_id:
                inp.source_node_id = ""
    # Stale the scorecard-equivalent signal for plans: mark the legacy tasks
    # list empty so flat_plan_for_reports rebuilds from the new DAG.
    s.tasks = []
    state.save_project_state()
    state._refresh_all_panels()
    _safe_notify("Task deleted", type="positive")


def _add_dag_node(
    state: AppState,
    name: str,
    description: str,
    executor_type: str,
    agent_role: str,
    workflow: str,
    dep_ids: List[str],
) -> None:
    """Append a manually-added node to state.dag."""
    from src.athanor.core.state import DAGNode, ExecutionSpec, TaskType
    s = state.project_state
    if not s:
        return
    ttype_map = {"AGENT": TaskType.AGENT, "HUMAN": TaskType.HUMAN, "HYBRID": TaskType.HYBRID}
    new_node = DAGNode(
        name=name or "Untitled Step",
        description=description or "",
        execution_spec=ExecutionSpec(
            executor_type=ttype_map.get(executor_type, TaskType.AGENT),
            agent_role=agent_role or None,
        ),
        workflow=workflow or "",
        dependencies=[d for d in dep_ids if d],
    )
    s.dag.append(new_node)
    s.tasks = []  # invalidate legacy view
    state.save_project_state()
    state._refresh_all_panels()
    _safe_notify(f"Added: {new_node.name}", type="positive")


def _set_refined_hypothesis(state: AppState, value: str) -> None:
    """Mutate state.refined_hypothesis and mark the scorecard stale.

    The stale flag tells the UI that the scorecard no longer matches the
    text the user is looking at. A fresh debate clears the flag.
    """
    if state.project_state:
        old = (state.project_state.refined_hypothesis or "").strip()
        new = (value or "").strip()
        state.project_state.refined_hypothesis = value
        # Only mark stale when the text actually changed AND a scorecard exists
        if old != new and state.project_state.scorecard is not None:
            state.project_state.scorecard_stale = True
        state.save_project_state()


def _set_abstract(state: AppState, value: str) -> None:
    if state.project_state:
        state.project_state.project_abstract = value
        state.save_project_state()


def _set_proposal_section(state: AppState, idx: int, value: str) -> None:
    if state.project_state and state.project_state.proposal:
        if 0 <= idx < len(state.project_state.proposal.sections):
            state.project_state.proposal.sections[idx].content = value
            state.save_project_state()


# ============================================================================
# Stage 2 — Hypothesis Debate
# ============================================================================

def _hypothesis_knobs(state: AppState):
    ui.label("Debate rounds").classes("text-xs").style("color: var(--ath-muted)")
    rounds = ui.number(value=config.debate_max_turns // 3 if config.debate_max_turns else 1, min=1, max=5).classes("w-32")
    rounds.on_value_change(lambda e: _set_config(["debate", "max_rounds"], int(e.value)))

    ui.label("Antagonist model (blank = use primary)").classes("text-xs mt-2").style("color: var(--ath-muted)")
    antag = ui.input(value=config.antagonist_model or "").classes("w-full")
    antag.on_value_change(lambda e: _set_config(["llm", "antagonist_model"], e.value))


def _render_scorecard_rows(sc: "HypothesisScorecard", previous: Optional["HypothesisScorecard"] = None):
    """Render the 5-dimension score bars. If `previous` is given, show a
    small Δ next to each score so the user can see what changed."""
    criteria = [
        ("Novelty", "novelty_score", "novelty_rationale"),
        ("Plausibility", "plausibility_score", "plausibility_rationale"),
        ("Falsifiability", "falsifiability_score", "falsifiability_rationale"),
        ("Executability", "executability_score", "executability_rationale"),
        ("Impact", "impact_score", "impact_rationale"),
        ("Reviewer Appeal", "reviewer_appeal_score", "reviewer_appeal_rationale"),
    ]
    for name, score_key, rat_key in criteria:
        score = getattr(sc, score_key)
        rationale = getattr(sc, rat_key)
        delta_text = ""
        delta_color = "var(--ath-muted)"
        if previous is not None:
            prev = getattr(previous, score_key)
            if score > prev:
                delta_text = f"▲ +{score - prev}"
                delta_color = "var(--ath-emerald)"
            elif score < prev:
                delta_text = f"▼ −{prev - score}"
                delta_color = "var(--ath-error)"
            else:
                delta_text = "="
                delta_color = "var(--ath-muted)"
        with ui.column().classes("w-full gap-1 mt-2"):
            with ui.row().classes("w-full items-center gap-3"):
                ui.label(name).classes("text-sm").style("color: var(--ath-text); width: 110px;")
                ui.label(f"{score}/5").classes("text-sm").style("color: var(--ath-emerald);")
                if delta_text:
                    ui.label(delta_text).classes("text-xs").style(f"color: {delta_color}; width: 44px;")
                with ui.element("div").classes("flex-1 ath-score-bar"):
                    ui.element("div").classes("ath-score-fill").style(f"width: {(score / 5.0) * 100:.0f}%")
            if rationale:
                ui.label(rationale).classes("text-xs").style("color: var(--ath-muted); padding-left: 110px;")


def _hypothesis_readout(state: AppState):
    """Three-slot Hypothesis tab.

    Slot 1 — Original Spark: read-only, pinned, never overwritten. Has a
             "Reset to this" button that wipes derived hypothesis state.
    Slot 2 — Current Hypothesis: editable + feedback + scorecard. Run
             Re-debate and Optimize from here.
    Slot 3 — Previous Version: only visible when a previous exists. Has a
             Revert button that swaps it back into slot 2.
    """
    s = state.project_state
    if not s:
        ui.label("No project loaded. Run Stage 1 first.").classes("text-sm").style("color: var(--ath-muted)")
        return

    # ── Slot 1: Original Spark ───────────────────────────────
    ui.label("ORIGINAL SPARK").classes("ath-readout-key").style("letter-spacing: 0.08em;")
    original = (s.initial_shower_thought or "").strip()
    if original:
        ui.label(original).classes("text-sm ath-readout").style(
            "background: rgba(201, 164, 75, 0.06); "
            "border-left: 3px solid var(--ath-gold); "
            "padding: 8px 12px; border-radius: 4px;"
        )
        with ui.row().classes("w-full gap-2 mt-1"):
            ui.button(
                "↻ Reset to spark",
                icon="restart_alt",
                on_click=lambda: _confirm_reset_to_spark(state),
            ).classes("ath-secondary-btn").props("size=sm")
            ui.label("Wipes current + previous, ready for a fresh Stage 2 run").classes("text-xs self-center").style("color: var(--ath-muted)")
    else:
        ui.label("(No spark set — type one in the Setup tab)").classes("text-sm").style("color: var(--ath-muted)")

    ui.element("div").classes("ath-section-rule mt-3")

    # ── Slot 2: Current Hypothesis (+ scorecard) ─────────────
    if not s.refined_hypothesis and not s.scorecard:
        ui.label("No hypothesis yet. Run Stage 2.").classes("text-sm mt-3").style("color: var(--ath-muted)")
        return

    ui.label("CURRENT HYPOTHESIS").classes("ath-readout-key mt-3").style("letter-spacing: 0.08em;")

    if s.scorecard_stale:
        with ui.row().classes("w-full items-center gap-2 mt-1").style(
            "background: rgba(201, 164, 75, 0.12); "
            "border-left: 3px solid var(--ath-gold); "
            "padding: 6px 10px; border-radius: 4px;"
        ):
            ui.label("⚠").classes("text-sm").style("color: var(--ath-gold);")
            ui.label("Scorecard is out of date — the hypothesis text has been edited since the last debate. Re-debate to refresh.").classes("text-xs flex-1").style("color: var(--ath-gold);")

    _editable_text(
        state,
        s.refined_hypothesis or "",
        lambda v: _set_refined_hypothesis(state, v),
    )

    # Feedback note (sticky until consumed by re-debate)
    ui.label("Feedback for next debate (optional — the Proponent will address it)").classes("text-xs mt-3").style("color: var(--ath-muted)")
    feedback_inp = ui.textarea(
        placeholder="e.g. Widen the regime to include all density gradients. Push harder on falsifiability.",
    ).classes("w-full").props("rows=3 dense outlined")

    with ui.row().classes("w-full gap-2 mt-2"):
        ui.button(
            "Edit + Re-debate",
            icon="replay",
            on_click=lambda: state.rerun_debate(
                edited_hypothesis=s.refined_hypothesis or "",
                feedback=feedback_inp.value or "",
                source="Re-debate",
            ),
        ).classes("ath-run-btn").props("size=sm")
        ui.button(
            "Optimize",
            icon="auto_awesome",
            on_click=lambda: _show_optimize_preview(state, feedback_inp),
        ).classes("ath-secondary-btn").props("size=sm").tooltip(
            "Generate a variant tuned for novelty + impact, preview it, then debate"
        )

    # Scorecard
    if s.scorecard:
        sc = s.scorecard
        ui.label("Verdict").classes("ath-readout-key mt-3")
        verdict_color = "var(--ath-emerald)" if sc.recommendation == "PROCEED" else "var(--ath-gold)"
        ui.label(sc.recommendation or "?").classes("text-lg").style(f"color: {verdict_color}; font-family: 'Cinzel', serif;")

        if sc.verdict_summary:
            ui.label(sc.verdict_summary).classes("ath-readout text-sm mt-1")

        # Scorecard header — include "before/after" callout when a previous
        # scorecard exists (i.e. the user just re-debated or optimized)
        if s.previous_scorecard is not None:
            ui.label("Scorecard (vs previous)").classes("ath-readout-key mt-3")
        else:
            ui.label("Scorecard").classes("ath-readout-key mt-3")

        _render_scorecard_rows(sc, previous=s.previous_scorecard)

        if sc.unresolved_issues:
            ui.label("Unresolved Issues").classes("ath-readout-key mt-3")
            for issue in sc.unresolved_issues:
                ui.label(f"• {issue}").classes("text-sm ath-readout")

    # ── Slot 3: Previous Version (if any) ────────────────────
    if s.previous_hypothesis:
        ui.element("div").classes("ath-section-rule mt-4")
        ui.label("PREVIOUS VERSION").classes("ath-readout-key mt-3").style("letter-spacing: 0.08em; color: var(--ath-muted);")
        ui.label(s.previous_hypothesis).classes("text-xs ath-readout").style(
            "background: var(--ath-surface-alt); "
            "padding: 8px 12px; border-radius: 4px; color: var(--ath-muted);"
        )
        ui.button(
            "↩ Revert to this",
            icon="undo",
            on_click=lambda: _revert_to_previous(state),
        ).classes("ath-secondary-btn mt-1").props("size=sm").tooltip(
            "Swap previous back into current. The revert is one-way — re-debate to refresh the scorecard."
        )


def _confirm_reset_to_spark(state: AppState) -> None:
    """Prompt before wiping all derived hypothesis state."""
    s = state.project_state
    if not s:
        return
    if not s.refined_hypothesis and not s.scorecard:
        ui.notify("Nothing to reset", type="info")
        return
    with ui.dialog() as dialog, ui.card():
        ui.label("Reset to original spark?").classes("ath-section-title")
        ui.label("Will clear refined hypothesis and scorecard").classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")
        ui.label("This wipes the current hypothesis, scorecard, and previous-version slot.").classes("text-sm mt-2")
        ui.label("The original spark and your researcher profile are untouched.").classes("text-xs").style("color: var(--ath-muted)")
        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Cancel", on_click=dialog.close).classes("ath-secondary-btn")
            def _do_reset():
                s.refined_hypothesis = None
                s.scorecard = None
                s.previous_hypothesis = None
                s.previous_scorecard = None
                s.scorecard_stale = False
                state.save_project_state()
                state._refresh_all_panels()
                ui.notify("Reset. Click RUN STAGE 2 to debate again.", type="positive")
                dialog.close()
            ui.button("Reset", icon="restart_alt", on_click=_do_reset).classes("ath-run-btn")
    dialog.open()


def _json_load(f):
    """Thin wrapper around json.load used by project listing helpers."""
    import json as _json
    return _json.load(f)


def _format_mtime(mtime: float) -> str:
    """Format a file mtime as a short relative string: '2 min ago', '3d ago'."""
    if not mtime:
        return ""
    from datetime import datetime as _dt
    delta = _dt.now() - _dt.fromtimestamp(mtime)
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    if secs < 86400 * 30:
        return f"{secs // 86400}d ago"
    return _dt.fromtimestamp(mtime).strftime("%Y-%m-%d")


def _safe_notify(message: str, *, type: str = "info") -> None:
    """Toast a notification if we're inside a client context; silently
    no-op otherwise. Keeps helper functions callable from unit tests."""
    try:
        ui.notify(message, type=type)
    except Exception:
        pass


def _revert_to_previous(state: AppState) -> None:
    """Swap the previous-version slot back into current."""
    s = state.project_state
    if not s or not s.previous_hypothesis:
        return
    # Swap: move previous into current, clear previous
    s.refined_hypothesis = s.previous_hypothesis
    s.scorecard = s.previous_scorecard
    s.previous_hypothesis = None
    s.previous_scorecard = None
    s.scorecard_stale = False
    state.save_project_state()
    state._refresh_all_panels()
    _safe_notify("Reverted to previous version", type="positive")


def _show_optimize_preview(state: AppState, feedback_inp: Any) -> None:
    """Run the optimizer, show a preview dialog, and on Send-to-Debate
    kick off a re-debate with the variant text."""
    s = state.project_state
    if not s or not s.refined_hypothesis:
        ui.notify("No hypothesis to optimize — run Stage 2 first", type="warning")
        return

    ui.notify("Optimizing…", type="info", position="bottom-right", timeout=1200)
    variant = state.optimize_hypothesis()
    if variant is None:
        return

    current_text = s.refined_hypothesis

    with ui.dialog() as dialog, ui.card().style("min-width: 720px; max-width: 920px;"):
        ui.label("Review optimized variant").classes("ath-section-title")
        ui.label("Preview before debate").classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")

        with ui.row().classes("w-full gap-3"):
            with ui.column().classes("flex-1"):
                ui.label("CURRENT").classes("text-xs").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                ui.label(current_text).classes("text-sm ath-readout").style(
                    "background: var(--ath-surface-alt); padding: 8px 12px; border-radius: 4px;"
                )
            with ui.column().classes("flex-1"):
                ui.label("OPTIMIZED VARIANT (editable)").classes("text-xs").style("color: var(--ath-emerald); letter-spacing: 0.08em;")
                variant_inp = ui.textarea(value=variant.text).classes("w-full").props("rows=6 dense outlined")

        if variant.rationale:
            ui.label("Optimizer rationale").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
            ui.label(variant.rationale).classes("text-sm ath-readout mt-1")

        if variant.targeted_scores:
            ui.label("Optimizer predicts").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
            with ui.row().classes("w-full gap-3 mt-1"):
                for name in ("novelty", "plausibility", "falsifiability", "executability", "impact"):
                    val = variant.targeted_scores.get(name, 0)
                    with ui.column().classes("items-center"):
                        ui.label(name.capitalize()).classes("text-xs").style("color: var(--ath-muted);")
                        ui.label(f"{val}/5").classes("text-sm").style("color: var(--ath-emerald);")

        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button("Cancel", on_click=dialog.close).classes("ath-secondary-btn")
            def _send():
                new_text = (variant_inp.value or "").strip()
                if not new_text:
                    ui.notify("Variant text is empty", type="warning")
                    return
                # Feed the optimizer's rationale as feedback to the debater so
                # the Proponent knows why the hypothesis was reshaped.
                existing_feedback = (feedback_inp.value or "").strip() if feedback_inp is not None else ""
                injected = variant.rationale or ""
                merged = "\n\n".join(x for x in (existing_feedback, injected) if x)
                state.rerun_debate(
                    edited_hypothesis=new_text,
                    feedback=merged,
                    source="Optimize",
                )
                dialog.close()
            ui.button("Send to debate", icon="play_arrow", on_click=_send).classes("ath-run-btn")

    dialog.open()


# ============================================================================
# Stage 3 — Research Plan
# ============================================================================

def _plan_knobs(state: AppState):
    ui.label("Max revision rounds").classes("text-xs").style("color: var(--ath-muted)")
    revs = ui.number(value=config.planner_max_revisions, min=1, max=10).classes("w-32")
    revs.on_value_change(lambda e: _set_config(["planner", "max_revisions"], int(e.value)))


def _plan_readout(state: AppState):
    """Topo-sorted plan view backed by state.dag.

    Layout:
      - Project title + abstract (editable)
      - Validation panel (only if errors exist)
      - Topo-sorted indented list, one expandable card per node
      - Re-plan feedback textarea + button
    """
    s = state.project_state
    if not s:
        ui.label("No project loaded. Run Stage 1 first.").classes("text-sm").style("color: var(--ath-muted)")
        return

    # Prefer DAG; fall back to legacy tasks if DAG is empty.
    has_dag = bool(s.dag)
    if not has_dag and not s.tasks:
        ui.label("No plan yet. Run Stage 3.").classes("text-sm").style("color: var(--ath-muted)")
        return

    # ── Title + abstract ──
    if s.project_name and s.project_name != "New-Research-Idea":
        ui.label(s.project_name).classes("text-lg").style("color: var(--ath-parchment); font-family: 'Cinzel', serif;")

    ui.label("Abstract (editable)").classes("ath-readout-key")
    _editable_text(state, s.project_abstract or "", lambda v: _set_abstract(state, v))

    # ── Validation panel (only when errors exist) ──
    errors = s.dag_validation_errors() if has_dag else []
    if errors:
        with ui.card().classes("w-full mt-3").style(
            "border-left: 4px solid var(--ath-error) !important; "
            "background-color: rgba(217, 106, 106, 0.08) !important;"
        ):
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("⚠").classes("text-lg").style("color: var(--ath-error);")
                ui.label(f"DAG has {len(errors)} validation error(s)").classes("ath-section-title").style("color: var(--ath-error);")
            ui.label("Stage 4 will refuse to run until these are resolved. Re-plan with feedback to regenerate.").classes("text-xs").style("color: var(--ath-muted);")
            for e in errors[:8]:
                ui.label(f"• {e}").classes("text-xs mt-1").style("color: var(--ath-error);")
            if len(errors) > 8:
                ui.label(f"… and {len(errors) - 8} more").classes("text-xs").style("color: var(--ath-muted);")

    # ── Aggregate compute budget (if any node has compute_spec) ──
    if has_dag:
        budget = s.aggregate_compute_budget()
        any_populated = any(
            budget[k] is not None
            for k in ("cpu_hours", "gpu_hours", "storage_gb", "human_hours", "wall_clock_days")
        )
        if any_populated:
            ui.label("Plan-derived compute budget").classes("ath-readout-key mt-3")
            with ui.row().classes("w-full gap-4 mt-1 p-2").style(
                "background: var(--ath-surface-alt); border-radius: 4px;"
            ):
                if budget["cpu_hours"] is not None:
                    with ui.column().classes("items-center"):
                        ui.label("CPU").classes("text-xs").style("color: var(--ath-muted);")
                        ui.label(f"{budget['cpu_hours']:,.0f} h").classes("text-sm").style("color: var(--ath-emerald);")
                if budget["gpu_hours"] is not None:
                    with ui.column().classes("items-center"):
                        gpu_label = "GPU"
                        if budget["gpu_types"]:
                            gpu_label = "GPU (" + "/".join(budget["gpu_types"]) + ")"
                        ui.label(gpu_label).classes("text-xs").style("color: var(--ath-muted);")
                        ui.label(f"{budget['gpu_hours']:,.0f} h").classes("text-sm").style("color: var(--ath-emerald);")
                if budget["storage_gb"] is not None:
                    with ui.column().classes("items-center"):
                        ui.label("Storage").classes("text-xs").style("color: var(--ath-muted);")
                        ui.label(f"{budget['storage_gb']:,.0f} GB").classes("text-sm").style("color: var(--ath-emerald);")
                if budget["memory_gb_peak"] is not None:
                    with ui.column().classes("items-center"):
                        ui.label("Peak RAM").classes("text-xs").style("color: var(--ath-muted);")
                        ui.label(f"{budget['memory_gb_peak']:,.0f} GB").classes("text-sm").style("color: var(--ath-emerald);")
                if budget["human_hours"] is not None:
                    with ui.column().classes("items-center"):
                        ui.label("Human").classes("text-xs").style("color: var(--ath-muted);")
                        ui.label(f"{budget['human_hours']:,.0f} h").classes("text-sm").style("color: var(--ath-emerald);")
                if budget["wall_clock_days"] is not None:
                    with ui.column().classes("items-center"):
                        ui.label("Wall clock").classes("text-xs").style("color: var(--ath-muted);")
                        ui.label(f"{budget['wall_clock_days']:,.0f} d").classes("text-sm").style("color: var(--ath-emerald);")

    # ── Plan view (List or Graph) ──
    if has_dag:
        nodes = s.topo_sorted_dag()
        depth_map = s.dag_depth_map()

        with ui.row().classes("w-full items-center gap-2 mt-3"):
            ui.label(f"Plan ({len(nodes)} nodes, DAG)").classes("ath-readout-key flex-1")
            view_toggle = ui.toggle(
                {"list": "List", "graph": "Graph"},
                value="list",
            ).props("dense color=primary")

        list_container = ui.column().classes("w-full")
        graph_container = ui.column().classes("w-full")

        with list_container:
            for i, node in enumerate(nodes, 1):
                _render_dag_node(node, i, depth_map.get(node.id, 0), s.dag, state=state)
            # ── Add task ──
            ui.button(
                "+ Add step",
                icon="add",
                on_click=lambda: _show_add_task_dialog(state),
            ).classes("ath-secondary-btn mt-3").props("size=sm").tooltip(
                "Manually append a step to the plan. Pick dependencies from existing steps."
            )

        with graph_container:
            ui.mermaid(_dag_to_mermaid(nodes)).classes("w-full").style(
                "background: var(--ath-surface-alt); border-radius: 4px; padding: 12px;"
            )
            ui.label("Hover or pinch to zoom. Node outline color encodes executor: 🤖 agent (emerald), 👤 human (gold), 🤝 hybrid (parchment).").classes("text-xs mt-1").style("color: var(--ath-muted);")

        # Default: list visible, graph hidden
        graph_container.set_visibility(False)

        def _switch_view(e):
            choice = e.value or "list"
            list_container.set_visibility(choice == "list")
            graph_container.set_visibility(choice == "graph")

        view_toggle.on_value_change(_switch_view)
    else:
        # Legacy projects: flat tasks only
        ui.label(f"Plan ({len(s.tasks)} tasks, legacy flat list)").classes("ath-readout-key mt-3")
        for i, t in enumerate(s.tasks, 1):
            type_icon = {"AGENT": "🤖", "HUMAN": "👤", "HYBRID": "🤝"}.get(
                t.task_type.value if hasattr(t.task_type, "value") else str(t.task_type),
                "○",
            )
            with ui.column().classes("w-full mt-2 p-2").style("background: var(--ath-surface-alt); border-radius: 4px;"):
                ui.label(f"{type_icon} {i}. {t.name}").classes("text-sm").style("color: var(--ath-parchment);")
                if t.description:
                    ui.label(t.description).classes("text-xs").style("color: var(--ath-muted); margin-top: 2px;")

    # ── Re-plan with feedback ──
    ui.element("div").classes("ath-section-rule mt-4")
    ui.label("Re-plan with feedback").classes("ath-readout-key mt-3")
    ui.label("Describe what should change; the planner will regenerate the DAG honoring your note as a hard constraint.").classes("text-xs").style("color: var(--ath-muted);")
    plan_feedback = ui.textarea(
        placeholder=(
            "e.g. Too much HPC time — rework with cloud credits only. "
            "Add an ablation step comparing method A vs B. "
            "Replace the manual wet-lab step with an automated protocol."
        ),
    ).classes("w-full mt-1").props("rows=3 dense outlined")

    with ui.row().classes("w-full gap-2 mt-2"):
        ui.button(
            "Re-plan",
            icon="replay",
            on_click=lambda: state.rerun_plan(feedback=plan_feedback.value or "", source="Re-plan"),
        ).classes("ath-run-btn").props("size=sm")
        ui.label("Leaves the hypothesis unchanged — only Stage 3 re-runs").classes("text-xs self-center").style("color: var(--ath-muted)")


def _confirm_delete_dag_node(state: AppState, node_id: str, node_name: str) -> None:
    """Confirm before removing a DAG node. Downstream dangling references
    will be cleaned up by _delete_dag_node and surfaced by the validation
    panel on the next render pass."""
    s = state.project_state
    if not s:
        return
    # Count downstream nodes that depend on this one (direct only)
    downstream = [n.name for n in s.dag if node_id in n.dependencies]

    with ui.dialog() as dialog, ui.card():
        ui.label("Delete step?").classes("ath-section-title")
        ui.label("Removes node from DAG").classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")
        ui.label(f'"{node_name}"').classes("text-sm mt-2").style("color: var(--ath-parchment);")
        if downstream:
            ui.label(
                f"⚠ {len(downstream)} downstream step(s) depend on this one: {', '.join(downstream)}."
            ).classes("text-xs mt-2").style("color: var(--ath-gold);")
            ui.label(
                "Those dependencies will be removed and the DAG will show validation "
                "errors (missing producers for inputs). Re-plan or re-wire after deletion."
            ).classes("text-xs mt-1").style("color: var(--ath-muted);")
        else:
            ui.label("No downstream steps depend on this one.").classes("text-xs mt-2").style("color: var(--ath-muted);")

        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Cancel", on_click=dialog.close).classes("ath-secondary-btn")
            def _do_delete():
                _delete_dag_node(state, node_id)
                dialog.close()
            ui.button("Delete", icon="delete_outline", on_click=_do_delete).classes("ath-run-btn")
    dialog.open()


def _show_add_task_dialog(state: AppState) -> None:
    """Dialog to manually add a new DAG node to the plan.

    Lets the user pick name / description / executor type / agent role /
    workflow / dependencies (multi-select of existing nodes). Inputs and
    outputs start empty — the user can edit them later once the node is
    in the plan, or the Re-plan button can regenerate them from text.
    """
    s = state.project_state
    if not s:
        ui.notify("No project loaded", type="warning")
        return

    existing_options = {n.id: n.name for n in s.dag}

    with ui.dialog() as dialog, ui.card().style("min-width: 520px; max-width: 680px;"):
        ui.label("Add a plan step").classes("ath-section-title")
        ui.label("Append a manually-authored node to the DAG").classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")

        ui.label("Name").classes("text-xs mt-2").style("color: var(--ath-muted)")
        name_inp = ui.input(placeholder="e.g. Validate signal against ATLAS data").classes("w-full").props("dense outlined")

        ui.label("Description").classes("text-xs mt-2").style("color: var(--ath-muted)")
        desc_inp = ui.textarea(placeholder="What this step accomplishes.").classes("w-full").props("rows=2 dense outlined")

        with ui.row().classes("w-full gap-2 mt-2"):
            with ui.column().classes("flex-1"):
                ui.label("Executor type").classes("text-xs").style("color: var(--ath-muted)")
                exec_sel = ui.select(
                    options=["AGENT", "HUMAN", "HYBRID"],
                    value="AGENT",
                ).classes("w-full").props("dense outlined")
            with ui.column().classes("flex-1"):
                ui.label("Agent / role").classes("text-xs").style("color: var(--ath-muted)")
                role_inp = ui.input(placeholder="e.g. Data Scientist").classes("w-full").props("dense outlined")

        ui.label("Workflow (optional)").classes("text-xs mt-2").style("color: var(--ath-muted)")
        wf_inp = ui.textarea(placeholder="Step-by-step instructions.").classes("w-full").props("rows=3 dense outlined")

        ui.label("Dependencies (optional — pick earlier steps this one consumes from)").classes("text-xs mt-2").style("color: var(--ath-muted)")
        if existing_options:
            dep_sel = ui.select(
                options=existing_options,
                multiple=True,
                value=[],
            ).classes("w-full").props("dense outlined use-chips")
        else:
            ui.label("(none — this will be a root node)").classes("text-xs").style("color: var(--ath-muted); font-style: italic;")
            dep_sel = None

        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button("Cancel", on_click=dialog.close).classes("ath-secondary-btn")
            def _do_add():
                nm = (name_inp.value or "").strip()
                if not nm:
                    _safe_notify("Name is required", type="warning")
                    return
                dep_ids = list(dep_sel.value or []) if dep_sel is not None else []
                _add_dag_node(
                    state,
                    name=nm,
                    description=(desc_inp.value or "").strip(),
                    executor_type=exec_sel.value or "AGENT",
                    agent_role=(role_inp.value or "").strip(),
                    workflow=(wf_inp.value or "").strip(),
                    dep_ids=dep_ids,
                )
                dialog.close()
            ui.button("Add step", icon="add", on_click=_do_add).classes("ath-run-btn")
    dialog.open()


def _dag_to_mermaid(nodes: List[Any]) -> str:
    """Render a DAG as a Mermaid flowchart definition.

    Produces left-to-right graph with type-aware styling:
      - AGENT nodes: emerald outline
      - HUMAN nodes: gold outline
      - HYBRID nodes: parchment outline
    Names are truncated to 42 chars so wide plans stay legible.
    """
    if not nodes:
        return "graph LR\n  empty[No DAG to render]"

    lines: List[str] = ["graph LR"]

    def _safe_id(node_id: str) -> str:
        # Mermaid node IDs must be alphanumeric; use first 8 chars of UUID
        return "n_" + node_id.replace("-", "")[:8]

    def _label(node: Any) -> str:
        name = node.name or "(unnamed)"
        if len(name) > 42:
            name = name[:39] + "..."
        # Mermaid labels: escape quotes and use bracket form
        safe = name.replace('"', "'").replace("[", "(").replace("]", ")")
        return f'"{safe}"'

    # Node declarations — shape encodes kind, icon encodes executor
    for node in nodes:
        nid = _safe_id(node.id)
        etype = ""
        if node.execution_spec and node.execution_spec.executor_type:
            etype = (
                node.execution_spec.executor_type.value
                if hasattr(node.execution_spec.executor_type, "value")
                else str(node.execution_spec.executor_type)
            )
        icon = {"AGENT": "🤖 ", "HUMAN": "👤 ", "HYBRID": "🤝 "}.get(etype, "")
        # Add star prefix for deliverables so reviewers can spot products
        if getattr(node, "is_deliverable", False):
            icon = "★ " + icon
        label = _label(node)
        label_with_icon = label[0] + icon + label[1:]

        # Kind-specific shape: rectangle for TASK, diamond for CHECKPOINT,
        # parallelogram for DATA_ACQUISITION.
        kind_value = (
            node.kind.value if hasattr(node, "kind") and hasattr(node.kind, "value")
            else "TASK"
        )
        if kind_value == "CHECKPOINT":
            lines.append(f"  {nid}{{{label_with_icon}}}")  # diamond
        elif kind_value == "DATA_ACQUISITION":
            lines.append(f"  {nid}[/{label_with_icon}/]")  # parallelogram
        else:
            lines.append(f"  {nid}[{label_with_icon}]")  # rectangle

    # Edges
    for node in nodes:
        nid = _safe_id(node.id)
        for dep in node.dependencies:
            dep_id = _safe_id(dep)
            lines.append(f"  {dep_id} --> {nid}")

    # Styling by executor type
    lines.append("  classDef agent fill:#1a1b22,stroke:#6CC48E,color:#d8d9e0;")
    lines.append("  classDef human fill:#1a1b22,stroke:#c9a44b,color:#d8d9e0;")
    lines.append("  classDef hybrid fill:#1a1b22,stroke:#e6d6b3,color:#d8d9e0;")

    for node in nodes:
        nid = _safe_id(node.id)
        etype = ""
        if node.execution_spec and node.execution_spec.executor_type:
            etype = (
                node.execution_spec.executor_type.value
                if hasattr(node.execution_spec.executor_type, "value")
                else str(node.execution_spec.executor_type)
            )
        css_class = {"AGENT": "agent", "HUMAN": "human", "HYBRID": "hybrid"}.get(etype, "agent")
        lines.append(f"  class {nid} {css_class};")

    return "\n".join(lines)


def _node_kind_badge(kind_value: str) -> tuple:
    """Return (label, color_css_var) for a node kind, used by the card
    header and the Mermaid renderer."""
    return {
        "TASK": ("task", "var(--ath-muted)"),
        "CHECKPOINT": ("checkpoint", "var(--ath-gold)"),
        "DATA_ACQUISITION": ("external data", "var(--ath-parchment)"),
    }.get(kind_value, ("task", "var(--ath-muted)"))


def _render_dag_node(node: Any, index: int, depth: int, all_nodes: List[Any], state: Optional[AppState] = None) -> None:
    """Render one DAG node as an indented expandable card.

    `depth` drives visual indentation; `all_nodes` is used to resolve
    dependency names; `state` (when provided) enables inline edit and
    delete affordances. Pass None for read-only contexts.
    """
    type_icon = {"AGENT": "🤖", "HUMAN": "👤", "HYBRID": "🤝"}.get(
        node.execution_spec.executor_type.value if hasattr(node.execution_spec.executor_type, "value")
        else str(node.execution_spec.executor_type),
        "○",
    )
    indent_px = depth * 24  # 24px per level

    # Build a quick id→name map for dep resolution
    id_to_name = {n.id: n.name for n in all_nodes}

    # Extract node kind for badge + rendering decisions
    kind_value = (
        node.kind.value if hasattr(node, "kind") and hasattr(node.kind, "value")
        else "TASK"
    )
    kind_label, kind_color = _node_kind_badge(kind_value)

    with ui.column().classes("w-full mt-1").style(f"margin-left: {indent_px}px;"):
        with ui.expansion().classes("w-full ath-readout").props('dense') as exp:
            # Header row
            with exp.add_slot("header"):
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(f"{type_icon}").classes("text-sm")
                    ui.label(f"{index}. {node.name}").classes("text-sm").style("color: var(--ath-parchment);")
                    if node.execution_spec and node.execution_spec.agent_role:
                        ui.label(f"· {node.execution_spec.agent_role}").classes("text-xs").style("color: var(--ath-muted);")
                    # Kind badge — only show if non-default
                    if kind_value != "TASK":
                        ui.label(f"[{kind_label}]").classes("text-xs").style(
                            f"color: {kind_color}; "
                            f"border: 1px solid {kind_color}; "
                            f"padding: 1px 6px; border-radius: 3px; "
                            f"letter-spacing: 0.05em;"
                        )
                    # Deliverable badge
                    if getattr(node, "is_deliverable", False):
                        dk = getattr(node, "deliverable_kind", None) or "deliverable"
                        ui.label(f"★ {dk}").classes("text-xs").style(
                            "color: var(--ath-emerald); "
                            "border: 1px solid var(--ath-emerald); "
                            "padding: 1px 6px; border-radius: 3px; "
                            "letter-spacing: 0.05em;"
                        )
                    # Target month chip
                    tm = getattr(node, "target_month", None)
                    if tm is not None:
                        ui.label(f"M{tm}+").classes("text-xs").style(
                            "color: var(--ath-gold); "
                            "padding: 1px 6px;"
                        )

            # Body
            if node.description:
                ui.label(node.description).classes("text-sm mt-1").style("color: var(--ath-text);")

            if node.dependencies:
                dep_names = [id_to_name.get(d, f"(missing: {d[:8]})") for d in node.dependencies]
                ui.label(f"Depends on: {', '.join(dep_names)}").classes("text-xs mt-2").style("color: var(--ath-muted);")

            if node.inputs:
                ui.label("Inputs").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                for inp in node.inputs:
                    source = id_to_name.get(inp.source_node_id, "(external)") if inp.source_node_id else "(external)"
                    ui.label(f"  ← {inp.name} [{inp.format}] from {source}").classes("text-xs").style("color: var(--ath-text);")

            if node.outputs:
                ui.label("Outputs").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                for out in node.outputs:
                    ui.label(f"  → {out.name} [{out.format}]").classes("text-xs").style("color: var(--ath-text);")

            # Compute spec — only if populated
            cs = node.execution_spec.compute_spec if node.execution_spec else None
            if cs is not None:
                ui.label("Estimated budget").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                budget_bits: List[str] = []
                if cs.cpu_hours:
                    budget_bits.append(f"{cs.cpu_hours:,.0f} CPU-h")
                if cs.gpu_hours:
                    gpu = f"{cs.gpu_hours:,.0f} GPU-h"
                    if cs.gpu_type:
                        gpu += f" ({cs.gpu_type})"
                    budget_bits.append(gpu)
                if cs.memory_gb:
                    budget_bits.append(f"{cs.memory_gb:,.0f} GB RAM")
                if cs.storage_gb:
                    budget_bits.append(f"{cs.storage_gb:,.0f} GB storage")
                if cs.human_hours:
                    budget_bits.append(f"{cs.human_hours:,.0f} human-h")
                if cs.wall_clock_days:
                    budget_bits.append(f"~{cs.wall_clock_days:,.0f}d wall clock")
                if budget_bits:
                    ui.label(" · ".join(budget_bits)).classes("text-xs").style("color: var(--ath-emerald);")
                if cs.notes:
                    ui.label(cs.notes).classes("text-xs").style("color: var(--ath-muted); font-style: italic;")

            if node.workflow:
                ui.label("Workflow").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                ui.label(node.workflow).classes("text-xs").style("color: var(--ath-text); white-space: pre-wrap;")

            if node.justification:
                ui.label("Justification").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                ui.label(node.justification).classes("text-xs").style("color: var(--ath-muted); font-style: italic;")

            # Risks + mitigations (paired)
            risks = getattr(node, "risks", None) or []
            mitigations = getattr(node, "mitigations", None) or []
            if risks or mitigations:
                ui.label("Risks & mitigations").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                # Pair them visually — even if counts differ
                max_len = max(len(risks), len(mitigations))
                for i in range(max_len):
                    r = risks[i] if i < len(risks) else ""
                    m = mitigations[i] if i < len(mitigations) else ""
                    if r:
                        ui.label(f"  ⚠ {r}").classes("text-xs").style("color: var(--ath-gold);")
                    if m:
                        ui.label(f"    → {m}").classes("text-xs").style("color: var(--ath-emerald);")

            # Alternatives considered
            alts = getattr(node, "alternatives_considered", None) or []
            if alts:
                ui.label("Alternatives considered").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                for alt in alts:
                    ui.label(f"  · {alt}").classes("text-xs").style("color: var(--ath-muted);")

            # Deliverable detail
            if getattr(node, "is_deliverable", False) and getattr(node, "deliverable_description", None):
                ui.label("Deliverable").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                ui.label(node.deliverable_description).classes("text-xs").style("color: var(--ath-emerald);")

            # ── Handoff brief export ──
            if state is not None:
                with ui.row().classes("w-full mt-2 gap-2"):
                    ui.button(
                        "Export handoff brief",
                        icon="description",
                        on_click=lambda nid=node.id: _show_handoff_brief_dialog(state, nid),
                    ).classes("ath-secondary-btn").props("size=sm").tooltip(
                        "Render a self-contained Markdown brief for this step — "
                        "ready to send to a collaborator, an AI agent, or a ticket."
                    )

            # ── Edit / Delete affordances ──
            if state is not None:
                with ui.expansion("Edit this step", icon="edit").classes("w-full mt-2").props("dense"):
                    ui.label("Name").classes("text-xs").style("color: var(--ath-muted)")
                    _editable_text(
                        state,
                        node.name,
                        lambda v, nid=node.id: _set_dag_node_field(state, nid, "name", v),
                    )
                    ui.label("Description").classes("text-xs mt-2").style("color: var(--ath-muted)")
                    _editable_text(
                        state,
                        node.description,
                        lambda v, nid=node.id: _set_dag_node_field(state, nid, "description", v),
                    )
                    ui.label("Workflow").classes("text-xs mt-2").style("color: var(--ath-muted)")
                    _editable_text(
                        state,
                        node.workflow or "",
                        lambda v, nid=node.id: _set_dag_node_field(state, nid, "workflow", v),
                    )
                    ui.label("Justification").classes("text-xs mt-2").style("color: var(--ath-muted)")
                    _editable_text(
                        state,
                        node.justification or "",
                        lambda v, nid=node.id: _set_dag_node_field(state, nid, "justification", v),
                    )
                    with ui.row().classes("w-full mt-3"):
                        ui.button(
                            "Delete this step",
                            icon="delete_outline",
                            on_click=lambda nid=node.id, nname=node.name: _confirm_delete_dag_node(state, nid, nname),
                        ).classes("ath-secondary-btn").props("size=sm").style("color: var(--ath-error);")


# ============================================================================
# Stage 4 — Grant Proposal
# ============================================================================

def _proposal_promoted(state: AppState):
    """Always-visible controls for Stage 4: RFP target + funder profile."""
    from src.athanor.ingest import list_profiles

    with ui.row().classes("w-full gap-3 items-end"):
        with ui.column().classes("flex-1 gap-0"):
            ui.label("RFP target (URL or path)").classes("text-xs").style("color: var(--ath-muted)")
            rfp = ui.input(value=config.rfp_target or "", placeholder="Leave blank for Mock RFP").classes("w-full").props("dense outlined")
            rfp.on_value_change(lambda e: _set_config(["grant", "rfp_target"], e.value))
            state.rfp_target_input = rfp
        with ui.column().classes("gap-0").style("width: 180px;"):
            ui.label("Funder profile").classes("text-xs").style("color: var(--ath-muted)")
            current_funder = (
                (state.project_state.funder_short_name if state.project_state else None)
                or config.funder_short_name
                or "generic"
            )
            funder_select = ui.select(
                options=list_profiles(),
                value=current_funder,
            ).classes("w-full").props("dense outlined")

            def _on_funder_change_promoted(e):
                if not state.project_state:
                    from src.athanor.core.state import ProjectState
                    state.project_state = ProjectState(project_name="New Project")
                state.project_state.funder_short_name = e.value
                state.save_project_state()
            funder_select.on_value_change(_on_funder_change_promoted)

    with ui.row().classes("w-full gap-2 mt-1"):
        ui.button(
            "RFP bank…",
            icon="folder_special",
            on_click=lambda: _show_rfp_bank_dialog(state),
        ).classes("ath-secondary-btn").props("size=sm dense")
        ui.button(
            "Mock RFP",
            icon="science",
            on_click=lambda: _apply_mock_rfp(state),
        ).classes("ath-secondary-btn").props("size=sm dense").tooltip(
            "Use a fictional funder for demos and dry runs"
        )


def _proposal_knobs(state: AppState):
    """Stage 4 settings: exemplar grant, budget defaults, voice, revisions."""
    from src.athanor.ingest import list_profiles, get_profile, ExemplarGrant, merge_with_defaults

    # ── Funder profile selector (also in promoted, but here for the budget merge) ──
    ui.label("Funder profile (defaults source)").classes("text-xs").style("color: var(--ath-muted)")
    current_funder = (
        (state.project_state.funder_short_name if state.project_state else None)
        or config.funder_short_name
        or "generic"
    )
    funder_options = list_profiles()
    funder_select = ui.select(
        options=funder_options,
        value=current_funder,
    ).classes("w-full")

    # ── Exemplar grant slot ──────────────────────────────────
    ui.label("Exemplar grant (optional — overrides defaults)").classes("text-xs mt-3").style("color: var(--ath-muted)")
    exemplar_path = ui.input(
        value=config.exemplar_grant_path or "",
        placeholder="/path/to/funded_grant.pdf",
    ).classes("w-full")
    exemplar_path.on_value_change(lambda e: _set_config(["grant", "exemplar_grant_path"], e.value))

    ui.separator().classes("mt-3 mb-2")

    # ── Resolved-defaults editor ─────────────────────────────
    ui.label("Resolved values (edit to override)").classes("ath-readout-key")

    # Compute current merged context
    def _current_ctx():
        eg_obj = None
        if state.project_state and state.project_state.exemplar_grant:
            try:
                eg_obj = ExemplarGrant.model_validate(state.project_state.exemplar_grant)
            except Exception:
                eg_obj = None
        return merge_with_defaults(eg_obj, current_funder), eg_obj

    ctx, exemplar = _current_ctx()

    # Editable fields. Each one writes back to state.exemplar_grant.budget on change.
    def _ensure_exemplar() -> dict:
        """Make sure state.project_state.exemplar_grant exists as a dict."""
        if not state.project_state:
            from src.athanor.core.state import ProjectState
            state.project_state = ProjectState(project_name="New Project")
        if not state.project_state.exemplar_grant:
            state.project_state.exemplar_grant = ExemplarGrant().model_dump()
        return state.project_state.exemplar_grant

    def _set_budget_field(key: str, value):
        eg = _ensure_exemplar()
        if "budget" not in eg or eg["budget"] is None:
            eg["budget"] = {}
        eg["budget"][key] = value
        state.save_project_state()

    def _set_style_field(key: str, value):
        eg = _ensure_exemplar()
        if "style" not in eg or eg["style"] is None:
            eg["style"] = {}
        eg["style"][key] = value
        state.save_project_state()

    # Two-column grid of editable fields
    with ui.grid(columns=2).classes("w-full gap-3"):
        with ui.column():
            ui.label("Budget cap (direct, $)").classes("text-xs").style("color: var(--ath-muted)")
            budget_cap = ui.number(value=ctx.budget_cap_usd, min=0, format="%.0f").classes("w-full")
            budget_cap.on_value_change(lambda e: _set_budget_field("total_direct_costs_usd", float(e.value or 0)))

        with ui.column():
            ui.label("IDC rate (e.g. 0.65 = 65%)").classes("text-xs").style("color: var(--ath-muted)")
            idc = ui.number(value=ctx.indirect_cost_rate, min=0, max=1, step=0.01, format="%.2f").classes("w-full")
            idc.on_value_change(lambda e: _set_budget_field("indirect_cost_rate", float(e.value or 0)))

        with ui.column():
            ui.label("PI salary base ($)").classes("text-xs").style("color: var(--ath-muted)")
            salary = ui.number(value=ctx.salary_base_usd, min=0, format="%.0f").classes("w-full")
            salary.on_value_change(lambda e: _set_budget_field("pi_salary_base_usd", float(e.value or 0)))

        with ui.column():
            ui.label("PI FTE (e.g. 0.25 = 25%)").classes("text-xs").style("color: var(--ath-muted)")
            fte = ui.number(value=ctx.pi_fte_percent, min=0, max=1, step=0.05, format="%.2f").classes("w-full")
            fte.on_value_change(lambda e: _set_budget_field("pi_fte_percent", float(e.value or 0)))

        with ui.column():
            ui.label("Fringe rate").classes("text-xs").style("color: var(--ath-muted)")
            fringe = ui.number(value=ctx.fringe_rate, min=0, max=1, step=0.01, format="%.2f").classes("w-full")
            fringe.on_value_change(lambda e: _set_budget_field("fringe_rate", float(e.value or 0)))

        with ui.column():
            ui.label("Duration (months)").classes("text-xs").style("color: var(--ath-muted)")
            duration = ui.number(value=ctx.duration_months, min=1, max=120, format="%.0f").classes("w-full")
            duration.on_value_change(lambda e: _set_budget_field("duration_months", int(e.value or 12)))

    # ── Style register ──────────────────────────────────────
    ui.label("Voice register").classes("text-xs mt-3").style("color: var(--ath-muted)")
    voice_select = ui.select(
        options=["formal academic", "narrative", "technical", "accessible"],
        value=ctx.voice,
    ).classes("w-full")
    voice_select.on_value_change(lambda e: _set_style_field("voice", e.value))

    # ── Computed totals (read-only display) ─────────────────
    ui.separator().classes("mt-3 mb-2")
    ui.label("Computed").classes("ath-readout-key")
    personnel = ctx.computed_personnel_cost()
    total_with_idc = ctx.computed_total_with_idc(ctx.budget_cap_usd)
    with ui.row().classes("w-full gap-4"):
        with ui.column():
            ui.label("Personnel cost (PI)").classes("text-xs").style("color: var(--ath-muted)")
            ui.label(f"${personnel:,.0f}").classes("text-sm").style("color: var(--ath-emerald)")
        with ui.column():
            ui.label("Total + IDC (on cap)").classes("text-xs").style("color: var(--ath-muted)")
            ui.label(f"${total_with_idc:,.0f}").classes("text-sm").style("color: var(--ath-emerald)")
        with ui.column():
            ui.label("Funder").classes("text-xs").style("color: var(--ath-muted)")
            ui.label(ctx.funder_name).classes("text-sm").style("color: var(--ath-parchment)")

    # ── Required sections preview ───────────────────────────
    if ctx.sections:
        ui.label("Required sections (from funder profile)").classes("ath-readout-key mt-3")
        ui.label(" · ".join(ctx.sections)).classes("text-xs").style("color: var(--ath-muted); line-height: 1.6;")

    # ── Funder profile change handler ───────────────────────
    def _on_funder_change(e):
        nonlocal current_funder
        current_funder = e.value
        if not state.project_state:
            from src.athanor.core.state import ProjectState
            state.project_state = ProjectState(project_name="New Project")
        state.project_state.funder_short_name = current_funder
        state.save_project_state()
        ui.notify(f"Funder set to {current_funder}. Reopen the Settings panel to see new defaults.", type="info")

    funder_select.on_value_change(_on_funder_change)

    # ── Max revisions ───────────────────────────────────────
    ui.separator().classes("mt-3 mb-2")
    ui.label("Max revision loops").classes("text-xs").style("color: var(--ath-muted)")
    revs = ui.number(value=config.weaver_max_revisions, min=1, max=5, format="%.0f").classes("w-32")
    revs.on_value_change(lambda e: _set_config(["grant", "max_revisions"], int(e.value)))

    # ── Preliminary results list ────────────────────────────
    # Pilot experiments, proof-of-concept work, and early results specific
    # to this proposal. Attachments get indexed into the Project KB so
    # the grant writer can cite them by filename + relevant chunks.
    ui.separator().classes("mt-3 mb-2")
    _build_preliminary_work_list(
        state,
        role="preliminary_result",
        heading="Preliminary results",
        subtitle="Pilot data, proof-of-concept, or early results you've already generated for this specific proposal. Attached files are indexed for retrieval during grant writing.",
        empty_hint="(None yet. Add pilot results manually, or mark DAG nodes DONE in the Execution tab to promote them automatically.)",
        add_button_label="+ Add preliminary result",
    )


def _proposal_readout(state: AppState):
    """Rich Proposal readout.

    Layout:
      1. Title
      2. Red team issues banner (if any)
      3. Compliance matrix panel (if populated)
      4. Per-section list: title, editable content, feedback textarea,
         Regenerate button
      5. Full-proposal re-run box at the bottom
    """
    s = state.project_state
    if not s or not s.proposal:
        ui.label("No proposal yet. Run Stage 4.").classes("text-sm").style("color: var(--ath-muted)")
        return

    p = s.proposal
    ui.label(p.rfp_title or "Grant Proposal").classes("text-lg").style("color: var(--ath-parchment); font-family: 'Cinzel', serif;")

    # ── Red team issues banner (unresolved concerns from the critique loop) ──
    if s.red_team_issues:
        with ui.card().classes("w-full mt-2").style(
            "border-left: 4px solid var(--ath-gold) !important; "
            "background-color: rgba(201, 164, 75, 0.08) !important;"
        ):
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("⚠").classes("text-lg").style("color: var(--ath-gold);")
                ui.label(f"{len(s.red_team_issues)} unresolved red-team concern(s)").classes("ath-section-title").style("color: var(--ath-gold);")
            ui.label("The critique loop hit max revisions without resolving these. Use per-section regenerate or full re-run with feedback to address them.").classes("text-xs").style("color: var(--ath-muted);")
            for issue in s.red_team_issues[:8]:
                if isinstance(issue, dict):
                    sev = issue.get("severity", "")
                    desc = issue.get("description", "") or issue.get("details", "")
                    loc = issue.get("location", "") or issue.get("issue", "")
                    label = f"• [{sev}] {loc}: {desc}" if sev else f"• {loc}: {desc}"
                else:
                    label = f"• {issue}"
                ui.label(label[:240]).classes("text-xs mt-1").style("color: var(--ath-gold);")
            if len(s.red_team_issues) > 8:
                ui.label(f"… and {len(s.red_team_issues) - 8} more").classes("text-xs").style("color: var(--ath-muted);")

    # ── Compliance matrix panel (RFP requirements) ──
    if p.compliance_matrix:
        cm = p.compliance_matrix
        with ui.expansion("RFP Compliance Matrix", icon="fact_check").classes("w-full mt-2"):
            # Try to pull out common shapes: a list of required sections,
            # a list of missing sections, formatting requirements, etc.
            if isinstance(cm, dict):
                required = cm.get("sections_required") or cm.get("sections") or []
                covered = cm.get("sections_covered") or []
                missing = cm.get("missing") or cm.get("sections_missing") or []
                fmt = cm.get("formatting_requirements") or cm.get("formatting") or {}
                if required:
                    ui.label("Required sections").classes("text-xs mt-1").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                    with ui.row().classes("w-full gap-2 flex-wrap"):
                        for req in required:
                            req_name = str(req) if not isinstance(req, dict) else (req.get("title") or req.get("section_id") or "?")
                            is_covered = any(req_name == c or req_name in str(c) for c in covered) if covered else False
                            color = "var(--ath-emerald)" if is_covered else "var(--ath-gold)"
                            mark = "✓" if is_covered else "○"
                            ui.label(f"{mark} {req_name}").classes("text-xs").style(
                                f"color: {color}; border: 1px solid {color}; "
                                f"padding: 2px 8px; border-radius: 3px;"
                            )
                if missing:
                    ui.label("Missing").classes("text-xs mt-2").style("color: var(--ath-error); letter-spacing: 0.08em;")
                    for m in missing:
                        ui.label(f"• {m}").classes("text-xs").style("color: var(--ath-error);")
                if fmt:
                    ui.label("Formatting requirements").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                    for k, v in fmt.items():
                        ui.label(f"  {k}: {v}").classes("text-xs").style("color: var(--ath-text);")
                # Fallback: dump unknown keys so nothing useful is hidden
                known = {"sections_required", "sections", "sections_covered", "missing",
                         "sections_missing", "formatting_requirements", "formatting",
                         "rfp_title", "agency"}
                extras = {k: v for k, v in cm.items() if k not in known}
                if extras:
                    ui.label("Additional fields").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                    for k, v in extras.items():
                        ui.label(f"  {k}: {str(v)[:200]}").classes("text-xs").style("color: var(--ath-muted);")

    # ── Sections list ──
    ui.label(f"{len(p.sections)} sections").classes("ath-readout-key mt-3")

    for idx, sec in enumerate(p.sections):
        with ui.expansion(sec.title or "Untitled").classes("w-full mt-1"):
            _editable_text(state, sec.content or "", lambda v, i=idx: _set_proposal_section(state, i, v))

            # Per-section feedback + Regenerate
            ui.label("Feedback for this section (optional)").classes("text-xs mt-3").style("color: var(--ath-muted)")
            section_feedback = ui.textarea(
                placeholder="e.g. Add a paragraph on sample-size justification. Tighten the claim about novelty.",
            ).classes("w-full").props("rows=2 dense outlined")

            with ui.row().classes("w-full gap-2 mt-2"):
                ui.button(
                    "Regenerate section",
                    icon="auto_awesome",
                    on_click=lambda si=idx, fi=section_feedback: state.rerun_proposal_section(
                        si, fi.value or ""
                    ),
                ).classes("ath-secondary-btn").props("size=sm").tooltip(
                    "Rewrite this one section. Fast — one LLM call. Uses the plan-derived context."
                )

    # ── Full-proposal re-run with feedback ──
    ui.element("div").classes("ath-section-rule mt-4")
    ui.label("Re-run full proposal with feedback").classes("ath-readout-key mt-3")
    ui.label("Use this for overall tone, structure, or strategy changes. Expensive — re-runs the whole Stage 4 pipeline.").classes("text-xs").style("color: var(--ath-muted);")
    full_feedback = ui.textarea(
        placeholder="e.g. Tone down the novelty claims; my advisor said reviewers will push back. Add explicit limitations section. Cut the broader-impacts paragraph in half.",
    ).classes("w-full mt-1").props("rows=3 dense outlined")

    ui.button(
        "Re-run full proposal",
        icon="replay",
        on_click=lambda: state.rerun_full_proposal(full_feedback.value or ""),
    ).classes("ath-run-btn mt-2").props("size=sm")


# ============================================================================
# Stage 5 — Execution Tracking
# ============================================================================

def _execution_knobs(state: AppState):
    """Stage 5 has no LLM knobs. Show a quick-reference card instead."""
    ui.label("Stage 5 is bookkeeping — Athanor tracks what you do, it doesn't run anything.").classes("text-xs").style("color: var(--ath-muted); line-height: 1.5;")
    ui.label("How to use it:").classes("text-xs mt-2").style("color: var(--ath-muted);")
    ui.label("  1. Click RUN STAGE 5 (or RUN ALL) to refresh the progress summary.").classes("text-xs").style("color: var(--ath-muted);")
    ui.label("  2. On each plan step below, click Start / Mark done / Mark blocked as you work.").classes("text-xs").style("color: var(--ath-muted);")
    ui.label("  3. Capture deviation notes when marking done — they feed into Stage 6's Methods section.").classes("text-xs").style("color: var(--ath-muted);")
    ui.label("  4. Click 'Re-plan from here' if blockers accumulate.").classes("text-xs").style("color: var(--ath-muted);")


def _execution_readout(state: AppState):
    """Stage 5 readout: DAG with status chips and per-node Mark buttons,
    progress summary card at top, re-plan-on-deviation trigger.
    """
    from src.athanor.core.state import ExecutionStatus

    s = state.project_state
    if not s:
        ui.label("No project loaded. Run Stage 1 first.").classes("text-sm").style("color: var(--ath-muted)")
        return
    if not s.dag:
        ui.label("No plan yet. Run Stage 3 to generate the DAG, then come back here to track progress.").classes("text-sm").style("color: var(--ath-muted)")
        return

    progress = s.execution_progress()

    # ── Progress summary card ──
    pct_int = int(round(progress["percent_complete"] * 100))
    with ui.card().classes("w-full mt-1").style(
        "border-left: 4px solid var(--ath-emerald) !important; "
        "background-color: rgba(108, 196, 142, 0.06) !important;"
    ):
        with ui.row().classes("w-full items-center gap-4"):
            with ui.column().classes("items-center"):
                ui.label("Done").classes("text-xs").style("color: var(--ath-muted);")
                ui.label(f"{progress['done_count']}/{progress['total_count']}").classes("text-lg").style("color: var(--ath-emerald); font-family: 'Cinzel', serif;")
            with ui.column().classes("items-center"):
                ui.label("Progress").classes("text-xs").style("color: var(--ath-muted);")
                ui.label(f"{pct_int}%").classes("text-lg").style("color: var(--ath-emerald); font-family: 'Cinzel', serif;")
            with ui.column().classes("items-center"):
                ui.label("In progress").classes("text-xs").style("color: var(--ath-muted);")
                ui.label(f"{progress['in_progress_count']}").classes("text-lg").style("color: var(--ath-gold); font-family: 'Cinzel', serif;")
            with ui.column().classes("items-center"):
                ui.label("Blocked").classes("text-xs").style("color: var(--ath-muted);")
                color = "var(--ath-error)" if progress["blocked_count"] else "var(--ath-muted)"
                ui.label(f"{progress['blocked_count']}").classes("text-lg").style(f"color: {color}; font-family: 'Cinzel', serif;")
            if progress["schedule_delta_days"] is not None:
                with ui.column().classes("items-center"):
                    ui.label("Schedule").classes("text-xs").style("color: var(--ath-muted);")
                    delta = progress["schedule_delta_days"]
                    if delta > 0:
                        txt = f"+{delta:.0f}d"
                        col = "var(--ath-error)"
                    elif delta < 0:
                        txt = f"{delta:.0f}d"
                        col = "var(--ath-emerald)"
                    else:
                        txt = "on plan"
                        col = "var(--ath-emerald)"
                    ui.label(txt).classes("text-lg").style(f"color: {col}; font-family: 'Cinzel', serif;")

    # ── Re-plan nudge if blockers accumulate ──
    from src.athanor.engines.execution_tracker import ExecutionTracker
    if ExecutionTracker.should_trigger_replan(s):
        with ui.card().classes("w-full mt-2").style(
            "border-left: 4px solid var(--ath-gold) !important; "
            "background-color: rgba(201, 164, 75, 0.08) !important;"
        ):
            ui.label("⚠ Plan has drifted from reality").classes("ath-section-title").style("color: var(--ath-gold);")
            ui.label("Blockers or significant schedule deviations suggest downstream steps should be re-planned with the new information.").classes("text-xs").style("color: var(--ath-muted);")
            ui.button(
                "Re-plan from here",
                icon="replay",
                on_click=lambda: state.rerun_plan(
                    feedback=ExecutionTracker.build_replan_feedback(s),
                    source="Re-plan (from execution)",
                ),
            ).classes("ath-run-btn mt-2").props("size=sm")

    # ── Per-node list with status chips and action buttons ──
    ui.label(f"Plan steps ({len(s.dag)} nodes)").classes("ath-readout-key mt-3")

    status_display = {
        ExecutionStatus.PLANNED: ("○ planned", "var(--ath-muted)"),
        ExecutionStatus.IN_PROGRESS: ("⏵ in progress", "var(--ath-gold)"),
        ExecutionStatus.DONE: ("✓ done", "var(--ath-emerald)"),
        ExecutionStatus.BLOCKED: ("⊘ blocked", "var(--ath-error)"),
        ExecutionStatus.ABANDONED: ("× abandoned", "var(--ath-muted)"),
    }

    for i, node in enumerate(s.topo_sorted_dag(), 1):
        type_icon = {"AGENT": "🤖", "HUMAN": "👤", "HYBRID": "🤝"}.get(
            node.execution_spec.executor_type.value if node.execution_spec and hasattr(node.execution_spec.executor_type, "value") else "",
            "○",
        )
        status_label, status_color = status_display.get(node.status, ("?", "var(--ath-muted)"))

        with ui.expansion().classes("w-full mt-1 ath-readout").props("dense") as exp:
            with exp.add_slot("header"):
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(f"{type_icon}").classes("text-sm")
                    ui.label(f"{i}. {node.name}").classes("text-sm flex-1").style("color: var(--ath-parchment);")
                    ui.label(status_label).classes("text-xs").style(
                        f"color: {status_color}; "
                        f"border: 1px solid {status_color}; "
                        f"padding: 1px 8px; border-radius: 3px; "
                        f"letter-spacing: 0.04em;"
                    )

            if node.description:
                ui.label(node.description).classes("text-sm").style("color: var(--ath-text);")

            # Action row
            with ui.row().classes("w-full gap-2 mt-3"):
                if node.status == ExecutionStatus.PLANNED:
                    ui.button(
                        "Start",
                        icon="play_arrow",
                        on_click=lambda nid=node.id: state.mark_node_started(nid),
                    ).classes("ath-secondary-btn").props("size=sm")
                if node.status != ExecutionStatus.DONE:
                    ui.button(
                        "Mark done…",
                        icon="done_all",
                        on_click=lambda nid=node.id, nname=node.name: _show_mark_done_dialog(state, nid, nname),
                    ).classes("ath-run-btn").props("size=sm")
                if node.status != ExecutionStatus.BLOCKED:
                    ui.button(
                        "Mark blocked…",
                        icon="block",
                        on_click=lambda nid=node.id, nname=node.name: _show_mark_blocked_dialog(state, nid, nname),
                    ).classes("ath-secondary-btn").props("size=sm")
                if node.status != ExecutionStatus.PLANNED:
                    ui.button(
                        "Reset",
                        icon="restart_alt",
                        on_click=lambda nid=node.id: state.mark_node_planned(nid),
                    ).classes("ath-secondary-btn").props("size=sm")
                ui.button(
                    "Export brief",
                    icon="description",
                    on_click=lambda nid=node.id: _show_handoff_brief_dialog(state, nid),
                ).classes("ath-secondary-btn").props("size=sm").tooltip(
                    "Render a self-contained handoff brief for this step — "
                    "ready to send to a collaborator, an AI agent, or a ticket."
                )

            # Execution detail (actual days, deviation, artifacts)
            if node.actual_wall_clock_days is not None:
                planned = None
                if node.execution_spec and node.execution_spec.compute_spec and node.execution_spec.compute_spec.wall_clock_days:
                    planned = node.execution_spec.compute_spec.wall_clock_days
                if planned:
                    delta = node.actual_wall_clock_days - planned
                    delta_str = f" ({'+' if delta >= 0 else ''}{delta:.0f}d vs plan)"
                    delta_col = "var(--ath-error)" if delta > 0 else "var(--ath-emerald)"
                else:
                    delta_str = ""
                    delta_col = "var(--ath-muted)"
                ui.label(f"Actual: {node.actual_wall_clock_days:.1f} days").classes("text-xs mt-2").style(f"color: {delta_col};")
                if delta_str:
                    ui.label(delta_str).classes("text-xs").style(f"color: {delta_col};")

            if node.deviation_notes:
                ui.label("Deviation notes").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                ui.label(node.deviation_notes).classes("text-xs").style("color: var(--ath-gold); font-style: italic;")

            if node.attached_artifact_paths:
                ui.label("Attached artifacts").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                for path in node.attached_artifact_paths:
                    ui.label(f"  · {path}").classes("text-xs").style("color: var(--ath-text);")

            # Attach artifact row
            with ui.row().classes("w-full gap-2 mt-2 items-end"):
                artifact_inp = ui.input(placeholder="/path/to/artifact.csv").classes("flex-1").props("dense outlined")
                ui.button(
                    "Attach",
                    icon="attach_file",
                    on_click=lambda nid=node.id, inp=artifact_inp: (
                        state.attach_artifact(nid, (inp.value or "").strip()),
                        setattr(inp, "value", ""),
                    ),
                ).classes("ath-secondary-btn").props("size=sm")


def _apply_mock_rfp(state: AppState) -> None:
    """Fill the Proposal settings with the bundled Mock RFP path so users
    can dry-run Stage 4 without a real funder in mind."""
    from src.athanor.data import (
        MOCK_RFP_FUNDER_SHORT_NAME,
        MOCK_RFP_NAME,
        mock_rfp_path,
    )

    path = mock_rfp_path()
    _set_config(["grant", "rfp_target"], path)
    _set_config(["grant", "funder_short_name"], MOCK_RFP_FUNDER_SHORT_NAME)
    if state.project_state:
        state.project_state.funder_short_name = MOCK_RFP_FUNDER_SHORT_NAME
        state.save_project_state()
    if state.rfp_target_input is not None:
        try:
            state.rfp_target_input.value = path
        except Exception:
            pass
    state._refresh_all_panels()
    ui.notify(f"Applied: {MOCK_RFP_NAME}", type="positive")


def _show_rfp_bank_dialog(state: AppState) -> None:
    """Browse the user-global RFP bank. Apply / edit / delete entries.

    Lives under ~/.athanor/rfp_bank.json — shared across all projects
    so the same research idea can be thrown at multiple RFPs without
    re-typing details.
    """
    from src.athanor.ingest import list_profiles

    lib = state.rfp_library()

    with ui.dialog() as dialog, ui.card().style("min-width: 720px; max-width: 860px; max-height: 85vh;"):
        ui.label("RFP bank").classes("ath-section-title")
        ui.label("User-global · reuse across projects").classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")
        ui.label(
            "RFPs saved here can be applied to any project with one click. Useful "
            "when you want to take the same hypothesis and plan and submit it to "
            "multiple funders."
        ).classes("text-xs").style("color: var(--ath-muted);")

        list_container = ui.column().classes("w-full mt-2").style("overflow-y: auto; max-height: 55vh;")

        def _build_list():
            list_container.clear()
            with list_container:
                rfps = lib.list()
                if not rfps:
                    ui.label("(No saved RFPs yet. Click + New RFP to add one.)").classes("text-sm").style("color: var(--ath-muted); font-style: italic;")
                    return
                for rfp in rfps:
                    with ui.card().classes("w-full mt-2"):
                        with ui.row().classes("w-full items-start gap-3"):
                            with ui.column().classes("flex-1"):
                                ui.label(rfp.name).classes("text-sm").style("color: var(--ath-parchment);")
                                meta_bits = []
                                if rfp.funder_short_name:
                                    meta_bits.append(rfp.funder_short_name)
                                if rfp.deadline:
                                    meta_bits.append(f"deadline: {rfp.deadline}")
                                if rfp.last_used_at:
                                    meta_bits.append(f"last used: {_format_mtime(rfp.last_used_at.timestamp())}")
                                if meta_bits:
                                    ui.label(" · ".join(meta_bits)).classes("text-xs").style("color: var(--ath-muted);")
                                ui.label(rfp.url_or_path).classes("text-xs").style("color: var(--ath-emerald); font-family: 'JetBrains Mono', monospace;")
                                if rfp.description:
                                    ui.label(rfp.description).classes("text-xs mt-1").style("color: var(--ath-text);")
                                if rfp.notes:
                                    ui.label(rfp.notes).classes("text-xs mt-1").style("color: var(--ath-muted); font-style: italic;")
                            with ui.column().classes("gap-1"):
                                def _apply(rid=rfp.id):
                                    state.apply_rfp_from_bank(rid)
                                    dialog.close()
                                ui.button(
                                    "Apply",
                                    icon="check",
                                    on_click=_apply,
                                ).classes("ath-run-btn").props("size=sm")
                                def _edit(rid=rfp.id):
                                    dialog.close()
                                    _show_edit_rfp_dialog(state, rid)
                                ui.button(
                                    "Edit",
                                    icon="edit",
                                    on_click=_edit,
                                ).classes("ath-secondary-btn").props("size=sm")
                                def _del(rid=rfp.id):
                                    lib.remove(rid)
                                    _build_list()
                                    _safe_notify("Deleted", type="info")
                                ui.button(
                                    icon="delete_outline",
                                    on_click=_del,
                                ).props("flat round size=sm").style("color: var(--ath-error);")

        _build_list()

        ui.element("div").classes("ath-section-rule mt-3")

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            def _new_rfp():
                dialog.close()
                _show_save_rfp_dialog(state, "", reopen_bank=True)
            ui.button("+ New RFP", icon="add", on_click=_new_rfp).classes("ath-secondary-btn")
            ui.button("Close", on_click=dialog.close).classes("ath-secondary-btn")

    dialog.open()


def _show_save_rfp_dialog(state: AppState, initial_url: str = "", *, reopen_bank: bool = False) -> None:
    """Add a new RFP to the bank. If initial_url is provided (from the
    Save this to bank button), pre-fills it. If reopen_bank is True, the
    bank dialog re-opens after saving so the user sees the new entry."""
    from src.athanor.ingest import list_profiles
    lib = state.rfp_library()

    with ui.dialog() as dialog, ui.card().style("min-width: 560px;"):
        ui.label("Add RFP to bank").classes("ath-section-title")
        ui.element("div").classes("ath-section-rule")

        ui.label("Name (short label)").classes("text-xs mt-2").style("color: var(--ath-muted)")
        name_inp = ui.input(placeholder="e.g. NSF AI for Science 2026").classes("w-full").props("dense outlined autofocus")

        ui.label("URL or local path").classes("text-xs mt-2").style("color: var(--ath-muted)")
        url_inp = ui.input(value=initial_url, placeholder="https://... or /path/to/rfp.pdf").classes("w-full").props("dense outlined")

        with ui.grid(columns=2).classes("w-full gap-2 mt-2"):
            with ui.column():
                ui.label("Funder profile").classes("text-xs").style("color: var(--ath-muted)")
                try:
                    funder_options = list_profiles()
                except Exception:
                    funder_options = ["generic"]
                funder_sel = ui.select(options=["(none)"] + funder_options, value="(none)").classes("w-full").props("dense outlined")
            with ui.column():
                ui.label("Deadline (optional)").classes("text-xs").style("color: var(--ath-muted)")
                deadline_inp = ui.input(placeholder="2026-10-15 or rolling").classes("w-full").props("dense outlined")

        ui.label("Description").classes("text-xs mt-2").style("color: var(--ath-muted)")
        desc_inp = ui.textarea(placeholder="What is this RFP about?").classes("w-full").props("rows=2 dense outlined")

        ui.label("Notes (optional)").classes("text-xs mt-2").style("color: var(--ath-muted)")
        notes_inp = ui.textarea(placeholder="Anything worth remembering next time you apply to this.").classes("w-full").props("rows=2 dense outlined")

        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Cancel", on_click=dialog.close).classes("ath-secondary-btn")
            def _do_save():
                nm = (name_inp.value or "").strip()
                url = (url_inp.value or "").strip()
                if not nm:
                    _safe_notify("Name is required", type="warning")
                    return
                if not url:
                    _safe_notify("URL or path is required", type="warning")
                    return
                try:
                    funder = funder_sel.value if funder_sel.value and funder_sel.value != "(none)" else None
                    lib.add(
                        name=nm,
                        url_or_path=url,
                        funder_short_name=funder,
                        description=(desc_inp.value or "").strip(),
                        deadline=(deadline_inp.value or "").strip() or None,
                        notes=(notes_inp.value or "").strip(),
                    )
                    _safe_notify(f"Added '{nm}' to RFP bank", type="positive")
                    dialog.close()
                    if reopen_bank:
                        _show_rfp_bank_dialog(state)
                except Exception as e:
                    _safe_notify(f"Save failed: {e}", type="negative")
            ui.button("Save", icon="save", on_click=_do_save).classes("ath-run-btn")

    dialog.open()


def _show_edit_rfp_dialog(state: AppState, rfp_id: str) -> None:
    """Edit an existing RFP in the bank."""
    from src.athanor.ingest import list_profiles
    lib = state.rfp_library()
    rfp = lib.get(rfp_id)
    if rfp is None:
        _safe_notify("RFP not found", type="warning")
        return

    with ui.dialog() as dialog, ui.card().style("min-width: 560px;"):
        ui.label("Edit RFP").classes("ath-section-title")
        ui.element("div").classes("ath-section-rule")

        ui.label("Name").classes("text-xs mt-2").style("color: var(--ath-muted)")
        name_inp = ui.input(value=rfp.name).classes("w-full").props("dense outlined autofocus")

        ui.label("URL or local path").classes("text-xs mt-2").style("color: var(--ath-muted)")
        url_inp = ui.input(value=rfp.url_or_path).classes("w-full").props("dense outlined")

        with ui.grid(columns=2).classes("w-full gap-2 mt-2"):
            with ui.column():
                ui.label("Funder profile").classes("text-xs").style("color: var(--ath-muted)")
                try:
                    funder_options = list_profiles()
                except Exception:
                    funder_options = ["generic"]
                funder_sel = ui.select(
                    options=["(none)"] + funder_options,
                    value=rfp.funder_short_name or "(none)",
                ).classes("w-full").props("dense outlined")
            with ui.column():
                ui.label("Deadline").classes("text-xs").style("color: var(--ath-muted)")
                deadline_inp = ui.input(value=rfp.deadline or "").classes("w-full").props("dense outlined")

        ui.label("Description").classes("text-xs mt-2").style("color: var(--ath-muted)")
        desc_inp = ui.textarea(value=rfp.description or "").classes("w-full").props("rows=2 dense outlined")

        ui.label("Notes").classes("text-xs mt-2").style("color: var(--ath-muted)")
        notes_inp = ui.textarea(value=rfp.notes or "").classes("w-full").props("rows=2 dense outlined")

        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Cancel", on_click=dialog.close).classes("ath-secondary-btn")
            def _do_update():
                funder = funder_sel.value if funder_sel.value and funder_sel.value != "(none)" else None
                lib.update(
                    rfp_id,
                    name=(name_inp.value or "").strip(),
                    url_or_path=(url_inp.value or "").strip(),
                    funder_short_name=funder,
                    description=(desc_inp.value or "").strip(),
                    deadline=(deadline_inp.value or "").strip() or None,
                    notes=(notes_inp.value or "").strip(),
                )
                _safe_notify("Updated", type="positive")
                dialog.close()
                _show_rfp_bank_dialog(state)
            ui.button("Save changes", icon="save", on_click=_do_update).classes("ath-run-btn")

    dialog.open()


def _show_project_switcher_dialog(state: AppState, on_refresh=None) -> None:
    """Full-list project switcher. Shows name, stage, last-modified,
    open/delete actions per project, plus a '+ New project' button."""
    with ui.dialog() as dialog, ui.card().style("min-width: 640px; max-width: 760px; max-height: 85vh;"):
        ui.label("Switch project").classes("ath-section-title")
        ui.label("Saved Sessions").classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")

        list_container = ui.column().classes("w-full").style("overflow-y: auto; max-height: 60vh;")

        def _build_list():
            list_container.clear()
            with list_container:
                entries = state.list_projects_detailed()
                if not entries:
                    ui.label("No saved projects yet. Create a new one by running Stage 1.").classes("text-sm").style("color: var(--ath-muted);")
                    return
                for entry in entries:
                    with ui.card().classes("w-full mt-2"):
                        with ui.row().classes("w-full items-center gap-3"):
                            with ui.column().classes("flex-1"):
                                ui.label(entry["name"]).classes("text-sm").style("color: var(--ath-parchment);")
                                meta_bits = []
                                if entry.get("stage"):
                                    meta_bits.append(f"Stage {entry['stage']}/6")
                                if entry.get("mtime_display"):
                                    meta_bits.append(entry["mtime_display"])
                                meta_bits.append(entry["id"])
                                ui.label(" · ".join(meta_bits)).classes("text-xs").style("color: var(--ath-muted);")
                            current = (state.current_project_id == entry["id"])
                            if current:
                                ui.label("CURRENT").classes("text-xs").style(
                                    "color: var(--ath-emerald); "
                                    "border: 1px solid var(--ath-emerald); "
                                    "padding: 2px 8px; border-radius: 3px; "
                                    "letter-spacing: 0.04em;"
                                )
                            else:
                                def _do_open(pid=entry["id"]):
                                    state.load_project(pid)
                                    if on_refresh:
                                        try:
                                            on_refresh()
                                        except Exception:
                                            pass
                                    dialog.close()
                                ui.button(
                                    "Open",
                                    icon="folder_open",
                                    on_click=_do_open,
                                ).classes("ath-run-btn").props("size=sm")
                            def _confirm_delete(pid=entry["id"], pname=entry["name"]):
                                _confirm_delete_project(state, pid, pname, on_done=lambda: (_build_list(), on_refresh and on_refresh()))
                            ui.button(
                                icon="delete_outline",
                                on_click=_confirm_delete,
                            ).props("flat round size=sm").style("color: var(--ath-error);").tooltip("Delete project")

        _build_list()

        ui.element("div").classes("ath-section-rule mt-3")

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            def _new_project():
                state.new_project()
                if on_refresh:
                    try:
                        on_refresh()
                    except Exception:
                        pass
                dialog.close()
            ui.button("+ New project", icon="add", on_click=_new_project).classes("ath-secondary-btn")
            ui.button("Close", on_click=dialog.close).classes("ath-secondary-btn")

    dialog.open()


def _confirm_delete_project(state: AppState, project_id: str, project_name: str, on_done=None) -> None:
    """Confirmation dialog before rmtree-ing a project directory."""
    with ui.dialog() as dialog, ui.card().style("min-width: 480px;"):
        ui.label("Delete project?").classes("ath-section-title")
        ui.element("div").classes("ath-section-rule")
        ui.label(f'"{project_name}"').classes("text-sm mt-2").style("color: var(--ath-parchment);")
        ui.label(f"Slug: {project_id}").classes("text-xs").style("color: var(--ath-muted);")
        ui.label(
            "⚠ This permanently removes the project directory including state.json, "
            "all reports, the knowledge base, and all attachments. This action "
            "cannot be undone."
        ).classes("text-xs mt-2").style("color: var(--ath-gold);")

        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Cancel", on_click=dialog.close).classes("ath-secondary-btn")
            def _do_delete():
                state.delete_project(project_id)
                dialog.close()
                if on_done:
                    try:
                        on_done()
                    except Exception:
                        pass
            ui.button("Delete", icon="delete_outline", on_click=_do_delete).classes("ath-run-btn")
    dialog.open()


def _show_handoff_brief_dialog(state: AppState, node_id: str) -> None:
    """Render a handoff brief for a DAG node in a modal.

    Offers a Local/Full toggle (just the node's neighborhood vs the
    entire plan at the bottom), a "Copy to clipboard" button, and a
    "Save to file" button that drops the brief into the project dir.
    """
    s = state.project_state
    if not s or not s.dag:
        _safe_notify("No plan to brief", type="warning")
        return

    from src.athanor.assistants.briefer import build_handoff_brief

    # Find the node (for default filename + title)
    node_name = "step"
    for n in s.dag:
        if n.id == node_id:
            node_name = n.name
            break

    # Mutable state for the dialog
    mode = {"full_plan": False}

    with ui.dialog() as dialog, ui.card().style("min-width: 720px; max-width: 900px; max-height: 85vh;"):
        with ui.row().classes("w-full items-center gap-2"):
            ui.label("Handoff brief").classes("ath-section-title flex-1")
            view_toggle = ui.toggle(
                {"local": "Local", "full": "Full plan"},
                value="local",
            ).props("dense color=primary").tooltip(
                "Local: just this step's neighborhood (short, copy-friendly). "
                "Full: include the full ordered plan at the bottom so a "
                "first-time collaborator has orientation."
            )
        ui.label(f'"{node_name}"').classes("text-sm").style("color: var(--ath-muted);")
        ui.element("div").classes("ath-section-rule")

        # The brief text area — re-rendered on toggle change
        brief_container = ui.column().classes("w-full").style("overflow-y: auto; max-height: 55vh;")

        def _render():
            brief_container.clear()
            try:
                text = build_handoff_brief(s, node_id, full_plan=mode["full_plan"])
            except Exception as e:
                text = f"(Error rendering brief: {e})"
            with brief_container:
                ta = ui.textarea(value=text).classes("w-full ath-readout").props("rows=24 dense outlined readonly")
                # Store reference to the textarea value so copy/save handlers use the current text
                brief_container._ta_value = text  # type: ignore[attr-defined]

        _render()

        def _on_toggle(e):
            mode["full_plan"] = (e.value == "full")
            _render()

        view_toggle.on_value_change(_on_toggle)

        # Action buttons
        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            def _copy():
                try:
                    text = getattr(brief_container, "_ta_value", "")
                    ui.run_javascript(
                        f"navigator.clipboard.writeText({_json_escape(text)})"
                    )
                    _safe_notify("Brief copied to clipboard", type="positive")
                except Exception as e:
                    _safe_notify(f"Copy failed: {e}", type="negative")

            def _save():
                try:
                    project_dir = state.manager.get_project_dir(s)
                    briefs_dir = os.path.join(project_dir, "briefs")
                    os.makedirs(briefs_dir, exist_ok=True)
                    safe_name = "".join(
                        c if c.isalnum() or c in "-_ " else "_" for c in node_name
                    ).strip().replace(" ", "_")[:80] or "brief"
                    path = os.path.join(briefs_dir, f"{safe_name}.md")
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(getattr(brief_container, "_ta_value", ""))
                    _safe_notify(f"Saved to {path}", type="positive")
                except Exception as e:
                    _safe_notify(f"Save failed: {e}", type="negative")

            ui.button("Close", on_click=dialog.close).classes("ath-secondary-btn")
            ui.button(
                "Copy to clipboard",
                icon="content_copy",
                on_click=_copy,
            ).classes("ath-secondary-btn")
            ui.button(
                "Save to file",
                icon="save",
                on_click=_save,
            ).classes("ath-run-btn")

    dialog.open()


def _json_escape(s: str) -> str:
    """Escape a string for safe embedding in a JavaScript literal."""
    import json as _json
    return _json.dumps(s)


def _show_mark_done_dialog(state: AppState, node_id: str, node_name: str) -> None:
    """Dialog to capture actual duration + deviation notes when marking
    a step done. The deviation notes flow into Stage 6's Methods section
    so the paper reflects what actually happened.

    Also offers to promote the completed step to the preliminary results
    list for Stage 4 grant writing — the Position 3 lifecycle loop."""
    # Check if the node is a deliverable (→ default-check the promote box)
    is_deliverable_default = False
    if state.project_state:
        for n in state.project_state.dag:
            if n.id == node_id:
                is_deliverable_default = bool(n.is_deliverable)
                break

    with ui.dialog() as dialog, ui.card().style("min-width: 560px;"):
        ui.label("Mark step as done").classes("ath-section-title")
        ui.label(f'"{node_name}"').classes("text-sm mt-1").style("color: var(--ath-parchment);")
        ui.element("div").classes("ath-section-rule")

        ui.label("Actual wall-clock days (optional)").classes("text-xs mt-2").style("color: var(--ath-muted)")
        actual_inp = ui.number(value=0, min=0, format="%.1f").classes("w-full").props("dense outlined")

        ui.label("Deviation notes (optional — describe anything that differed from the plan)").classes("text-xs mt-3").style("color: var(--ath-muted)")
        notes_inp = ui.textarea(
            placeholder="e.g. Had to switch reagent vendor mid-run; final concentration 12% lower than planned.",
        ).classes("w-full").props("rows=3 dense outlined")

        ui.element("div").classes("ath-section-rule mt-3")
        promote_inp = ui.checkbox(
            "Promote to preliminary results for Stage 4",
            value=is_deliverable_default,
        ).classes("mt-2")
        ui.label(
            "When checked, a Preliminary Results entry is created automatically "
            "from this step's name, description, and any attached artifacts. The "
            "next Stage 4 run will cite it as pilot/feasibility evidence."
        ).classes("text-xs").style("color: var(--ath-muted);")

        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Cancel", on_click=dialog.close).classes("ath-secondary-btn")
            def _do_mark():
                actual = float(actual_inp.value) if actual_inp.value else None
                if actual == 0:
                    actual = None
                state.mark_node_done(node_id, actual, (notes_inp.value or "").strip())
                if promote_inp.value:
                    state.promote_node_to_preliminary_result(node_id)
                dialog.close()
            ui.button("Mark done", icon="done_all", on_click=_do_mark).classes("ath-run-btn")
    dialog.open()


def _show_mark_blocked_dialog(state: AppState, node_id: str, node_name: str) -> None:
    """Capture a reason when marking a step blocked."""
    with ui.dialog() as dialog, ui.card().style("min-width: 480px;"):
        ui.label("Mark step as blocked").classes("ath-section-title")
        ui.label(f'"{node_name}"').classes("text-sm mt-1").style("color: var(--ath-parchment);")
        ui.element("div").classes("ath-section-rule")

        ui.label("What's blocking this step?").classes("text-xs mt-2").style("color: var(--ath-muted)")
        reason_inp = ui.textarea(
            placeholder="e.g. MTA still being negotiated with the collaborating institution. ETA unknown.",
        ).classes("w-full").props("rows=3 dense outlined autofocus")

        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("Cancel", on_click=dialog.close).classes("ath-secondary-btn")
            def _do_mark():
                state.mark_node_blocked(node_id, (reason_inp.value or "").strip())
                dialog.close()
            ui.button("Mark blocked", icon="block", on_click=_do_mark).classes("ath-run-btn")
    dialog.open()


# ============================================================================
# Stage 6 — Paper Writing
# ============================================================================

def _paper_promoted(state: AppState):
    """Always-visible: target venue input for Stage 6."""
    with ui.row().classes("w-full items-end gap-3"):
        with ui.column().classes("flex-1 gap-0"):
            ui.label("Target venue").classes("text-xs").style("color: var(--ath-muted)")
            venue_inp = ui.input(
                value=(state.project_state.manuscript.target_venue if state.project_state and state.project_state.manuscript and state.project_state.manuscript.target_venue else ""),
                placeholder="e.g. Nature Methods, PNAS, arXiv",
            ).classes("w-full").props("dense outlined")

            def _save_venue(e=None):
                if state.project_state and state.project_state.manuscript:
                    state.project_state.manuscript.target_venue = venue_inp.value or None
                    state.save_project_state()
            venue_inp.on("blur", _save_venue)


def _paper_knobs(state: AppState):
    ui.label("Stage 6 drafts a paper from the executed DAG.").classes("text-xs").style("color: var(--ath-muted);")
    ui.label("  · Methods from completed plan steps + deviation notes (Stage 5)").classes("text-xs").style("color: var(--ath-muted);")
    ui.label("  · Results from attached artifacts").classes("text-xs").style("color: var(--ath-muted);")
    ui.label("  · Per-section regenerate and full re-run with feedback available in Output below").classes("text-xs").style("color: var(--ath-muted);")


def _paper_readout(state: AppState):
    """Stage 6 readout: manuscript sections with editing affordances,
    source-materials panel, full re-run box."""
    s = state.project_state
    if not s or not s.manuscript:
        ui.label("No manuscript yet. Run Stage 6 — it drafts the paper from your executed plan.").classes("text-sm").style("color: var(--ath-muted)")
        return

    m = s.manuscript
    ui.label(m.title or "Manuscript").classes("text-lg").style("color: var(--ath-parchment); font-family: 'Cinzel', serif;")
    if m.target_venue:
        ui.label(f"Target: {m.target_venue}").classes("text-xs").style("color: var(--ath-muted);")

    # ── Source materials panel (what the writer saw) ──
    from src.athanor.core.state import ExecutionStatus
    done_nodes = [n for n in s.dag if n.status == ExecutionStatus.DONE] if s.dag else []
    in_progress_nodes = [n for n in s.dag if n.status == ExecutionStatus.IN_PROGRESS] if s.dag else []

    if done_nodes or m.figures:
        with ui.expansion("Source materials (what the writer saw)", icon="science").classes("w-full mt-2").props("dense"):
            if done_nodes:
                ui.label(f"Completed plan steps ({len(done_nodes)})").classes("text-xs").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                for node in done_nodes:
                    bits = [f"✓ {node.name}"]
                    if node.actual_wall_clock_days is not None:
                        bits.append(f"({node.actual_wall_clock_days:.0f}d actual)")
                    ui.label("  " + " ".join(bits)).classes("text-xs").style("color: var(--ath-emerald);")
                    if node.deviation_notes:
                        ui.label(f"    deviation: {node.deviation_notes}").classes("text-xs").style("color: var(--ath-gold); font-style: italic;")
            if in_progress_nodes:
                ui.label(f"In-progress plan steps ({len(in_progress_nodes)})").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                for node in in_progress_nodes:
                    ui.label(f"  ⏵ {node.name}").classes("text-xs").style("color: var(--ath-gold);")
            if m.figures:
                ui.label(f"Attached artifacts / figures ({len(m.figures)})").classes("text-xs mt-2").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                for fig in m.figures:
                    ui.label(f"  · {fig}").classes("text-xs").style("color: var(--ath-text);")
    else:
        ui.label("⚠ No plan steps are marked DONE. The Methods and Results sections will be thin until you mark progress in Stage 5.").classes("text-xs mt-2").style("color: var(--ath-gold);")

    # ── Manuscript sections (same pattern as Proposal) ──
    ui.label(f"{len(m.sections)} sections").classes("ath-readout-key mt-3")

    for idx, sec in enumerate(m.sections):
        with ui.expansion(sec.title or "Untitled").classes("w-full mt-1"):
            def _set_paper_section(v, i=idx):
                if state.project_state and state.project_state.manuscript:
                    state.project_state.manuscript.sections[i].content = v
                    state.save_project_state()
            _editable_text(state, sec.content or "", _set_paper_section)

            # Per-section feedback + Regenerate
            ui.label("Feedback for this section (optional)").classes("text-xs mt-3").style("color: var(--ath-muted)")
            section_feedback = ui.textarea(
                placeholder="e.g. Tighten the limitations paragraph. Add a citation for the novel method.",
            ).classes("w-full").props("rows=2 dense outlined")

            with ui.row().classes("w-full gap-2 mt-2"):
                ui.button(
                    "Regenerate section",
                    icon="auto_awesome",
                    on_click=lambda si=idx, fi=section_feedback: state.rerun_paper_section(
                        si, fi.value or ""
                    ),
                ).classes("ath-secondary-btn").props("size=sm").tooltip(
                    "Rewrite this one section with the feedback. Uses the executed-DAG context."
                )

    # ── Full-paper re-run ──
    ui.element("div").classes("ath-section-rule mt-4")
    ui.label("Re-run full manuscript with feedback").classes("ath-readout-key mt-3")
    ui.label("Expensive — re-drafts all 6 sections. Use for overall voice or structure changes.").classes("text-xs").style("color: var(--ath-muted);")
    full_feedback = ui.textarea(
        placeholder="e.g. Rewrite as a methods paper, not a results paper. Emphasize the reproducibility angle. Match the voice of the target venue.",
    ).classes("w-full mt-1").props("rows=3 dense outlined")

    ui.button(
        "Re-run full manuscript",
        icon="replay",
        on_click=lambda: state.rerun_full_paper(full_feedback.value or ""),
    ).classes("ath-run-btn mt-2").props("size=sm")


# ============================================================================
# Output tab
# ============================================================================

def _build_output_tab(state: AppState):
    with ui.card().classes("w-full"):
        ui.label("Generated Artifacts").classes("ath-section-title")
        ui.label("Final Output").classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")

        if not state.project_state:
            ui.label("Run the pipeline to generate artifacts.").classes("text-sm").style("color: var(--ath-muted)")
            return

        try:
            project_dir = state.manager.get_project_dir(state.project_state)
            ui.label(f"📁 {project_dir}").classes("text-xs").style("color: var(--ath-muted)")

            stages_info = [
                ("researcher_profile", "Stage 1: Profile"),
                ("hypothesis_analysis", "Stage 2: Hypothesis"),
                ("research_plan", "Stage 3: Plan"),
                ("grant_proposal", "Stage 4: Proposal"),
                ("execution_summary", "Stage 5: Execution"),
                ("manuscript", "Stage 6: Paper"),
            ]

            any_found = False
            for stage_key, stage_label in stages_info:
                exts_present = []
                for ext in ["pdf", "tex", "md", "json"]:
                    path = os.path.join(project_dir, f"{stage_key}.{ext}")
                    if os.path.exists(path):
                        exts_present.append((ext, path))
                if not exts_present:
                    continue
                any_found = True
                with ui.expansion(stage_label).classes("w-full mt-1"):
                    for ext, path in exts_present:
                        size_kb = os.path.getsize(path) // 1024
                        with ui.row().classes("w-full items-center gap-2 mt-1"):
                            ui.label(f"{stage_key}.{ext}").classes("text-sm flex-1")
                            ui.label(f"{size_kb} KB").classes("text-xs").style("color: var(--ath-muted)")
                            if ext == "md":
                                def _preview(p=path):
                                    _show_markdown_preview(p)
                                ui.button("Preview", icon="visibility", on_click=_preview).classes("ath-secondary-btn").props("size=sm")

            if not any_found:
                ui.label("No artifacts found yet.").classes("text-sm").style("color: var(--ath-muted)")

        except Exception as e:
            ui.label(f"Error listing artifacts: {e}").classes("text-sm").style("color: var(--ath-error)")


def _show_markdown_preview(path: str) -> None:
    """Open a dialog with the markdown rendered."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        ui.notify(f"Read failed: {e}", type="negative")
        return

    with ui.dialog() as dialog, ui.card().style("min-width: 800px; max-width: 900px; max-height: 85vh;"):
        with ui.row().classes("w-full items-center"):
            ui.label(os.path.basename(path)).classes("ath-section-title flex-1")
            ui.button(icon="close", on_click=dialog.close).props("flat round size=sm")
        ui.element("div").classes("ath-section-rule")
        with ui.scroll_area().classes("w-full").style("max-height: 70vh;"):
            ui.markdown(content)
    dialog.open()


# ============================================================================
# Helpers
# ============================================================================

def _build_status_bar(state: AppState) -> None:
    """Render the fixed-footer status bar.

    Layout: [STATUS · ticker ...flex...] [SESSION stats | LIFETIME stats | Trace]
    """
    state.sb_root = ui.element("div").classes("ath-statusbar")
    with state.sb_root:
        # ── Left: status + live ticker (flex-grows to fill) ──
        with ui.element("div").classes("ath-statusbar-section").style(
            "flex: 1 1 auto; min-width: 0; overflow: hidden;"
        ):
            state.sb_status = ui.label("Idle").classes("ath-statusbar-value ath-statusbar-emerald")
            ui.element("div").classes("ath-statusbar-divider")
            state.log_ticker = ui.label("").style(
                "color: var(--ath-muted); "
                "white-space: nowrap; overflow: hidden; text-overflow: ellipsis; "
                "flex: 1 1 auto; min-width: 0;"
            )

        # ── Right: cost accumulators + trace button ──
        with ui.element("div").classes("ath-statusbar-section"):
            ui.label("SESSION").classes("ath-statusbar-label")
            state.sb_session_tokens = ui.label("0 tok").classes("ath-statusbar-value")
            ui.label("·").classes("ath-statusbar-label")
            state.sb_session_cost = ui.label("$0.0000").classes("ath-statusbar-value ath-statusbar-emerald")
            ui.label("·").classes("ath-statusbar-label")
            state.sb_session_time = ui.label("0s").classes("ath-statusbar-value")

        ui.element("div").classes("ath-statusbar-divider")

        with ui.element("div").classes("ath-statusbar-section"):
            ui.label("LIFETIME").classes("ath-statusbar-label")
            state.sb_lifetime_tokens = ui.label("0 tok").classes("ath-statusbar-value")
            ui.label("·").classes("ath-statusbar-label")
            state.sb_lifetime_cost = ui.label("$0.00").classes("ath-statusbar-value ath-statusbar-gold")
            ui.label("·").classes("ath-statusbar-label")
            state.sb_lifetime_runs = ui.label("0 runs").classes("ath-statusbar-value")

        ui.element("div").classes("ath-statusbar-divider")

        ui.button(icon="terminal", on_click=lambda: _show_trace_dialog(state)).props(
            "flat round size=xs"
        ).style("color: var(--ath-muted)").tooltip("View full trace log")

    _refresh_status_bar(state)


def _refresh_running_indicators(state: AppState) -> None:
    """Update tab glow, run-button loading, and sidebar pulse for running stage."""
    rs = state.running_stage if state.is_running else None

    # 1. Tab glow — add/remove ath-tab-running class
    for num, tab in state.stage_tabs.items():
        try:
            if num == rs:
                tab.classes(add="ath-tab-running")
            else:
                tab.classes(remove="ath-tab-running")
        except Exception:
            pass

    # 2. Run button — toggle loading spinner
    for num, btn in state.stage_run_btns.items():
        try:
            if num == rs:
                btn.props(add="loading")
            else:
                btn.props(remove="loading")
        except Exception:
            pass

    # 3. Sidebar pipeline rows — handled by _refresh_status (ath-status-active)
    if state.refresh_status:
        try:
            state.refresh_status()
        except Exception:
            pass


def _refresh_status_bar(state: AppState) -> None:
    """Pull the latest tracker numbers and update the status bar in place."""
    if state.sb_session_tokens is None:
        return
    try:
        from src.athanor.core.tracker import tracker
        m = tracker.get_metrics_data()
    except Exception:
        return

    # Session
    state.sb_session_tokens.text = f"{m.get('total_tokens', 0):,} tok"
    state.sb_session_cost.text = f"${m.get('total_cost', 0):.4f}"
    secs = m.get("total_time", 0)
    if secs < 60:
        state.sb_session_time.text = f"{secs:.0f}s"
    else:
        state.sb_session_time.text = f"{int(secs // 60)}m{int(secs % 60):02d}s"

    # Lifetime
    lt = m.get("lifetime", {})
    state.sb_lifetime_tokens.text = f"{lt.get('tokens', 0):,} tok"
    state.sb_lifetime_cost.text = f"${lt.get('cost', 0):.2f}"
    state.sb_lifetime_runs.text = f"{lt.get('runs', 0)} runs"

    # Run status indicator
    if state.is_running:
        state.sb_status.text = "Running"
        state.sb_status.style("color: var(--ath-emerald)")
        if state.sb_root:
            state.sb_root.classes(add="ath-statusbar-running")
    elif state.error_count > 0:
        state.sb_status.text = f"Error ({state.error_count})"
        state.sb_status.style("color: var(--ath-error)")
        if state.sb_root:
            state.sb_root.classes(remove="ath-statusbar-running")
    else:
        state.sb_status.text = "Idle"
        state.sb_status.style("color: var(--ath-emerald)")
        if state.sb_root:
            state.sb_root.classes(remove="ath-statusbar-running")


def _show_trace_dialog(state: AppState) -> None:
    """Open a dialog showing the full session trace log."""
    err_count = sum(1 for e in state.log_entries if e.get("level") == "error")
    with ui.dialog() as dialog, ui.card().style(
        "min-width: 720px; max-width: 900px; max-height: 85vh;"
    ):
        with ui.row().classes("w-full items-center"):
            ui.label("Trace Log").classes("ath-section-title flex-1")
            if err_count:
                ui.label(f"{err_count} error{'s' if err_count != 1 else ''}").classes(
                    "text-xs"
                ).style(
                    "color: var(--ath-error); border: 1px solid var(--ath-error); "
                    "padding: 1px 8px; border-radius: 3px;"
                )
            if state.current_project_id:
                log_path = os.path.join("projects", state.current_project_id, "trace.log")
                if os.path.exists(log_path):
                    ui.label(log_path).classes("text-xs").style("color: var(--ath-muted)")
            ui.button(icon="close", on_click=dialog.close).props("flat round size=sm")
        ui.element("div").classes("ath-section-rule")

        with ui.scroll_area().classes("w-full ath-log").style("height: 65vh;"):
            if not state.log_entries:
                ui.label("(no trace entries this session)").classes("ath-log-verbose")
            else:
                for entry in state.log_entries:
                    level = entry.get("level", "info")
                    css_class = f"ath-log-{level}"
                    ts = datetime.fromtimestamp(entry["ts"]).strftime("%H:%M:%S")
                    lbl = ui.label(f"{ts}  {entry['msg']}").classes(css_class)
                    if level == "error":
                        lbl.style(
                            "background: rgba(217, 106, 106, 0.1); "
                            "padding: 2px 6px; border-radius: 3px; "
                            "border-left: 3px solid var(--ath-error);"
                        )
    dialog.open()


def _render_log_entry(state: AppState, entry: Dict[str, Any]) -> None:
    """Update the single-line ticker and stash the entry for the trace dialog.

    Error-level entries also get a persistent toast notification and turn
    the status bar red so they can't be missed.
    """
    state.log_entries.append(entry)
    msg = entry.get("msg", "").strip()
    level = entry.get("level", "info")

    # Update ticker
    if state.log_ticker is not None:
        color = {
            "error": "var(--ath-error)",
            "success": "var(--ath-emerald)",
            "verbose": "var(--ath-muted)",
        }.get(level, "var(--ath-text)")
        try:
            state.log_ticker.text = msg
            state.log_ticker.style(f"color: {color}")
        except Exception:
            pass

    # Errors: sticky toast + red status bar
    if level == "error" and msg:
        state.error_count += 1
        try:
            ui.notify(
                msg[:200],
                type="negative",
                close_button="Dismiss",
                timeout=0,
            )
        except Exception:
            pass
        if state.sb_status is not None:
            try:
                state.sb_status.text = f"Error"
                state.sb_status.style("color: var(--ath-error)")
            except Exception:
                pass


def _update_metrics(state: AppState, metrics: Dict[str, Any]) -> None:
    """Update the sidebar tally cards (called from main thread)."""
    if state.token_label:
        state.token_label.text = f"{metrics.get('total_tokens', 0):,} tokens"
    if state.cost_label:
        state.cost_label.text = f"${metrics.get('total_cost', 0):.2f}"


def _editable_text(state: AppState, value: str, on_save) -> None:
    """Read-first text widget.

    Default: renders the content as plain styled text, full height, no
    scroll constraint. Reads like a paragraph on a web page.

    On "Edit" click: swaps to a full-height textarea with Save / Cancel
    buttons. Save persists via on_save callback and returns to read
    mode. Cancel discards and returns to read mode.

    This single widget is used everywhere: proposal sections, hypothesis
    text, plan abstracts, manuscript sections, DAG node descriptions.
    The read-first pattern keeps long prose readable and only enters
    the editing affordance when the user explicitly chooses to write.
    """
    # Outer container that swaps between read and edit modes
    container = ui.column().classes("w-full")

    def _show_read():
        container.clear()
        with container:
            text = (value or "").strip()
            if not text:
                ui.label("(empty — click Edit to add content)").classes("text-sm").style(
                    "color: var(--ath-muted); font-style: italic;"
                )
            else:
                # Render as plain text with markdown-like line breaks.
                # No height constraint — reads like a web page.
                ui.markdown(text).classes("w-full ath-readout").style(
                    "color: var(--ath-text); "
                    "line-height: 1.7; "
                    "font-size: 0.88rem;"
                )
            ui.button(
                "Edit",
                icon="edit",
                on_click=lambda: _show_edit(),
            ).classes("ath-secondary-btn mt-1").props("size=sm flat")

    def _show_edit():
        container.clear()
        with container:
            rows = max(4, min(30, (value or "").count("\n") + 3))
            ta = ui.textarea(value=value).classes("w-full ath-readout").props(
                f"rows={rows} dense outlined autofocus"
            )

            with ui.row().classes("w-full gap-2 mt-1"):
                def _do_save():
                    nonlocal value
                    try:
                        on_save(ta.value)
                        value = ta.value
                        _safe_notify("Saved", type="positive")
                    except Exception as e:
                        _safe_notify(f"Save failed: {e}", type="negative")
                        return
                    _show_read()

                def _do_cancel():
                    _show_read()

                ui.button("Save", icon="save", on_click=_do_save).classes("ath-run-btn").props("size=sm")
                ui.button("Cancel", icon="close", on_click=_do_cancel).classes("ath-secondary-btn").props("size=sm")

    _show_read()


def _editable_input(state: AppState, value: str, on_save) -> None:
    """An editable single-line input with auto-save."""
    inp = ui.input(value=value).classes("w-full")

    def _on_blur():
        try:
            on_save(inp.value)
            ui.notify("Saved", type="positive", position="bottom-right", timeout=800)
        except Exception as e:
            ui.notify(f"Save failed: {e}", type="negative")

    inp.on("blur", _on_blur)


def _get_pi_affiliation() -> str:
    try:
        return config._config.get("pi", {}).get("affiliation", "")
    except Exception:
        return ""


def _set_config(path: List[str], value: Any) -> None:
    """Set a nested config value (e.g. ['llm', 'provider'] -> 'gemini')."""
    try:
        cur = config._config
        for k in path[:-1]:
            if k not in cur or not isinstance(cur[k], dict):
                cur[k] = {}
            cur = cur[k]
        cur[path[-1]] = value
    except Exception as e:
        ui.notify(f"Config set failed: {e}", type="negative")


def _show_input_dialog(state: AppState, kind: str, prompt: str, default: Any) -> None:
    with ui.dialog() as dialog, ui.card():
        ui.label(prompt).classes("ath-section-title")
        value_input = ui.input(value=str(default) if default else "").classes("w-full")

        def submit():
            state.ui_provider.submit_input(value_input.value)
            dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=lambda: (state.ui_provider.submit_input(default or ""), dialog.close())).classes("ath-secondary-btn")
            ui.button("Submit", on_click=submit).classes("ath-run-btn")
    dialog.open()


def _show_choice_dialog(state: AppState, message: str, options: List[str], default: Optional[str]) -> None:
    with ui.dialog() as dialog, ui.card():
        ui.label(message).classes("ath-section-title")
        choice = ui.select(options=options, value=default or options[0]).classes("w-full")

        def submit():
            state.ui_provider.submit_choice(choice.value)
            dialog.close()

        ui.button("Submit", on_click=submit).classes("ath-run-btn mt-2")
    dialog.open()


def _show_approval_dialog(state: AppState, original: str, refined: str) -> None:
    with ui.dialog() as dialog, ui.card().style("min-width: 720px; max-width: 900px;"):
        ui.label("Your Decision").classes("ath-section-title")
        ui.label("Approve or revise the refined hypothesis").classes("ath-section-subtitle")
        ui.element("div").classes("ath-section-rule")

        with ui.row().classes("w-full gap-3"):
            with ui.column().classes("flex-1"):
                ui.label("ORIGINAL").classes("text-xs").style("color: var(--ath-muted); letter-spacing: 0.08em;")
                with ui.element("div").classes("p-3").style("background: var(--ath-surface-alt); border-radius: 6px; border-left: 3px solid var(--ath-muted);"):
                    ui.label(original).classes("text-sm").style("color: var(--ath-text); white-space: pre-wrap;")
            with ui.column().classes("flex-1"):
                ui.label("REFINED").classes("text-xs").style("color: var(--ath-emerald); letter-spacing: 0.08em;")
                with ui.element("div").classes("p-3").style("background: var(--ath-surface-alt); border-radius: 6px; border-left: 3px solid var(--ath-emerald);"):
                    ui.label(refined).classes("text-sm").style("color: var(--ath-emerald); white-space: pre-wrap;")

        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button("Keep original", on_click=lambda: (state.ui_provider.submit_approval("a"), dialog.close())).classes("ath-secondary-btn")
            ui.button("Re-debate", on_click=lambda: (state.ui_provider.submit_approval("c"), dialog.close())).classes("ath-secondary-btn")
            ui.button("Accept refined", on_click=lambda: (state.ui_provider.submit_approval("b"), dialog.close())).classes("ath-run-btn")
    dialog.open()


# ============================================================================
# Entry point
# ============================================================================

def main():
    desktop_mode = "--web" not in sys.argv

    if desktop_mode:
        ui.run(
            title="Athanor",
            native=True,
            window_size=(1400, 900),
            reload=False,
            dark=True,
        )
    else:
        ui.run(
            title="Athanor",
            port=8080,
            reload=False,
            dark=True,
        )


if __name__ in {"__main__", "__mp_main__"}:
    main()
