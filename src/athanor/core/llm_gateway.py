"""
ATHANOR LLM Gateway.

Unified agent runtime built on PydanticAI, exposed through
PydanticLLMClient.

Tool management (RAG) is retained for backward compatibility until
Phase 4 removes those dependencies.
"""

from __future__ import annotations

import json
import os
import asyncio
import re
import threading
import time
from typing import Any, Dict, List, Optional, Type

# Python 3.13 warns when get_event_loop() is called without a current loop.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.google import GoogleModel

from .config_loader import config
from .rate_limiter import limiter
from .types import LLMTextResponse

# --- Tool Imports (RAG) ---
from .rag_tools import (
    WebSearchTool, OpenAlexTool, SemanticScholarTool,
    HuggingFaceSearchTool, GitHubSearchTool, GoogleDatasetTool, ZenodoTool,
    WebScrapeTool, BudgetEngine
)
from ..assistants.librarian import ResourceOpenerTool
RAG_TOOLS_AVAILABLE = True



# ---------------------------------------------------------------------------
# API-key helpers
# ---------------------------------------------------------------------------

def _is_placeholder_key(value: str | None) -> bool:
    if not value:
        return True
    low = value.strip().lower()
    return any(x in low for x in ("replace", "your_", "your-", "placeholder", "changeme", "todo"))


def _resolve_gemini_api_key() -> str:
    google = os.getenv("GOOGLE_API_KEY")
    gemini = os.getenv("GEMINI_API_KEY")
    if not _is_placeholder_key(google):
        return str(google).strip()
    if not _is_placeholder_key(gemini):
        return str(gemini).strip()
    return ""


# ---------------------------------------------------------------------------
# Error-detection helpers
# ---------------------------------------------------------------------------

