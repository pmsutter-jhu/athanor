"""
ATHANOR UI Base.

This module defines the UIProvider abstract base class and the CLIProvider 
concrete implementation, enabling a decoupled architecture for user interactions.
"""
import abc
import sys
import inquirer
from typing import List, Optional, Any, Dict
from concurrent.futures import ThreadPoolExecutor

class UIProvider(abc.ABC):
    """
    Abstract Base Class for ATHANOR User Interface.
    Decouples the logic engine from the specific UI implementation (CLI, Web, etc.).
    """
    
    @property
    def is_web(self) -> bool:
        """Returns True if the UI is web-based, False otherwise (CLI)."""
        return False

    def __init__(self):
        self.log_file = None
        self._log_executor = ThreadPoolExecutor(max_workers=1)

    def set_log_file(self, path: str) -> None:
        """Sets the path to a file where all logs should be appended."""
        self.log_file = path
        # Ensure we can write to it
        if self.log_file:
            import os
            dirname = os.path.dirname(self.log_file)
            if dirname:
                os.makedirs(dirname, exist_ok=True)

    def _write_to_log_file(self, message: str, level: str) -> None:
        """Internal helper to write a log entry to the file."""
        if self.log_file:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._log_executor.submit(self._do_write_log, message, level, timestamp)

    def _do_write_log(self, message: str, level: str, timestamp: str) -> None:
        try:
            # Strip ANSI codes if any (though usually not present in our UI strings)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] [{level.upper()}] {message}\n")
        except Exception:
            pass # Fail silently for logging errors to avoid crashing the main loop

    @abc.abstractmethod
    def log_status(self, message: str, level: str = "info") -> None:
        """Display a high-level status update to the user."""
        pass


    @abc.abstractmethod
    def get_input(self, prompt: str, default: Optional[str] = None) -> str:
        """Request free-text input from the user."""
        pass

    @abc.abstractmethod
    def get_choice(self, message: str, options: List[str], default: Optional[str] = None) -> str:
        """Request the user to select one option from a list."""
        pass

    @abc.abstractmethod
    def get_initial_spark(self, default: str) -> str:
        """Specialized input for the initial research thought."""
        pass
    
    @abc.abstractmethod
    def request_approval(self, original: str, refined: str) -> str:
        """
        Stage 2 Human Sovereignty Gate.
        Returns: 'a' (original), 'b' (refined), 'c' (retry), or others depending on implementation.
        """
        pass

    @abc.abstractmethod
    def display_report(self, file_path: str, title: str) -> None:
        """Display a generated report or artifact to the user."""
        pass
    
    @abc.abstractmethod
    def display_header(self) -> None:
        """Display the welcome header."""
        pass

    def display_metrics(self, metrics_data: Dict[str, Any]) -> None:
        """Display session performance metrics."""
        pass
        
    def refresh_artifacts(self, state: Any = None) -> None:
        """Hooks for UI to update artifact lists during execution."""
        pass

    def check_interruption(self) -> None:
        """Check if the user has requested to stop execution. Raises KeyboardInterrupt if so."""
        pass

class CLIProvider(UIProvider):
    """
    Concrete implementation of UIProvider for Command Line Interface.
    Uses print, input, and inquirer.
    """
    def __init__(self, verbose: bool = False):
        super().__init__()
        self.verbose = verbose

    def log_status(self, message: str, level: str = "info") -> None:
        # Write to trace file if set
        self._write_to_log_file(message, level)

        if level == "verbose":
            if self.verbose:
                 print(message)
        else:
            # Standard print for other levels
            print(message)


    def get_input(self, prompt: str, default: Optional[str] = None) -> str:
        p_text = f"{prompt} [{default}]: " if default else f"{prompt}: "
        val = input(p_text).strip()
        return val if val else (default or "")

    def get_choice(self, message: str, options: List[str], default: Optional[str] = None) -> str:
        # Use inquirer for nice selection
        if not options:
            return default or ""
        
        # Inquirer works best with a list of dictionaries if we want values, but here we just return the string
        questions = [
            inquirer.List('choice',
                          message=message,
                          choices=options,
                          default=default,
                          carousel=True
            ),
        ]
        answers = inquirer.prompt(questions)
        return answers['choice'] if answers else (default or options[0])

    def get_initial_spark(self, default: str) -> str:
        print("Enter a research idea (or press Enter for default): " + (f"\n> " if self.verbose else ""), end="")
        val = input().strip()
        return val if val else default

    def request_approval(self, original: str, refined: str) -> str:
        print("\n    >> HUMAN SOVEREIGNTY CHECK 👑")
        print(f"   Original: {original}")
        print(f"   Refined : {refined}")
        
        print("\nSelect Action:")
        print("   [a] Accept Original Hypothesis (Ignore Refinement)")
        print("   [b] Accept Refined Hypothesis (Proceed)")
        # Note: Logic for 'c' (retry) is handled by the caller checking the return value
        print("   [c] Reject & Retry Loop (Debate Refined Version)")
        
        choice = input("   > Selection [b]: ").strip().lower()
        return choice if choice in ['a', 'b', 'c'] else 'b'

    def display_report(self, file_path: str, title: str) -> None:
        print(f"\n    [Artifact] {title} generated at: {file_path}")
        
    def display_header(self) -> None:
        print("\n==============================================")
        print("                   ATHANOR")
        print("                    v0.3")
        print("==============================================\n")

    def display_metrics(self, metrics_data: Dict[str, Any]) -> None:
        # Basic CLI table-like fallback
        print("\n[Performance Metrics]")
        print(f"       SESSION TOTAL: {metrics_data['total_time']}s | ~{metrics_data['total_tokens']} tox")
        print(f"       ESTIMATED COST: ${metrics_data['total_cost']} (Model: {metrics_data['model']})")
        print("       Breakdown:")
        for s in metrics_data.get('breakdown', []):
            print(f"       - {s['stage']:<20}: {s['duration']}s | {s['tokens']} tox | ${s['cost']}")

