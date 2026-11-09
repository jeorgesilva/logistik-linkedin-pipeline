"""Status des aktuell laufenden (oder letzten) Pipeline-Batches im Arbeitsspeicher.

Deployment mit einem Prozess und einem Worker (siehe docker-compose.yml) — ein durch einen
Lock geschütztes Dict auf Modulebene reicht, Redis/DB sind nicht nötig. Wird von
GET /pipeline/status gelesen, damit das Dashboard den Fortschritt live abfragen und anzeigen
kann, statt eines Toasts, der ohne weiteres Feedback verschwindet (siehe cloudflare-dashboard).

`articles_target` ist das Ziel an *erstellten Posts*, keine Obergrenze dafür, wie viele
Rohkandidaten angesehen werden — pipeline.py durchsucht den deduplizierten RSS-Pool weiter
und schickt Artikel für Artikel durch den ganzen Trichter, bis so viele Posts erstellt sind
oder der Pool erschöpft ist.
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
    """Markiert einen Lauf als gestartet. Gibt False zurück, wenn bereits einer läuft."""
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
