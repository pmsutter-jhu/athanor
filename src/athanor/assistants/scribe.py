"""
ATHANOR Scribe Assistant.

This module manages the generation of high-fidelity research artifacts, including 
Markdown summaries and LaTeX-compiled PDF reports. It provides specialized formatters 
for each stage of the research pipeline.
"""
from datetime import datetime
from typing import List, Any, Optional
import os
import subprocess
import tempfile
import shutil
from pylatex import Document, Section, Subsection, Command, Itemize, Enumerate, Description, Package, Table, Tabular, Center, Figure
from pylatex.utils import italic, bold, NoEscape
from fpdf import FPDF
from ..core.state import ProjectState, TaskType, GrantProposal, ProposalSection, ResearcherProfile
import re
from pydantic import BaseModel




class FormattingConfig(BaseModel):
    margin: str = "1in"
    paper_size: str = "a4paper"
    font: str = "charter"
    primary_color_rgb: tuple = (0, 51, 102) # RGB: primaryblue
    include_toc: bool = True
    header_text: str = "Research Report"

class CitationManager:
    def __init__(self):
        self.references = [] # List of dicts: {url, category, name}
        self.url_to_index = {} # url -> index (1-based)

    def process_text(self, text: str, fmt: str = "markdown") -> str:
        """
        Scans text for [Category: Name](URL) OR standard [Name](URL).
        Replaces with Name[^N] (Markdown) or Name [N] (PDF Text).
        Adds unique URLs to references.
        """
        if not text: return ""
        # Regex for [Category: Name](URL) OR just [Category: Name]
        # We make the URL part optional: (?:\((.*?)\))?
        # Non-greedy match for first part: \[((?:[^\[\]]|\[.*?\])*)\]
        pattern = r"\[((?:[^\[\]]|\[.*?\])*)\](?:\s*\((.*?)\))?"
        
        if fmt == "markdown":
            def replacer(match):
                link_text = match.group(1).strip()
                # Group 2 might be None if no URL provided
                url = match.group(2).strip() if match.group(2) else "No URL Linked"
                
                cat_match = re.match(r"^([a-zA-Z]+)\s*[:\-]\s*(.*)", link_text)
                if cat_match:
                    cat, name = cat_match.group(1), cat_match.group(2)
                else:
                    cat, name = "Ref", link_text
                
                if url not in self.url_to_index:
                    idx = len(self.references) + 1
                    self.references.append({"id": idx, "category": cat.capitalize(), "name": name, "url": url})
                    self.url_to_index[url] = idx
                else:
                    idx = self.url_to_index[url]
                return f"{name}[^{idx}]"
            return re.sub(pattern, replacer, text, flags=re.IGNORECASE)
            
        elif fmt == "latex":
            # Split and escape segments to avoid escaping the citations themselves
            from .scribe import LaTeXFormatter
            esc = LaTeXFormatter.escape
            
            # Pre-replacement of known Greek/Math chars to avoid font warnings
            # We map specific unicodes to LaTeX math commands or similar
            replacements = {
                "ν": r"$\nu$", "σ": r"$\sigma$", "γ": r"$\gamma$", 
                "μ": r"$\mu$", "π": r"$\pi$", "Ω": r"$\Omega$",
                "λ": r"$\lambda$", "θ": r"$\theta$", "Δ": r"$\Delta$",
                "✅": r"[PASS]", "❌": r"[FAIL]", "⚠️": r"[WARN]"
            }
            for char, repl in replacements.items():
                text = text.replace(char, repl)
            
            last_idx = 0
            res_parts = []
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                # Escape the text BEFORE the match
                res_parts.append(esc(text[last_idx:match.start()]))
                
                link_text = match.group(1).strip()
                url = match.group(2).strip() if match.group(2) else "No URL Linked"
                
                cat_match = re.match(r"^([a-zA-Z]+)\s*[:\-]\s*(.*)", link_text)
                if cat_match:
                    cat, name = cat_match.group(1), cat_match.group(2)
                else:
                    cat, name = "Ref", link_text
                
                if url not in self.url_to_index:
                    idx = len(self.references) + 1
                    self.references.append({"id": idx, "category": cat.capitalize(), "name": name, "url": url})
                    self.url_to_index[url] = idx
                else:
                    idx = self.url_to_index[url]
                
                # Just show superscript number in text, not the full paper name
                # Use math mode superscript with brackets for visibility: ^[1]
                res_parts.append(f"$^{{[{idx}]}}$")
                last_idx = match.end()
            
            # Escape the remaining text
            res_parts.append(esc(text[last_idx:]))
            return "".join(res_parts)
        else:
            # Text format
            def replacer(match):
                link_text = match.group(1).strip()
                url = match.group(2).strip() if match.group(2) else "No URL Linked"
                cat_match = re.match(r"^([a-zA-Z]+)\s*[:\-]\s*(.*)", link_text)
                name = cat_match.group(2) if cat_match else link_text
                
                if url not in self.url_to_index:
                    idx = len(self.references) + 1
                    self.references.append({"id": idx, "category": "Ref", "name": name, "url": url})
                    self.url_to_index[url] = idx
                else:
                    idx = self.url_to_index[url]
                return f"{name} [{idx}]"
            return re.sub(pattern, replacer, text, flags=re.IGNORECASE)

    def get_bibliography_markdown(self) -> str:
        if not self.references: return "No references cited."
        md = ""
        for ref in self.references:
            md += f"[^{ref['id']}]: **[{ref['category']}]** {ref['name']} - {ref['url']}\n"
        return md

    def get_bibliography_list(self) -> List[str]:
        if not self.references: return []
        res = []
        for ref in self.references:
            res.append(f"{ref['id']}. [{ref['category']}] {ref['name']} - {ref['url']}")
        return res

class ProfileFormatter:
    """Formatters for Stage 1: Researcher Profile."""
    @staticmethod
    def generate_markdown(state: ProjectState) -> str:
        p = state.researcher_profile
        if not p: return "# No Profile Data"
        
        md = f"# Research Team Profile: {state.project_name}\n\n"
        
        if state.team_summary:
            md += "## Team Overview\n"
            md += state.team_summary + "\n\n"
            
        # Helper to format a single profile
        def format_profile(prof: ResearcherProfile):
            out = f"## {prof.role}: {prof.name}\n"
            out += f"**Affiliation**: {prof.affiliation}\n\n"
            
            out += "### Domain Expertise\n"
            out += prof.domain_expertise + "\n\n"
            
            out += "### Available Resources\n"
            out += prof.available_resources + "\n\n"

            out += "### Publication History\n"
            out += prof.publication_history + "\n\n"

            out += "### Important Connections\n"
            out += prof.important_connections + "\n\n"
            
            out += "### Motivation & Grounding\n"
            out += prof.persona_motivation + "\n\n"
            return out

        # 1. Primary PI
        md += format_profile(p)

        # 2. Co-Investigators
        if state.co_investigators:
            md += "---\n\n"
            for coi in state.co_investigators:
                md += format_profile(coi)
                md += "\n"

        # 3. Global Team Members
        if p.team_members:
            md += "## Additional Team Members\n"
            for m in p.team_members:
                md += f"- {m}\n"
            md += "\n"
            
        return md

    @staticmethod
    def generate_pdf(state: ProjectState, filename: str, config: FormattingConfig = None, verbose: bool = False):
        if config is None: config = FormattingConfig(header_text="Research Team Profile")
        try:
            doc = Document(geometry_options={"margin": config.margin, "a4paper": True})
            # Rich LaTeX for Team Profile
            doc.packages.append(Package('xcolor'))
            doc.packages.append(Package('fancyhdr'))
            doc.packages.append(Package('tcolorbox'))
            doc.packages.append(Package(config.font))
            doc.preamble.append(NoEscape(r'\definecolor{primaryblue}{RGB}{0, 51, 102}'))
            
            with doc.create(Center()):
                doc.append(NoEscape(r'{\huge \bfseries \color{primaryblue} Research Team Profile} \\'))
                doc.append(NoEscape(r'{\Large ' + LaTeXFormatter.escape(state.project_name) + r'}'))
                
            if state.team_summary:
                doc.append(Section("Team Overview", numbering=False))
                doc.append(state.team_summary)
            
            def add_researcher_to_doc(prof: ResearcherProfile, is_pi: bool = False):
                with doc.create(Section(f"{prof.role}: {prof.name}", numbering=False)):
                    doc.append(italic(f"Affiliation: {prof.affiliation}"))
                    doc.append(NoEscape(r'\vspace{0.5em}'))
                    
                    doc.append(Subsection("Domain Expertise", numbering=False))
                    doc.append(prof.domain_expertise)
                    
                    doc.append(Subsection("Available Resources", numbering=False))
                    doc.append(prof.available_resources)
                    
                    doc.append(Subsection("Publication History", numbering=False))
                    doc.append(prof.publication_history)
                    
                    doc.append(Subsection("Important Connections", numbering=False))
                    doc.append(prof.important_connections)
                    
                    doc.append(Subsection("Motivation & Grounding", numbering=False))
                    doc.append(prof.persona_motivation)

            # 1. PI
            if state.researcher_profile:
                add_researcher_to_doc(state.researcher_profile, is_pi=True)

            # 2. Co-Is
            for coi in state.co_investigators:
                doc.append(NoEscape(r'\newpage'))
                add_researcher_to_doc(coi)

            # 3. Global Team Members
            if state.researcher_profile and state.researcher_profile.team_members:
                doc.append(Section("Additional Team Members", numbering=False))
                with doc.create(Itemize()) as itemize:
                    for m in state.researcher_profile.team_members:
                        itemize.add_item(m)
            
            LaTeXFormatter.compile_pdf(doc.dumps(), filename, verbose=verbose)
        except Exception as e:
            Scribe.log_status(f"    [Scribe] PDF unavailable, using Markdown.", level="info")
            md_content = ProfileFormatter.generate_markdown(state)
            ProposalFormatter.save_markdown(md_content, filename.replace(".pdf", ".md"))
            SimplePDFFormatter.generate_proposal_pdf("Researcher Profile", md_content, filename)