def _is_malformed_function_call_error(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    return (
        "malformed_function_call" in msg
        or ("finish_reason" in msg and "function_call_filter" in msg)
    )


def _is_model_not_found_error(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    return (
        "model_name" in msg and "not found" in msg
    ) or ("models/" in msg and "not found" in msg)


def _is_event_loop_binding_error(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    return (
        "bound to a different event loop" in msg
        or "event loop is closed" in msg
        or "there is no current event loop" in msg
    )


def _is_transient_gemini_error(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    markers = (
        "503", "504", "429",
        "resourceexhausted", "quota", "too many requests",
        "unavailable", "high demand", "deadline exceeded",
        "timed out", "timeout", "connection reset",
        "temporarily unavailable",
    )
    return any(m in msg for m in markers)


def _is_output_validation_failure(exc: Exception) -> bool:
    return isinstance(exc, UnexpectedModelBehavior)


# ---------------------------------------------------------------------------
# Retry backoff schedule (seconds)
# ---------------------------------------------------------------------------
_RETRY_BACKOFF = (5, 15, 30)


def _backoff_seconds(attempt: int) -> int:
    return _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class CircuitBreakerOpen(RuntimeError):
    """Raised when too many consecutive LLM calls have failed."""


class _CircuitBreaker:
    def __init__(self, threshold: int = 3):
        self._threshold = threshold
        self._consecutive_failures = 0
        self._lock = threading.Lock()

    def check(self):
        with self._lock:
            if self._consecutive_failures >= self._threshold:
                raise CircuitBreakerOpen(
                    f"Circuit breaker open: {self._consecutive_failures} consecutive LLM calls "
                    f"have failed. The LLM service may be down or rate-limiting. "
                    f"Check API key, network, and Gemini service status."
                )

    def record_success(self):
        with self._lock:
            self._consecutive_failures = 0

    def record_failure(self):
        with self._lock:
            self._consecutive_failures += 1

    def reset(self):
        with self._lock:
            self._consecutive_failures = 0


_circuit_breaker = _CircuitBreaker(threshold=3)


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Structured runner
# ---------------------------------------------------------------------------

class StructuredLLMRunner:
    def __init__(self, parent: "PydanticLLMClient", schema: Type[Any]):
        self.parent = parent
        self.schema = schema

    def run(self, input_data: Any, config: Optional[Any] = None, timeout: Optional[int] = None, **kwargs: Any) -> Any:
        prompt = self.parent._normalize_input(input_data)
        try:
            result = self.parent._run(prompt, output_type=self.schema, timeout=timeout)
            return result.output
        except Exception as e:
            if not _is_malformed_function_call_error(e):
                raise
            if self.parent.verbose:
                print("    [LLM] Structured call malformed_function_call; retrying via text+schema coercion.")
            json_prompt = (
                f"{prompt}\n\n"
                "Return ONLY valid JSON matching the required schema. "
                "Do not include markdown fences or any commentary."
            )
            text_resp = self.parent.run_text(json_prompt, timeout=timeout)
            return self.parent._coerce_schema_output(self.schema, text_resp.content)


# ---------------------------------------------------------------------------
# PydanticLLMClient — the core replacement for SafeGemini
# ---------------------------------------------------------------------------

class PydanticLLMClient:
    """Pydantic-AI native client for text and structured runs."""

    def __init__(self, name: str, verbose: bool = False, model_name: Optional[str] = None, timeout: int = 600):
        self.name = name
        self.verbose = verbose
        self.timeout = timeout
        self.model_name = model_name or config.llm_model or "gemini-2.5-flash"
        self.model = self._build_model()
        # Persistent event loop — avoids httpx "Event loop is closed" errors
        # from TLS connection cleanup across loop boundaries.
        self._loop = asyncio.new_event_loop()

    def _build_model(self) -> Any:
        provider = (config.llm_provider or "gemini").lower()
        if provider != "gemini":
            raise RuntimeError(f"Unsupported llm_provider='{provider}'. This project is configured for Gemini only.")
        raw_model = str(self.model_name or "gemini-2.5-flash").replace("gemini/", "")
        _deprecated_lite_flash = {"gemini-1.5-lite", "gemini-1.5-flash"}
        model_name = "gemini-2.0-flash" if raw_model in _deprecated_lite_flash else raw_model
        api_key = _resolve_gemini_api_key()
        if not api_key:
            raise RuntimeError("Missing GOOGLE_API_KEY or GEMINI_API_KEY in .env")
        os.environ["GEMINI_API_KEY"] = api_key
        os.environ.pop("GOOGLE_API_KEY", None)
        return GoogleModel(model_name, provider="google-gla")

    def _normalize_input(self, input_data: Any) -> str:
        if isinstance(input_data, str):
            return input_data
        if isinstance(input_data, list):
            parts: List[str] = []
            for item in input_data:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and "content" in item:
                    parts.append(str(item["content"]))
                elif hasattr(item, "content"):
                    parts.append(str(item.content))
                else:
                    parts.append(str(item))
            return "\n".join([p for p in parts if p]).strip()
        if isinstance(input_data, dict) and "content" in input_data:
            return str(input_data["content"])
        if hasattr(input_data, "content"):
            return str(input_data.content)
        return str(input_data)

    def _usage_dict(self, run_result: Any) -> Optional[Dict[str, int]]:
        usage_attr = getattr(run_result, "usage", None)
        usage = usage_attr() if callable(usage_attr) else usage_attr
        if usage is None:
            return None
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", 0) or (prompt_tokens + completion_tokens))
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
        }

    @staticmethod
    def _coerce_schema_output(schema: Type[Any], text: str) -> Any:
        raw = (text or "").strip()
        if hasattr(schema, "model_validate_json"):
            try:
                return schema.model_validate_json(raw)
            except Exception:
                pass
        match = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
        if match and hasattr(schema, "model_validate_json"):
            return schema.model_validate_json(match.group(1))
        if hasattr(schema, "model_validate"):
            return schema.model_validate(json.loads(raw))
        raise RuntimeError("Unable to coerce text response into structured schema.")

    def _run(self, prompt: str, output_type: Any = str, timeout: Optional[int] = None) -> Any:
        _circuit_breaker.check()
        limiter.wait_for_slot()
        effective_timeout = timeout or self.timeout
        if self.verbose:
            print(f"    [LLM] {self.name} is thinking...\r", end="", flush=True)

        try:
            loop_check = asyncio.get_event_loop()
            if loop_check.is_closed():
                asyncio.set_event_loop(asyncio.new_event_loop())
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        attempts = 3 if output_type is not str else 2
        last_error: Exception | None = None
        result = None
        model_fallback_attempted = False
        for attempt in range(attempts):
            try:
                asyncio.set_event_loop(self._loop)
                agent = Agent(self.model, output_type=output_type, output_retries=3)
                result = self._loop.run_until_complete(
                    asyncio.wait_for(agent.run(prompt), timeout=effective_timeout)
                )
                break
            except Exception as e:
                last_error = e
                if (
                    _is_model_not_found_error(e)
                    and not model_fallback_attempted
                    and str(self.model_name or "") != str(config.llm_model or "")
                ):
                    model_fallback_attempted = True
                    self.model_name = config.llm_model
                    self.model = self._build_model()
                    if self.verbose:
                        print(
                            f"    [LLM] model unavailable for {self.name}; "
                            f"falling back to primary_model={self.model_name}"
                        )
                    continue
                if (_is_event_loop_binding_error(e) or _is_transient_gemini_error(e)) and attempt < attempts - 1:
                    self._loop = asyncio.new_event_loop()
                    self.model = self._build_model()
                    backoff_s = _backoff_seconds(attempt)
                    if self.verbose:
                        print(
                            f"    [LLM] transient Gemini/runtime error for {self.name}; "
                            f"retry {attempt + 2}/{attempts} after {backoff_s}s..."
                        )
                    time.sleep(backoff_s)
                    continue
                if _is_malformed_function_call_error(e) and attempt < attempts - 1:
                    backoff_s = _backoff_seconds(attempt)
                    if self.verbose:
                        print(
                            f"    [LLM] malformed_function_call for {self.name}; "
                            f"retry {attempt + 2}/{attempts} after {backoff_s}s..."
                        )
                    time.sleep(backoff_s)
                    continue
                if _is_output_validation_failure(e) and attempt < attempts - 1:
                    self._loop = asyncio.new_event_loop()
                    self.model = self._build_model()
                    backoff_s = _backoff_seconds(attempt)
                    if self.verbose:
                        print(
                            f"    [LLM] output validation exhausted for {self.name}; "
                            f"gateway retry {attempt + 2}/{attempts} after {backoff_s}s..."
                        )
                    time.sleep(backoff_s)
                    continue
                raise
        if result is None and last_error is not None:
            _circuit_breaker.record_failure()
            raise last_error

        _circuit_breaker.record_success()

        if self.verbose:
            print(" " * 60 + "\r", end="", flush=True)

        usage = self._usage_dict(result)
        if usage:
            try:
                from .tracker import tracker
                tracker.track({"usage_metadata": usage})
            except Exception:
                pass

        return result

    def run_text(self, input_data: Any, config: Optional[Any] = None, timeout: Optional[int] = None, **kwargs: Any) -> LLMTextResponse:
        prompt = self._normalize_input(input_data)
        result = self._run(prompt, output_type=str, timeout=timeout)
        output = result.output
        if not isinstance(output, str):
            if hasattr(output, "model_dump_json"):
                output = output.model_dump_json(indent=2)
            else:
                output = json.dumps(output, default=str)
        return LLMTextResponse(content=output, usage_metadata=self._usage_dict(result))

    def structured(self, schema: Type[Any]) -> StructuredLLMRunner:
        return StructuredLLMRunner(self, schema)



# ---------------------------------------------------------------------------
# LLMGateway — static factory (public API unchanged)
# ---------------------------------------------------------------------------

class LLMGateway:
    """Central authority for agent capabilities (LLM + Tools + Protocol)."""

    @staticmethod
    def is_native_google_enabled() -> bool:
        return config.llm_provider == "gemini" and config.search_provider == "google"

    @staticmethod
    def get_grounding_tool() -> Any:
        if not LLMGateway.is_native_google_enabled():
            return None
        try:
            from google.ai.generativelanguage import GoogleSearchRetrieval, DynamicRetrievalConfig
            return GoogleSearchRetrieval(dynamic_retrieval_config=DynamicRetrievalConfig(
                mode=DynamicRetrievalConfig.Mode.MODE_DYNAMIC,
                dynamic_threshold=0.3
            ))
        except Exception:
            return None

    @staticmethod
    def get_llm(
        name: str,
        force_grounding: bool = True,
        verbose: bool = False,
        model_name: Optional[str] = None,
        timeout: int = 600,
    ) -> Any:
        """Standardized LLM Factory. Returns PydanticLLMClient or MockLLMClient."""
        _ = force_grounding  # Grounding is handled by prompt+tools in the new runtime.
        from .mock_llm import is_mock_enabled, MockLLMClient
        if is_mock_enabled():
            return MockLLMClient(name=name, verbose=verbose, model_name=model_name)
        return PydanticLLMClient(name=name, verbose=verbose, model_name=model_name, timeout=timeout)

    @staticmethod
    def get_structured_llm(
        name: str,
        schema: type,
        verbose: bool = False,
        model_name: Optional[str] = None,
    ) -> Any:
        return LLMGateway.get_llm(
            name, force_grounding=False, verbose=verbose, model_name=model_name,
        ).structured(schema)

    @staticmethod
    def get_search_instruction() -> str:
        depth = config.search_depth
        if LLMGateway.is_native_google_enabled():
            return "Use Google Search grounding."
        if depth > 1:
            return "Use specialized search tools (OpenAlex, GitHub) and general search. Search NOW, don't delegate to tasks."
        return "Use basic search to verify key claims."

    @staticmethod
    def get_tools(role: str = "general", force_ddg: bool = False) -> List[Any]:
        if not RAG_TOOLS_AVAILABLE:
            return []
        depth = config.search_depth
        use_native = LLMGateway.is_native_google_enabled()
        base = [ResourceOpenerTool(use_native_search=use_native)]
        if not use_native or force_ddg:
            base.append(WebSearchTool())
        specialized = [HuggingFaceSearchTool(), GitHubSearchTool()]
        deep = [OpenAlexTool(), GoogleDatasetTool(), ZenodoTool()]
        reviewer = [SemanticScholarTool(), ZenodoTool()]
        refiner = [OpenAlexTool(), SemanticScholarTool()]
        tools = base
        if depth > 1:
            if role == "drafter":
                tools += specialized + deep
            elif role == "reviewer":
                tools += specialized + reviewer
            elif role == "refiner":
                tools += refiner
            else:
                tools += specialized
        return tools

    @staticmethod
    def get_agent_bundle(name: str, role: str = "general", verbose: bool = False) -> Dict[str, Any]:
        return {
            "llm": LLMGateway.get_llm(name, verbose=verbose),
            "tools": LLMGateway.get_tools(role),
            "instructions": LLMGateway.get_search_instruction()
        }
