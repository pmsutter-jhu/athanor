"""
ATHANOR Librarian Assistant.

This module provides the Librarian class, which manages Retrieval-Augmented 
Generation (RAG) for localized document sets (e.g., RFP files or local research 
libraries). It handles ingestion, vector storage, and semantic querying.
"""
import os
import sys
import logging
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from ..core.rag_tools import Tool

class Librarian:
    """
    Core RAG engine for long documents. 
    Handles loading (PDF, URL, Text) and vector storage.
    """
    def __init__(self, doc_path: Optional[str] = None, text_content: Optional[str] = None, collection_name: str = "doc_rag_store", ui: Optional[Any] = None):
        self.doc_path = doc_path
        self.text_content = text_content
        self.collection_name = collection_name
        self.ui = ui
        
        if self.doc_path:
            loaded_text = self._load_document(self.doc_path)
            if self.text_content:
                self.text_content += "\n\n" + loaded_text
            else:
                self.text_content = loaded_text
            
        self.vector_store = None

    def _log(self, msg: str, level: str = "verbose"):
        if self.ui:
            self.ui.log_status(msg, level=level)
        elif level == "error":
            print(msg)

    def _load_document(self, path: str) -> str:
        """Robust loader (URL, PDF, Text)."""
        if not path:
            return ""
            
        self._log(f"    [Librarian] Loading document from: {path}")

        # 1. Handle URL
        if path.startswith("http://") or path.startswith("https://"):
            try:
                import requests
                from bs4 import BeautifulSoup
                
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
                
                if path.lower().endswith(".pdf"):
                    self._log(f"    [Librarian] Detected Remote PDF. Downloading...")
                    try:
                        resp = requests.get(path, headers=headers, timeout=30)
                        resp.raise_for_status()
                    except requests.exceptions.RequestException as e:
                        return f"Network Error downloading PDF: {e}"
                    
                    import tempfile
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                        tmp.write(resp.content)
                        temp_path = tmp.name
                        
                    text = self._parse_pdf(temp_path)
                    
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                        
                    return f"Source: {path} (Remote PDF)\n\n{text}"

                # Standard HTML Scraping
                self._log(f"    [Librarian] Scraping from URL")
                try:
                    resp = requests.get(path, headers=headers, timeout=15)
                    resp.raise_for_status()
                except requests.exceptions.RequestException as e:
                    return f"Network Error scraping URL: {e}"
                
                soup = BeautifulSoup(resp.content, 'html.parser')
                content_node = soup.find('main') or soup.find('article') or soup.body
                if content_node:
                    text = content_node.get_text(separator='\n\n')
                else:
                    text = soup.get_text(separator='\n\n')
                    
                return f"Source: {path}\n\n{text}"
                
            except Exception as e:
                return f"Error loading URL {path}: {str(e)}"

        # 2. Check File Existence
        if not os.path.exists(path):
            self._log(f"    [Librarian] Error: Path not found {path}")
            return f"Error: File not found at {path}"
        
        # 3. Handle Directory (Auto-find context)
        if os.path.isdir(path):
            self._log(f"    [Librarian] Detected directory: {path}")
            # Try to find a logical main file: README.md, or the first PDF/TXT
            files = sorted(os.listdir(path))
            
            # Helper to read first match
            for ext in [".pdf", ".md", ".txt"]:
                for f in files:
                    if f.lower().endswith(ext):
                        full = os.path.join(path, f)
                        self._log(f"    [Librarian] Auto-selecting file from directory: {f}")
                        return self._load_document(full)
            
            return f"Error: The path '{path}' is a directory, and no obvious PDF/MD/TXT file was found inside."
            
        # 3. Handle JSON
        if path.lower().endswith(".json"):
            try:
                import json
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return f"Source: {path} (JSON)\n\n{json.dumps(data, indent=2)}"
            except Exception as e:
                return f"Error parsing JSON {path}: {str(e)}"
                 
        # 4. Handle PDF
        if path.lower().endswith(".pdf"):
            text = self._parse_pdf(path)
            return f"Source: {path} (PDF)\n\n{text}"
                 
        # 5. Default Text Loader (MD, TXT, etc)
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception as e:
            return f"Error reading text file {path}: {str(e)}"

    def _parse_pdf(self, path: str) -> str:
        """Helper to parse PDF using available libraries."""
        try:
            import pypdfium2 as pdfium
            self._log(f"    [Librarian] Parsing PDF with pypdfium2")
            pdf = pdfium.PdfDocument(path)
            text = ""
            for page in pdf:
                text_page = page.get_textpage()
                text += text_page.get_text_range() + "\n\n"
            return text
        except ImportError:
            try:
                import pypdf
                self._log(f"    [Librarian] Parsing PDF with pypdf")
                reader = pypdf.PdfReader(path)
                text = ""
                for page in reader.pages:
                    text += page.extract_text() + "\n\n"
                return text
            except ImportError:
                return "Error: No PDF parsing library found (pypdfium2 or pypdf)."
        except Exception as e:
            return f"Error parsing PDF: {str(e)}"

    def read_resource(self, path: str) -> str:
        """One-off fetch of a resource without permanent indexing."""
        return self._load_document(path)

    def query(self, query_text: str, k: int = 4) -> str:
        """Retrieves relevant sections using RAG or Naive Search."""
        if self.vector_store:
            try:
                docs = self.vector_store.similarity_search(query_text, k=k)
                if docs:
                    context = "\n\n---\n\n".join([d.page_content for d in docs])
                    return f"*** RELATED EXCERPTS FOR '{query_text}' (RAG) ***\n\n{context}"
            except Exception as e:
                self._log(f"    [Librarian] RAG Query failed: {e}. Falling back to naive.")
        
        # Naive Fallback
        paragraphs = self.text_content.split('\n\n')
        hits = []
        query_terms = query_text.lower().split()
        for p in paragraphs:
            p_lower = p.lower()
            score = sum(1 for term in query_terms if term in p_lower)
            if score > 0:
                hits.append((score, p))
        
        hits.sort(key=lambda x: x[0], reverse=True)
        top_hits = [h[1] for h in hits[:k]]
        
        if not top_hits:
            return f"No relevant information found for '{query_text}'."
        
        return f"*** RELATED EXCERPTS FOR '{query_text}' (Naive) ***\n\n" + "\n---\n".join(top_hits)

