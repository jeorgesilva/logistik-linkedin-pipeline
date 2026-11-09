import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import rss


def _feed(entries):
    return SimpleNamespace(entries=entries)


def _entry(url: str, title: str, summary: str = "s"):
    return {"link": url, "title": title, "summary": summary, "published": ""}


def _fake_parse(feed_map):
    def _parse(url, **kwargs):
        return _feed(feed_map[url])
    return _parse


def test_fetch_rss_feeds_interleaves_round_robin():
    feeds = [
        {"url": "http://feed-a", "topic": "A"},
        {"url": "http://feed-b", "topic": "B"},
    ]
    feed_map = {
        "http://feed-a": [_entry(f"http://a.example/{i}", f"A{i}") for i in range(3)],
        "http://feed-b": [_entry(f"http://b.example/{i}", f"B{i}") for i in range(3)],
    }

    with patch("rss.feedparser.parse", side_effect=_fake_parse(feed_map)):
        articles = rss.fetch_rss_feeds(feeds)

    assert [a["topic"] for a in articles] == ["A", "B", "A", "B", "A", "B"]


def test_fetch_rss_feeds_handles_uneven_feed_sizes():
    feeds = [
        {"url": "http://feed-a", "topic": "A"},
        {"url": "http://feed-b", "topic": "B"},
    ]
    feed_map = {
        "http://feed-a": [_entry("http://a.example/0", "A0")],
        "http://feed-b": [_entry(f"http://b.example/{i}", f"B{i}") for i in range(3)],
    }

    with patch("rss.feedparser.parse", side_effect=_fake_parse(feed_map)):
        articles = rss.fetch_rss_feeds(feeds)

    # Feed A ist nach Runde 1 erschöpft; Feed B füllt die folgenden Runden weiter auf.
    assert [a["topic"] for a in articles] == ["A", "B", "B", "B"]


def test_fetch_rss_feeds_first_feed_wins_cross_feed_duplicate():
    shared_url = "http://shared.example/1"
    feeds = [
        {"url": "http://feed-a", "topic": "A"},
        {"url": "http://feed-b", "topic": "B"},
    ]
    feed_map = {
        "http://feed-a": [_entry(shared_url, "Shared")],
        "http://feed-b": [_entry(shared_url, "Shared duplicate")],
    }

    with patch("rss.feedparser.parse", side_effect=_fake_parse(feed_map)):
        articles = rss.fetch_rss_feeds(feeds)

    assert len(articles) == 1
    assert articles[0]["topic"] == "A"


def test_fetch_rss_feeds_one_feed_erroring_does_not_drop_others():
    feeds = [
        {"url": "http://feed-a", "topic": "A"},
        {"url": "http://feed-b", "topic": "B"},
    ]

    def _parse(url, **kwargs):
        if url == "http://feed-a":
            raise RuntimeError("network error")
        return _feed([_entry("http://b.example/0", "B0")])

    with patch("rss.feedparser.parse", side_effect=_parse):
        articles = rss.fetch_rss_feeds(feeds)

    assert [a["topic"] for a in articles] == ["B"]