class AnalysisFormatter:
    """Formatters for Stage 2: Hypothesis Analysis."""
    @staticmethod
    def generate_markdown(state: ProjectState) -> str:
        sc = state.scorecard
        md = f"# Hypothesis Stress-Test: {state.project_name}\n\n"
        
        # Team Names
        team_names = []
        if state.researcher_profile: team_names.append(state.researcher_profile.name)
        for coi in state.co_investigators: team_names.append(coi.name)
        
        if team_names:
            md += "## Research Team\n"
            md += ", ".join(team_names) + "\n\n"

        if state.team_summary:
            md += "## Team Overview\n"
            md += state.team_summary + "\n\n"
            
        md += "## Initial Thought\n"
        md += f"> {state.initial_shower_thought}\n\n"
        
        if state.refined_hypothesis:
            md += "## Refined Hypothesis\n"
            md += f"**{state.refined_hypothesis}**\n\n"
            
        if sc:
            md += "## Scorecard & Verdict\n"
            md += f"**Recommendation**: {sc.recommendation}\n\n"
            
            if sc.verdict_summary:
                md += f"**Judge's Verdict**: {sc.verdict_summary}\n\n"

            md += "| Metric | Score | Rationale |\n"
            md += "| :--- | :--- | :--- |\n"
            md += f"| Novelty | {sc.novelty_score}/5 | {sc.novelty_rationale} |\n"
            md += f"| Plausibility | {sc.plausibility_score}/5 | {sc.plausibility_rationale} |\n"
            md += f"| Falsifiability | {sc.falsifiability_score}/5 | {sc.falsifiability_rationale} |\n"
            md += f"| Executability | {sc.executability_score}/5 | {sc.executability_rationale} |\n"
            md += f"| Impact | {sc.impact_score}/5 | {sc.impact_rationale} |\n"
            md += f"| Safety | {'PASS' if sc.is_safe else 'FAIL'} | {sc.safety_rationale} |\n\n"
            
            if sc.unresolved_issues:
                md += "## Unresolved Issues\n"
                for issue in sc.unresolved_issues:
                    md += f"- {issue}\n"
                md += "\n"
            else:
                md += "## Unresolved Issues\n"
                md += "None identified during stress-test.\n\n"
            
        return md

    @staticmethod
    def generate_json(state: ProjectState, filename: str):
        """Generates a focused JSON report for the Hypothesis Analysis stage."""
        team = []
        if state.researcher_profile: team.append(state.researcher_profile.name)
        for coi in state.co_investigators: team.append(coi.name)
        
        data = {
            "project_name": state.project_name,
            "investigators": team,
            "team_overview": state.team_summary,
            "initial_shower_thought": state.initial_shower_thought,
            "refined_hypothesis": state.refined_hypothesis,
            "scorecard": state.scorecard.dict() if state.scorecard else None,
            "unresolved_issues": state.scorecard.unresolved_issues if state.scorecard else []
        }
        ProposalFormatter.save_json(data, filename)

    @staticmethod
    def generate_pdf(state: ProjectState, filename: str, config: FormattingConfig = None, verbose: bool = False):
        if config is None: config = FormattingConfig(header_text="Hypothesis Analysis")
        cm = CitationManager()
        esc = LaTeXFormatter.escape
        
        try:
            doc = Document(geometry_options={"margin": config.margin, "a4paper": True})
            doc.packages.append(Package('xcolor'))
            doc.packages.append(Package('tcolorbox'))
            doc.packages.append(Package(config.font))
            doc.preamble.append(NoEscape(r'\definecolor{primaryblue}{RGB}{0, 51, 102}'))
            
            with doc.create(Center()):
                doc.append(NoEscape(r'{\huge \bfseries \color{primaryblue} Hypothesis Stress-Test} \\'))
                
                # Team Names
                team_names = []
                if state.researcher_profile: team_names.append(state.researcher_profile.name)
                for coi in state.co_investigators: team_names.append(coi.name)
                if team_names:
                    doc.append(NoEscape(r'{\large ' + esc(", ".join(team_names)) + r'}'))
            
            if state.team_summary:
                doc.append(Section("Team Overview", numbering=False))
                doc.append(NoEscape(cm.process_text(state.team_summary, fmt="latex")))
            
            doc.append(Section("Initial Thought", numbering=False))
            doc.append(NoEscape(r'\begin{quote}' + esc(state.initial_shower_thought) + r'\end{quote}'))
            
            if state.refined_hypothesis:
                doc.append(Section("Refined Hypothesis", numbering=False))
                doc.append(NoEscape(cm.process_text(state.refined_hypothesis, fmt="latex")))
            
            sc = state.scorecard
            if sc:
                doc.append(Section("Scorecard & Verdict", numbering=False))
                doc.append(bold(f"Recommendation: {sc.recommendation}"))
                doc.append(NoEscape(r'\vspace{1em}'))
                
                if sc.verdict_summary:
                    doc.append(NoEscape(r'\vspace{0.5em}'))
                    doc.append(NoEscape(r' \\ '))
                    doc.append(italic("Verdict Summary:"))
                    doc.append(NoEscape(r' \\ '))
                    doc.append(NoEscape(cm.process_text(sc.verdict_summary, fmt="latex")))
                    doc.append(NoEscape(r'\vspace{1em}'))
                else:
                    doc.append(NoEscape(r'\vspace{1em}'))
                
                with doc.create(Description()) as desc:
                    def add_score_item(name, score, rationale):
                         desc.add_item(name, NoEscape(f" ({score}/5): {cm.process_text(rationale, fmt='latex')}"))
                    
                    add_score_item("Novelty", sc.novelty_score, sc.novelty_rationale)
                    add_score_item("Plausibility", sc.plausibility_score, sc.plausibility_rationale)
                    add_score_item("Falsifiability", sc.falsifiability_score, sc.falsifiability_rationale)
                    add_score_item("Executability", sc.executability_score, sc.executability_rationale)
                    add_score_item("Impact", sc.impact_score, sc.impact_rationale)
                    
                    # Safety is PASS/FAIL
                    lat_safe_rat = NoEscape(cm.process_text(sc.safety_rationale, fmt="latex"))
                    desc.add_item("Safety", NoEscape(f" ({'PASS' if sc.is_safe else 'FAIL'}): {lat_safe_rat}"))

                if sc.unresolved_issues:
                    doc.append(Section("Unresolved Issues", numbering=False))
                    with doc.create(Itemize()) as itemize:
                        for issue in sc.unresolved_issues:
                            itemize.add_item(NoEscape(cm.process_text(issue, fmt="latex")))
                else:
                    doc.append(Section("Unresolved Issues", numbering=False))
                    doc.append("None identified during stress-test.")
            
            # Bibliography
            bib = cm.get_bibliography_list()
            if bib:
                doc.append(Section("References", numbering=False))
                with doc.create(Enumerate()) as enum:
                    for ref_str in bib:
                        # Strip existing "1. " prefix since Enumerate adds it
                        clean_ref = re.sub(r"^\d+\.\s*", "", ref_str)
                        enum.add_item(esc(clean_ref))

            LaTeXFormatter.compile_pdf(doc.dumps(), filename, verbose=verbose)
        except Exception as e:
            Scribe.log_status(f"    [Scribe] PDF unavailable, using Markdown.", level="info")
            md_content = AnalysisFormatter.generate_markdown(state)
            ProposalFormatter.save_markdown(md_content, filename.replace(".pdf", ".md"))
            SimplePDFFormatter.generate_proposal_pdf("Hypothesis Analysis", md_content, filename)