class LibrarianTool(Tool):
    """
    Tool wrapper for the Librarian class (RAG search).
    """
    name: str = "Librarian"
    description: str = "Searches internally loaded documents for specific information. Input: 'search query'."
    librarian: Optional[Any] = None 
    
    # Define explicit schema to handle LLMs using 'topic' instead of 'query'
    class Config:
        arbitrary_types_allowed = True
    
    def __init__(self, librarian: Librarian, name: Optional[str] = None, description: Optional[str] = None):
        super().__init__()
        object.__setattr__(self, "librarian", librarian)
        if name: object.__setattr__(self, "name", name)
        if description: object.__setattr__(self, "description", description)

    def _run(self, query: Optional[str] = None, topic: Optional[str] = None, **kwargs) -> str:
        """
        Accept both 'query' and 'topic' as parameter names to handle LLM variations.
        """
        search_term = query or topic or kwargs.get('q', '')
        if not search_term:
            return "Error: Please provide a search query (use 'query' or 'topic' parameter)."
        if not self.librarian:
            return "Error: Librarian not initialized."
        return self.librarian.query(search_term)

class ResourceOpenerTool(Tool):
    """
    Directly opens and reads a specific resource (URL/FilePath).
    'Give it a link, it gives you the text.'
    """
    name: str = "URL or File Opener"
    description: str = "Reads a specific resource (URL or FilePath) and returns the full content. NOT for searching - use your LLM's built-in search capability or the Librarian tool for that. Input: A valid URL or local FilePath."
    librarian: Optional[Any] = None
    _use_native_search: bool = False  # Set by llm_gateway

    def __init__(self, librarian: Optional[Librarian] = None, use_native_search: bool = False):
        super().__init__()
        object.__setattr__(self, "librarian", librarian or Librarian())
        object.__setattr__(self, "_use_native_search", use_native_search)

    def _run(self, path: str) -> str:
        # Clean paths (sometimes agents wrap in quotes)
        path = path.strip().strip("'").strip('"')
        
        # Heuristic to catch misuse as a search tool or hallucinated filenames
        is_url = path.startswith("http")
        is_likely_path = "/" in path or "\\" in path or (path.endswith(('.pdf', '.json', '.txt', '.md', '.csv')) and len(path.split()) == 1)
        
        # New: Catch search engine URLs
        search_engines = ["google.com/search", "bing.com/search", "duckduckgo.com/?q", "duckduckgo.com/search"]
        if any(se in path.lower() for se in search_engines):
            if self._use_native_search:
                return f"Error: '{path}' looks like a search engine query. You have Google Search built into your LLM - just ask the question directly instead of trying to construct search URLs. This tool is only for opening specific articles once you have their direct URL."
            else:
                return f"Error: '{path}' looks like a search engine query. Please use the specialized search tools (like 'Search DuckDuckGo' or 'Search OpenAlex') instead of the Opener tool. This tool is only for opening a specific article or website once you have its direct URL."

        if not (is_url or is_likely_path or os.path.exists(path)):
             return f"Error: '{path}' does not look like a valid URL or existing FilePath. If you are trying to search the RFP content for '{path}', use the 'Librarian' tool instead. This tool is only for opening specific, known files or links."

        return self.librarian.read_resource(path)

