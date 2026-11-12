from __future__ import annotations

import logging
import os
from pathlib import Path

import chromadb
import requests

logger = logging.getLogger(__name__)

OLLAMA_HOST = os.getenv("OLLAMA_EMBED_HOST", os.getenv("OLLAMA_HOST", "http://ollama:11434"))
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
KB_PATH = os.getenv("KB_PATH", "/data/chroma")
KB_MIN_EXAMPLES = int(os.getenv("KB_MIN_EXAMPLES", "3"))
KB_MIN_REJECTED = int(os.getenv("KB_MIN_REJECTED", "3"))
STYLE_GUIDE_PATH = os.getenv("STYLE_GUIDE_PATH", "/data/style_guide")

_EMBED_TIMEOUT = 60

_client: chromadb.PersistentClient | None = None
_examples_col = None
_guidelines_col = None
_rejected_col = None


def _get_collections():
    global _client, _examples_col, _guidelines_col, _rejected_col
    if _client is None:
        os.makedirs(KB_PATH, exist_ok=True)
        _client = chromadb.PersistentClient(path=KB_PATH)
        _examples_col = _client.get_or_create_collection("style_examples")
        _guidelines_col = _client.get_or_create_collection("style_guidelines")
        _rejected_col = _client.get_or_create_collection("rejected_examples")
    return _examples_col, _guidelines_col, _rejected_col


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
        ex_col, _, _ = _get_collections()
        doc_id = f"post_{post_id}"
        query_text = f"{title}\n\n{post_text}"
        embedding = embed(query_text)
        ex_col.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[post_text],
            metadatas=[{"post_id": post_id, "topic": topic, "title": title}],
        )
        logger.info("Post %d in KB style_examples eingelesen", post_id)
        return True
    except Exception as e:
        logger.error("Post %d konnte nicht in die KB eingelesen werden: %s", post_id, e)
        return False


def ingest_rejected_post(post_id: int, topic: str, title: str, post_text: str, reason: str) -> bool:
    try:
        _, _, rej_col = _get_collections()
        doc_id = f"post_{post_id}"
        query_text = f"{title}\n\n{post_text}"
        embedding = embed(query_text)
        rej_col.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[post_text],
            metadatas=[{"post_id": post_id, "topic": topic, "title": title, "reason": reason}],
        )
        logger.info("Abgelehnter Post %d in KB rejected_examples eingelesen", post_id)
        return True
    except Exception as e:
        logger.error("Abgelehnter Post %d konnte nicht in die KB eingelesen werden: %s", post_id, e)
        return False


def ingest_guideline(doc_id: str, text: str, metadata: dict) -> bool:
    try:
        _, gu_col, _ = _get_collections()
        embedding = embed(text)
        gu_col.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[metadata],
        )
        logger.debug("Style-Guide-Abschnitt %s eingelesen", doc_id)
        return True
    except Exception as e:
        logger.error("Style-Guide-Abschnitt %s konnte nicht eingelesen werden: %s", doc_id, e)
        return False


def retrieve_for_article(
    article_text: str,
    n_examples: int = 2,
    n_guidelines: int = 2,
    n_rejected: int = 2,
) -> dict:
    """Gibt {examples: [...], guidelines: [...], rejected: [...]} zum Einfügen in den LLM-Prompt zurück.

    Gibt leere Listen zurück, wenn die KB weniger als KB_MIN_EXAMPLES freigegebene Posts enthält
    (bzw. für die Liste `rejected` weniger als KB_MIN_REJECTED abgelehnte Posts) oder wenn Ollama /
    ChromaDB nicht verfügbar ist — die Pipeline läuft unbeeinträchtigt weiter.
    """
    try:
        ex_col, gu_col, rej_col = _get_collections()

        ex_count = ex_col.count()
        if ex_count < KB_MIN_EXAMPLES:
            logger.debug(
                "KB hat %d Beispiel(e), Minimum ist %d — RAG-Abruf wird übersprungen",
                ex_count,
                KB_MIN_EXAMPLES,
            )
            return {"examples": [], "guidelines": [], "rejected": []}

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

        rejected = []
        rej_count = rej_col.count()
        if rej_count >= KB_MIN_REJECTED:
            results = rej_col.query(
                query_embeddings=[query_embedding],
                n_results=min(n_rejected, rej_count),
                include=["documents", "metadatas"],
            )
            for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                rejected.append({
                    "title": meta.get("title", ""),
                    "post_text": doc,
                    "reason": meta.get("reason", ""),
                })

        logger.debug(
            "KB lieferte %d Beispiele, %d Style-Guide-Abschnitte, %d abgelehnte Beispiele",
            len(examples), len(guidelines), len(rejected),
        )
        return {"examples": examples, "guidelines": guidelines, "rejected": rejected}

    except Exception as e:
        logger.error("KB-Abruf fehlgeschlagen (Pipeline läuft ohne RAG weiter): %s", e)
        return {"examples": [], "guidelines": [], "rejected": []}


