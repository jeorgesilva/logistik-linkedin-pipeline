import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from extract import extract_article


def test_extract_falls_back_to_rss_summary():
    with patch("extract._extract_with_trafilatura", return_value=(None, None)), \
         patch("extract._extract_with_crawl4ai", return_value=(None, None)):
        result = extract_article("http://example.com", rss_summary="Fallback summary")
    assert result["full_text"] == "Fallback summary"
    assert result["top_image"] is None


def test_extract_uses_trafilatura_when_available():
    with patch("extract._extract_with_trafilatura", return_value=("Long article text " * 10, "http://img.example.com/a.jpg")):
        result = extract_article("http://example.com")
    assert "Long article text" in result["full_text"]
    assert result["top_image"] == "http://img.example.com/a.jpg"


def test_extract_falls_back_to_crawl4ai_on_short_text():
    with patch("extract._extract_with_trafilatura", return_value=("short", None)), \
         patch("extract._extract_with_crawl4ai", return_value=("Crawl4ai full article text " * 5, None)):
        result = extract_article("http://example.com")
    assert "Crawl4ai" in result["full_text"]
