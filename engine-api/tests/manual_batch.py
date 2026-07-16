"""Manual article injection — feeds pre-curated articles into the existing pipeline.

Usage (from engine-api/ directory):
    python -m tests.manual_batch

Edit tests/manual_articles.json to add or update articles before running.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Ensure engine-api/ is on the path when run as `python -m tests.manual_batch`
sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import _process_articles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("manual_batch")

_JSON_PATH = Path(__file__).parent / "manual_articles.json"


def _normalize(article: dict) -> dict:
    """Add fields required by _process_articles that are not in the JSON schema."""
    art = dict(article)
    art.setdefault("topic", "Manual")
    # rss_summary is the extraction fallback: if the URL is paywalled or unreachable,
    # extract_article() falls back to rss_summary — so we seed it with our full_text.
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
