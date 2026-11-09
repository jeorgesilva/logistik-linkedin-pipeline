"""Hintergrund-Poller für die Warteschlange bei ausgeschaltetem Rechner.

Ermöglicht einem Teammitglied, auf der kleinen, immer erreichbaren Seite des Cloudflare
Workers (cloudflare-worker/) auf „Posts generieren“ zu klicken, auch wenn dieser Rechner
offline ist. Die Anfrage liegt im KV-Store des Workers, bis dieser Poller — der nur läuft,
solange die engine-api selbst läuft — sie abholt, die Pipeline ausführt und das Ergebnis
zurückmeldet.

No-op, wenn QUEUE_WORKER_URL / QUEUE_SHARED_SECRET nicht gesetzt sind; kann daher gefahrlos
auch in Umgebungen aktiviert bleiben, die die Warteschlange nicht nutzen.
"""
from __future__ import annotations

import asyncio
import logging
import os

import requests

from pipeline import run_batch

logger = logging.getLogger(__name__)

QUEUE_WORKER_URL = os.getenv("QUEUE_WORKER_URL", "").rstrip("/")
QUEUE_SHARED_SECRET = os.getenv("QUEUE_SHARED_SECRET", "")
QUEUE_POLL_INTERVAL_SECONDS = int(os.getenv("QUEUE_POLL_INTERVAL_SECONDS", "60"))


def _auth_headers() -> dict:
    # Beim Aufruf erzeugt (nicht beim Laden des Moduls), damit immer der aktuelle Wert von
    # QUEUE_SHARED_SECRET verwendet wird — wichtig für Tests, die ihn per Monkeypatch setzen.
    return {"X-Queue-Secret": QUEUE_SHARED_SECRET}


def poll_once() -> bool:
    """Fragt beim Worker eine offene Anfrage ab und verarbeitet sie, falls vorhanden.

    Gibt True zurück, wenn eine Anfrage gefunden (und verarbeitet, erfolgreich oder nicht)
    wurde, False, wenn die Warteschlange deaktiviert oder nichts offen ist. Absichtlich
    blockierend — über asyncio.to_thread aus poll_loop() aufrufen.
    """
    if not QUEUE_WORKER_URL or not QUEUE_SHARED_SECRET:
        return False

    try:
        resp = requests.get(
            f"{QUEUE_WORKER_URL}/api/pending",
            headers=_auth_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Queue poll failed (worker unreachable?): %s", e)
        return False

    if not data.get("pending"):
        return False

    requested_at = data.get("requested_at")
    logger.info("Queue: pending request found (requested_at=%s) — running pipeline", requested_at)

    try:
        result = run_batch()
        summary = f"{result.get('created', 0)} posts criados."
        _report_complete(status="done", requested_at=requested_at, result_summary=summary)
        logger.info("Queue: request processed — %s", summary)
    except Exception as e:
        logger.error("Queue: pipeline run failed — %s", e)
        _report_complete(status="error", requested_at=requested_at, result_summary=str(e)[:300])

    return True


def _report_complete(*, status: str, requested_at: str, result_summary: str) -> None:
    try:
        requests.post(
            f"{QUEUE_WORKER_URL}/api/complete",
            headers={**_auth_headers(), "Content-Type": "application/json"},
            json={
                "status": status,
                "requested_at": requested_at,
                "result_summary": result_summary,
            },
            timeout=15,
        )
    except Exception as e:
        logger.error("Queue: failed to report completion back to the worker: %s", e)


async def poll_loop() -> None:
    """Führt poll_once() zeitgesteuert aus, solange die engine-api läuft."""
    if not QUEUE_WORKER_URL or not QUEUE_SHARED_SECRET:
        logger.info("Queue poller disabled (QUEUE_WORKER_URL/QUEUE_SHARED_SECRET not set)")
        return

    logger.info(
        "Queue poller started — checking %s every %ds",
        QUEUE_WORKER_URL,
        QUEUE_POLL_INTERVAL_SECONDS,
    )
    while True:
        try:
            await asyncio.to_thread(poll_once)
        except Exception as e:
            logger.error("Queue poller loop error: %s", e)
        await asyncio.sleep(QUEUE_POLL_INTERVAL_SECONDS)
