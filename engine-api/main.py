from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import kb
import pipeline_status
from agents import build_bridge, generate_post
from extract import extract_article
from images import regenerate_image
from pipeline import collect_articles, regenerate_missing_texts, run_batch
from queue_poller import poll_loop
from tunnel_reporter import report_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

IMAGE_DIR = os.getenv("IMAGE_DIR", "/data/images")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Style Guide beim Start einlesen (idempotent — vorhandene Abschnitte werden per Upsert aktualisiert)
    style_guide_path = os.getenv("STYLE_GUIDE_PATH", "/data/style_guide")
    result = kb.ingest_style_guide(style_guide_path)
    logging.getLogger(__name__).info(
        "Style Guide beim Start eingelesen: %d Abschnitte", result.get("ingested", 0)
    )

    # Poller für die Warteschlange bei ausgeschaltetem Rechner — No-op, solange
    # QUEUE_WORKER_URL/QUEUE_SHARED_SECRET nicht gesetzt sind (siehe cloudflare-worker/README.md).
    poller_task = asyncio.create_task(poll_loop())
    # Veröffentlicht die aktuelle Quick-Tunnel-URL, damit cloudflare-dashboard sie automatisch
    # findet — No-op, wenn cloudflared nicht im Quick-Tunnel-Modus läuft.
    tunnel_reporter_task = asyncio.create_task(report_loop())
    yield
    poller_task.cancel()
    tunnel_reporter_task.cancel()


app = FastAPI(title="Logistik LinkedIn Pipeline API", lifespan=lifespan)

# CORS — nötig, weil das Dashboard (cloudflare-dashboard/) eine statische Seite auf einem
# anderen Origin als diese API ist. Standardmäßig leer (kein Cross-Origin-Zugriff), bis
# CORS_ALLOWED_ORIGINS gesetzt ist, z. B.:
#   CORS_ALLOWED_ORIGINS=https://dashboard.yourdomain.example
_cors_origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Erzeugte Bilder per HTTP ausliefern, damit ein Browser auf einem anderen Origin sie laden
# kann — das Dashboard baut die URLs als {API_BASE}/images/{post_id}.jpg.
os.makedirs(IMAGE_DIR, exist_ok=True)
app.mount("/images", StaticFiles(directory=IMAGE_DIR), name="images")


# ---------- Modelle ----------

class CollectRequest(BaseModel):
    keywords: list[str] | None = None
    max: int | None = None


class PostCreate(BaseModel):
    topic: str = ""
    source_url: str = ""
    title: str = ""
    post_text: str = ""
    image_prompt: str = ""
    image_path: str = ""
    seed: int = 0
    status: str = "draft"


class PostUpdate(BaseModel):
    post_text: str | None = None
    status: str | None = None
    image_prompt: str | None = None
    image_path: str | None = None
    seed: int | None = None
    linkedin_urn: str | None = None


class PostReject(BaseModel):
    reason: str


class RunRequest(BaseModel):
    keywords: list[str] | None = None
    max: int | None = None


# ---------- Routen ----------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/collect")
def collect(req: CollectRequest):
    articles = collect_articles(
        keywords=req.keywords,
        max_articles=req.max,
    )
    return articles


@app.post("/posts", status_code=201)
def create_post(req: PostCreate):
    post_id = db.insert_post(
        topic=req.topic,
        source_url=req.source_url,
        title=req.title,
        post_text=req.post_text,
        image_prompt=req.image_prompt,
        image_path=req.image_path,
        seed=req.seed,
        status=req.status,
    )
    return {"id": post_id}


@app.get("/posts")
def list_posts(status: str | None = None):
    return db.list_posts(status=status)


@app.get("/posts/{post_id}")
def get_post(post_id: int):
    post = db.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@app.patch("/posts/{post_id}")
