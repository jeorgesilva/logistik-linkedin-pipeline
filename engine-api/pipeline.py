from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import pipeline_status
from agents import (
    build_bridge,
    evaluate_promotional_tone,
    evaluate_relevance,
    generate_image_prompt,
    generate_post,
)
from db import insert_post, list_posts, source_url_exists, update_post
from extract import extract_article
from images import generate_image
from rank import dedup_and_rank
from rss import fetch_rss_feeds
from seed_utils import make_seed

logger = logging.getLogger(__name__)

MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", "3"))
MIN_TEXT_CHARS = int(os.getenv("MIN_TEXT_CHARS", "500"))


def collect_articles(
    keywords: list[str] | None = None,  # aus Gründen der API-Kompatibilität beibehalten, im RSS-Modus ungenutzt
    max_articles: int | None = None,
) -> list[dict]:
    """Ruft Überschriften aus den RSS-Feeds ab und gibt die Top-N deduplizierten Artikel zurück.

    Wird vom Debug-Endpunkt /collect genutzt (nur Vorschau, durchläuft nicht den Trichter).
    run_batch nutzt stattdessen _collect_deduped_pool() — warum, steht in dessen Docstring.
    """
    n = max_articles or MAX_ARTICLES
    raw = fetch_rss_feeds()
    selected = dedup_and_rank(raw, top_n=n)
    logger.info("RSS → Dedup/Ranking: %d → %d Artikel ausgewählt", len(raw), len(selected))
    return selected


def _collect_deduped_pool() -> list[dict]:
    """Ruft die RSS-Feeds ab und dedupliziert den *gesamten* Pool, ohne Obergrenze.

    run_batch geht diesen Pool durch, bis `target_relevant` Artikel den Relevanzfilter
    (Schritt 1) passiert haben, statt wie früher die Kandidatenliste vorab auf
    MAX_ARTICLES zu begrenzen — dadurch konnte ein Lauf 10 Kandidaten auswählen, die
    meisten in Schritt 1 verwerfen und mit 0 Posts enden, obwohl Dutzende ungeprüfte
    Artikel im selben RSS-Batch lagen.
    """
    raw = fetch_rss_feeds()
    deduped = dedup_and_rank(raw, top_n=len(raw))
    logger.info("RSS → Dedup: %d → %d eindeutige Artikel verfügbar", len(raw), len(deduped))
    return deduped


def _process_from_relevance(art: dict, rel_motivo: str) -> int | None:
    """Schritte 2–4 — setzt voraus, dass `art` Schritt 1 (Relevanz) bereits bestanden hat."""
    try:
        # Volltext-Extraktion — nur für Artikel, die den Filter passiert haben
        extraction = extract_article(art["source_url"], art.get("rss_summary", ""))
        art.update(extraction)
        logger.info(
            "Volltext extrahiert: %d Zeichen für '%s'",
            len(art.get("full_text", "")),
            art.get("title", "")[:60],
        )

        # Schritt 2 — erzählerische Brücke (läuft auf dem vollständigen Artikeltext)
        bridge = build_bridge(art, relevance_motivo=rel_motivo)
        if not bridge:
            logger.info(
                "Verworfen: ungültige Brücke oder generische Auswirkung — '%s'",
                art.get("title", "")[:70],
            )
            return None
        logger.info(
            "=== [SCHRITT 2 — BRÜCKE] Auswirkung='%s' | Risiko='%s'",
            bridge.get("impacto_para_spedition", "")[:100],
            bridge.get("risco_ou_oportunidade", "")[:80],
        )

        # Schritt 3 — Erzeugung des Posts
        post_text = generate_post(art, bridge=bridge)
        if not post_text:
            logger.info(
                "Verworfen: Writer lieferte leeres Ergebnis — '%s'",
                art.get("title", "")[:70],
            )
            return None
        logger.info("=== [SCHRITT 3 — POST ERZEUGT] ===\n%s", post_text[:400])

        # Schritt 4 — Prüfung auf werblichen Ton
        ad_check = evaluate_promotional_tone(post_text)
        if ad_check and ad_check.get("soa_como_ad"):
            logger.info(
                "Verworfen: werblicher Ton erkannt — '%s' | %s",
                art.get("title", "")[:70],
                ad_check.get("motivo", "")[:120],
            )
            return None

        # Alle Filter bestanden — jetzt speichern
        seed = make_seed(art["source_url"])
        post_id = insert_post(
            topic=art.get("topic", ""),
            source_url=art.get("source_url", ""),
            title=art.get("title", ""),
            seed=seed,
            status="candidate",
        )

        image_prompt = generate_image_prompt(art, post_text)
        image_path = generate_image(post_id, image_prompt, seed)

        if not image_path and art.get("top_image"):
            image_path = art["top_image"]

        update_post(
            post_id,
            post_text=post_text,
            image_prompt=image_prompt,
            image_path=image_path,
            status="draft",
        )

        logger.info("Post %d erstellt: %s", post_id, art.get("title", "")[:60])
        return post_id

    except Exception as e:
        logger.error("Verarbeitung des Artikels '%s' fehlgeschlagen: %s", art.get("title", ""), e)
        return None


