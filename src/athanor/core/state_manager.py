"""
ATHANOR State Manager.

This module handles the persistence layer for the ATHANOR project. It is responsible for:
- Saving `ProjectState` objects to JSON files on disk.
- Loading `ProjectState` objects by Project ID.
- Listing available projects in the data directory.

It ensures that the entire research state (Plan, Ledger, Hypothesis) can be reliably stored and retrieved.
"""
import json
import os
from .state import ProjectState
from .config_loader import config

import re

class StateManager:
    def __init__(self, data_dir: str = None):
        self.fixed_project_dir = None
        
        if data_dir is None:
             # Check config
             cfg_dir = config.output_directory
             if cfg_dir and cfg_dir.strip():
                 # "Specific Directory" Mode
                 self.fixed_project_dir = cfg_dir
                 self.data_dir = cfg_dir  # Fallback for internal refs
             else:
                 # Default Mode
                 self.data_dir = "projects"
        else:
             self.data_dir = data_dir

        # Ensure directory exists
        path_to_create = self.fixed_project_dir if self.fixed_project_dir else self.data_dir
        os.makedirs(path_to_create, exist_ok=True)
    
    def _generate_slug(self, state: ProjectState) -> str:
        """
        Generates a stable, descriptive slug for the project.
        Priority:
        1. state.project_slug (if already set)
        2. [PI Last Name]-[LLM 3-5 keywords]
        """
        if state.project_slug:
            return state.project_slug

        # 1. Determine Researcher Last Name
        name_part = "Scientist"
        raw_name = ""
        if state.researcher_profile and state.researcher_profile.name:
            raw_name = state.researcher_profile.name
        elif config.pi_name and config.pi_name != "Scientist":
            raw_name = config.pi_name
        
        if raw_name:
            name_parts = raw_name.strip().split()
            name_part = name_parts[-1] if name_parts else "Scientist"
        
        # 2. Determine Keyword Part from initial spark
        thought_slug = "setup"
        if state.initial_shower_thought:
            # TRY LLM FIRST
            try:
                from .llm_gateway import LLMGateway

                llm = LLMGateway.get_llm("ProjectNamer", force_grounding=False)
                prompt = (
                    "Provide a super-short (3-5 words) description of this research idea.\n"
                    f"IDEA: {state.initial_shower_thought}\n\n"
                    "Return ONLY the words, separated by spaces. No punctuation. "
                    "Be punchy and academic."
                )
                summary = llm.run_text(prompt).content
                summary = re.sub(r'[^a-zA-Z0-9\s]', '', summary)
                words = [w.strip() for w in summary.split() if len(w) > 1][:5]
                if words:
                    thought_slug = "-".join(words)
            except Exception:
                # FALLBACK to regex logic
                words = re.findall(r'\b\w+\b', state.initial_shower_thought.lower())
                ignore = {
                    'i', 'the', 'a', 'an', 'and', 'or', 'to', 'of', 'in', 'is', 'that', 
                    'we', 'can', 'with', 'for', 'on', 'it', 'my', 'is', 'at', 'by', 'from',
                    'suspect', 'think', 'believe'
                }
                keywords = [w for w in words if w not in ignore and len(w) > 2][:3]
                if keywords:
                    thought_slug = "-".join(keywords)
        
        # 3. Assemble
        slug = f"{name_part}-{thought_slug}"
        # Sanitize slug further
        slug = re.sub(r'[^a-z0-9\-]', '', slug.lower().replace(" ", "-"))
        
        return slug

    def stabilize_project_identity(self, state: ProjectState):
        """
        Determines and saves the final project_slug and project_name 
        BEFORE any artifacts are generated.
        """
        if not state.project_slug:
            state.project_slug = self._generate_slug(state)
        
        # If project name is default placeholder, update it
        if state.project_name == "New-Research-Idea":
            # Use keywords for a better human-readable name
            slug_parts = state.project_slug.split('-')
            if len(slug_parts) > 1:
                state.project_name = " ".join([p.capitalize() for p in slug_parts])
            else:
                state.project_name = state.project_slug.capitalize()
        
        # Ensure the directory exists
        project_dir = self.get_project_dir(state)
        os.makedirs(project_dir, exist_ok=True)
        return state.project_slug

    def get_project_dir(self, state: ProjectState) -> str:
        if self.fixed_project_dir:
            return self.fixed_project_dir
            
        # If slug exists, use it. If not, we are in a weird state, but _generate_slug is safe.
        slug = state.project_slug if state.project_slug else self._generate_slug(state)
        return os.path.join(self.data_dir, slug)

    def save_state(self, state: ProjectState, filename: str = None):
        project_dir = self.get_project_dir(state)
        os.makedirs(project_dir, exist_ok=True)
        
        # Always use state.json for the new structure
        # unless filename is explicitly provided AND is not just the legacy ID
        if not filename or filename == self._generate_slug(state) or filename == "state.json":
            target_filename = "state.json"
        else:
            # If it has .json, strip it for the internal filename if we are in a dir
            target_filename = os.path.basename(filename)
            if not target_filename.endswith('.json'):
                target_filename += '.json'

        filepath = os.path.join(project_dir, target_filename)

        with open(filepath, 'w') as f:
            f.write(state.model_dump_json(indent=2))
            
        # Also save a snapshot of the current configuration
        try:
            config_snapshot_path = os.path.join(project_dir, "config.toml")
            # Only save if not already there or overwrite?
            # Ideally we update it so it reflects the config that generated this state.
            config.save_config(config_snapshot_path)
        except Exception:
            # Don't fail the main save if config snapshot fails
            pass
            
        return filepath

    def load_state(self, project_id: str) -> ProjectState:
        # If running in Fixed Directory mode, ignore ID and load from that dir
        if self.fixed_project_dir:
             filepath = os.path.join(self.fixed_project_dir, "state.json")
             if not os.path.exists(filepath):
                  # If we can't find it, we can't load it.
                  # But maybe the user intends to create it? AthanorEngine handles creation.
                  raise FileNotFoundError(f"No existing project state found in configured output directory: {self.fixed_project_dir}")
             
             with open(filepath, 'r') as f:
                  data = json.load(f)
                  return ProjectState(**data)

        # Standard Mode: project_id is the slug
        project_dir = os.path.join(self.data_dir, project_id)
        
        # Priority 1: projects/{id}/state.json
        filepath = os.path.join(project_dir, "state.json")
        if not os.path.exists(filepath):
            # Priority 2: projects/{id}/{id} (Transitionary state)
            alt_path = os.path.join(project_dir, project_id)
            if os.path.exists(alt_path):
                filepath = alt_path
            else:
                # Fallback: check if it's an old-style file in ideas/
                legacy_path = os.path.join("ideas", project_id + ".json")
                if os.path.exists(legacy_path):
                    filepath = legacy_path
                else:
                    raise FileNotFoundError(f"Project state {project_id} not found at {filepath}, {alt_path}, or {legacy_path}")
        
        with open(filepath, 'r') as f:
            data = json.load(f)
            return ProjectState(**data)

    def list_projects(self):
        # Specific Directory Mode
        if self.fixed_project_dir:
            return [f"(Fixed) {self.fixed_project_dir}"]
            
        # List subdirectories in projects/
        if not os.path.exists(self.data_dir):
            return []
        
        valid_projects = []
        try:
            dirs = [d for d in os.listdir(self.data_dir) if os.path.isdir(os.path.join(self.data_dir, d))]
            # Only return those that have a state.json
            for d in dirs:
                if os.path.exists(os.path.join(self.data_dir, d, "state.json")):
                    valid_projects.append(d)
        except OSError:
            pass
        
        # Legacy support - ONLY if we are in the default "projects" directory
        # This prevents user A from seeing global 'ideas' if they are stuck in a weird path,
        # but mostly this cleans up the scope.
        if self.data_dir == "projects" and os.path.exists("ideas"):
            legacy_files = [f.replace('.json', '') for f in os.listdir("ideas") if f.endswith('.json')]
            valid_projects.extend(legacy_files)
            
        return sorted(list(set(valid_projects)), key=str.lower)

    def delete_project(self, project_id: str) -> None:
        """
        Delete a project directory and all its contents.
        Safe for both legacy single-file projects and new directory-based projects.
        """
        import shutil

        if self.fixed_project_dir:
            raise ValueError("Cannot delete project in Fixed Directory mode.")

        # Standard Mode
        project_dir = os.path.join(self.data_dir, project_id)
        
        # Check if it's a directory (New Style)
        if os.path.isdir(project_dir):
            shutil.rmtree(project_dir)
            return

        # Check for legacy file in ideas/
        legacy_path = os.path.join("ideas", project_id + ".json")
        if os.path.exists(legacy_path):
            os.remove(legacy_path)
            return
            
        # If neither found
        # We might silently fail or raise, but raising is better for UI feedback
        # raise FileNotFoundError(f"Project {project_id} not found to delete.")
        pass
