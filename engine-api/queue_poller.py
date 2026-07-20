"""Background poller for the offline-friendly request queue.

Lets a colleague click "Posts generieren" on the small always-on Cloudflare Worker
page (cloudflare-worker/) even while this machine is offline. The request
sits in the Worker's KV store until this poller — which only runs while
engine-api itself is up — picks it up, runs the pipeline, and reports the
result back.

No-op if QUEUE_WORKER_URL / QUEUE_SHARED_SECRET are not set, so this is safe
to leave enabled in environments that don't use the queue feature.
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
    # Built at call time (not module load time) so the current value of
    # QUEUE_SHARED_SECRET is always used — matters for tests that monkeypatch it.
    return {"X-Queue-Secret": QUEUE_SHARED_SECRET}


def poll_once() -> bool:
    """Check the Worker for one pending request; process it if found.

    Returns True if a request was found (and processed, successfully or not),
    False if the queue feature is disabled or nothing is pending. Blocking by
    design — call it via asyncio.to_thread from poll_loop().
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
    """Run poll_once() on a timer for as long as engine-api is up."""
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