class TopologySorter:
    """Sorts tasks based on their dependencies."""
    @staticmethod
    def sort_tasks(tasks: List[Any]) -> List[Any]:
        if not tasks: return []
        
        # Build graph
        node_map = {t.id: t for t in tasks}
        # In-degree count
        in_degree = {t.id: 0 for t in tasks}
        # Adjacency list
        adj = {t.id: [] for t in tasks}
        
        for t in tasks:
            for dep_id in t.dependencies:
                if dep_id in node_map:
                    adj[dep_id].append(t.id)
                    in_degree[t.id] += 1
        
        # Queue for nodes with in-degree 0
        from collections import deque
        queue = deque([t.id for t in tasks if in_degree[t.id] == 0])
        
        sorted_ids = []
        while queue:
            # To keep original relative order for same-level tasks, we could use a priority queue or just sort the queue
            # But standard topo sort is enough for the rule "not listed until prerequisites are"
            u = queue.popleft()
            sorted_ids.append(u)
            for v in adj[u]:
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    queue.append(v)
        
        # In case of cycles or missing deps, just append remaining tasks to avoid data loss
        seen = set(sorted_ids)
        result = [node_map[tid] for tid in sorted_ids]
        for t in tasks:
            if t.id not in seen:
                result.append(t)
        
        return result

class SimplePDFFormatter:
    """Basic FPDF-based PDF generator as a fallback when LaTeX is missing."""
    
    @staticmethod
    def generate_plan_pdf(state: ProjectState, filename: str):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        
        # Title
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, f"Research Proposal: {state.project_name}", ln=True, align='C')
        pdf.ln(5)
        
        # Info
        pdf.set_font("Arial", '', 10)
        pdf.cell(0, 6, f"PI: {state.researcher_profile.name} ({state.researcher_profile.affiliation})", ln=True)
        pdf.cell(0, 6, f"Date: {datetime.now().strftime('%Y-%m-%d')}", ln=True)
        pdf.ln(10)
        
        # Tasks (Sorted)
        sorted_tasks = TopologySorter.sort_tasks(state.tasks)
        for i, task in enumerate(sorted_tasks, 1):
            pdf.set_font("Arial", 'B', 12)
            pdf.cell(0, 10, f"Step {i}: {task.name}", ln=True)
            pdf.set_font("Arial", '', 10)
            
            # Use multi_cell for wrapping text
            # Sanitize text for CP1252 (Basic PDF encoding)
            def safe_txt(s):
                if not s: return ""
                return s.encode('latin-1', 'replace').decode('latin-1')

            pdf.multi_cell(0, 6, safe_txt(f"Type: {task.task_type.value} | Assigned To: {task.assigned_to}"))
            pdf.ln(2)
            pdf.multi_cell(0, 6, safe_txt(f"Description: {task.description}"))
            pdf.ln(2)
            if task.justification:
                pdf.set_font("Arial", 'I', 9)
                pdf.multi_cell(0, 5, safe_txt(f"Rationale: {task.justification}"))
                pdf.set_font("Arial", '', 10)
            pdf.ln(5)
            
        pdf.output(filename)

    @staticmethod
    def generate_proposal_pdf(title: str, content: str, filename: str):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, title.encode('latin-1', 'replace').decode('latin-1'), ln=True, align='C')
        pdf.ln(10)
        
        pdf.set_font("Arial", '', 11)
        # Naive markdown strip/format
        clean_content = content.replace("### ", "== ").replace("## ", "= ").replace("# ", "TITLE: ")
        pdf.multi_cell(0, 6, clean_content.encode('latin-1', 'replace').decode('latin-1'))
        
        pdf.output(filename)

