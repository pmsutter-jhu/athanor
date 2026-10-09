"""
ATHANOR Project Knowledge Base.

Per-project vector-backed retrieval over user-attached documents
(papers, pilot data, notebooks, CV, prior grant drafts, whatever the
PI drops into the Prior Work / Preliminary Results slots). Used by
GrantWeaver in Stage 4 to ground the proposal in real PI material
instead of fabricating it.

Why not Chroma / LangChain / etc.: this class is scoped to a single
PI's grant-writing workflow, which typically means ~10–30 documents,
~100–500 pages of text. At that scale, a SQLite-backed dot-product
store with embeddings from Gemini's text-embedding-004 model is
100x simpler than pulling in a real vector DB and completely
sufficient for the retrieval quality we need. Zero new dependencies
— google.genai and sqlite3 are both already in the stack.

Why not just naive keyword search: because grant writers ask
semantic questions like "preliminary evidence for the proposed
methodology" and need retrieval that works across synonymy and
domain vocabulary. Real embeddings handle that; BM25 partially
does; keyword overlap doesn't.

Storage layout:
  <project_dir>/knowledge_base/
      kb.sqlite           — chunks + embeddings + metadata
      files/              — copies of ingested source files
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.config_loader import config


# Dimensionality of Gemini's text-embedding-004 output. We pin this
# rather than discovering it at runtime so the SQLite schema is stable.
EMBEDDING_DIM = 768
EMBEDDING_MODEL = "text-embedding-004"


class ProjectKnowledgeBase:
    """Lightweight per-project vector store over attached documents."""

    def __init__(self, project_dir: str, ui: Optional[Any] = None) -> None:
        self.project_dir = project_dir
        self.ui = ui
        self.kb_dir = os.path.join(project_dir, "knowledge_base")
        self.files_dir = os.path.join(self.kb_dir, "files")
        self.db_path = os.path.join(self.kb_dir, "kb.sqlite")

        os.makedirs(self.kb_dir, exist_ok=True)
        os.makedirs(self.files_dir, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # DB setup
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id    TEXT NOT NULL,
                source_path  TEXT NOT NULL,
                chunk_index  INTEGER NOT NULL,
                chunk_text   TEXT NOT NULL,
                embedding    BLOB,
                metadata     TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_source_id ON chunks(source_id)")
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Public ingest / query / remove
    # ------------------------------------------------------------------

    def ingest_file(
        self,
        path: str,
        source_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Ingest a file: extract text, chunk, embed, store.

        Args:
            path: path to the source file (PDF, docx, md, txt, etc.)
            source_id: stable id used to later remove all chunks from
                this file (typically the PreliminaryWork.id)
            metadata: optional dict stored alongside each chunk

        Returns the number of chunks created. Raises on fatal read
        errors; embedding failures degrade to storing chunks without
        vectors (they're still retrievable via keyword fallback).
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"No file at {path}")

        text = self._extract_text(path)
        if not text or not text.strip():
            self._log(f"    [KB] No text extracted from {path}", level="verbose")
            return 0

        chunks = self._chunk_text(text)
        if not chunks:
            return 0

        embeddings = self._embed_batch(chunks)
        # embeddings is parallel to chunks; may contain None on error

        conn = sqlite3.connect(self.db_path)
        meta_json = json.dumps(metadata or {})
        for i, chunk in enumerate(chunks):
            emb = embeddings[i] if i < len(embeddings) else None
            emb_blob = _pack_embedding(emb) if emb is not None else None
            conn.execute(
                "INSERT INTO chunks (source_id, source_path, chunk_index, chunk_text, embedding, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                (source_id, path, i, chunk, emb_blob, meta_json),
            )
        conn.commit()
        conn.close()

        self._log(f"    [KB] Ingested {len(chunks)} chunks from {os.path.basename(path)}", level="verbose")
        return len(chunks)

    def ingest_text(
        self,
        text: str,
        source_id: str,
        source_label: str = "inline",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Ingest an in-memory text blob without a source file on disk.

        Used when promoting DAG nodes to preliminary results — the
        node's description + workflow become searchable text."""
        if not text or not text.strip():
            return 0
        chunks = self._chunk_text(text)
        if not chunks:
            return 0
        embeddings = self._embed_batch(chunks)

        conn = sqlite3.connect(self.db_path)
        meta_json = json.dumps(metadata or {})
        for i, chunk in enumerate(chunks):
            emb = embeddings[i] if i < len(embeddings) else None
            emb_blob = _pack_embedding(emb) if emb is not None else None
            conn.execute(
                "INSERT INTO chunks (source_id, source_path, chunk_index, chunk_text, embedding, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                (source_id, source_label, i, chunk, emb_blob, meta_json),
            )
        conn.commit()
        conn.close()
        return len(chunks)

    def query(self, query_text: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Return the top-k most relevant chunks for a query.

        Each result: {source_id, source_path, chunk_text, score, metadata}.
        Falls back to keyword overlap when embeddings are unavailable
        (mock mode, embedding failure, empty KB).
        """
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT source_id, source_path, chunk_text, embedding, metadata FROM chunks"
        ).fetchall()
        conn.close()

        if not rows:
            return []

        # Try semantic retrieval first
        query_emb = self._embed_one(query_text)
        results: List[Tuple[float, Dict[str, Any]]] = []

        if query_emb is not None:
            for source_id, source_path, chunk_text, emb_blob, meta_json in rows:
                if emb_blob is None:
                    continue
                emb = _unpack_embedding(emb_blob)
                score = _dot(query_emb, emb)
                results.append((score, {
                    "source_id": source_id,
                    "source_path": source_path,
                    "chunk_text": chunk_text,
                    "score": score,
                    "metadata": json.loads(meta_json) if meta_json else {},
                }))

        if not results:
            # Fallback: naive keyword overlap
            query_terms = set(query_text.lower().split())
            for source_id, source_path, chunk_text, _emb_blob, meta_json in rows:
                terms = set(chunk_text.lower().split())
                overlap = len(query_terms & terms)
                if overlap > 0:
                    results.append((float(overlap), {
                        "source_id": source_id,
                        "source_path": source_path,
                        "chunk_text": chunk_text,
                        "score": float(overlap),
                        "metadata": json.loads(meta_json) if meta_json else {},
                    }))

        results.sort(key=lambda x: x[0], reverse=True)
        return [r[1] for r in results[:top_k]]

    def remove_source(self, source_id: str) -> int:
        """Delete all chunks associated with a source_id. Returns count."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.execute("DELETE FROM chunks WHERE source_id = ?", (source_id,))
        count = cur.rowcount
        conn.commit()
        conn.close()
        return count

    def list_sources(self) -> List[Dict[str, Any]]:
        """Return one entry per unique source_id with chunk count."""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT source_id, source_path, COUNT(*) FROM chunks GROUP BY source_id"
        ).fetchall()
        conn.close()
        return [
            {"source_id": sid, "source_path": path, "chunk_count": count}
            for sid, path, count in rows
        ]

    def stats(self) -> Dict[str, Any]:
        """Summary statistics for the KB."""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT source_id), SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END) FROM chunks"
        ).fetchone()
        conn.close()
        total, sources, with_emb = row if row else (0, 0, 0)
        return {
            "total_chunks": total or 0,
            "total_sources": sources or 0,
            "chunks_with_embeddings": with_emb or 0,
        }

    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------

    def _extract_text(self, path: str) -> str:
        """Extract text from a file. Best-effort across common formats."""
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".pdf":
                return self._extract_pdf(path)
            if ext in (".txt", ".md", ".markdown", ".rst"):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
            if ext == ".json":
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    data = json.load(f)
                return json.dumps(data, indent=2)
            if ext == ".docx":
                return self._extract_docx(path)
            if ext in (".csv", ".tsv"):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    # Just read the first 500 lines — CSVs can be huge
                    lines = []
                    for i, line in enumerate(f):
                        if i >= 500:
                            lines.append("... (truncated)")
                            break
                        lines.append(line.rstrip("\n"))
                    return "\n".join(lines)
            # Unknown extension — try to read as text
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            self._log(f"    [KB] Text extraction failed for {path}: {e}", level="error")
            return ""

    def _extract_pdf(self, path: str) -> str:
        """Best-effort PDF extraction using whatever's installed."""
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(path)
            parts = []
            for page in doc:
                textpage = page.get_textpage()
                parts.append(textpage.get_text_range())
            return "\n\n".join(parts)
        except ImportError:
            pass
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
            return "\n\n".join((p.extract_text() or "") for p in reader.pages)
        except ImportError:
            pass
        self._log(f"    [KB] No PDF library available to read {path}", level="error")
        return ""

    def _extract_docx(self, path: str) -> str:
        try:
            from docx import Document  # python-docx
            d = Document(path)
            return "\n\n".join(p.text for p in d.paragraphs if p.text)
        except ImportError:
            self._log(f"    [KB] python-docx not installed; cannot read {path}", level="error")
            return ""
        except Exception as e:
            self._log(f"    [KB] docx read failed: {e}", level="error")
            return ""

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def _chunk_text(self, text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
        """Split text into word-based chunks with overlap.

        chunk_size and overlap are in words, not tokens — close enough
        for our retrieval quality at this scale."""
        words = text.split()
        if not words:
            return []
        chunks: List[str] = []
        i = 0
        while i < len(words):
            chunk_words = words[i:i + chunk_size]
            chunk = " ".join(chunk_words)
            if chunk.strip():
                chunks.append(chunk)
            if i + chunk_size >= len(words):
                break
            i += chunk_size - overlap
        return chunks

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def _embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """Embed a batch of texts. In mock mode or on error, returns
        None for each slot so callers can store the chunks without
        embeddings (keyword fallback still works)."""
        if not texts:
            return []
        if config.llm_provider == "mock":
            return [None] * len(texts)

        try:
            from google import genai
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                self._log("    [KB] No GEMINI_API_KEY — storing without embeddings", level="verbose")
                return [None] * len(texts)
            client = genai.Client(api_key=api_key)
            # Gemini's embedding API accepts a list
            out: List[Optional[List[float]]] = []
            # Conservative batch size — Gemini can handle larger but we
            # prefer predictable behavior on large ingests
            BATCH = 20
            for start in range(0, len(texts), BATCH):
                batch = texts[start:start + BATCH]
                try:
                    response = client.models.embed_content(
                        model=EMBEDDING_MODEL,
                        contents=batch,
                    )
                    # Response shape: .embeddings is a list of ContentEmbedding
                    # each with .values that's a list[float]
                    for emb in response.embeddings:
                        out.append(list(emb.values))
                except Exception as e:
                    self._log(f"    [KB] Embedding batch failed: {e}", level="error")
                    for _ in batch:
                        out.append(None)
            return out
        except ImportError:
            return [None] * len(texts)
        except Exception as e:
            self._log(f"    [KB] Embedding setup failed: {e}", level="error")
            return [None] * len(texts)

    def _embed_one(self, text: str) -> Optional[List[float]]:
        """Embed a single query string."""
        out = self._embed_batch([text])
        return out[0] if out else None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, message: str, level: str = "info") -> None:
        if self.ui is not None:
            try:
                self.ui.log_status(message, level=level)
            except Exception:
                pass


# =============================================================================
# Binary helpers
# =============================================================================

def _pack_embedding(emb: List[float]) -> bytes:
    """Pack a float32 vector into raw bytes for SQLite storage."""
    return struct.pack(f"{len(emb)}f", *emb)


def _unpack_embedding(blob: bytes) -> List[float]:
    """Reverse of _pack_embedding."""
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


def _dot(a: List[float], b: List[float]) -> float:
    """Dot product of two equal-length vectors."""
    n = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(n))
