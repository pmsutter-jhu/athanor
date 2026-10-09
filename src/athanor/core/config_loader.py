"""
ATHANOR Configuration Loader.

This module is responsible for loading the global configuration from the `config/athanor_config.toml` file.
It implements a singleton `ConfigLoader` that provides typed access properties for various settings,
including LLM providers, API keys (RAG tools), and workflow flags.

Usage:
    from athanor.core.config_loader import config
    api_key = config.huggingface_api_key
"""
import toml
import os
from typing import Dict
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

class ConfigLoader:
    _instance = None
    _config = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigLoader, cls).__new__(cls)
            cls._instance.load_config()
        return cls._instance

    @classmethod
    def from_dict(cls, data: Dict) -> "ConfigLoader":
        """Create a ConfigLoader from a raw dict (for testing or programmatic use)."""
        instance = object.__new__(cls)
        instance._config = data
        return instance

    @classmethod
    def reset(cls):
        """Reset the singleton (for test isolation)."""
        cls._instance = None
        cls._config = None

    def load_config(self, custom_path: str = None):
        config_path = custom_path if custom_path else "config/athanor_config.toml"

        # First-run convenience: if the real config is missing but the
        # checked-in example is present, copy it. This means a fresh clone
        # works without requiring the user to copy any files manually.
        if not custom_path and not os.path.exists(config_path):
            example_path = "config/athanor_config.toml.example"
            if os.path.exists(example_path):
                try:
                    import shutil
                    shutil.copyfile(example_path, config_path)
                except Exception:
                    pass  # fall through to fallback defaults below

        if os.path.exists(config_path):
            self._config = toml.load(config_path)
        else:
            # Fallback defaults
            self._config = {
                "llm": {"provider": "gemini", "model": "gemini/gemini-1.5-flash"},
                "search": {"provider": "duckduckgo"},
                "workflow": {"skip_profile": False, "skip_debate": False}
            }

    def save_config(self, filepath: str):
        """Save the current configuration state to a file."""
        if self._config:
            with open(filepath, 'w') as f:
                toml.dump(self._config, f)

    def set_stage_range(self, start: int = None, end: int = None):
        """Override the workflow stages programmatically."""
        if "workflow" not in self._config:
            self._config["workflow"] = {}
        if start is not None:
             self._config["workflow"]["start_stage"] = start
        if end is not None:
             self._config["workflow"]["end_stage"] = end

    @property
    def start_stage(self) -> int:
        return self._config.get("workflow", {}).get("start_stage", 1)

    @start_stage.setter
    def start_stage(self, value: int):
        if "workflow" not in self._config:
            self._config["workflow"] = {}
        self._config["workflow"]["start_stage"] = value

    @property
    def end_stage(self) -> int:
        return self._config.get("workflow", {}).get("end_stage", 4)

    @end_stage.setter
    def end_stage(self, value: int):
        if "workflow" not in self._config:
            self._config["workflow"] = {}
        self._config["workflow"]["end_stage"] = value
    
    def set_verbosity_override(self, verbose: bool):
        """Allow CLI args to override config."""
        if "system" not in self._config:
            self._config["system"] = {}
        self._config["system"]["verbose"] = verbose

    @property
    def llm_provider(self) -> str:
        return self._config.get("llm", {}).get("provider", "gemini")

    @property
    def llm_model(self) -> str:
        # Check primary_model first, fallback to "model" for legacy, then default
        return self._config.get("llm", {}).get("primary_model", self._config.get("llm", {}).get("model", "gemini/gemini-1.5-flash"))

    @property
    def antagonist_model(self) -> str:
        algo = self._config.get("llm", {}).get("antagonist_model", "")
        if not algo:
            return self.llm_model
        return algo

    @property
    def search_provider(self) -> str:
        return self._config.get("search", {}).get("provider", "duckduckgo")

        
    @property
    def human_in_the_loop(self) -> bool:
        return self._config.get("workflow", {}).get("human_in_the_loop", True)

    @property
    def initial_spark(self) -> str:
        return self._config.get("workflow", {}).get("initial_spark", "")
    @property
    def output_directory(self) -> str:
        return self._config.get("workflow", {}).get("output_directory", "")



    @property
    def planner_max_revisions(self) -> int:
        return self._config.get("planner", {}).get("max_revisions", 10)

    @property
    def debate_max_turns(self) -> int:
        # Each round consists of 3 agents: Proponent, Antagonist, Judge
        rounds = self._config.get("debate", {}).get("max_rounds", 3)
        return rounds * 3

    @property
    def planner_depth(self) -> int:
        # Check explicit legacy key first, else use search_depth
        if "planning_depth" in self._config.get("planner", {}):
            return self._config.get("planner", {}).get("planning_depth")
        return self.search_depth

    @property
    def search_depth(self) -> int:
        # 1 = Quick/Web, 2 = Deep/RAG
        return self._config.get("search", {}).get("search_depth", 1)


    # RAG Keys
    @property
    def openalex_email(self) -> str:
        return self._config.get("rag", {}).get("openalex_email", "")

    @property
    def semanticscholar_api_key(self) -> str:
        return self._config.get("rag", {}).get("semanticscholar_api_key", "")

    @property
    def huggingface_api_key(self) -> str:
        return self._config.get("rag", {}).get("huggingface_api_key", "")

    @property
    def github_api_key(self) -> str:
        return self._config.get("rag", {}).get("github_api_key", "")
        
    @property
    def google_search_api_key(self) -> str:
        return self._config.get("rag", {}).get("google_search_api_key", "")
        
    @property
    def zenodo_api_key(self) -> str:
        return self._config.get("rag", {}).get("zenodo_api_key", "")

    @property
    def rfp_target(self) -> str:
        return self._config.get("grant", {}).get("rfp_target", "")

    @property
    def funder_short_name(self) -> str:
        return self._config.get("grant", {}).get("funder_short_name", "generic")

    @property
    def exemplar_grant_path(self) -> str:
        return self._config.get("grant", {}).get("exemplar_grant_path", "")

    @property
    def pi_name(self) -> str:
        return self._config.get("pi", {}).get("name", "Scientist")

    @property
    def research_domain(self) -> str:
        """The PI's research domain (e.g. "ai_ml", "biomedical").

        Selects the domain-specific resource defaults baseline. Falls back
        to "general" if unset. See RESEARCH_DOMAINS in ingest/resources.py.
        """
        return self._config.get("pi", {}).get("research_domain", "general")
        
    @property
    def weaver_max_revisions(self) -> int:
        return self._config.get("grant", {}).get("max_revisions", self._config.get("grant", {}).get("max_loops", 3))

    @property
    def generate_demo_figures(self) -> bool:
        """When True, GrantWeaver calls ScienceOfficer to synthesize a
        demo preliminary-data figure. Default False — real grant runs
        should use the PI's actual prior work and preliminary results,
        not fabricated plots. Enable only for demos or when the PI has
        explicitly opted in."""
        return bool(self._config.get("grant", {}).get("generate_demo_figures", False))

    @property
    def verbose(self) -> bool:
        # Check system override first (from CLI args)
        if "system" in self._config and "verbose" in self._config["system"]:
            return self._config["system"]["verbose"]
        # Then check workflow/system config
        return self._config.get("system", {}).get("verbose", False)

    @property
    def ground_truth(self) -> str:
        return self._config.get("system", {}).get("ground_truth", "")

    @property
    def all_configs(self) -> Dict:
        """Returns the raw configuration dictionary."""
        return self._config

config = ConfigLoader()
