"""Manuelles Einspeisen von Artikeln — schickt vorab kuratierte Artikel durch die bestehende Pipeline.

Aufruf (aus dem Verzeichnis engine-api/):
    python -m scripts.debug.manual_batch

Vor dem Ausführen scripts/debug/manual_articles.json bearbeiten, um Artikel hinzuzufügen oder zu aktualisieren.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Sicherstellen, dass engine-api/ im Pfad liegt, wenn als `python -m scripts.debug.manual_batch` aufgerufen
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pipeline import _process_articles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("manual_batch")

_JSON_PATH = Path(__file__).parent / "manual_articles.json"


def _normalize(article: dict) -> dict:
    """Ergänzt Felder, die _process_articles benötigt, die aber nicht im JSON-Schema stehen."""
    art = dict(article)
    art.setdefault("topic", "Manual")
    # rss_summary ist der Fallback der Extraktion: Ist die URL hinter einer Paywall oder nicht
    # erreichbar, greift extract_article() auf rss_summary zurück — daher füllen wir es mit full_text.
    art.setdefault("rss_summary", art.get("full_text", "")[:500])
    return art


def main() -> None:
    if not _JSON_PATH.exists():
        logger.error("Article file not found: %s", _JSON_PATH)
        sys.exit(1)

    raw = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        logger.error("manual_articles.json must be a non-empty JSON array")
        sys.exit(1)

    logger.info("Loaded %d article(s) from %s", len(raw), _JSON_PATH.name)

    articles = [_normalize(a) for a in raw]
    result = _process_articles(articles)

    logger.info(
        "Done — %d post(s) created | ids: %s",
        result["created"],
        result["ids"] or "none",
    )


if __name__ == "__main__":
    main()
