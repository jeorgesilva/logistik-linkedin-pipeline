from __future__ import annotations

import logging
import os
import re
from itertools import zip_longest

import feedparser

logger = logging.getLogger(__name__)

RSS_MAX_PER_FEED = int(os.getenv("RSS_MAX_PER_FEED", "20"))

# Kuratierte Standard-Feeds — Fokus Logistik DE/EU (FreightWaves/TTNews am 16.07.2026
# per Nutzerentscheidung entfernt, um die Liste auf deutsche/europäische Logistik zu beschränken).
# DVZ (dvz.de) und Verkehrs-Rundschau (verkehrsrundschau.de) stehen hinter einer Paywall
# und bieten keinen öffentlichen RSS-Feed; bei Zugang über RSS_FEEDS konfigurieren.
#
# Destatis, Bundesnetzagentur, BALM, EUR-Lex und BMAS liefern jeweils den kompletten
# Presse-/Gesetzgebungs-Feed der Institution, nicht vorab auf Logistik gefiltert —
# evaluate_relevance() verwirft nachgelagert themenfremde Artikel, daher ist pro Lauf mit mehr
# Rauschen zu rechnen als bei den enger gefassten Fachpresse-Feeds unten.
#
# 21.07.2026: MM Logistik + Logistik Express ergänzt (Lücke bei KI/Technik in der Logistik und
# TMS/Software, festgestellt im Nischen-Abgleich) sowie BMAS (Lücke Arbeitsmarkt/
# Fachkräftemangel). Für das Fraunhofer IML selbst wurde kein funktionierender öffentlicher
# RSS-Feed gefunden, obwohl dessen Inhalte thematisch am besten passen (Übersichten zu SAP/ERP-
# Partnern, Whitepaper zu KI in der Logistik) — es gibt nur den Feed aller Institute unter
# fraunhofer.de/en/rss/press.rss, der ungefiltert zu breit/verrauscht ist. Dasselbe gilt für
# eine eigene Quelle zu Speditions-Insolvenzen: Creditreform und DSLV decken das Thema ab,
# bieten aber keinen RSS-Feed. Ein Google-News-Such-RSS könnte die Insolvenz-Berichterstattung
# ersetzen — unten nicht eingebunden, da er beim Test aus dieser Sitzung leer zurückkam und
# zuerst mit einem echten Client geprüft werden muss:
#   https://news.google.com/rss/search?q=(Spedition+OR+Logistik)+(Insolvenz+OR+Pleite)&hl=de&gl=DE&ceid=DE:de
_DEFAULT_FEEDS: list[dict] = [
    {"url": "https://logistik-heute.de/rss.xml", "topic": "Logistik Heute"},
    {"url": "https://www.logistra.de/rss.xml", "topic": "Logistra"},
    {"url": "https://logistik-news24.de/feed", "topic": "Logistik News24"},
    # /rss/logistik (nicht /rss/alle oder /rss/test) — am 21.07.2026 bestätigt, dass Artikel
    # hier mit resetPaywallArticle:true laden (kostenlos); der /test-Feed enthält die
    # kostenpflichtigen Vergleichstests der Vogel Communications Group.
    {"url": "https://www.eurotransport.de/rss/logistik", "topic": "Eurotransport"},
    # Fachpresse zu TMS/WMS/Intralogistik — deckt die Lücke bei Software/ERP ab.
    # Vogel Communications Group, dieselbe Verlagsgruppe wie Eurotransport oben; vor dauerhaftem
    # Vertrauen genauso stichprobenartig auf Paywall-Artikel prüfen.
    # Feed am 21.07.2026 als aktiv bestätigt (echte aktuelle Einträge, RSS 2.0).
    {"url": "https://www.mm-logistik.vogel.de/rss/news.xml", "topic": "MM Logistik"},
    # Österreichische/DACH-Fachpresse, breitere Berichterstattung zu Digitalisierung/Technik in der
    # Logistik als die rein deutschen Titel oben. Feed am 21.07.2026 als aktiv bestätigt.
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
    # Bundesministerium für Arbeit und Soziales — Lücke Arbeitsmarkt/Fachkräftemangel.
    # Auf der eigenen Website als offizieller allgemeiner RSS-Feed des BMAS (RSS 2.0)
    # dokumentiert; aus dieser Sitzung nicht unabhängig als aktiv bestätigt (der Abruf kam hier
    # leer zurück, wie bei mehreren anderen .xml-Endpunkten von Behörden) — beim ersten echten
    # Lauf prüfen, bevor man sich darauf verlässt.
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
    """Parst die Umgebungsvariable RSS_FEEDS. Format: 'url|Topic;url2|Topic2' oder nur 'url1;url2'."""
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
    """Ruft alle konfigurierten RSS-Feeds ab und gibt normalisierte Artikel-Dicts zurück,
    im Round-Robin-Verfahren über die Feeds verschränkt (ein Artikel pro Feed und Runde).

    Jeder Artikel hat: source_url, title, rss_summary, full_text (= anfangs die Zusammenfassung),
    topic, pub_date. Der Volltext wird nach der Relevanzprüfung durch extract_article() ersetzt.

    Warum Round-Robin statt Feed-für-Feed-Aneinanderreihung: collect_articles() begrenzt
    den Pool über dedup_and_rank() auf MAX_ARTICLES, *bevor* irgendein Relevanzfilter
    läuft, und dedup_and_rank() behält einfach die ersten top_n eindeutigen Artikel in
    Listenreihenfolge. Bei simpler Aneinanderreihung würden die zuerst in _DEFAULT_FEEDS
    (oder RSS_FEEDS) aufgeführten Feeds alle anderen stillschweigend aus dem Lauf
    verdrängen, sobald sie allein >= MAX_ARTICLES Einträge liefern. Die Verschränkung gibt
    jedem Feed unabhängig von seiner Position in der Konfiguration eine anteilige Chance.
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

                # Auch feed-übergreifend deduplizieren (nicht nur innerhalb dieses Feeds): Der erste Feed in
                # `feeds`, der eine URL liefert, behält sie, spätere Feeds überspringen sie.
                if not url or not title or url in seen_urls:
                    continue
                seen_urls.add(url)

                feed_articles.append({
                    "topic": feed["topic"],
                    "source_url": url,
                    "title": title,
                    "rss_summary": summary,
                    "full_text": summary,  # wird nach der Relevanzprüfung von extract_article überschrieben
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