class PlanFormatter:
    @staticmethod
    def clean_summary_prefix(text: str) -> str:
        """Removes redundant prefixes like 'Input is...'"""
        if not text: return ""
        # Common prefixes to strip (case insensitive)
        prefixes = ["input is", "the input is", "input:", "output is", "the output is", "output:", "inputs are", "outputs are"]
        lower_text = text.lower()
        for p in prefixes:
            if lower_text.strip().startswith(p):
                # Find where the prefix ends (ignoring case)
                prefix_len = len(p)
                # Check actual start index in case of leading whitespace
                start_idx = lower_text.find(p)
                remainder = text[start_idx + prefix_len:].strip()
                # Remove leading colon or punctuation if present
                if remainder.startswith(":") or remainder.startswith("-"):
                    remainder = remainder[1:].strip()
                return remainder
        return text

    @staticmethod
    def format_as_bullets(text: str) -> List[str]:
        """Converts text with potential * or - bullets into a clean list of strings."""
        if not text: return []
        lines = text.split('\n')
        clean_lines = []
        for line in lines:
            line = line.strip()
            # Strip common bullet markers
            if line.startswith('*') or line.startswith('-') or line.startswith('•'):
                line = line[1:].strip()
            if line:
                clean_lines.append(line)
        return clean_lines

    @staticmethod
    def format_as_paragraph(text: str) -> str:
        """Converts bulleted text into a single paragraph."""
        if not text: return ""
        import re
        # Remove bullet markers
        clean = re.sub(r'^\s*[-*•]\s*', '', text, flags=re.MULTILINE)
        # Replace newlines with spaces
        clean = clean.replace('\n', ' ')
        # Normalize whitespace and commas
        clean = re.sub(r'\s+', ' ', clean).strip()
        clean = re.sub(r',\s*,', ',', clean)
        clean = re.sub(r'^,\s*', '', clean)
        return clean

    @staticmethod
    def generate_json(state: ProjectState) -> List[dict]:
        """Generates a clean JSON representation of the research plan."""
        plan_data = []  # Initialize the list
        sorted_tasks = TopologySorter.sort_tasks(state.tasks)
        dep_map = {t.id: f"Step {j+1}" for j, t in enumerate(sorted_tasks)}
        
        for i, task in enumerate(sorted_tasks, 1):
            prereqs = [dep_map[d] for d in task.dependencies if d in dep_map]
            if not prereqs:
                prereqs = ["None"]
                
            node = {
                "step": i,
                "name": task.name,
                "type": task.task_type.value,
                "assigned_to": task.assigned_to,
                "description": task.description,
                "prerequisites": prereqs,
                "input": PlanFormatter.clean_summary_prefix(task.input_summary or task.input_requirements or ""),
                "output": PlanFormatter.clean_summary_prefix(task.output_summary or task.expected_output or ""),
                "workflow": task.suggested_workflow.split("\n") if task.suggested_workflow else ["None"],
                "justification": task.justification
            }
            plan_data.append(node)
        return plan_data

    @staticmethod
    def generate_markdown(state: ProjectState) -> str:
        """Generates a beautifully formatted Markdown research proposal."""
        cm = CitationManager()
        sorted_tasks = TopologySorter.sort_tasks(state.tasks)
        
        # Pre-process text fields? Or process as we build?
        # As we build is safer to ensure order if we wanted sequential numbering, 
        # but numbering is global based on encounter order.
        
        md = f"# Research Proposal: {state.project_name}\n\n"
        
        # Header Info
        md += f"**Principal Investigator:** {state.researcher_profile.name}  \n"
        md += f"**Affiliation:** {state.researcher_profile.affiliation}  \n"
        md += f"**Date:** {datetime.now().strftime('%B %d, %Y')}  \n"
        md += "---\n\n"
        
        # Profile Details (Paragraph Format)
        md += "## 0. Research Team Profile\n\n"
        
        if state.researcher_profile.domain_expertise:
            txt = cm.process_text(state.researcher_profile.domain_expertise)
            md += f"**Domain Expertise:** {PlanFormatter.format_as_paragraph(txt)}\n\n"
        
        if state.researcher_profile.team_members:
            md += f"**Team:** {', '.join(state.researcher_profile.team_members)}\n\n"
            
        if state.researcher_profile.available_resources:
            txt = cm.process_text(state.researcher_profile.available_resources)
            md += f"**Available Resources:** {PlanFormatter.format_as_paragraph(txt)}\n\n"
        md += "---\n\n"
        
        # Hypothesis (Evolution)
        md += "## 1. Research Hypothesis\n\n"
        
        md += "### 1.1 Original Prompt\n"
        md += f"> {cm.process_text(state.initial_shower_thought)}\n\n"
        
        md += "### 1.2 Refined Hypothesis\n"
        hyp = state.refined_hypothesis if state.refined_hypothesis else state.initial_shower_thought
        md += f"> {cm.process_text(hyp)}\n\n"
        
        # Abstract (Swapped)
        if state.project_abstract:
            md += "## 2. Abstract\n\n"
            md += f"{cm.process_text(state.project_abstract)}\n\n"

        # Scorecard
        if state.scorecard:
            sc = state.scorecard
            md += "### Strengths & Weaknesses (Scorecard) ⚖️\n"
            md += f"**Recommendation:** `{sc.recommendation}`\n"
            md += f"**Novelty:** {sc.novelty_score}/5 - {cm.process_text(sc.novelty_rationale)}\n"
            md += f"**Plausibility:** {sc.plausibility_score}/5 - {cm.process_text(sc.plausibility_rationale)}\n"
            md += f"**Falsifiable:** {sc.falsifiability_score}/5 - {cm.process_text(sc.falsifiability_rationale)}\n"
            md += f"**Executability:** {sc.executability_score}/5 - {cm.process_text(sc.executability_rationale)}\n"
            md += f"**Impact:** {sc.impact_score}/5 - {cm.process_text(sc.impact_rationale)}\n"
            md += f"**Safety:** {'✅' if sc.is_safe else '❌'} - {cm.process_text(sc.safety_rationale)}\n\n"
        
        # Methodology
        md += "## 3. Research Methodology Plan\n\n"
        
        for i, task in enumerate(sorted_tasks, 1):
            icon = "🤖" if task.task_type == TaskType.AGENT else ("👤" if task.task_type == TaskType.HUMAN else "🤝")
            typ_str = task.task_type.value.capitalize()
            if task.task_type != TaskType.HUMAN and task.assigned_to:
                 clean_assign = task.assigned_to.replace("AGENT", "").replace("Agent", "").strip(" ()")
                 if clean_assign:
                     typ_str += f" ({clean_assign})"
            
            md += f"### Step {i}: {task.name} {icon}\n"
            md += f"- **Type:** `{typ_str}`\n"
            
            # Dependencies (Readable)
            dep_map = {t.id: j+1 for j, t in enumerate(sorted_tasks)}
            if task.dependencies:
                readable_deps = [f"Step {dep_map[d]}" for d in task.dependencies if d in dep_map]
                if readable_deps:
                    md += f"- **Prerequisites:**  \n{', '.join(readable_deps)}\n"
                else:
                    md += "- **Prerequisites:**  \nNone\n"
            else:
                md += "- **Prerequisites:**  \nNone\n"

            md += f"\n**Description:**  \n{cm.process_text(task.description)}\n\n"
            
            if task.input_requirements:
                raw_sum = task.input_summary or task.input_requirements
                md += f"**📥 Input:** {PlanFormatter.clean_summary_prefix(raw_sum)}\n"
            
            if task.expected_output:
                raw_out = task.output_summary or task.expected_output
                md += f"**📤 Output:** {PlanFormatter.clean_summary_prefix(raw_out)}\n"
                
            if task.suggested_workflow:
                # Format as bullet list
                wf_lines = task.suggested_workflow.split('\n')
                wf_bullets = "\n".join([f"{cm.process_text(line.strip())}\n" for line in wf_lines if line.strip()])
                md += f"**⚙️ Workflow Summary:**  \n{wf_bullets}\n"
                
            # Justification is where most citations live
            if task.justification:
                md += f"**Justification:** {cm.process_text(task.justification)}\n"
                
            md += "\n---\n"
        
        # Alternatives
        md += "## 4. Alternatives Considered\n\n"
        if state.rejected_alternatives:
            for alt in state.rejected_alternatives:
                 md += f"- {cm.process_text(alt)}\n"
            md += "\n"
        else:
            md += "No alternatives considered.\n\n"

        # References
        md += "## 5. References\n\n"
        md += cm.get_bibliography_markdown()
            
        md += "\n---\n*Generated by ATHANOR*"
        return md

    @staticmethod
    def generate_pdf(state: ProjectState, filename: str, config: FormattingConfig = None, verbose: bool = False):
        """Generates a professional PDF research proposal using LaTeX and Tectonic, with FPDF fallback."""
        try:
            latex_content = LaTeXFormatter.generate_plan_latex(state, config=config)
            LaTeXFormatter.compile_pdf(latex_content, filename, verbose=verbose)
        except Exception as e:
            Scribe.log_status(f"    [Scribe] PDF unavailable, using Markdown.", level="info")
            md = PlanFormatter.generate_markdown(state)
            ProposalFormatter.save_markdown(md, filename.replace(".pdf", ".md"))
            SimplePDFFormatter.generate_plan_pdf(state, filename)

