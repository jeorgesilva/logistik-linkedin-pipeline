"""On-demand full-text extractor for a single URL.

NOT called automatically by the pipeline. Use this interactively or from a script
to fetch and inspect article text before adding it to manual_articles.json.

Example:
    python -c "
    import sys; sys.path.insert(0, '.')
    from tests.fetch_article import fetch_article_text
    print(fetch_article_text('https://trans.info/en/some-article'))
    "
"""

from __future__ import annotations

import logging

import trafilatura

logger = logging.getLogger(__name__)


def fetch_article_text(url: str) -> str:
    """Extract full text from url using trafilatura. Returns empty string on failure."""
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
