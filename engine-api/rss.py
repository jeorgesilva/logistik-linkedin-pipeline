from __future__ import annotations

import logging
import os
import re
from itertools import zip_longest
from typing import Optional

import feedparser

logger = logging.getLogger(__name__)

RSS_MAX_PER_FEED = int(os.getenv("RSS_MAX_PER_FEED", "20"))

# Default curated feeds — DE/EU logistics focus (FreightWaves/TTNews dropped by
# user decision on 2026-07-16 to keep the list scoped to German/EU logistics).
# DVZ (dvz.de) and Verkehrs-Rundschau (verkehrsrundschau.de) are behind paywalls
# and do not expose public RSS; configure via RSS_FEEDS if you have access.
#
# Destatis, Bundesnetzagentur, BALM and EUR-Lex are each institution's full
# press/legislation feed, not pre-filtered to logistics — evaluate_relevance()
# downstream discards off-topic articles, so expect more noise per run than the
# narrower trade-press feeds below.
_DEFAULT_FEEDS: list[dict] = [
    {"url": "https://logistik-heute.de/rss.xml", "topic": "Logistik Heute"},
    {"url": "https://www.logistra.de/rss.xml", "topic": "Logistra"},
    {"url": "https://logistik-news24.de/feed", "topic": "Logistik News24"},
    {
        "url": "https://www.destatis.de/SiteGlobals/Functions/RSSFeed/DE/RSSNewsfeed/Aktuell.xml?nn=3624",
        "topic": "Destatis",
    },
    {
        "url": "https://www.bundesnetzagentur.de/SiteGlobals/Functions/RSSFeed/DE/RSSNewsfeed/RSSNewsfeed_Pressemitteilungen.xml?nn=654666",
        "topic": "Bundesnetzagentur",
    },
    {
        "url": "https://www.balm.bund.de/SiteGlobals/Functions/RSSFeed/rssnewsfeed.xml?nn=539648",
        "topic": "BALM",
    },
    {
        "url": "https://eur-lex.europa.eu/EN/display-feed.rss?rssId=222",
        "topic": "EUR-Lex",
    },
    {
        "url": "https://ec.europa.eu/commission/presscorner/api/rss?text=transport",
        "topic": "DG MOVE",
    },
]

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _HTML_TAG.sub(" ", text).strip()


def _parse_feed_configs(raw: str) -> list[dict]:
    """Parse RSS_FEEDS env var. Format: 'url|Topic;url2|Topic2' or just 'url1;url2'."""
    configs = []
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("|", 1)
        url = parts[0].strip()
        topic = parts[1].strip() if len(parts) > 1 else url.split("/")[2]
        configs.append({"url": url, "topic": topic})
    return configs


def _get_feed_configs() -> list[dict]:
    raw = os.getenv("RSS_FEEDS", "")
    return _parse_feed_configs(raw) if raw.strip() else _DEFAULT_FEEDS


def fetch_rss_feeds(feed_configs: Optional[list[dict]] = None) -> list[dict]:
    """Fetch all configured RSS feeds and return normalised article dicts,
    interleaved round-robin across feeds (one article per feed per round).

    Each article has: source_url, title, rss_summary, full_text (= summary initially),
    topic, pub_date. Full text is replaced by extract_article() after the relevance check.

    Why round-robin instead of feed-by-feed concatenation: collect_articles() caps
    the pool at MAX_ARTICLES via dedup_and_rank() *before* any relevance filtering
    runs, and dedup_and_rank() simply keeps the first top_n unique articles in list
    order. A straight concatenation would let whichever feeds are listed first in
    _DEFAULT_FEEDS (or RSS_FEEDS) silently starve every other feed out of the run
    whenever they alone produce >= MAX_ARTICLES entries. Interleaving gives every
    feed a proportional shot at the cap regardless of its position in the config.
    """
    feeds = feed_configs or _get_feed_configs()
    seen_urls: set[str] = set()
    per_feed: list[list[dict]] = []

    for feed in feeds:
        feed_articles: list[dict] = []
        try:
            parsed = feedparser.parse(
                feed["url"],
                request_headers={"User-Agent": _UA},
            )
            for entry in parsed.entries[:RSS_MAX_PER_FEED]:
                url = (entry.get("link") or "").strip()
                title = (entry.get("title") or "").strip()
                raw_summary = entry.get("summary") or entry.get("description") or ""
                summary = _strip_html(raw_summary).strip()

                # Dedup across feeds too (not just within this one): first feed in
                # `feeds` to surface a given URL keeps it, later feeds skip it.
                if not url or not title or url in seen_urls:
                    continue
                seen_urls.add(url)

                feed_articles.append({
                    "topic": feed["topic"],
                    "source_url": url,
                    "title": title,
                    "rss_summary": summary,
                    "full_text": summary,  # overwritten by extract_article after relevance check
                    "pub_date": entry.get("published", ""),
                })

            logger.info("RSS %s: %d articles", feed["topic"], len(feed_articles))
        except Exception as e:
            logger.error("RSS feed error for %s: %s", feed.get("url", "?"), e)

        per_feed.append(feed_articles)

    articles: list[dict] = []
    for round_slot in zip_longest(*per_feed):
        for art in round_slot:
            if art is not None:
                articles.append(art)

    logger.info("RSS total: %d unique articles from %d feeds", len(articles), len(feeds))
    return articles
