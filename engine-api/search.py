"""Per-topic article search via SearXNG + DuckDuckGo HTML scraping.

Core function: search_topic(query, num_results, searxng_url)
    1. SearXNG (if searxng_url provided) — primary source
    2. DuckDuckGo HTML scraping — automatic fallback
    Social domains and listing/category URLs are filtered before results are returned.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SEARXNG_URL = os.getenv("SEARXNG_URL", "")

_REQUEST_TIMEOUT = 15

_DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
_DDG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "de,en;q=0.9",
}

SOCIAL_DOMAINS: frozenset[str] = frozenset({
    "twitter.com", "x.com", "facebook.com", "instagram.com",
    "reddit.com", "tiktok.com", "youtube.com", "linkedin.com",
    "t.me", "telegram.org",
})

_LISTING_PATH_PREFIXES: frozenset[str] = frozenset({
    "themen", "thema", "tag", "tags",
    "kategorie", "kategorien", "rubrik", "rubriken",
    "schlagwort", "schlagwoerter", "ressort",
    "autor", "autoren", "archiv",
    "topic", "topics", "category", "categories",
    "section", "sections", "author", "authors",
    "search", "suche", "newsletter",
})


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if not path.endswith("/"):
        path += "/"
    return parsed.netloc + path


def _is_social_domain(url: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc in SOCIAL_DOMAINS


def _is_listing_url(url: str) -> bool:
    path = urlparse(url).path
    # Root / homepage — never an article
    if not path or path == "/" or path == "":
        return True
    segments = [s for s in path.split("/") if s]
    if not segments:
        return True
    # Skip PDF/binary files
    if path.lower().endswith((".pdf", ".zip", ".doc", ".docx")):
        return True
    return segments[0].lower() in _LISTING_PATH_PREFIXES


def _searxng_search(query: str, num_results: int, base_url: str) -> list[dict]:
    try:
        resp = requests.get(
            f"{base_url}/search",
            params={
                "q": query,
                "format": "json",
                "categories": "news,general",
                "language": "de",
                "time_range": "month",
            },
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.debug("SearXNG unavailable: %s", exc)
        return []

    try:
        data = resp.json()
    except ValueError:
        logger.debug("SearXNG returned non-JSON response")
        return []

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        return []

    results: list[dict] = []
    for r in raw_results[:num_results]:
        url = (r.get("url") or "").strip()
        if not url:
            continue
        results.append({
            "url": url,
            "title": (r.get("title") or "").strip(),
            "snippet": (r.get("content") or "").strip(),
            "source_engine": "searxng",
        })

    logger.info("SearXNG: %d results for %r", len(results), query)
    return results


def _ddg_extract_results(soup: BeautifulSoup) -> list[dict]:
    results: list[dict] = []
    for div in soup.select("div.result"):
        a = div.select_one("a.result__a")
        if not a:
            continue
        href = a.get("href", "")
        try:
            if href.startswith(("http://", "https://")):
                url = href
            else:
                parsed_href = urlparse("https:" + href)
                url = unquote(parse_qs(parsed_href.query).get("uddg", [""])[0])
        except Exception:
            continue
        if not url.startswith(("http://", "https://")):
            continue
        snippet_elem = div.select_one("a.result__snippet")
        results.append({
            "url": url,
            "title": a.get_text(strip=True),
            "snippet": snippet_elem.get_text(strip=True) if snippet_elem else "",
            "source_engine": "duckduckgo",
        })
    return results


def _ddg_next_page_params(soup: BeautifulSoup) -> dict | None:
    nav = soup.find("div", class_="nav-link")
    if not nav:
        return None
    form = nav.find("form")
    if not form:
        return None
    params: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        value = inp.get("value", "")
        if name:
            params[name] = value
    return params if params else None


def _ddg_html_search(query: str, num_results: int = 30) -> list[dict]:
    session = requests.Session()
    session.headers.update(_DDG_HEADERS)

    post_data: dict[str, str] = {"q": query, "b": "", "kl": "de-de", "df": "m"}
    results: list[dict] = []

    for _page in range(3):
        try:
            resp = session.post(_DDG_ENDPOINT, data=post_data, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.debug("DuckDuckGo request failed: %s", exc)
            break

        soup = BeautifulSoup(resp.text, "lxml")
        page_results = _ddg_extract_results(soup)
        results.extend(page_results)

        if len(results) >= num_results:
            break

        next_params = _ddg_next_page_params(soup)
        if not next_params or not page_results:
            break
        post_data = next_params

    logger.info("DuckDuckGo: %d results for %r", len(results), query)
    return results[:num_results]


def search_topic(
    query: str,
    num_results: int = 20,
    searxng_url: str | None = None,
) -> list[dict]:
    """Search for articles using SearXNG (primary) + DuckDuckGo (fallback).

    Returns list of dicts: {url, title, snippet, source_engine}.
    """
    effective_url = searxng_url or SEARXNG_URL
    raw: list[dict] = []

    if effective_url:
        raw = _searxng_search(query, num_results, effective_url)

    if len(raw) < 20:
        ddg = _ddg_html_search(query, num_results=min(num_results, 30))
        existing = {_normalize_url(r["url"]) for r in raw}
        for r in ddg:
            nurl = _normalize_url(r["url"])
            if nurl not in existing:
                raw.append(r)
                existing.add(nurl)

    if not raw:
        raise RuntimeError(
            f"Both SearXNG and DuckDuckGo returned no results for {query!r}."
        )

    seen: set[str] = set()
    filtered: list[dict] = []
    for r in raw:
        url = r.get("url", "")
        if not url or _is_social_domain(url) or _is_listing_url(url):
            continue
        nurl = _normalize_url(url)
        if nurl in seen:
            continue
        seen.add(nurl)
        filtered.append(r)
        if len(filtered) >= num_results:
            break

    return filtered


def search_all_topics(
    topics: list[str],
    num_per_topic: int = 20,
    searxng_url: str | None = None,
) -> list[dict]:
    """Search multiple topics, deduplicate across them, return enriched dicts.

    Each result dict includes `topic` and `source_url` keys so it is compatible
    with the pipeline's article format.
    """
    seen_urls: set[str] = set()
    all_results: list[dict] = []

    for topic in topics:
        try:
            results = search_topic(topic, num_results=num_per_topic, searxng_url=searxng_url)
        except RuntimeError as e:
            logger.warning("search_topic failed for %r: %s", topic, e)
            continue

        for r in results:
            url = r["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            all_results.append({
                "topic": topic,
                "source_url": url,
                "title": r["title"],
                "rss_summary": r["snippet"],
                "source_engine": r["source_engine"],
            })

    logger.info(
        "search_all_topics: %d unique results across %d topics",
        len(all_results),
        len(topics),
    )
    return all_results