def get_stats() -> dict:
    try:
        ex_col, gu_col, rej_col = _get_collections()
        n_examples = ex_col.count()
        n_rejected = rej_col.count()
        return {
            "style_examples": n_examples,
            "style_guidelines": gu_col.count(),
            "rejected_examples": n_rejected,
            "kb_path": KB_PATH,
            "min_examples_for_rag": KB_MIN_EXAMPLES,
            "min_rejected_for_rag": KB_MIN_REJECTED,
            "rag_active": n_examples >= KB_MIN_EXAMPLES,
            "rejected_rag_active": n_rejected >= KB_MIN_REJECTED,
        }
    except Exception as e:
        logger.error("KB-Statistik fehlgeschlagen: %s", e)
        return {
            "style_examples": 0,
            "style_guidelines": 0,
            "rejected_examples": 0,
            "rag_active": False,
            "rejected_rag_active": False,
            "error": str(e),
        }


def ingest_style_guide(path: str = STYLE_GUIDE_PATH) -> dict:
    """Zerlegt alle .md-Dateien im Pfad nach ##-Abschnitten und schreibt sie per Upsert in style_guidelines."""
    guide_path = Path(path)
    if not guide_path.exists():
        logger.warning("Style-Guide-Pfad nicht gefunden: %s", path)
        return {"ingested": 0, "failed": 0, "error": "path not found"}

    md_files = sorted(guide_path.glob("*.md"))
    logger.info("Style Guide wird eingelesen: %d Dateien aus %s", len(md_files), path)

    ingested = failed = 0
    current_ids: set[str] = set()
    for md_file in md_files:
        try:
            content = md_file.read_text(encoding="utf-8")
            chunks = _split_by_h2(content, md_file.name)
            for doc_id, text, metadata in chunks:
                current_ids.add(doc_id)
                if ingest_guideline(doc_id, text, metadata):
                    ingested += 1
                else:
                    failed += 1
        except Exception as e:
            logger.error("Verarbeitung von %s fehlgeschlagen: %s", md_file.name, e)
            failed += 1

    removed = _remove_stale_guidelines(current_ids) if failed == 0 else 0
    logger.info(
        "Style Guide eingelesen: %d Abschnitte eingelesen, %d fehlgeschlagen, %d veraltete entfernt",
        ingested, failed, removed,
    )
    return {"ingested": ingested, "failed": failed, "removed": removed}


def _remove_stale_guidelines(current_ids: set[str]) -> int:
    """Löscht Abschnitte, die es im Style Guide nicht mehr gibt.

    Die Doc-IDs bestehen aus Dateiname und Abschnittsüberschrift. Ohne diesen Schritt bleiben nach
    dem Umbenennen einer Überschrift oder dem Löschen einer Datei die alten Abschnitte per Upsert
    in der Collection liegen und werden weiter abgerufen. Wird nur nach einem fehlerfreien Lauf
    aufgerufen, damit ein Teilfehler nie gültige Abschnitte entfernt.
    """
    try:
        _, gu_col, _ = _get_collections()
        stale = [doc_id for doc_id in gu_col.get(include=[])["ids"] if doc_id not in current_ids]
        if stale:
            gu_col.delete(ids=stale)
            logger.info("Veraltete Style-Guide-Abschnitte entfernt: %s", ", ".join(stale))
        return len(stale)
    except Exception as e:
        logger.error("Veraltete Style-Guide-Abschnitte konnten nicht entfernt werden: %s", e)
        return 0


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
