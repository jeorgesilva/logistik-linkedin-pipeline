from __future__ import annotations

import logging
import os
import re
from itertools import zip_longest

import feedparser

logger = logging.getLogger(__name__)

RSS_MAX_PER_FEED = int(os.getenv("RSS_MAX_PER_FEED", "20"))

# Default curated feeds — DE/EU logistics focus (FreightWaves/TTNews dropped by
# user decision on 2026-07-16 to keep the list scoped to German/EU logistics).
# DVZ (dvz.de) and Verkehrs-Rundschau (verkehrsrundschau.de) are behind paywalls
# and do not expose public RSS; configure via RSS_FEEDS if you have access.
#
# Destatis, Bundesnetzagentur, BALM, EUR-Lex and BMAS are each institution's
# full press/legislation feed, not pre-filtered to logistics — evaluate_relevance()
# downstream discards off-topic articles, so expect more noise per run than the
# narrower trade-press feeds below.
#
# 2026-07-21: added MM Logistik + Logistik Express (AI/tech-in-logistics and
# TMS/software gap identified in niche-alignment review) and BMAS (labor-market
# / Fachkräftemangel gap). No working public RSS found for Fraunhofer IML
# specifically, despite its content being the closest topical match (SAP/ERP
# partner overviews, KI-in-Logistik whitepapers) — only the all-institutes
# feed at fraunhofer.de/en/rss/press.rss exists, and it's too broad/noisy to
# add unfiltered. Same for a dedicated Spedition-insolvency source: Creditreform
# and DSLV cover the topic but expose no RSS. A Google News search-RSS can
# stand in for insolvency coverage — not wired in below since it returned empty
# when tested from this session and needs verification with a real client first:
#   https://news.google.com/rss/search?q=(Spedition+OR+Logistik)+(Insolvenz+OR+Pleite)&hl=de&gl=DE&ceid=DE:de
_DEFAULT_FEEDS: list[dict] = [
    {"url": "https://logistik-heute.de/rss.xml", "topic": "Logistik Heute"},
    {"url": "https://www.logistra.de/rss.xml", "topic": "Logistra"},
    {"url": "https://logistik-news24.de/feed", "topic": "Logistik News24"},
    # /rss/logistik (not /rss/alle or /rss/test) — confirmed 2026-07-21 that
    # articles here load with resetPaywallArticle:true (free); the /test feed
    # is Vogel Communications Group's paid comparison-test content.
    {"url": "https://www.eurotransport.de/rss/logistik", "topic": "Eurotransport"},
    # TMS/WMS/intralogistics trade press — covers the software/ERP gap.
    # Vogel Communications Group, same publisher family as Eurotransport above;
    # spot-check for paywalled articles the same way before trusting long-term.
    # Feed confirmed live 2026-07-21 (real recent items, RSS 2.0).
    {"url": "https://www.mm-logistik.vogel.de/rss/news.xml", "topic": "MM Logistik"},
    # Austrian/DACH trade press, broader digitalization/tech-in-logistics
    # coverage than the DE-only titles above. Feed confirmed live 2026-07-21.
    {"url": "https://www.logistik-express.com/feed", "topic": "Logistik Express"},
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
    # Bundesministerium für Arbeit und Soziales — labor-market/Fachkräftemangel
    # gap. Documented as BMAS's official general RSS feed (RSS 2.0) on their
    # own site; not independently confirmed live from this session (fetch
    # returned empty here, same as several other .xml gov endpoints) — verify
    # on first real run before relying on it.
    {
        "url": "https://www.bmas.de/SiteGlobals/Functions/RSSFeed/DE/RSSNewsfeed/RSSNewsfeed.xml",
        "topic": "BMAS",
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


def fetch_rss_feeds(feed_configs: list[dict] | None = None) -> list[dict]:
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
