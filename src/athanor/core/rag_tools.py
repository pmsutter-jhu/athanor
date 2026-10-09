"""
ATHANOR RAG & Search Tools.

Standalone tools for gathering information from the web and scientific
databases (OpenAlex, Semantic Scholar, GitHub, Zenodo).
"""
import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel
from .config_loader import config


class Tool(BaseModel):
    """Minimal tool base class. Subclasses implement _run()."""
    name: str = ""
    description: str = ""

    def _run(self, *args, **kwargs) -> str:
        raise NotImplementedError

    def run(self, *args, **kwargs) -> str:
        return self._run(*args, **kwargs)

# --- General Search Tools ---

class WebSearchTool(Tool):
    name: str = "Web Search"
    description: str = "Useful for searching the internet for current events, libraries, and facts."

    def _run(self, query: str) -> str:
        if config.search_provider == "quick":
            return f"[QUICK DuckDuckGo] Found general info about '{query}'."
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=3))
            if not results:
                return f"No results found for '{query}'."
            return "\n".join(
                f"- {r.get('title', '')}: {r.get('body', '')}" for r in results
            )
        except Exception as e:
            return f"Error searching DuckDuckGo: {e}"

# --- RAG Tools ---

class OpenAlexTool(Tool):
    name: str = "Search OpenAlex"
    description: str = "Search OpenAlex for papers. Returns titles and citations."
    def _run(self, query: str) -> str:
        if config.search_provider == "quick": return f"[QUICK OpenAlex] Found 5 papers relevant to '{query}'."
        headers = {"User-Agent": f"ATHANOR/0.1 ({config.openalex_email})"}
        url = f"https://api.openalex.org/works?search={query}&per-page=3"
        try:
            r = requests.get(url, headers=headers)
            if r.status_code == 200:
                data = r.json()
                results = [f"- {w['title']} ({w.get('publication_year', 'N/A')})" for w in data.get('results', [])]
                return "\n".join(results) if results else "No papers found."
            return f"OpenAlex Error: {r.status_code}"
        except Exception as e:
            return f"OpenAlex Exception: {e}"

class SemanticScholarTool(Tool):
    name: str = "Search Semantic Scholar"
    description: str = "Search Semantic Scholar for influential papers."
    def _run(self, query: str) -> str:
        if config.search_provider == "quick": return f"[QUICK Semantic Scholar] Found 3 highly cited papers for '{query}'."
        url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={query}&limit=3&fields=title,citationCount,year"
        headers = {"x-api-key": config.semanticscholar_api_key} if config.semanticscholar_api_key else {}
        try:
            r = requests.get(url, headers=headers)
            if r.status_code == 200:
                data = r.json()
                results = [f"- {p['title']} ({p['year']}) - Cited by {p['citationCount']}" for p in data.get('data', [])]
                return "\n".join(results) if results else "No papers found."
            return f"S2 Error: {r.status_code}"
        except Exception as e:
            return f"S2 Exception: {e}"

class HuggingFaceSearchTool(Tool):
    name: str = "Search HuggingFace"
    description: str = "Search HF Hub for models."
    def _run(self, query: str) -> str:
        if config.search_provider == "quick": return f"[QUICK HuggingFace] Found 1 model for '{query}'."
        url = f"https://huggingface.co/api/models?search={query}&limit=3&sort=downloads"
        headers = {"Authorization": f"Bearer {config.huggingface_api_key}"} if config.huggingface_api_key else {}
        try:
            r = requests.get(url, headers=headers)
            if r.status_code == 200:
                data = r.json()
                # API returns list directly
                results = [f"- {m['modelId']} (Downloads: {m.get('downloads', 'N/A')})" for m in data[:3]]
                return "\n".join(results) if results else "No models found."
            return f"HF Error: {r.status_code}"
        except Exception as e:
            return f"HF Exception: {e}"

class GitHubSearchTool(Tool):
    name: str = "Search GitHub"
    description: str = "Search GitHub for repos."
    def _run(self, query: str) -> str:
        if config.search_provider == "quick": return f"[QUICK GitHub] Found 2 repositories for '{query}'."
        url = f"https://api.github.com/search/repositories?q={query}&sort=stars&per_page=3"
        headers = {"Authorization": f"token {config.github_api_key}"} if config.github_api_key else {}
        try:
            r = requests.get(url, headers=headers)
            if r.status_code == 200:
                data = r.json()
                results = [f"- {i['full_name']} (Stars: {i['stargazers_count']}): {i['description']}" for i in data.get('items', [])]
                return "\n".join(results) if results else "No repos found."
            return f"GitHub Error: {r.status_code}"
        except Exception as e:
            return f"GitHub Exception: {e}"

