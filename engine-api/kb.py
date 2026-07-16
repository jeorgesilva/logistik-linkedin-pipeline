from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import chromadb
import requests

logger = logging.getLogger(__name__)

OLLAMA_HOST = os.getenv("OLLAMA_EMBED_HOST", os.getenv("OLLAMA_HOST", "http://ollama:11434"))
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
KB_PATH = os.getenv("KB_PATH", "/data/chroma")
KB_MIN_EXAMPLES = int(os.getenv("KB_MIN_EXAMPLES", "3"))
STYLE_GUIDE_PATH = os.getenv("STYLE_GUIDE_PATH", "/data/style_guide")

_EMBED_TIMEOUT = 60

_client: Optional[chromadb.PersistentClient] = None
_examples_col = None
_guidelines_col = None


def _get_collections():
    global _client, _examples_col, _guidelines_col
    if _client is None:
        os.makedirs(KB_PATH, exist_ok=True)
        _client = chromadb.PersistentClient(path=KB_PATH)
        _examples_col = _client.get_or_create_collection("style_examples")
        _guidelines_col = _client.get_or_create_collection("style_guidelines")
    return _examples_col, _guidelines_col


def embed(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_HOST}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=_EMBED_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def ingest_post(post_id: int, topic: str, title: str, post_text: str) -> bool:
    try:
        ex_col, _ = _get_collections()
        doc_id = f"post_{post_id}"
        query_text = f"{title}\n\n{post_text}"
        embedding = embed(query_text)
        ex_col.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[post_text],
            metadatas=[{"post_id": post_id, "topic": topic, "title": title}],
        )
        logger.info("Ingested post %d into style_examples KB", post_id)
        return True
    except Exception as e:
        logger.error("Failed to ingest post %d into KB: %s", post_id, e)
        return False


def ingest_guideline(doc_id: str, text: str, metadata: dict) -> bool:
    try:
        _, gu_col = _get_collections()
        embedding = embed(text)
        gu_col.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[metadata],
        )
        logger.debug("Ingested guideline chunk %s", doc_id)
        return True
    except Exception as e:
        logger.error("Failed to ingest guideline %s: %s", doc_id, e)
        return False


def retrieve_for_article(
    article_text: str,
    n_examples: int = 2,
    n_guidelines: int = 2,
) -> dict:
    """Return {examples: [...], guidelines: [...]} for injection into the LLM prompt.

    Returns empty lists if KB has fewer than KB_MIN_EXAMPLES approved posts,
    or if Ollama / ChromaDB is unavailable — pipeline continues unaffected.
    """
    try:
        ex_col, gu_col = _get_collections()

        ex_count = ex_col.count()
        if ex_count < KB_MIN_EXAMPLES:
            logger.debug(
                "KB has %d example(s), minimum is %d — skipping RAG retrieval",
                ex_count,
                KB_MIN_EXAMPLES,
            )
            return {"examples": [], "guidelines": []}

        query_embedding = embed(article_text[:500])

        examples = []
        results = ex_col.query(
            query_embeddings=[query_embedding],
            n_results=min(n_examples, ex_count),
            include=["documents", "metadatas"],
        )
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            examples.append({"title": meta.get("title", ""), "post_text": doc})

        guidelines = []
        gu_count = gu_col.count()
        if gu_count > 0:
            results = gu_col.query(
                query_embeddings=[query_embedding],
                n_results=min(n_guidelines, gu_count),
                include=["documents", "metadatas"],
            )
            for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                guidelines.append({"source": meta.get("source", ""), "section": meta.get("section", ""), "text": doc})

        logger.debug("KB retrieved %d examples, %d guideline chunks", len(examples), len(guidelines))
        return {"examples": examples, "guidelines": guidelines}

    except Exception as e:
        logger.error("KB retrieval failed (pipeline continues without RAG): %s", e)
        return {"examples": [], "guidelines": []}


def get_stats() -> dict:
    try:
        ex_col, gu_col = _get_collections()
        n_examples = ex_col.count()
        return {
            "style_examples": n_examples,
            "style_guidelines": gu_col.count(),
            "kb_path": KB_PATH,
            "min_examples_for_rag": KB_MIN_EXAMPLES,
            "rag_active": n_examples >= KB_MIN_EXAMPLES,
        }
    except Exception as e:
        logger.error("KB stats failed: %s", e)
        return {
            "style_examples": 0,
            "style_guidelines": 0,
            "rag_active": False,
            "error": str(e),
        }


def ingest_style_guide(path: str = STYLE_GUIDE_PATH) -> dict:
    """Chunk all .md files in path by ## sections and upsert into style_guidelines."""
    guide_path = Path(path)
    if not guide_path.exists():
        logger.warning("Style guide path not found: %s", path)
        return {"ingested": 0, "failed": 0, "error": "path not found"}

    md_files = sorted(guide_path.glob("*.md"))
    logger.info("Ingesting style guide: %d files from %s", len(md_files), path)

    ingested = failed = 0
    for md_file in md_files:
        try:
            content = md_file.read_text(encoding="utf-8")
            chunks = _split_by_h2(content, md_file.name)
            for doc_id, text, metadata in chunks:
                if ingest_guideline(doc_id, text, metadata):
                    ingested += 1
                else:
                    failed += 1
        except Exception as e:
            logger.error("Failed processing %s: %s", md_file.name, e)
            failed += 1

    logger.info("Style guide ingestion done: %d chunks ingested, %d failed", ingested, failed)
    return {"ingested": ingested, "failed": failed}


def _split_by_h2(content: str, filename: str) -> list[tuple[str, str, dict]]:
    sections: list[tuple[str, str, dict]] = []
    current_section = "header"
    current_lines: list[str] = []

    for line in content.splitlines():
        if line.startswith("## "):
            text = "\n".join(current_lines).strip()
            if len(text) > 80:
                doc_id = f"{filename}::{current_section}"
                sections.append((doc_id, text, {"source": filename, "section": current_section}))
            current_section = line[3:].strip().lower().replace(" ", "_")
            current_lines = [line]
        else:
            current_lines.append(line)

    text = "\n".join(current_lines).strip()
    if len(text) > 80:
        doc_id = f"{filename}::{current_section}"
        sections.append((doc_id, text, {"source": filename, "section": current_section}))

    return sections
