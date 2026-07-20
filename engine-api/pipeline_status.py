"""In-memory status of the currently running (or last) pipeline batch.

Single-process, single-worker deployment (see docker-compose.yml) — a
module-level dict guarded by a lock is enough, no need for Redis/DB. Read by
GET /pipeline/status so the dashboard can poll and show live progress instead
of a toast that disappears with no further feedback (see cloudflare-dashboard).

`articles_target` is the goal of *posts created*, not a cap on how many raw
candidates get looked at — pipeline.py keeps scanning the deduped RSS pool,
testing article after article through the full funnel, until it has created
that many posts or the pool runs out.
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_status: dict = {
    "running": False,
    "phase": "idle",  # idle | collecting | processing | done | error
    "articles_target": 0,
    "articles_scanned": 0,
    "articles_relevant": 0,
    "posts_created": 0,
    "started_at": None,
    "finished_at": None,
    "last_error": None,
}


def start_run() -> bool:
    """Marks a run as started. Returns False if one is already in progress."""
    with _lock:
        if _status["running"]:
            return False
        _status.update(
            running=True,
            phase="collecting",
            articles_target=0,
            articles_scanned=0,
            articles_relevant=0,
            posts_created=0,
            started_at=time.time(),
            finished_at=None,
            last_error=None,
        )
        return True


def set_target(target: int) -> None:
    with _lock:
        _status["phase"] = "processing"
        _status["articles_target"] = target


def mark_scanned() -> None:
    with _lock:
        _status["articles_scanned"] += 1


def mark_relevant() -> None:
    with _lock:
        _status["articles_relevant"] += 1


def mark_post_created() -> None:
    with _lock:
        _status["posts_created"] += 1


def finish(error: str | None = None) -> None:
    with _lock:
        _status["running"] = False
        _status["phase"] = "error" if error else "done"
        _status["finished_at"] = time.time()
        _status["last_error"] = error


def get_status() -> dict:
    with _lock:
        return dict(_status)