def patch_post(post_id: int, req: PostUpdate):
    post = db.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    updates = req.model_dump(exclude_none=True)
    if updates:
        db.update_post(post_id, **updates)
    updated = db.get_post(post_id)

    # Automatische Synchronisierung mit der KB, wenn ein Post freigegeben wird
    if req.status == "approved":
        final_text = (req.post_text or post.get("post_text", "")).strip()
        if final_text:
            kb.ingest_post(
                post_id=post_id,
                topic=updated.get("topic", ""),
                title=updated.get("title", ""),
                post_text=final_text,
            )

    return updated


@app.post("/posts/{post_id}/reject")
def reject_post(post_id: int, req: PostReject):
    reason = req.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason must not be empty")

    post = db.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    db.update_post(post_id, status="rejected")
    db.add_feedback(post_id, reason)

    post_text = post.get("post_text", "")
    if post_text:
        kb.ingest_rejected_post(
            post_id=post_id,
            topic=post.get("topic", ""),
            title=post.get("title", ""),
            post_text=post_text,
            reason=reason,
        )

    return db.get_post(post_id)


def _regenerate_text_task(post_id: int) -> None:
    post = db.get_post(post_id)
    if not post:
        return
    source_url = post.get("source_url", "")
    if not source_url:
        return
    article = extract_article(source_url, "")
    article["title"] = post.get("title", "")
    bridge = build_bridge(article)
    post_text = generate_post(article, bridge=bridge)
    if post_text:
        db.update_post(post_id, post_text=post_text)


@app.post("/posts/{post_id}/regenerate-text")
def regenerate_post_text(post_id: int, background_tasks: BackgroundTasks):
    post = db.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if not post.get("source_url"):
        raise HTTPException(status_code=400, detail="Post has no source_url to extract from")
    background_tasks.add_task(_regenerate_text_task, post_id)
    return {"status": "started", "post_id": post_id}


@app.post("/posts/{post_id}/regenerate-image")
def regenerate_post_image(post_id: int):
    post = db.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    current_seed = post.get("seed") or 0
    image_prompt = post.get("image_prompt") or "Professional logistics scene, editorial, 16:9"
    path, new_seed = regenerate_image(post_id, image_prompt, current_seed)
    db.update_post(post_id, image_path=path, seed=new_seed)
    return {"image_path": path, "seed": new_seed}


@app.post("/posts/regenerate-missing-text")
def regenerate_missing_text(background_tasks: BackgroundTasks):
    posts = [p for p in db.list_posts() if not p.get("post_text")]
    background_tasks.add_task(regenerate_missing_texts)
    return {"status": "started", "count": len(posts)}


@app.post("/run")
def run(background_tasks: BackgroundTasks, req: RunRequest = None):
    if not pipeline_status.start_run():
        return {"status": "already_running"}
    keywords = req.keywords if req else None
    max_articles = req.max if req else None
    background_tasks.add_task(run_batch, keywords, max_articles)
    return {"status": "started"}


@app.get("/pipeline/status")
def pipeline_status_route():
    return pipeline_status.get_status()


# ---------- Knowledge Base ----------

def _sync_approved_posts_task(posts: list[dict]) -> None:
    for p in posts:
        kb.ingest_post(
            post_id=p["id"],
            topic=p.get("topic", ""),
            title=p.get("title", ""),
            post_text=p["post_text"],
        )


@app.get("/kb/stats")
def kb_stats():
    return kb.get_stats()


@app.post("/kb/sync")
def kb_sync(background_tasks: BackgroundTasks):
    approved = [p for p in db.list_posts(status="approved") if p.get("post_text")]
    background_tasks.add_task(_sync_approved_posts_task, approved)
    return {"status": "started", "count": len(approved)}


@app.post("/kb/reload-guidelines")
def kb_reload_guidelines(background_tasks: BackgroundTasks):
    style_guide_path = os.getenv("STYLE_GUIDE_PATH", "/data/style_guide")
    background_tasks.add_task(kb.ingest_style_guide, style_guide_path)
    return {"status": "started", "path": style_guide_path}
