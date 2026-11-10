"""Volltext-Extraktor auf Abruf für eine einzelne URL.

Wird von der Pipeline NICHT automatisch aufgerufen. Interaktiv oder aus einem Skript nutzen,
um Artikeltext abzurufen und zu prüfen, bevor er in manual_articles.json übernommen wird.

Beispiel:
    python -c "
    import sys; sys.path.insert(0, '.')
    from scripts.debug.fetch_article import fetch_article_text
    print(fetch_article_text('https://trans.info/en/some-article'))
    "
"""

from __future__ import annotations

import logging

import trafilatura

logger = logging.getLogger(__name__)


def fetch_article_text(url: str) -> str:
    """Extrahiert den Volltext der URL mit trafilatura. Gibt bei Fehler einen leeren String zurück."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            logger.warning("fetch_article_text: no content downloaded from %s", url)
            return ""
        text = trafilatura.extract(downloaded, include_comments=False, favor_recall=True)
        if not text:
            logger.warning("fetch_article_text: trafilatura returned no text for %s", url)
            return ""
        return text.strip()
    except Exception as exc:
        logger.warning("fetch_article_text: failed for %s — %s", url, exc)
        return ""
