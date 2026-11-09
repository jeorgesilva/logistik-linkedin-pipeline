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
    keywords: list[str] | None = None,  # kept for API compat, not used in RSS mode
    max_articles: int | None = None,
) -> list[dict]:
    """Fetch headlines from RSS feeds and return top N deduplicated articles.

    Used by the /collect debug endpoint (preview only, doesn't run the funnel).
    run_batch uses _collect_deduped_pool() instead — see its docstring for why.
    """
    n = max_articles or MAX_ARTICLES
    raw = fetch_rss_feeds()
    selected = dedup_and_rank(raw, top_n=n)
    logger.info("RSS → dedup/rank: %d → %d articles selected", len(raw), len(selected))
    return selected


def _collect_deduped_pool() -> list[dict]:
    """Fetch RSS feeds and dedup the *entire* pool, uncapped.

    run_batch walks this pool until `target_relevant` articles have passed
    the relevance filter (Etapa 1), instead of the old behaviour of capping
    the candidate list to MAX_ARTICLES upfront — that meant a run could
    select 10 candidates, watch most get discarded by Etapa 1, and finish
    with 0 posts even though dozens of untried articles were sitting in the
    same RSS batch.
    """
    raw = fetch_rss_feeds()
    deduped = dedup_and_rank(raw, top_n=len(raw))
    logger.info("RSS → dedup: %d → %d unique articles available", len(raw), len(deduped))
    return deduped


def _process_from_relevance(art: dict, rel_motivo: str) -> int | None:
    """Etapas 2-4 — assumes Etapa 1 (relevância) already passed for `art`."""
    try:
        # Full-text extraction — only for articles that passed the filter
        extraction = extract_article(art["source_url"], art.get("rss_summary", ""))
        art.update(extraction)
        logger.info(
            "Full text extracted: %d chars for '%s'",
            len(art.get("full_text", "")),
            art.get("title", "")[:60],
        )

        # Etapa 2 — Ponte narrativa (runs on full article text)
        bridge = build_bridge(art, relevance_motivo=rel_motivo)
        if not bridge:
            logger.info(
                "Descartado: bridge inválida ou impacto genérico — '%s'",
                art.get("title", "")[:70],
            )
            return None
        logger.info(
            "=== [ETAPA 2 — PONTE] impacto='%s' | risco='%s'",
            bridge.get("impacto_para_spedition", "")[:100],
            bridge.get("risco_ou_oportunidade", "")[:80],
        )

        # Etapa 3 — Geração do post
        post_text = generate_post(art, bridge=bridge)
        if not post_text:
            logger.info(
                "Descartado: writer retornou vazio — '%s'",
                art.get("title", "")[:70],
            )
            return None
        logger.info("=== [ETAPA 3 — POST GERADO] ===\n%s", post_text[:400])

        # Etapa 4 — Verificação de tom promocional
        ad_check = evaluate_promotional_tone(post_text)
        if ad_check and ad_check.get("soa_como_ad"):
            logger.info(
                "Descartado: tom promocional detectado — '%s' | %s",
                art.get("title", "")[:70],
                ad_check.get("motivo", "")[:120],
            )
            return None

        # Todos os filtros passaram — persiste agora
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

        logger.info("Post %d created: %s", post_id, art.get("title", "")[:60])
        return post_id

    except Exception as e:
        logger.error("Failed processing article '%s': %s", art.get("title", ""), e)
        return None


def _process_articles(articles: list[dict], target_posts: int) -> dict:
    """Walk `articles` (already deduped, in feed order) testing Etapa 1 on
    each one, and keep going — pulling the next candidate from the same
    pool — until `target_posts` posts have actually been created. Passing
    Etapa 1 does not by itself count towards the target: Etapas 2-4 are
    independent quality gates that can still discard the article, and if
    they do, the search keeps going instead of stopping short.
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
            logger.info("Skipping duplicate source_url: %s", art["source_url"][:70])
            continue

        # Etapa 1 — Filtro de relevância (runs on RSS headline + summary — fast)
        relevance = evaluate_relevance(art)
        rel_flag = relevance.get("relevante") if relevance else None
        rel_motivo = (relevance.get("motivo", "") if relevance else "parse failed")[:150]
        logger.info(
            "=== [ETAPA 1 — FILTRO] relevante=%s | %s | '%s'",
            rel_flag,
            art.get("title", "")[:60],
            rel_motivo,
        )
        if not relevance or not rel_flag:
            logger.info("Descartado por irrelevância: %s", art.get("title", "")[:70])
            continue

        relevant_count += 1
        pipeline_status.mark_relevant()

        post_id = _process_from_relevance(art, rel_motivo)
        if post_id is not None:
            created_ids.append(post_id)
            pipeline_status.mark_post_created()

    if len(created_ids) < target_posts:
        logger.info(
            "Pool esgotado antes da meta: %d/%d posts criados (%d artigos relevantes de %d escaneados, %d disponíveis no pool)",
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
        logger.error("Pipeline run failed: %s", e)
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
            "=== [ETAPA 2 — PONTE] regenerate post=%d | impacto='%s'",
            post_id,
            (bridge.get("impacto_para_spedition", "") if bridge else "ERRO")[:100],
        )
        post_text = generate_post(article, bridge=bridge)
        if not post_text:
            logger.warning("Agent returned empty for post %d", post_id)
            return {"id": post_id, "success": False, "error": "LLM returned empty"}
        logger.info(
            "=== [ETAPA 3 — POST GERADO] regenerate post=%d ===\n%s",
            post_id,
            post_text[:400],
        )
        update_post(post_id, post_text=post_text)
        logger.info("Regenerated text for post %d", post_id)
        return {"id": post_id, "success": True}
    except Exception as e:
        logger.error("Failed regenerating post %d: %s", post_id, e)
        return {"id": post_id, "success": False, "error": str(e)}


def regenerate_missing_texts() -> dict:
    """Regenerate post_text for every post where it is empty, in parallel."""
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
    logger.info("Regenerated %d posts, %d failed", len(succeeded), len(failed))
    return {"regenerated": len(succeeded), "failed": len(failed), "ids": succeeded}
