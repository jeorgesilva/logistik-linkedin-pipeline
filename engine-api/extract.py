import logging
from typing import Optional

import trafilatura

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; LogistikBot/1.0; +https://logaid.ai)"
    )
}
_TIMEOUT = 20


def _extract_with_trafilatura(url: str) -> tuple[Optional[str], Optional[str]]:
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None, None
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            favor_recall=True,
        )
        meta = trafilatura.extract_metadata(downloaded)
        image = meta.image if meta and meta.image else None
        return text, image
    except Exception as e:
        logger.debug("trafilatura failed for %s: %s", url, e)
        return None, None


def _extract_with_crawl4ai(url: str) -> tuple[Optional[str], Optional[str]]:
    try:
        import asyncio
        from crawl4ai import AsyncWebCrawler

        async def _crawl():
            async with AsyncWebCrawler(verbose=False) as crawler:
                result = await crawler.arun(url=url)
                return result.markdown or result.extracted_content, None

        return asyncio.run(_crawl())
    except Exception as e:
        logger.debug("crawl4ai failed for %s: %s", url, e)
        return None, None


def extract_article(url: str, rss_summary: str = "") -> dict:
    """Return {full_text, top_image} for the given URL."""
    text, image = _extract_with_trafilatura(url)

    if not text or len(text.strip()) < 100:
        logger.info("trafilatura insufficient for %s, trying crawl4ai", url)
        text, image = _extract_with_crawl4ai(url)

    if not text or len(text.strip()) < 50:
        logger.warning("All extractors failed for %s; using RSS summary", url)
        text = rss_summary

    return {
        "full_text": (text or "").strip(),
        "top_image": image,
    }
