"""
ATHANOR Science Officer Agent.

Responsible for scientific visualization (plots) and safe code execution.
Uses PydanticLLMClient to generate Python code, then executes it in an
isolated process via ProcessPoolExecutor.
"""
import os
import re
import logging
from typing import Optional, Any
from concurrent.futures import ProcessPoolExecutor

from ..core.config_loader import config
from ..core.llm_gateway import LLMGateway



def _safe_run_code(work_dir: str, code: str) -> str:
    """Execute Python code in a separate process with correct working directory.

    This function must be picklable (top-level, no closures).
    """
    import os
    import io
    import contextlib

    old_cwd = os.getcwd()
    try:
        os.chdir(work_dir)

        code = code.strip()
        if code.startswith("```python"):
            code = code.replace("```python", "").replace("```", "")
        elif code.startswith("```"):
            code = code.replace("```", "")

        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                exec(
                    code,
                    {
                        "print": print,
                        "len": len,
                        "range": range,
                        "abs": abs,
                        "round": round,
                        "sum": sum,
                        "min": min,
                        "max": max,
                        "__import__": __import__,
                    },
                )
        except Exception as e:
            return f"Code Execution Error: {e}"

        output = stdout_capture.getvalue()
        return output or "[Code executed successfully with no output.]"
    finally:
        os.chdir(old_cwd)


class ScienceOfficer:
    """Generates evidence plots by asking an LLM to write Python code,
    then executing it in an isolated process."""

    def __init__(self, work_dir: str = ".agent/science_deck", ui: Optional[Any] = None):
        self.work_dir = work_dir
        self.ui = ui
        os.makedirs(self.work_dir, exist_ok=True)

    def _log(self, msg: str, level: str = "info"):
        if self.ui:
            self.ui.log_status(msg, level="error" if level == "error" else "verbose")
        elif level != "verbose":
            print(msg)

    def generate_evidence(self, hypothesis_summary: str) -> str:
        """Generate a proof-of-concept plot for the given hypothesis."""
        try:
            return self._generate(hypothesis_summary)
        except Exception as e:
            self._log(f"    [Science Officer] Evidence Generation Failed: {e}", level="error")
            return f"Error: {e}"

    def _generate(self, hypothesis_summary: str, max_attempts: int = 2) -> str:
        llm = LLMGateway.get_llm("ScienceOfficer", verbose=False)

        code_prompt = (
            "You are a Python Data Science Expert.\n"
            "Goal: Generate a synthetic 'Proof of Concept' plot based on a "
            "scientific hypothesis.\n\n"
            "Rules:\n"
            "- ALWAYS set the backend first: `import matplotlib; matplotlib.use('Agg')`\n"
            "- Use matplotlib or seaborn.\n"
            "- Generate synthetic data that makes the hypothesis look plausible.\n"
            "- Save the plot as 'figure_1.png' in the current directory.\n"
            "- DO NOT call plt.show().\n"
            "- Return ONLY executable Python code. No markdown fences or commentary.\n\n"
            f"Hypothesis: {hypothesis_summary}"
        )

        self._log(f"    [Science Officer] Visualizing Evidence in {self.work_dir}...")

        last_error = None
        for attempt in range(max_attempts):
            prompt = code_prompt if attempt == 0 else (
                f"{code_prompt}\n\n"
                f"Previous attempt failed with:\n{last_error}\n\n"
                "Fix the error and return corrected code."
            )
            resp = llm.run_text(prompt)
            code = self._extract_code(resp.content)
            result = self._execute_code(code)

            expected_path = os.path.join(self.work_dir, "figure_1.png")
            if os.path.exists(expected_path):
                return expected_path

            last_error = result
            self._log(
                f"    [Science Officer] Attempt {attempt + 1} failed: {result[:120]}",
                level="verbose",
            )

        return "No figure generated."

    def _execute_code(self, code: str) -> str:
        """Run code in an isolated process."""
        with ProcessPoolExecutor() as pool:
            future = pool.submit(_safe_run_code, self.work_dir, code)
            return future.result(timeout=120)

    @staticmethod
    def _extract_code(text: str) -> str:
        """Strip markdown fences if present."""
        text = text.strip()
        match = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        text = text.replace("```python", "").replace("```", "")
        return text.strip()