class LaTeXFormatter:
    """Handles LaTeX generation and Tectonic compilation."""
    
    @staticmethod
    def escape(text: str) -> str:
        """Escapes LaTeX special characters using pylatex logic if possible, else manual."""
        if not text: return ""
        # PyLaTeX doesn't have a standalone 'escape' for strings easily accessible without a class context 
        # that handles EVERYTHING, but it has escape_latex in pylatex.utils
        from pylatex.utils import escape_latex
        res = escape_latex(text)
        # Handle smart quotes and other common symbols not handled by escape_latex
        res = res.replace('“', "``").replace('”', "''").replace('’', "'").replace('—', "---").replace('–', "--")
        return res

    @staticmethod
    def compile_pdf(latex_code: str, output_path: str, verbose: bool = False):
        """Writes LaTeX to a temp file and compiles with tectonic."""
        out_dir = os.path.dirname(output_path)
        base_name = os.path.basename(output_path).replace(".pdf", "")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy figures if they exist in the destination directory
            figures_src = os.path.join(out_dir, "figures")
            if os.path.exists(figures_src):
                shutil.copytree(figures_src, os.path.join(tmpdir, "figures"), dirs_exist_ok=True)
            
            tex_file = os.path.join(tmpdir, f"{base_name}.tex")
            with open(tex_file, 'w', encoding='utf-8') as f:
                f.write(latex_code)
            
            try:
                # Find tectonic
                tectonic_bin = shutil.which("tectonic")
                if not tectonic_bin:
                    # Extended Search Paths
                    common_paths = [
                        "/usr/local/bin/tectonic",
                        "/usr/bin/tectonic",
                        "/bin/tectonic",
                        os.path.expanduser("~/.cargo/bin/tectonic"),
                    ]
                    
                    found = False
                    for p in common_paths:
                         if os.path.exists(p):
                             tectonic_bin = p
                             found = True
                             break
                    
                    if not found:
                         # Last ditch: try to see if it's in the PATH env var manually
                         Scribe.log_status(f"    [Debug] PATH: {os.environ.get('PATH')}", level="verbose")
                         raise FileNotFoundError("Tectonic executable not found in PATH or standard locations.")

                # Run tectonic
                Scribe.log_status(f"    [Scribe] Compiling LaTeX with {tectonic_bin}...", level="verbose")
                result = subprocess.run([tectonic_bin, tex_file], check=True, capture_output=True)
                
                pdf_file = os.path.join(tmpdir, f"{base_name}.pdf")
                if os.path.exists(pdf_file):
                    shutil.move(pdf_file, output_path)
                    
                    # ALWAYS save the .tex file for the user
                    tex_output_path = output_path.replace(".pdf", ".tex")
                    shutil.copy(tex_file, tex_output_path)
                    Scribe.log_status(f"    [Scribe] PDF and LaTeX saved: {output_path} | {tex_output_path}", level="verbose")

                    # Cleanup detritus in the output directory (if any leaked out of tmpdir, or if Tectonic put them there)
                    for ext in [".aux", ".log", ".out", ".fls", ".fdb_latexmk", ".synctex.gz"]:
                         junk = os.path.join(out_dir, base_name + ext)
                         if os.path.exists(junk):
                             try:
                                 os.remove(junk)
                             except:
                                 pass
                    # Also clean _latexmk file which Tectonic sometimes leaves?
                    # Tectonic shouldn't, but let's be safe.
                else:
                    Scribe.log_status(f"    [Scribe] Tectonic finished but {pdf_file} is missing.", level="error")
                    Scribe.log_status(f"    Output: {result.stdout.decode('utf-8')}", level="error")
                    raise FileNotFoundError(f"Tectonic failed to produce {pdf_file}")
            except subprocess.CalledProcessError as e:
                Scribe.log_status(f"    [Scribe] Tectonic failed with exit code {e.returncode}", level="error")
                Scribe.log_status(f"    Error: {e.stderr.decode('utf-8')}", level="error")
                # Fallback: keep the .tex file for debugging
                debug_tex = output_path.replace(".pdf", ".tex")
                shutil.copy(tex_file, debug_tex)
                Scribe.log_status(f"    [Scribe] Debug LaTeX file saved to: {debug_tex}", level="error")
                raise
            except Exception as e:
                Scribe.log_status(f"    [Scribe] LaTeX compilation unavailable: {e}", level="verbose")
                raise

    @staticmethod
    def generate_plan_latex(state: ProjectState, config: FormattingConfig = None) -> str:
        """Generates the full LaTeX source for a research plan using PyLaTeX."""
        if config is None: config = FormattingConfig(header_text="Research Proposal")
        cm = CitationManager()
        esc = LaTeXFormatter.escape
        
        doc = Document(geometry_options={"margin": config.margin, "a4paper": True})
        
        # Packages
        doc.packages.append(Package('xcolor'))
        doc.packages.append(Package('titlesec'))
        doc.packages.append(Package('enumitem'))
        doc.packages.append(Package('fancyhdr'))
        doc.packages.append(Package('tcolorbox'))
        doc.packages.append(Package(config.font))
        doc.packages.append(Package('microtype'))
        
        # Custom Commands
        rgb = config.primary_color_rgb
        doc.preamble.append(NoEscape(rf'\definecolor{{primaryblue}}{{RGB}}{{{rgb[0]}, {rgb[1]}, {rgb[2]}}}'))
        doc.preamble.append(NoEscape(r'\titleformat{\section}{\large\bfseries\color{primaryblue}}{}{0em}{}[\titlerule]'))
        doc.preamble.append(NoEscape(r'\titleformat{\subsection}{\normalsize\bfseries\color{primaryblue}}{}{0em}{}'))
        doc.preamble.append(NoEscape(r'\pagestyle{fancy}'))
        doc.preamble.append(NoEscape(r'\fancyhf{}'))
        doc.preamble.append(NoEscape(r'\rhead{\color{gray} ' + esc(config.header_text) + r': ' + esc(state.project_name) + '}'))
        doc.preamble.append(NoEscape(r'\lfoot{\color{gray} Generated by ATHANOR}'))
        doc.preamble.append(NoEscape(r'\rfoot{\thepage}'))

        # Title Section
        with doc.create(Center()):
            doc.append(NoEscape(r'{\huge \bfseries \color{primaryblue} Research Work Plan}'))
            doc.append(NoEscape(r'\\[0.5em]'))
            doc.append(NoEscape(r'{\Large ' + esc(state.project_name) + r'}'))
            doc.append(NoEscape(r'\\[1.5em]'))
            
            # Principal Investigator
            doc.append(bold('Principal Investigator:'))
            doc.append(NoEscape(r'\\ ' + esc(state.researcher_profile.name)))
            doc.append(NoEscape(r'\\[1em]'))
            
            # Co-Investigators
            if state.co_investigators:
                for coi in state.co_investigators:
                    doc.append(bold('Co-Investigator:'))
                    doc.append(NoEscape(r'\\ ' + f"{esc(coi.name)} ({esc(coi.affiliation)})"))
                    doc.append(NoEscape(r'\\[1em]'))

            doc.append(bold('Date:'))
            doc.append(NoEscape(r'\\ ' + datetime.now().strftime('%B %d, %Y')))
            
        doc.append(NoEscape(r'\vspace{1em}'))
        
        if state.researcher_profile.team_members:
            doc.append(italic(f"Team: {', '.join(state.researcher_profile.team_members)}"))
            doc.append(NoEscape(r'\\'))
        
        doc.append(NoEscape(r'\vspace{1em}'))
        
        # Section 0: Profile
        with doc.create(Section('0. Research Team Profile', numbering=False)):
            if state.team_summary:
                txt = cm.process_text(state.team_summary, fmt="latex")
                doc.append(NoEscape(PlanFormatter.format_as_paragraph(txt)))
            elif state.researcher_profile.domain_expertise:
                txt = cm.process_text(state.researcher_profile.domain_expertise, fmt="latex")
                doc.append(bold("Expertise: "))
                doc.append(NoEscape(PlanFormatter.format_as_paragraph(txt)))

        # Section 1: Hypothesis
        with doc.create(Section('1. Research Hypothesis', numbering=False)):
            # Original Prompt (Red Box)
            doc.append(bold("1.1 Original Prompt"))
            doc.append(NoEscape(r'\\[0.5em]'))
            doc.append(NoEscape(r'\begin{tcolorbox}[colback=red!5,colframe=red!75]'))
            doc.append(NoEscape(cm.process_text(state.initial_shower_thought, fmt="latex")))
            doc.append(NoEscape(r'\end{tcolorbox}'))
            
            doc.append(NoEscape(r'\vspace{1em}'))
            
            # Refined Hypothesis (Blue Box)
            doc.append(bold("1.2 Refined Hypothesis"))
            doc.append(NoEscape(r'\\[0.5em]'))
            hyp = state.refined_hypothesis if state.refined_hypothesis else state.initial_shower_thought
            doc.append(NoEscape(r'\begin{tcolorbox}[colback=blue!5,colframe=primaryblue!75]'))
            doc.append(NoEscape(cm.process_text(hyp, fmt="latex")))
            doc.append(NoEscape(r'\end{tcolorbox}'))

        # Section 2: Abstract
        if state.project_abstract:
            with doc.create(Section('2. Abstract', numbering=False)):
                doc.append(NoEscape(cm.process_text(state.project_abstract, fmt="latex")))

        # Scorecard
        if state.scorecard:
            sc = state.scorecard
            with doc.create(Subsection('Scorecard Verification', numbering=False)):
                with doc.create(Itemize()) as itemize:
                    itemize.add_item(NoEscape(bold("Recommendation: ") + " " + esc(sc.recommendation)))
                    itemize.add_item(NoEscape(bold("Novelty: ") + f" {sc.novelty_score}/5 - " + cm.process_text(sc.novelty_rationale, fmt="latex")))
                    itemize.add_item(NoEscape(bold("Plausibility: ") + f" {sc.plausibility_score}/5 - " + cm.process_text(sc.plausibility_rationale, fmt="latex")))
                    itemize.add_item(NoEscape(bold("Falsifiability: ") + f" {sc.falsifiability_score}/5 - " + cm.process_text(sc.falsifiability_rationale, fmt="latex")))
                    itemize.add_item(NoEscape(bold("Executability: ") + f" {sc.executability_score}/5 - " + cm.process_text(sc.executability_rationale, fmt="latex")))
                    itemize.add_item(NoEscape(bold("Impact: ") + f" {sc.impact_score}/5 - " + cm.process_text(sc.impact_rationale, fmt="latex")))
                    safety_sym = "✅ Pass" if sc.is_safe else "❌ Fail"
                    itemize.add_item(NoEscape(bold("Safety: ") + f" {safety_sym} - " + cm.process_text(sc.safety_rationale, fmt="latex")))

        # Section 3: Plan
        with doc.create(Section('3. Research Methodology Plan', numbering=False)):
            sorted_tasks = TopologySorter.sort_tasks(state.tasks)
            for i, task in enumerate(sorted_tasks, 1):
                typ_str = task.task_type.value.upper()
                if "HUMAN" in typ_str: typ_str = "HUMAN" # Fix "HUMAN PROTOCOL"
                
                if task.task_type != TaskType.HUMAN and task.assigned_to:
                     clean_assign = task.assigned_to.replace("AGENT", "").replace("Agent", "").strip(" ()")
                     if clean_assign: typ_str += f" ({clean_assign})"
                
                # Prerequisites Mapping
                dep_map = {t.id: j+1 for j, t in enumerate(sorted_tasks)}

                with doc.create(Subsection(f"Step {i}: {task.name}", numbering=False)):
                    doc.append(NoEscape(r'\noindent '))
                    doc.append(bold("Type: "))
                    doc.append(NoEscape(r'\texttt{' + esc(typ_str) + r'} \\'))
                    
                    doc.append(NoEscape(r'\noindent '))
                    doc.append(bold("Prerequisites: "))
                    if task.dependencies:
                        readable_deps = [f"Step {dep_map[d]}" for d in task.dependencies if d in dep_map]
                        if readable_deps:
                            doc.append(NoEscape(esc(", ".join(readable_deps)) + r" \\"))
                        else: doc.append(NoEscape(r"None \\"))
                    else: doc.append(NoEscape(r"None \\"))
                    
                    doc.append(NoEscape(r'\noindent '))
                    doc.append(bold("Inputs: "))
                    doc.append(NoEscape(esc(PlanFormatter.clean_summary_prefix(task.input_summary or task.input_requirements or "None")) + r" \\"))
                    
                    doc.append(NoEscape(r'\noindent '))
                    doc.append(bold("Outputs: "))
                    doc.append(NoEscape(esc(PlanFormatter.clean_summary_prefix(task.output_summary or task.expected_output or "None")) + r" \\"))
                    
                    doc.append(NoEscape(r'\noindent '))
                    doc.append(bold("Description: "))
                    doc.append(NoEscape(r'\\ ')) # Ensure description starts on next line or is at least separated
                    doc.append(NoEscape(cm.process_text(task.description, fmt="latex")))
                    doc.append(NoEscape(r' \\[0.5em]')) # Add some space after description
                    
                    if task.suggested_workflow:
                        doc.append(NoEscape(r'\vspace{0.5em}\noindent '))
                        doc.append(bold("Workflow Summary:"))
                        #doc.append(NoEscape(r'\\'))
                        
                        # Robust splitting for list conversion
                        import re
                        width_text = task.suggested_workflow
                        # If simple newline split yields 1 line but we see numbering (e.g. "1. Step one 2. Step two"), force split
                        if len(width_text.split('\n')) < 2 and re.search(r'\d+\.\s', width_text):
                             # Split by Numbering "1. ", "2. " etc.
                             items = re.split(r'\s*(?=\d+\.\s)', width_text)
                        else:
                             items = width_text.split('\n')

                        with doc.create(Itemize()) as itemize:
                            for line in items:
                                clean_line = line.strip()
                                # Remove leading numbers if we are putting them into bullets (optional, but cleaner)
                                clean_line = re.sub(r'^\d+\.\s*', '', clean_line)
                                if clean_line: 
                                    itemize.add_item(NoEscape(cm.process_text(clean_line, fmt="latex")))
                    
                    if task.justification:
                        doc.append(NoEscape(r'\noindent\textit{Justification: ' + cm.process_text(task.justification, fmt="latex") + '}'))
                    
                    doc.append(NoEscape(r'\vspace{1em}'))

        # Alternatives
        with doc.create(Section('4. Alternatives Considered', numbering=False)):
            if state.rejected_alternatives:
                with doc.create(Itemize()) as itemize:
                    for alt in state.rejected_alternatives:
                        itemize.add_item(NoEscape(cm.process_text(alt, fmt="latex")))
            else:
                doc.append("No alternatives considered.")
        
        # Unresolved Issues (Red Team)
        if state.red_team_issues:
            with doc.create(Section('5. Summary of Evaluation', numbering=False)):
                # We iterate through issues (likely just one after pruning)
                import json
                for issue in state.red_team_issues:
                     # issue is dict {stage, issue, details}
                     # issue['issue'] usually contains "Round X Review (Converged)" or "Plan failed..."
                     header = issue.get('issue', 'Evaluation')
                     stage_info = issue.get('stage', '')
                     
                     # Determine Convergence Status
                     is_converged = "Converged" in header or "Success" in header
                     status_color = "ForestGreen" if is_converged else "BrickRed" # Requires xcolor package usually, or just bold text
                     status_text = "CONVERGED" if is_converged else "NOT CONVERGED"
                     
                     raw_details = str(issue.get('details', '')).strip()
                     
                     with doc.create(Subsection(f"Evaluation: {header}", numbering=False)):
                         
                         txt_status = r"\textcolor{" + ("black" if is_converged else "black") + r"}{\textbf{" + status_text + r"}}"
                         
                         doc.append(bold("Convergence Status: "))
                         doc.append(NoEscape(status_text + r"\\"))
                         doc.append(NoEscape(r"\vspace{0.5em}"))

                         try:
                             # Regex to find the LAST valid JSON object block
                             import re
                             # Look for { ... "summary" ... }
                             json_candidates = re.findall(r'\{[^{}]*"summary"[^{}]*\}', raw_details, re.DOTALL)
                             
                             if not json_candidates:
                                 match = re.search(r'\{.*"summary".*\}', raw_details, re.DOTALL)
                                 if match: json_candidates = [match.group(0)]
                             
                             data = None
                             if json_candidates:
                                 candidate = json_candidates[-1].replace("```json", "").replace("```", "").strip()
                                 try: data = json.loads(candidate)
                                 except: pass

                             if not data:
                                 clean_json = raw_details.replace("```json", "").replace("```", "").strip()
                                 s = clean_json.find('{')
                                 e = clean_json.rfind('}')
                                 if s != -1 and e != -1: data = json.loads(clean_json[s:e+1])

                             if isinstance(data, dict):
                                 # 1. Judgement Summary
                                 if "summary" in data and data['summary']:
                                     doc.append(bold("Judgement Summary:"))
                                     doc.append(NoEscape(r"\\ " + cm.process_text(data['summary'], fmt="latex") + r"\\"))
                                     doc.append(NoEscape(r"\vspace{0.5em}"))
                                 
                                 # 2. Critical Issues
                                 if "critical_issues" in data and data['critical_issues']:
                                     valid_crits = [str(c) for c in data['critical_issues'] if str(c).strip()]
                                     if valid_crits:
                                         doc.append(bold("Critical Issues:"))
                                         with doc.create(Itemize()) as itemize:
                                             for crit in valid_crits:
                                                 itemize.add_item(NoEscape(cm.process_text(crit, fmt="latex")))
                                 
                                 # 3. Recommendations
                                 if "recommendations" in data and data['recommendations']:
                                     valid_recs = [str(r) for r in data['recommendations'] if str(r).strip()]
                                     if valid_recs:
                                         doc.append(bold("Recommendations:"))
                                         with doc.create(Itemize()) as itemize:
                                             for rec in valid_recs:
                                                 itemize.add_item(NoEscape(cm.process_text(rec, fmt="latex")))
                                                     
                             elif data:
                                 # JSON but not dict
                                 doc.append(NoEscape(cm.process_text(str(data)[:800], fmt="latex")))
                             
                         except Exception as e:
                             # Fallback
                             if len(raw_details) > 20:
                                 clean_raw = re.sub(r'```.*?```', '', raw_details, flags=re.DOTALL)[:800]
                                 doc.append(NoEscape(cm.process_text(clean_raw, fmt="latex")))
                         
                         doc.append(NoEscape(r'\vspace{1em}'))

        # References
        refs = cm.get_bibliography_list()
        if refs:
            with doc.create(Section('6. References', numbering=False)):
                with doc.create(Enumerate()) as enum:  # Use numbered list to match citations
                    for ref in refs:
                        clean_ref = re.sub(r"^\d+\.\s*", "", ref)
                        enum.add_item(NoEscape(esc(clean_ref)))

        return doc.dumps()

    @staticmethod
    def generate_proposal_latex(proposal_data: dict, pi_info: dict = None, config: FormattingConfig = None, base_dir: str = None) -> str:
        """Generates LaTeX for a generic grant proposal using structured JSON/dict data."""
        from datetime import datetime
        if config is None: config = FormattingConfig(header_text="Grant Proposal")
        
        # If base_dir not provided, use current directory
        if base_dir is None:
            base_dir = os.getcwd()
        
        cm = CitationManager()
        esc = LaTeXFormatter.escape
        
        title = proposal_data.get("rfp_title", "Grant Proposal")
        pi_name = pi_info.get("name") if pi_info else None
        pi_aff = pi_info.get("affiliation") if pi_info else None
        
        doc = Document(geometry_options={"margin": config.margin, "a4paper": True})
        
        # Packages
        doc.packages.append(Package('xcolor'))
        doc.packages.append(Package('titlesec'))
        doc.packages.append(Package('graphicx'))
        doc.packages.append(Package('float'))
        doc.packages.append(Package('enumitem'))
        doc.packages.append(Package('fancyhdr'))
        doc.packages.append(Package(config.font))
        doc.packages.append(Package('microtype'))
        doc.packages.append(Package('tcolorbox', options=['most']))
        doc.packages.append(Package('tocloft'))  # For TOC formatting
        
        # Custom Commands
        rgb = config.primary_color_rgb
        doc.preamble.append(NoEscape(rf'\definecolor{{primaryblue}}{{RGB}}{{{rgb[0]}, {rgb[1]}, {rgb[2]}}}'))
        doc.preamble.append(NoEscape(r'\titleformat{\section}{\large\bfseries\color{primaryblue}}{}{0em}{}[\titlerule]'))
        doc.preamble.append(NoEscape(r'\titleformat{\subsection}{\normalsize\bfseries\color{primaryblue}}{}{0em}{}'))
        
        # Table of Contents formatting - add spacing between entries
        doc.preamble.append(NoEscape(r'\setlength{\cftbeforesecskip}{8pt}'))  # Add vertical space between TOC entries
        doc.preamble.append(NoEscape(r'\renewcommand{\cftsecleader}{\cftdotfill{\cftdotsep}}'))  # Add dots to TOC
        
        # Fancy Headers
        doc.preamble.append(NoEscape(r'\pagestyle{fancy}'))
        doc.preamble.append(NoEscape(r'\fancyhf{}'))
        doc.preamble.append(NoEscape(r'\lhead{\small \color{gray} ' + esc(config.header_text) + r'}'))
        header_title = title[:40] + ("..." if len(title) > 40 else "")
        doc.preamble.append(NoEscape(r'\rhead{\small \color{gray} ' + esc(header_title) + r'}'))
        doc.preamble.append(NoEscape(r'\cfoot{\thepage}'))
        doc.preamble.append(NoEscape(r'\renewcommand{\headrulewidth}{0.4pt}'))
        doc.preamble.append(NoEscape(r'\renewcommand{\footrulewidth}{0pt}'))

        # Title Section
        with doc.create(Center()):
            doc.append(NoEscape(r'\vspace*{1in}'))
            doc.append(NoEscape(r'{\huge \bfseries \color{primaryblue} ' + esc(title) + r'} \\'))
            doc.append(NoEscape(r'\vspace{0.5in}'))
            doc.append(NoEscape(r'{\Large \bfseries Research Grant Proposal} \\'))
            doc.append(NoEscape(r'\vspace{0.5in}'))
            if pi_name:
                doc.append(NoEscape(r'{\large \textbf{Principal Investigator:} ' + esc(pi_name) + r'} \\'))
            if pi_aff:
                doc.append(NoEscape(r'{\large \textbf{Affiliation:} ' + esc(pi_aff) + r'} \\'))
            doc.append(NoEscape(r'\vspace{1in}'))
            doc.append(NoEscape(r'{\large \color{gray} Generated by ATHANOR} \\'))
            doc.append(NoEscape(r'{\color{gray} Date: ' + esc(datetime.now().strftime('%B %d, %Y')) + r'}'))
            doc.append(NoEscape(r'\vfill'))
            doc.append(NoEscape(r'\newpage'))

        doc.append(NoEscape(r'\sloppy')) # Help with overfull boxes
        if config.include_toc:
            doc.append(NoEscape(r'\tableofcontents'))
            doc.append(NoEscape(r'\newpage'))
        
        sections = proposal_data.get("sections", [])
        
        # Filter out LLM-generated "Table of Contents" section - we use LaTeX's auto-generated TOC instead
        sections = [s for s in sections if s.get("title", "").lower() not in ["table of contents", "contents"]]
        
        if not sections:
            doc.append(NoEscape(r'\section*{Proposal Narrative}'))
            doc.append("No structured proposal data found in JSON.")
        else:
            for section in sections:
                title_text = section.get("title", "Untitled")
                content_text = section.get("content", "")
                doc.append(Section(NoEscape(LaTeXFormatter._format_markdown_text(title_text, cm)), numbering=False))
                
                lines = content_text.split('\n')
                for line in lines:
                    line = line.strip()
                    if not line:
                        doc.append(NoEscape(r'\par '))
                        continue
                    
                    # Skip LLM junk (placeholders already handled by sectioning)
                    low_line = line.lower()
                    if "articles with" in low_line and "identifiers" in low_line: continue
                    if "wikidata" in low_line or "wikimedia" in low_line: continue
        
                    # Sub-Header Handling
                    header_match = re.match(r'^(#+)\s+(.*)$', line)
                    if header_match:
                        level = len(header_match.group(1))
                        h_text = LaTeXFormatter._format_markdown_text(header_match.group(2).strip(), cm)
                        if level == 1:
                            doc.append(Subsection(NoEscape(h_text), numbering=False))
                        elif level == 2:
                            doc.append(Command('subsubsection*', arguments=NoEscape(h_text)))
                        else:
                            doc.append(NoEscape(r'\vspace{0.5em}\noindent\textbf{' + h_text + r'}\par\noindent'))
                        continue
        
                    if line.startswith('- ') or line.startswith('* '):
                        h_text = LaTeXFormatter._format_markdown_text(line[2:].strip(), cm)
                        doc.append(NoEscape(r'\noindent $\bullet$ ' + h_text + r'\par'))
                        continue
                    elif line.startswith('> '):
                        doc.append(NoEscape(r'\begin{quote}'))
                        doc.append(NoEscape(LaTeXFormatter._format_markdown_text(line[2:].strip(), cm)))
                        doc.append(NoEscape(r'\end{quote}'))
                        continue
                    elif line.startswith('!['):
                        img_match = re.search(r'!\[(.*?)\]\s*\((.*?)\)', line)
                        if img_match:
                            caption = img_match.group(1)
                            img_path = img_match.group(2)
                            # Check if image exists relative to base_dir
                            full_img_path = os.path.join(base_dir, img_path) if not os.path.isabs(img_path) else img_path
                            if os.path.exists(full_img_path):
                                with doc.create(Figure(position='H')) as plot:
                                    plot.add_image(img_path, width=NoEscape(r'0.8\textwidth'))
                                    plot.add_caption(caption)
                            else:
                                doc.append(NoEscape(r'\begin{tcolorbox}[colback=orange!5!white,colframe=orange!75!black,title=Image Reference]'))
                                doc.append(NoEscape(f"Referenced image {esc(img_path)} was not found at {esc(full_img_path)}. Title: {esc(caption)}"))
                                doc.append(NoEscape(r'\end{tcolorbox}'))
                            continue
                        
                    txt = LaTeXFormatter._format_markdown_text(line, cm)
                    doc.append(NoEscape(txt + ' '))

        # Append Bibliography
        refs = cm.get_bibliography_list()
        if refs:
            doc.append(Section('References', numbering=False))
            with doc.create(Enumerate()) as enum:
                for ref in refs:
                    # Remove the number prefix since Enumerate adds it automatically
                    clean_ref = re.sub(r"^\d+\.\s*", "", ref)
                    enum.add_item(NoEscape(esc(clean_ref)))

        return doc.dumps()

    @staticmethod
    def _format_markdown_text(text: str, cm: CitationManager) -> str:
        """Helper to process citations, bold, and italics in a string."""
        txt = cm.process_text(text, fmt="latex")
        # Robust bold and italic (non-greedy)
        txt = re.sub(r"\*\*(.*?)\*\*", r"\\textbf{\1}", txt)
        # Italic: handle *word* but be careful not to match \*
        txt = re.sub(r"(?<!\\)\*(.*?)(?<!\\)\*", r"\\textit{\1}", txt)
        # Soft-escape any remaining single * that might cause LaTeX errors if interpreted as something else
        # (Though usually single * is fine in text)
        return txt

