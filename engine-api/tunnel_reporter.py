"""Hintergrund-Reporter für die aktuelle öffentliche URL der engine-api.

Die engine-api liegt hinter einem kostenlosen Cloudflare Quick Tunnel (cloudflared, siehe
docker-compose.yml), dessen Hostname bei jedem Neustart dieses Containers zufällig neu
vergeben wird. Dieses Modul fragt den Metrics-Endpunkt von cloudflared nach dem aktuellen
Hostnamen ab und überträgt ihn bei jeder Änderung in den KV-Store des Cloudflare Workers
(derselbe Worker/dasselbe Secret wie queue_poller.py), damit cloudflare-dashboard ihn
automatisch findet, statt dass nach jedem Neustart jemand eine neue URL einfügen muss.

No-op, wenn QUEUE_WORKER_URL / QUEUE_SHARED_SECRET nicht gesetzt sind oder der
Metrics-Endpunkt von cloudflared nicht erreichbar ist (z. B. bei einem benannten Tunnel
ohne Quick Tunnel) — kann überall gefahrlos aktiviert bleiben.
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
    """Fragt cloudflared nach dem aktuellen Hostnamen und meldet ihn, falls er sich geändert hat.

    Gibt True zurück, wenn eine (geänderte) URL erfolgreich gemeldet wurde, sonst False
    (deaktiviert, cloudflared nicht erreichbar, unverändert oder Meldung fehlgeschlagen).
    Absichtlich blockierend — über asyncio.to_thread aus report_loop() aufrufen.
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
    """Führt check_and_report_once() zeitgesteuert aus, solange die engine-api läuft."""
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
