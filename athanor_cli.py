#!/usr/bin/python3
"""
ATHANOR Main Entry Point (CLI).

This script initializes the CLI Provider and launches the AthanorEngine.
"""
import argparse
from dotenv import load_dotenv

# Load environment variables
load_dotenv(dotenv_path="config/.env")
import os

# --- Enforce single API key source ---
# Some libraries prioritize GOOGLE_API_KEY if present.
# We explicitly remove it to force them to use the one we configure (or fail cleanly).
if "GOOGLE_API_KEY" in os.environ:
    if "GEMINI_API_KEY" not in os.environ:
        os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]
    del os.environ["GOOGLE_API_KEY"]
# ---------------------------------------------

from src.athanor.core.engine import AthanorEngine
from src.athanor.core.ui import CLIProvider
from src.athanor.core.state_manager import StateManager
from src.athanor.engines.team_profiler import TeamProfiler
from src.athanor.engines.hypothesis_refiner import HypothesisRefiner
from src.athanor.engines.research_planner import ResearchPlanner
from src.athanor.engines.grant_weaver import GrantWeaver

def main():
    parser = argparse.ArgumentParser(description="Athanor: AI-assisted research planning and grant proposal engine")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("-r", "--restart", nargs='?', const='INTERACTIVE', help="Restart/Resume a previous session.")
    parser.add_argument("--list", action="store_true", help="List saved projects")
    parser.add_argument("-c", "--config", help="Path to config file")
    args = parser.parse_args()
    
    # Override Verbosity in Config
    from src.athanor.core.config_loader import config
    if args.config:
        config.load_config(custom_path=args.config)
    config.set_verbosity_override(args.verbose)

    manager = StateManager()

    # CLI Provider
    ui = CLIProvider(verbose=args.verbose)

    if args.list:
        ui.log_status("Available Projects:")
        for pid in manager.list_projects():
            ui.log_status(f" - {pid}")
        return

    ui.display_header()
    
    project_state = None
    if args.restart:
        project_id = args.restart
        if project_id == 'INTERACTIVE':
            # We can use the UI provider for this!
            projects = manager.list_projects()
            if not projects:
                ui.log_status("No saved projects found.", level="error")
                return
            project_id = ui.get_choice("Select a project to resume", projects)
            if not project_id: return
        
        try:
            project_state = manager.load_state(project_id)
            ui.log_status(f"Loaded Project: {project_state.project_name} ({project_id})")
        except FileNotFoundError:
            ui.log_status(f"Error: Project {project_id} not found.", level="error")
            return

    # Initialize Engine
    engine = AthanorEngine(ui=ui, project_state=project_state)
    
    # Run
    try:
        engine.run(verbose=args.verbose)
    except RuntimeError as e:
        ui.log_status(f"Top-level Error: {e}", level="error")
    except KeyboardInterrupt:
        ui.log_status("\nInterrupted by user.")

if __name__ == "__main__":
    main()
