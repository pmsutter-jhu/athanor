"""
Athanor JSON Parser — robust JSON extraction from LLM responses.

Multi-strategy extraction.  Handles markdown
fences, truncated output, Python dict syntax, and preamble/postamble.

Usage:
    from athanor.core.json_parser import JSONParser

    data = JSONParser.extract_object(llm_text)   # -> dict | None
    data = JSONParser.extract_array(llm_text)     # -> list | None
"""

import ast
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class RecoveryMethod(Enum):
    DIRECT = "direct"
    CONTROL_CHAR_CLEAN = "control_char_clean"
    TRUNCATION_REPAIR = "truncation_repair"
    AST_LITERAL = "ast_literal"
    REGEX_EXTRACT = "regex_extract"
    FAILED = "failed"


@dataclass
class JSONParseResult:
    success: bool
    data: Any = None
    error_message: str = ""
    recovery_method: RecoveryMethod = RecoveryMethod.FAILED


class JSONParser:
    """Multi-strategy JSON parser for LLM output."""

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @classmethod
    def extract_object(cls, text: str) -> Optional[Dict[str, Any]]:
        """Extract the first JSON object ``{...}`` from *text*.

        Returns the parsed dict, or ``None`` if extraction fails.
        """
        result = cls._parse(text, shape="object")
        return result.data if result.success else None

    @classmethod
    def extract_array(cls, text: str) -> Optional[List[Any]]:
        """Extract the first JSON array ``[...]`` from *text*.

        Returns the parsed list, or ``None`` if extraction fails.
        """
        result = cls._parse(text, shape="array")
        return result.data if result.success else None

    @classmethod
    def parse(cls, text: str, shape: str = "object") -> JSONParseResult:
        """Full parse with metadata.  *shape* is ``"object"`` or ``"array"``."""
        return cls._parse(text, shape=shape)

    # ------------------------------------------------------------------ #
    # Internal strategies
    # ------------------------------------------------------------------ #

    @classmethod
    def _parse(cls, text: str, shape: str = "object") -> JSONParseResult:
        if not text or not text.strip():
            return JSONParseResult(False, error_message="Empty response")

        text = text.strip()

        for strategy in (
            cls._try_direct,
            cls._try_control_char_clean,
            cls._try_truncation_repair,
            cls._try_ast_literal,
            cls._try_regex_extract,
        ):
            result = strategy(text, shape)
            if result.success:
                return result

        return JSONParseResult(False, error_message="No valid JSON found")

    # -- Strategy 1: direct parse after stripping markdown ---------------

    @classmethod
    def _try_direct(cls, text: str, shape: str) -> JSONParseResult:
        try:
            clean = cls._strip_fences(text)
            json_str = cls._find_boundaries(clean, shape)
            if json_str is None:
                return JSONParseResult(False, error_message="No JSON boundaries")
            data = json.loads(json_str, strict=False)
            if not data:
                return JSONParseResult(False, error_message="Parsed JSON is empty")
            return JSONParseResult(True, data=data, recovery_method=RecoveryMethod.DIRECT)
        except (json.JSONDecodeError, ValueError):
            return JSONParseResult(False, error_message="Direct parse failed")

    # -- Strategy 2: clean control characters ----------------------------

    @classmethod
    def _try_control_char_clean(cls, text: str, shape: str) -> JSONParseResult:
        try:
            clean = cls._strip_fences(text)
            json_str = cls._find_boundaries(clean, shape)
            if json_str is None:
                return JSONParseResult(False)
            # Protect escaped sequences, strip raw control chars, restore
            protected = json_str
            for esc, marker in (("\\n", "\x00N"), ("\\r", "\x00R"), ("\\t", "\x00T"), ("\\\\", "\x00B")):
                protected = protected.replace(esc, marker)
            protected = re.sub(r"[\x00-\x1f]", " ", protected)
            for marker, esc in (("\x00N", "\\n"), ("\x00R", "\\r"), ("\x00T", "\\t"), ("\x00B", "\\\\")):
                protected = protected.replace(marker, esc)
            data = json.loads(protected, strict=False)
            if not data:
                return JSONParseResult(False)
            return JSONParseResult(True, data=data, recovery_method=RecoveryMethod.CONTROL_CHAR_CLEAN)
        except Exception:
            return JSONParseResult(False)

    # -- Strategy 3: truncation repair (close unclosed brackets) ---------

    @classmethod
    def _try_truncation_repair(cls, text: str, shape: str) -> JSONParseResult:
        try:
            clean = cls._strip_fences(text)
            # Truncation repair: find the opening brace but accept text-to-end
            # if the closing brace is missing.
            open_char = "[" if shape == "array" else "{"
            start = clean.find(open_char)
            if start == -1:
                return JSONParseResult(False)
            json_str = clean[start:]

            # Strip trailing partial token (e.g. an unclosed string or number)
            # by walking backwards to a clean boundary character.
            for cut_back in range(len(json_str), max(0, len(json_str) - 200), -1):
                candidate = json_str[:cut_back].rstrip().rstrip(",")
                # Try appending closing brackets
                suffixes = ["", "]", "}]", "}", "}}", "}}]", "\"]", "\"}", "\"}]"]
                for suffix in suffixes:
                    try:
                        data = json.loads(candidate + suffix, strict=False)
                        if data:
                            return JSONParseResult(True, data=data, recovery_method=RecoveryMethod.TRUNCATION_REPAIR)
                    except (json.JSONDecodeError, ValueError):
                        continue
                # Only retry the cut-back loop if the candidate ends mid-token
                if not candidate or candidate[-1] in '}]"':
                    break
            return JSONParseResult(False)
        except Exception:
            return JSONParseResult(False)

    # -- Strategy 4: ast.literal_eval (handles Python dict syntax) -------

    @classmethod
    def _try_ast_literal(cls, text: str, shape: str) -> JSONParseResult:
        try:
            clean = cls._strip_fences(text)
            json_str = cls._find_boundaries(clean, shape)
            if json_str is None:
                return JSONParseResult(False)
            data = ast.literal_eval(json_str)
            if data:
                return JSONParseResult(True, data=data, recovery_method=RecoveryMethod.AST_LITERAL)
            return JSONParseResult(False)
        except Exception:
            return JSONParseResult(False)

    # -- Strategy 5: regex extraction ------------------------------------

    @classmethod
    def _try_regex_extract(cls, text: str, shape: str) -> JSONParseResult:
        try:
            clean = cls._strip_fences(text)
            if shape == "array":
                match = re.search(r"\[.*\]", clean, re.DOTALL)
            else:
                match = re.search(r"\{.*\}", clean, re.DOTALL)
            if not match:
                return JSONParseResult(False)
            data = json.loads(match.group(0), strict=False)
            if data:
                return JSONParseResult(True, data=data, recovery_method=RecoveryMethod.REGEX_EXTRACT)
            return JSONParseResult(False)
        except Exception:
            return JSONParseResult(False)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _strip_fences(text: str) -> str:
        """Remove markdown code fences."""
        if "```json" in text:
            text = text.split("```json", 1)[1]
            if "```" in text:
                text = text.split("```", 1)[0]
        elif "```" in text:
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1]
        return text.strip()

    @staticmethod
    def _find_boundaries(text: str, shape: str) -> Optional[str]:
        """Find the outermost JSON object or array boundaries."""
        if shape == "array":
            start = text.find("[")
            end = text.rfind("]")
        else:
            start = text.find("{")
            end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        return text[start : end + 1]
