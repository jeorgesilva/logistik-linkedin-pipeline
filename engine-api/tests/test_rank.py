import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rank import dedup_and_rank


def _make_article(title: str, text: str = "", topic: str = "Logistik"):
    return {"title": title, "full_text": text, "topic": topic, "source_url": f"http://example.com/{hash(title)}"}


def test_dedup_removes_near_duplicates():
    articles = [
        _make_article("DHL erweitert Lager in Hamburg", "DHL baut neues Logistikzentrum in Hamburg"),
        _make_article("DHL erweitert sein Lager in Hamburg", "DHL baut neues Logistikzentrum in Hamburg"),
        _make_article("Frachtmarkt: Neue Routen nach Asien", "Asia Freight routes expand significantly for German shippers"),
    ]
    result = dedup_and_rank(articles, top_n=10)
    assert len(result) <= 2, "Near-duplicate should be removed"


def test_dedup_respects_top_n():
    articles = [_make_article(f"Artikel {i}", f"Unique logistics content number {i} about supply chain") for i in range(20)]
    result = dedup_and_rank(articles, top_n=5)
    assert len(result) <= 5


def test_dedup_handles_empty():
    result = dedup_and_rank([], top_n=10)
    assert result == []


def test_dedup_keeps_distinct():
    articles = [
        _make_article("DHL neues Lager", "Hamburg logistics expansion"),
        _make_article("Frachtmarkt Krise", "Fuel costs hit freight market"),
        _make_article("E-Commerce Boom", "Online retail drives last-mile delivery"),
    ]
    result = dedup_and_rank(articles, top_n=10)
    assert len(result) == 3
