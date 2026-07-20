"""Background reporter for engine-api's current public URL.

engine-api sits behind a free Cloudflare Quick Tunnel (cloudflared, see
docker-compose.yml), whose hostname is randomly regenerated every time that
container restarts. This polls cloudflared's own metrics endpoint for the
current hostname and, whenever it changes, pushes it to the Cloudflare
Worker's KV store (same Worker/secret as queue_poller.py) so
cloudflare-dashboard can discover it automatically instead of requiring a
human to paste a fresh URL in after every restart.

No-op if QUEUE_WORKER_URL / QUEUE_SHARED_SECRET are not set, or if
cloudflared's metrics endpoint isn't reachable (e.g. a named-tunnel setup
without Quick Tunnel) — safe to leave enabled everywhere.
"""
from __future__ import annotations

import asyncio
import logging
import os

import requests

logger = logging.getLogger(__name__)

QUEUE_WORKER_URL = os.getenv("QUEUE_WORKER_URL", "").rstrip("/")
QUEUE_SHARED_SECRET = os.getenv("QUEUE_SHARED_SECRET", "")
CLOUDFLARED_METRICS_URL = os.getenv(
    "CLOUDFLARED_METRICS_URL", "http://cloudflared:20241/quicktunnel"
)
TUNNEL_REPORT_INTERVAL_SECONDS = int(os.getenv("TUNNEL_REPORT_INTERVAL_SECONDS", "30"))

_last_reported_url: str | None = None


def _auth_headers() -> dict:
    return {"X-Queue-Secret": QUEUE_SHARED_SECRET, "Content-Type": "application/json"}


def check_and_report_once() -> bool:
    """Check cloudflared for its current hostname; report it if it changed.

    Returns True if a (changed) URL was successfully reported, False
    otherwise (disabled, cloudflared unreachable, unchanged, or report
    failed). Blocking by design — call via asyncio.to_thread from
    report_loop().
    """
    global _last_reported_url

    if not QUEUE_WORKER_URL or not QUEUE_SHARED_SECRET:
        return False

    try:
        resp = requests.get(CLOUDFLARED_METRICS_URL, timeout=10)
        resp.raise_for_status()
        hostname = resp.json().get("hostname")
    except Exception as e:
        logger.debug("Tunnel reporter: cloudflared metrics unreachable: %s", e)
        return False

    if not hostname:
        return False

    current_url = f"https://{hostname}"
    if current_url == _last_reported_url:
        return False

    try:
        resp = requests.post(
            f"{QUEUE_WORKER_URL}/api/tunnel-url",
            headers=_auth_headers(),
            json={"url": current_url},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Tunnel reporter: failed to report %s to worker: %s", current_url, e)
        return False

    _last_reported_url = current_url
    logger.info("Tunnel reporter: published new public URL %s", current_url)
    return True


async def report_loop() -> None:
    """Run check_and_report_once() on a timer for as long as engine-api is up."""
    if not QUEUE_WORKER_URL or not QUEUE_SHARED_SECRET:
        logger.info("Tunnel reporter disabled (QUEUE_WORKER_URL/QUEUE_SHARED_SECRET not set)")
        return

    logger.info(
        "Tunnel reporter started — checking %s every %ds",
        CLOUDFLARED_METRICS_URL,
        TUNNEL_REPORT_INTERVAL_SECONDS,
    )
    while True:
        try:
            await asyncio.to_thread(check_and_report_once)
        except Exception as e:
            logger.error("Tunnel reporter loop error: %s", e)
        await asyncio.sleep(TUNNEL_REPORT_INTERVAL_SECONDS)
