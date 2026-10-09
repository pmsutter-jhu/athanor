"""
ATHANOR script entry points.

Thin shims so ``uv run athanor`` and ``uv run athanor-gui`` work from a
fresh clone. Both currently launch the NiceGUI desktop app — the CLI
(``athanor.py``) is invoked directly via ``python athanor.py`` for
scripting and automation.

The top-level ``athanor_gui.py`` is imported here via a sys.path
adjustment because hatchling only packages the ``src/athanor/``
package, not the sibling top-level entry files. In dev mode
(``uv run``, editable install) the repo root is already on sys.path
and the import succeeds directly; we keep the fallback for robustness.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _import_root_module(name: str):
    """Import a top-level .py file from the repo root.

    The repo root contains ``athanor_gui.py`` and ``athanor_cli.py`` —
    both are scripts, not part of the ``athanor`` package. The package
    name ``athanor`` was colliding with a previous top-level
    ``athanor.py`` until we renamed it; this helper exists so future
    script additions at the repo root can be imported the same way.
    """
    try:
        return __import__(name)
    except ImportError:
        pass

    # src/athanor/cli.py → parents[2] is the repo root
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        return __import__(name)
    except ImportError as e:
        print(
            f"Error: could not locate {name} module.\n"
            f"  Expected: {repo_root / (name + '.py')}\n"
            f"  Detail:   {e}\n"
            "  Run from the repository root, or use `uv run python <file>.py` directly.",
            file=sys.stderr,
        )
        sys.exit(1)


def gui_main() -> None:
    """Launch the Athanor NiceGUI desktop app."""
    mod = _import_root_module("athanor_gui")
    mod.main()


def cli_main() -> None:
    """Launch the Athanor CLI pipeline runner."""
    mod = _import_root_module("athanor_cli")
    mod.main()


# Alias so `athanor` and `athanor-gui` both work
main = gui_main