def _process_articles(articles: list[dict], target_posts: int) -> dict:
    """Geht `articles` (bereits dedupliziert, in Feed-Reihenfolge) durch, prüft bei jedem
    Schritt 1 und macht weiter — mit dem nächsten Kandidaten aus demselben Pool —, bis
    tatsächlich `target_posts` Posts erstellt wurden. Das Bestehen von Schritt 1 zählt für
    sich allein nicht zum Ziel: Die Schritte 2–4 sind unabhängige Qualitätsschranken, die den
    Artikel noch verwerfen können, und wenn sie das tun, geht die Suche weiter, statt vorzeitig
    abzubrechen.
    """
    created_ids = []
    relevant_count = 0
    scanned = 0

    for art in articles:
        if len(created_ids) >= target_posts:
            break
        scanned += 1
        pipeline_status.mark_scanned()

        if source_url_exists(art["source_url"]):
            logger.info("Doppelte source_url übersprungen: %s", art["source_url"][:70])
            continue

        # Schritt 1 — Relevanzfilter (läuft auf RSS-Überschrift + Zusammenfassung — schnell)
        relevance = evaluate_relevance(art)
        rel_flag = relevance.get("relevante") if relevance else None
        rel_motivo = (relevance.get("motivo", "") if relevance else "parse failed")[:150]
        logger.info(
            "=== [SCHRITT 1 — FILTER] relevant=%s | %s | '%s'",
            rel_flag,
            art.get("title", "")[:60],
            rel_motivo,
        )
        if not relevance or not rel_flag:
            logger.info("Verworfen wegen Irrelevanz: %s", art.get("title", "")[:70])
            continue

        relevant_count += 1
        pipeline_status.mark_relevant()

        post_id = _process_from_relevance(art, rel_motivo)
        if post_id is not None:
            created_ids.append(post_id)
            pipeline_status.mark_post_created()

    if len(created_ids) < target_posts:
        logger.info(
            "Pool vor Erreichen des Ziels erschöpft: %d/%d Posts erstellt (%d relevante von %d geprüften Artikeln, %d im Pool verfügbar)",
            len(created_ids),
            target_posts,
            relevant_count,
            scanned,
            len(articles),
        )

    return {
        "created": len(created_ids),
        "ids": created_ids,
        "relevant_found": relevant_count,
        "scanned": scanned,
    }


def run_batch(
    keywords: list[str] | None = None,
    max_articles: int | None = None,
) -> dict:
    target = max_articles or MAX_ARTICLES
    try:
        articles = _collect_deduped_pool()
        pipeline_status.set_target(target)
        result = _process_articles(articles, target_posts=target)
        pipeline_status.finish()
        return result
    except Exception as e:
        logger.error("Pipeline-Lauf fehlgeschlagen: %s", e)
        pipeline_status.finish(error=str(e))
        raise


def _regenerate_one(post: dict) -> dict:
    post_id = post["id"]
    source_url = post.get("source_url", "")
    try:
        article = extract_article(source_url, "")
        article["title"] = post.get("title", "")
        bridge = build_bridge(article)
        logger.info(
            "=== [SCHRITT 2 — BRÜCKE] Neugenerierung Post=%d | Auswirkung='%s'",
            post_id,
            (bridge.get("impacto_para_spedition", "") if bridge else "FEHLER")[:100],
        )
        post_text = generate_post(article, bridge=bridge)
        if not post_text:
            logger.warning("Agent lieferte leeres Ergebnis für Post %d", post_id)
            return {"id": post_id, "success": False, "error": "LLM returned empty"}
        logger.info(
            "=== [SCHRITT 3 — POST ERZEUGT] Neugenerierung Post=%d ===\n%s",
            post_id,
            post_text[:400],
        )
        update_post(post_id, post_text=post_text)
        logger.info("Text für Post %d neu generiert", post_id)
        return {"id": post_id, "success": True}
    except Exception as e:
        logger.error("Neugenerierung von Post %d fehlgeschlagen: %s", post_id, e)
        return {"id": post_id, "success": False, "error": str(e)}


def regenerate_missing_texts() -> dict:
    """Erzeugt post_text für jeden Post, bei dem er leer ist, parallel neu."""
    posts = [p for p in list_posts() if not p.get("post_text")]
    if not posts:
        return {"regenerated": 0, "failed": 0, "ids": []}

    results = []
    with ThreadPoolExecutor(max_workers=len(posts)) as executor:
        futures = {executor.submit(_regenerate_one, p): p["id"] for p in posts}
        for future in as_completed(futures):
            results.append(future.result())

    succeeded = [r["id"] for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]
    logger.info("%d Posts neu generiert, %d fehlgeschlagen", len(succeeded), len(failed))
    return {"regenerated": len(succeeded), "failed": len(failed), "ids": succeeded}
