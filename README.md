> **This project is not maintained.** It is an early research prototype, released as-is.

# Athanor

Athanor is an AI-assisted research planning tool. It takes a research idea and produces a researcher profile, a stress-tested hypothesis, a structured research plan, and a draft grant proposal. It can also track execution of the plan and draft a paper from what was actually done.

## Design principles

- **Human in the loop.** The researcher approves or overrides the system's output at each stage.
- **Grounded in the researcher's real resources.** Plans and proposals are constrained by the researcher's actual expertise, team, and facilities.
- **Provenance.** Claims, numbers, and citations are traced to sources. Unsupported claims are flagged rather than filled in.

## Pipeline

| Stage | Name | What it does |
|---|---|---|
| 1 | Researcher Profile | Builds a profile from a CV, websites, ORCID, and publication records: expertise, resources, team, and publication history. |
| 2 | Hypothesis Debate | Runs an adversarial debate (proponent, critic, judge) on the hypothesis and scores it on novelty, plausibility, falsifiability, executability, and impact. The researcher accepts the refined hypothesis or keeps the original. |
| 3 | Research Plan | Decomposes the hypothesis into a task graph (DAG). Each task is classified as agent, human, or hybrid, and linked to real tools, datasets, and literature found via OpenAlex, Semantic Scholar, GitHub, and Hugging Face. |
| 4 | Grant Proposal | Drafts a proposal against a specific RFP. Specialist agents handle requirements extraction, methods, budget, impact, biosketches, and citations, followed by red-team compliance review and revision until the draft passes or a revision limit is reached. |
| 5 | Execution Tracking | Records progress on each plan step, deviations from the plan, and attached results. Athanor does not run experiments itself. |
| 6 | Paper Writing | Drafts a manuscript from the executed plan, deviation notes, and attached results. |

Outputs for each stage are saved as JSON, Markdown, and PDF under `projects/<project-name>/`.

## Installation

Requirements:

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- A Google Gemini API key
- Optional: API keys for Semantic Scholar, GitHub, and Hugging Face for deeper literature and tool search
- Optional: [Tectonic](https://tectonic-typesetting.github.io/) for LaTeX/PDF output

```bash
git clone https://github.com/pmsutter-jhu/athanor.git
cd athanor
uv sync
export GEMINI_API_KEY=your_key_here
```

## Usage

### Desktop app

```bash
uv run athanor
```

Opens the desktop app with a tab for each stage, file slots for CV, RFP, and exemplar grants, editable outputs, and per-stage settings. To run in a browser instead:

```bash
uv run athanor --web
```

### Command line

```bash
uv run athanor-cli -v                            # Start a new project
uv run athanor-cli --restart                     # Resume a saved project
uv run athanor-cli --list                        # List saved projects
uv run athanor-cli -v -c config/mock_test.toml   # Run with mock LLM responses, no API key needed
```

Options:

- `-v`, `--verbose`: verbose output
- `-r`, `--restart [ID]`: resume a saved project; omit the ID to pick interactively
- `-c`, `--config PATH`: use a specific config file
- `--list`: list saved projects

## Configuration

Copy `config/athanor_config.toml.example` to `config/athanor_config.toml` and edit it. The file sets both system behavior and the researcher profile.

- `[llm]`: `provider` (`gemini`, or `mock` for testing) and `primary_model` (e.g. `gemini-2.5-flash`)
- `[search]`: `search_depth` (`1` for web search only, `2` for OpenAlex, Semantic Scholar, and GitHub) and `provider` (`google` or `duckduckgo`)
- `[workflow]`: `start_stage` and `end_stage`, `human_in_the_loop`, optional `output_directory`, optional `initial_spark` (the starting research idea)
- `[grant]`: `rfp_target`, a URL or path to the RFP. If blank, a bundled mock RFP is used.
- `[pi]`, `[team]`, `[resources]`: the researcher profile

```toml
[pi]
name = "Your Name"
affiliation = "Your Institution"

[team]
members = ["Co-Investigator Name"]
```

## Project structure

```
athanor/
├── athanor_gui.py          # Desktop app
├── athanor_cli.py          # Command-line entry point
├── pyproject.toml
├── config/
│   ├── athanor_config.toml.example
│   └── mock_test.toml
└── src/athanor/
    ├── core/               # LLM gateway, config, state, pipeline orchestration
    ├── engines/            # One engine per stage
    ├── assistants/         # Report generation, document search, citation audit, figures
    ├── ingest/             # Profile, resource, grant, and funder ingestion
    └── data/               # Bundled mock RFP
```

## Citation

If you use Athanor in your work, please cite it using the metadata in `CITATION.cff`.

## License

Copyright 2026 Paul M. Sutter and Benjamin Wandelt.

Licensed under the Apache License, Version 2.0. See `LICENSE` and `NOTICE`.