class GoogleDatasetTool(Tool):
    name: str = "Search Google Datasets"
    description: str = "Search Google Dataset Search."
    def _run(self, query: str) -> str:
        if config.search_provider == "quick":
            return f"[QUICK GoogleDataset] Found 1 dataset for '{query}'."
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(
                    f"site:datasetsearch.research.google.com {query}", max_results=3
                ))
            if not results:
                return f"No datasets found for '{query}'."
            return "\n".join(
                f"- {r.get('title', '')}: {r.get('body', '')}" for r in results
            )
        except Exception as e:
            return f"Dataset Search Error: {e}"

class ZenodoTool(Tool):
    name: str = "Search Zenodo"
    description: str = "Search Zenodo for datasets."
    def _run(self, query: str) -> str:
        if config.search_provider == "quick": return f"[QUICK Zenodo] Found 2 datasets for '{query}'."
        url = f"https://zenodo.org/api/records?q={query}&size=3&sort=mostrecent"
        headers = {"Authorization": f"Bearer {config.zenodo_api_key}"} if config.zenodo_api_key else {}
        try:
            r = requests.get(url, headers=headers)
            if r.status_code == 200:
                data = r.json()
                results = [f"- {h['metadata']['title']} (Date: {h['metadata']['publication_date']})" for h in data.get('hits', {}).get('hits', [])]
                return "\n".join(results) if results else "No Zenodo records found."
            return f"Zenodo Error: {r.status_code}"
        except Exception as e:
            return f"Zenodo Exception: {e}"

class WebScrapeTool(Tool):
    name: str = "Scrape Website"
    description: str = "Useful to read the text content of a website (CV, homepage, paper abstract)."

    def _run(self, url: str) -> str:
        import os
        # Expand user home directory (~)
        url = os.path.expanduser(url)
        
        # Check if local file
        if os.path.exists(url) and os.path.isfile(url):
            try:
                ext = os.path.splitext(url)[1].lower()
                text = ""
                
                if ext == ".docx":
                    try:
                        from docx import Document
                        doc = Document(url)
                        text = "\n".join([para.text for para in doc.paragraphs])
                    except ImportError:
                        return f"Error: python-docx not installed. Cannot read {url}"
                    except Exception as e:
                        return f"Error reading docx: {e}"
                        
                elif ext == ".pdf":
                    try:
                        import pypdfium2 as pdfium
                        pdf = pdfium.PdfDocument(url)
                        for page in pdf:
                            text += page.get_textpage().get_text_range() + "\n"
                    except ImportError:
                         # Fallback to PyPDF2 or pypdf if available
                         try:
                             from pypdf import PdfReader
                             reader = PdfReader(url)
                             for page in reader.pages:
                                 text += page.extract_text() + "\n"
                         except ImportError:
                             return f"Error: pypdf/pypdfium2 not installed. Cannot read {url}"
                    except Exception as e:
                        return f"Error reading pdf: {e}"
                        
                else:
                    # Try plain text
                    try:
                        with open(url, 'r', encoding='utf-8', errors='ignore') as f:
                            text = f.read()
                    except Exception as e:
                        return f"Error reading text file: {e}"
                
                if not text.strip():
                     return "File appears empty."
                return f"[LOCAL FILE CONTENT from {url}]\n{text[:15000]}" # Limit size for context window

            except Exception as e:
                return f"Error processing local file {url}: {e}"

        # If not local file, treat as URL
        if not url.startswith("http"):
            return f"Error: Path '{url}' does not exist locally and is not a valid URL."

        try:
            # Basic header to avoid some bot detection
            headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'}
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                # Remove scripts and styles
                for script in soup(["script", "style"]):
                    script.extract()
                text = soup.get_text()
                # Clean up whitespace
                lines = (line.strip() for line in text.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                text = '\\n'.join(chunk for chunk in chunks if chunk)
                if not text:
                    return "No readable text content found on this page."
                return text[:8000] # Truncate to fit context if needed
            else:
                return f"Error: Status code {response.status_code}"
        except Exception as e:
            return f"Error scraping {url}: {e}"

class BudgetEngine(Tool):
    name: str = "BudgetEngine"
    description: str = "A calculation tool for grant budgets. Input: A mathematical expression or list of costs to total, e.g., '10000 + (10000 * 0.5)'. It supports basic arithmetic."

    def _run(self, query: str) -> str:
        # Simple cleanup and evaluation
        import re
        # Remove currency symbols and non-math characters except basic operators
        math_query = re.sub(r'[^0-9\.\+\-\*\/\(\)\s]', '', query)
        try:
            # Using a safe evaluation approach if possible, but for this context eval is acceptable if gated
            result = eval(math_query, {"__builtins__": None}, {})
            return f"Result: ${result:,.2f}"
        except Exception as e:
            return f"Calculation Error: {e}. Please provide a valid mathematical expression."