class ProposalFormatter:
    """Handles formatting and output for generic text-based proposals."""
    
    @staticmethod
    def save_json(data: dict, filename: str):
        import json
        from datetime import datetime
        def json_serial(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Type {type(obj)} not serializable")
            
        with open(filename, 'w') as f:
            json.dump(data, f, indent=4, default=json_serial)
            
    @staticmethod
    def save_markdown(content: str, filename: str):
        with open(filename, 'w') as f:
            f.write(content)

    @staticmethod
    def generate_pdf(proposal_data: dict, filename: str, pi_info: dict = None, config: FormattingConfig = None, verbose: bool = False):
        """Generates a professional PDF from structured grant JSON data."""
        if config is None: config = FormattingConfig(header_text="Grant Proposal")
        
        # Get base directory from filename for proper image path resolution
        base_dir = os.path.dirname(os.path.abspath(filename))
        
        try:
            latex_content = LaTeXFormatter.generate_proposal_latex(proposal_data, pi_info=pi_info, config=config, base_dir=base_dir)
            LaTeXFormatter.compile_pdf(latex_content, filename, verbose=verbose)
        except Exception as e:
            Scribe.log_status(f"    [Scribe] PDF unavailable, using Markdown.", level="info")
            md_path = filename.replace(".pdf", ".md")
            md_content = ProposalFormatter.generate_markdown(proposal_data)
            ProposalFormatter.save_markdown(md_content, md_path)
            
            title = proposal_data.get("rfp_title", "Grant Proposal")
            SimplePDFFormatter.generate_proposal_pdf(title, md_content, filename)

    @staticmethod
    def generate_markdown(proposal_data: dict) -> str:
        """Converts structured JSON proposal data to Markdown."""
        cm = CitationManager()
        title = proposal_data.get("rfp_title", "Grant Proposal")
        md = f"# {title}\n\n"
        
        sections = proposal_data.get("sections", [])
        for section in sections:
            title_text = section.get("title", "Untitled")
            content_text = section.get("content", "")
            processed_content = cm.process_text(content_text, fmt="markdown")
            md += f"## {title_text}\n\n{processed_content}\n\n"
            
        bib = cm.get_bibliography_markdown()
        if bib and "No references cited" not in bib:
            md += "## References\n" + bib
        return md

class Scribe:
    """The Final Aurificer: Orchestrates all output formats from structured JSON data."""
    
    _ui = None

    @staticmethod
    def set_ui(ui_provider: Any):
        """Sets the global UI provider for all formatters."""
        Scribe._ui = ui_provider

    @staticmethod
    def log_status(message: str, level: str = "info"):
        """Routes logs through the UI provider if available."""
        if Scribe._ui:
            Scribe._ui.log_status(message, level=level)
        else:
            # Fallback to print if UI is not yet registered
            if level != "verbose": # Skip verbose logs if no UI (to keep CLI clean)
                print(message)

    @staticmethod
    def generate_reports(state: ProjectState, base_path: str, config: FormattingConfig = None, verbose: bool = False, stage_num: int = None):
        """Generates appropriate reports based on the current stage of the project state.

        When stage_num is provided, the Scribe routes to the matching report
        type explicitly (used by engine.py's _finalize_stage for stages 5
        and 6, where state contents would otherwise misroute).
        """
        # 1. Save JSON Source (The Source of Truth)
        ProposalFormatter.save_json(state.dict(), f"{base_path}.json")

        # 2a. Explicit routing by stage_num (stages 5 and 6)
        if stage_num == 5:
            # Execution summary — a plain markdown writeup of progress
            summary = state.context.get("execution_summary", {}).get("summary_text", "")
            if not summary:
                summary = "No execution summary yet. Mark DAG nodes as done or in-progress from the Execution tab."
            with open(f"{base_path}.md", "w", encoding="utf-8") as f:
                f.write(f"# Execution Summary\n\n{summary}\n")
            Scribe.log_status("    [Scribe] Execution Summary synchronized", level="info")
            return

        if stage_num == 6:
            # Manuscript — markdown dump of title + abstract + sections
            if not state.manuscript:
                Scribe.log_status("    [Scribe] No manuscript to write.", level="verbose")
                return
            m = state.manuscript
            lines = [f"# {m.title}\n"]
            if m.target_venue:
                lines.append(f"*Target venue: {m.target_venue}*\n")
            lines.append(f"_Generated: {m.generated_at.isoformat()}_\n")
            lines.append("")
            for section in m.sections:
                lines.append(f"## {section.title}\n")
                lines.append(section.content or "")
                lines.append("")
            if m.figures:
                lines.append("## Figures\n")
                for fig in m.figures:
                    lines.append(f"- `{fig}`")
            with open(f"{base_path}.md", "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            Scribe.log_status("    [Scribe] Manuscript synchronized", level="info")
            Scribe.log_status(f"    [Scribe] Assets: {base_path}.[json|md]", level="verbose")
            return

        # 2b. Determine report type based on stage or content (stages 1-4)
        if state.proposal:
            # Stage 4: Grant Proposal
            if config is None: config = FormattingConfig(header_text="Grant Proposal")
            Scribe.generate_grant_report(state.proposal.dict(),
                                         {"name": state.researcher_profile.name if state.researcher_profile else None,
                                          "affiliation": state.researcher_profile.affiliation if state.researcher_profile else None},
                                          base_path, config=config, verbose=verbose)
        elif state.tasks:
            # Stage 3: Research Plan
            if config is None: config = FormattingConfig(header_text="Research Plan")
            with open(f"{base_path}.md", "w", encoding="utf-8") as f:
                f.write(PlanFormatter.generate_markdown(state))
            PlanFormatter.generate_pdf(state, f"{base_path}.pdf", config=config, verbose=verbose)
            Scribe.log_status(f"    [Scribe] Research Plan synchronized", level="info")
            Scribe.log_status(f"    [Scribe] Assets: {base_path}.[json|md|pdf|tex]", level="verbose")
        elif state.refined_hypothesis or state.scorecard: # Check for refined_hypothesis to indicate Hypothesis Analysis stage
            # Stage 2: Hypothesis Analysis
            AnalysisFormatter.generate_json(state, f"{base_path}.json")
            if config is None: config = FormattingConfig(header_text="Hypothesis Analysis")
            with open(f"{base_path}.md", "w", encoding="utf-8") as f:
                f.write(AnalysisFormatter.generate_markdown(state))
            AnalysisFormatter.generate_pdf(state, f"{base_path}.pdf", config=config, verbose=verbose)
            Scribe.log_status(f"    [Scribe] Hypothesis Analysis synchronized", level="info")
            Scribe.log_status(f"    [Scribe] Assets: {base_path}.[json|md|pdf|tex]", level="verbose")
        else:
            # Stage 1: Researcher Profile
            if config is None: config = FormattingConfig(header_text="Research Team Profile")
            with open(f"{base_path}.md", "w", encoding="utf-8") as f:
                f.write(ProfileFormatter.generate_markdown(state))
            ProfileFormatter.generate_pdf(state, f"{base_path}.pdf", config=config, verbose=verbose)
            Scribe.log_status(f"    [Scribe] Research Team Profile synchronized", level="info")
            Scribe.log_status(f"    [Scribe] Assets: {base_path}.[json|md|pdf|tex]", level="verbose")

    @staticmethod
    def generate_grant_report(proposal_json: dict, pi_info: dict, base_path: str, config: FormattingConfig = None, verbose: bool = False):
        """Generates full reports for a Grant Proposal (Stage 4) from independent JSON bundles."""
        # 1. Save independent JSON
        ProposalFormatter.save_json(proposal_json, f"{base_path}.json")
        
        # 2. Generate PDF & LaTeX
        if config is None: config = FormattingConfig(header_text="Grant Proposal")
        ProposalFormatter.generate_pdf(proposal_json, f"{base_path}.pdf", pi_info=pi_info, config=config, verbose=verbose)
        
        Scribe.log_status(f"    [Scribe] Grant Proposal synchronized", level="info")
        Scribe.log_status(f"    [Scribe] Full assets (independent JSON): {base_path}.[json|pdf|tex]", level="verbose")
