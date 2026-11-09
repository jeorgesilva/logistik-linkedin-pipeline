# Logistik LinkedIn Pipeline

Self-hosted, 24/7 content pipeline that turns German logistics news into LinkedIn drafts automatically.

**Daily at 06:00 (Berlin time):**
1. Reads from curated RSS feeds — German trade press (Logistik Heute, Logistra, Logistik News24) + DE/EU institutional (Destatis, Bundesnetzagentur, BALM, EUR-Lex, DG MOVE)
2. Deduplicates and selects top articles (TF-IDF ranking)
3. Filters by relevance using an Ollama LLM agent
4. Extracts full article text (trafilatura + crawl4ai fallback)
5. Builds a narrative bridge between the news event and a Spedition's daily reality (discards articles with no concrete, defensible impact)
6. Runs 2 Ollama LLM agents: German post writer + image-prompt generator. Post writer uses RAG: retrieves similar approved posts + style guide chunks from ChromaDB
7. Checks the generated post for promotional tone toward third-party companies — auto-discarded if it reads like advertising/PR for someone other than LogAid (see "Anti-Propaganda / Critical Distance" below)
8. Generates LinkedIn header images via Pollinations.ai (free, no API key)
9. Stores drafts in SQLite → surfaces them in a Streamlit review queue

---

## Architecture

```
n8n (scheduler)
    └─► engine-api (FastAPI)
            ├─► RSS feeds (curated logistics sources) ─ article discovery
            ├─► Ollama — main LLM (host machine)         qwen2.5-coder:latest
            ├─► Ollama — embed model (Docker)            nomic-embed-text
            ├─► ChromaDB ─ RAG (approved posts + style guide)
            ├─► Pollinations.ai (images)
            └─► SQLite (posts.db)
review-ui (Streamlit)
    └─► engine-api
```

**Note on Ollama split:** In the default local setup, `OLLAMA_HOST` points to Ollama running natively on the host machine (better GPU/performance) while `OLLAMA_EMBED_HOST` points to the Dockerised Ollama service used for embeddings. On a cloud VM, set both to the same host.

---

## Quick Start (local)

```bash
git clone <repo>
cd logistik-linkedin-pipeline
cp .env.template .env
# Edit .env if needed (defaults are fine for first run)

docker compose up -d

# Pull the writer model into your local Ollama (one-time)
ollama pull qwen2.5-coder:latest

# nomic-embed-text is pulled automatically by ollama-model-init on first docker compose up

# Verify all services are healthy
docker compose ps

# Test end-to-end (without waiting for n8n schedule)
curl -X POST http://localhost:8000/run
```

Then open http://localhost:8503 to see drafts in the review queue.

### KB Bootstrap (first time only)

After the stack is up, seed the Knowledge Base with the 5 approved example posts so RAG is active from the start:

```bash
docker exec engine-api python ingest_seed_posts.py
```

This inserts 5 LogAid-style posts into SQLite as `approved` and embeds them into ChromaDB. RAG activates automatically once `KB_MIN_EXAMPLES` (default: 3) approved posts are present.

---

## Services & Ports

| Service | External Port | URL |
|---|---|---|
| engine-api | 8000 | http://localhost:8000 |
| review-ui (Streamlit) | 8503 | http://localhost:8503 |
| n8n | 5678 | http://localhost:5678 |
| ollama (embed) | internal only | — |

---

## n8n Setup

1. Open http://localhost:5678 (login: `N8N_USER` / `N8N_PASSWORD` from `.env`)
2. Go to **Workflows → Import from File**
3. Import `n8n/content_pipeline.workflow.json`
4. **Activate** the workflow — it will run daily at 06:00 Berlin time
5. To test immediately: click **Execute Workflow**

---

## Configuration (`.env`)

All settings are in `.env` (copy from `.env.template`):

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama for main LLM (host machine in local dev) |
| `OLLAMA_EMBED_HOST` | `http://ollama:11434` | Ollama for embeddings (Docker service) |
| `OLLAMA_WRITER_MODEL` | `qwen2.5-coder:latest` | LLM for German post writing |
| `OLLAMA_IMAGE_PROMPT_MODEL` | `qwen2.5-coder:latest` | LLM for image-prompt generation |
| `OLLAMA_REQUEST_TIMEOUT` | `600` | Seconds before LLM call times out |
| `MAX_ARTICLES` | `10` | Max articles selected per pipeline run |
| `EMBED_MODEL` | `nomic-embed-text` | Ollama model used for ChromaDB embeddings |
| `KB_PATH` | `/data/chroma` | ChromaDB persistence path |
| `KB_MIN_EXAMPLES` | `3` | Minimum approved posts for RAG to activate |
| `STYLE_GUIDE_PATH` | `/data/style_guide` | Path to style guide `.md` files |
| `IMAGE_WIDTH/HEIGHT` | `1200/627` | LinkedIn header image size |
| `IMAGE_MODEL` | `flux` | Pollinations.ai model |
| `LINKEDIN_ENABLED` | `false` | Enable LinkedIn auto-publishing (Phase 2) |

---

## Knowledge Base (RAG)

Posts are generated with retrieval-augmented generation using two ChromaDB collections:

| Collection | Content | Updated by |
|---|---|---|
| `style_examples` | Approved posts (embedded as title + text) | Auto on PATCH `status=approved`; "Sync" button in UI |
| `style_guidelines` | Style guide chunks (split by `##` section) | Auto on startup; "Reload Style Guide" button in UI |

**RAG flow:**
```
New article → embed(title + text[:300]) via nomic-embed-text
           → top-2 similar approved posts + top-2 style guide chunks
           → injected into qwen2.5 prompt as context
```

RAG is silently skipped if ChromaDB has fewer than `KB_MIN_EXAMPLES` approved posts, Ollama is unavailable, or ChromaDB errors. The pipeline runs normally regardless.

**Style guide files** (`data/style_guide/`) are on a Docker volume — edit them directly on the host and click **Reload Style Guide** in the sidebar (or `POST /kb/reload-guidelines`). No rebuild needed.

---

## Anti-Propaganda / Critical Distance

When an article names a specific third-party company, the pipeline is designed to keep critical
distance instead of echoing the source's praise:

- **Prompt rules** (`engine-api/agents.py`, `_BRIDGE_SYSTEM` and `_WRITER_SYSTEM`): a named
  third-party company (never LogAid) is treated only as an illustrative example of a broader
  industry pattern — never as a protagonist or "success story". The writer is instructed to stay
  analytical (risks, trade-offs, competitive implications) rather than complimentary.
- **Automated check** (`evaluate_promotional_tone()` in `agents.py`, wired into
  `pipeline.py:_process_articles`): after a post is generated, a dedicated LLM call classifies
  whether it reads like independent analysis or like advertising/PR for a third party. LogAid's
  own (brief, standard) mention at the end of every post is explicitly excluded from this check —
  only promotional tone toward *other* companies triggers it.
- **On a positive match, the post is discarded automatically** — same treatment as an invalid
  narrative bridge. It never reaches the database or the review queue, and there is no persisted
  flag for it (no `ad_risk` column). This was an explicit product decision (2026-07-16): favor a
  clean review queue over surfacing false positives for manual triage.

---

## API Endpoints (engine-api :8000)

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/posts` | List posts (`?status=draft`) |
| GET | `/posts/{id}` | Single post |
| POST | `/posts` | Create post manually |
| PATCH | `/posts/{id}` | Update fields; `status=approved` auto-ingests into KB |
| POST | `/posts/{id}/regenerate-text` | Re-extract article and regenerate text via LLM |
| POST | `/posts/{id}/regenerate-image` | Regenerate image |
| POST | `/posts/regenerate-missing-text` | Regenerate text for posts without text (background) |
| POST | `/run` | Run full pipeline batch |
| POST | `/collect` | Collect articles only, no post creation |
| GET | `/kb/stats` | Count docs in KB collections; indicates if RAG is active |
| POST | `/kb/sync` | Bulk-sync all approved posts into KB (background) |
| POST | `/kb/reload-guidelines` | Re-ingest `/data/style_guide/` into KB (background) |

---

## GCP Deployment

### 1. Create VM

```bash
gcloud compute instances create logistik-pipeline \
  --zone=europe-west3-a \
  --machine-type=e2-standard-4 \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=60GB \
  --boot-disk-type=pd-balanced
```

| Tier | Machine type | RAM | Recommended model | ~Cost/mo |
|---|---|---|---|---|
| Best quality | `e2-standard-8` | 32 GB | `qwen2.5:32b` | ~$196 |
| **Recommended** | **`e2-standard-4`** | **16 GB** | **`qwen2.5:14b-instruct`** | **~$98** |
| Budget | `e2-standard-2` | 8 GB | `llama3.1:8b` | ~$49 |

Cut costs ~60–90% with Spot VMs or 1-year committed-use discounts.

### 2. Install Docker on VM

```bash
gcloud compute ssh logistik-pipeline --zone=europe-west3-a

# On the VM:
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# Add swap (important for large models)
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 3. Deploy

```bash
git clone <repo> logistik-linkedin-pipeline
cd logistik-linkedin-pipeline
cp .env.template .env
nano .env  # Set OLLAMA_HOST=http://ollama:11434 (same Docker network on VM), update N8N_PASSWORD

docker compose up -d

# Pull models (takes 10–30 min)
docker compose exec ollama ollama pull qwen2.5:14b-instruct
# nomic-embed-text is pulled automatically

docker compose ps  # All should be "healthy"

# Seed the KB
docker exec engine-api python ingest_seed_posts.py
```

### 4. Access via SSH Tunnel (recommended — keeps ports closed)

```bash
gcloud compute ssh logistik-pipeline --zone=europe-west3-a \
  -- -L 5678:localhost:5678 -L 8503:localhost:8503 -L 8000:localhost:8000
```

Then open http://localhost:8503 (Streamlit) and http://localhost:5678 (n8n) locally.

### 5. Stop VM to save money

```bash
gcloud compute instances stop logistik-pipeline --zone=europe-west3-a
# Restart:
gcloud compute instances start logistik-pipeline --zone=europe-west3-a
```

---

## Phase 2 — LinkedIn Auto-Publishing

The LinkedIn publisher (`engine-api/linkedin.py`) is implemented but **disabled by default** (`LINKEDIN_ENABLED=false`).

**To enable:**
1. Create a LinkedIn developer app at https://www.linkedin.com/developers/
2. Request the `w_member_social` scope
3. Complete OAuth 2.0 flow to get an access token (~60-day expiry)
4. Set in `.env`:
   ```
   LINKEDIN_ENABLED=true
   LINKEDIN_ACCESS_TOKEN=your_token_here
   LINKEDIN_AUTHOR_URN=urn:li:person:your_id
   ```
5. Approved posts are pushed as **DRAFT** to LinkedIn (you publish manually)

**Rate limits:** ~10–100 posts/day. Publishing as DRAFT is the safe posture vs. LinkedIn ToS.
**Token refresh:** Access tokens expire; renew manually every ~60 days.

---

## Offline-Friendly Remote Trigger

A Cloudflare Worker + KV queue (`cloudflare-worker/`) lets a teammate trigger the pipeline
(click "Posts generieren") even while the host machine is off. The request waits in the Worker's
KV store until `engine-api`'s poller (`queue_poller.py`) picks it up once the host is back online,
runs the batch, and reports the result back. The engine-api ↔ Worker routes
(`/api/pending`, `/api/complete`) are authenticated via a shared-secret header
(`X-Queue-Secret`).

A lightweight static dashboard (`cloudflare-dashboard/`, no build step) gives a non-technical
teammate a review queue UI — filter, edit, approve, reject, trigger a run — without local access
to the Streamlit app. It talks to `engine-api` directly through a free Cloudflare Quick Tunnel
(`cloudflared` in `docker-compose.yml`), so unlike the trigger button it needs the host online.
Because the Quick Tunnel hostname changes on every restart, `tunnel_reporter.py` pushes the
current URL to the same Worker's KV, and the dashboard discovers it on load — the link given to
the teammate never changes.

See `cloudflare-worker/README.md` for deploy steps (Cloudflare account required).

---

## Product Decisions

- **DE/EU-only sources.** International feeds (FreightWaves, Transport Topics) were dropped to
  keep the scope on the German/EU market; feeds are interleaved round-robin so `MAX_ARTICLES`
  isn't exhausted by trade press before institutional sources (Destatis, EUR-Lex, …) get a turn.
- **Discard, don't flag, promotional posts.** Posts that read like PR for a third party are
  dropped before reaching the review queue instead of being flagged for manual triage — a clean
  queue was preferred over reviewing false positives (see "Anti-Propaganda / Critical Distance").
- **Rejections teach the model.** Rejecting a post requires a reason; rejected posts are embedded
  into a separate ChromaDB collection and retrieved as "patterns to avoid" for similar articles.
- **No public ports.** All services bind to `127.0.0.1`; public access goes only through the
  `cloudflared` tunnel.
- **Human stays in the loop on LinkedIn.** Auto-publishing is off by default and, when enabled,
  pushes approved posts as LinkedIn *drafts* — a person always clicks "publish".

---

## Troubleshooting

**Ollama not responding:**
```bash
docker compose logs ollama
docker compose exec ollama ollama list
```

**No articles collected:**
```bash
docker compose logs engine-api | grep -E "RSS|articles"
# Check feed availability manually
curl -s https://logistik-heute.de/rss.xml | head -50
```

**Articles returning empty full_text:**
- Some paywalled sites won't extract; RSS summary is used as fallback
- Check: `docker compose logs engine-api | grep "Full text extracted"`

**Images not generating:**
- Pollinations.ai is free but rate-limited; retries are built-in
- Check: `docker compose logs engine-api | grep -i image`

**RAG not active (yellow indicator in UI):**
- Need at least `KB_MIN_EXAMPLES` (default: 3) approved posts in ChromaDB
- Run `docker exec engine-api python ingest_seed_posts.py` to seed with 5 example posts
- Check: `curl http://localhost:8000/kb/stats`

**n8n workflow not triggering:**
- Ensure workflow is **activated** (green toggle in n8n UI)
- Check timezone is `Europe/Berlin` in n8n settings

**Reset everything:**
```bash
docker compose down
rm -f data/posts.db data/images/*.jpg
docker volume rm logistik-linkedin-pipeline_chroma_data
docker compose up -d
docker exec engine-api python ingest_seed_posts.py
```

---

## Development (without Docker)

```bash
cd engine-api
pip install -r requirements.txt
export OLLAMA_HOST=http://localhost:11434
export OLLAMA_EMBED_HOST=http://localhost:11434
export DB_PATH=./dev.db
export IMAGE_DIR=./dev_images
export KB_PATH=./dev_chroma
export STYLE_GUIDE_PATH=../data/style_guide
uvicorn main:app --reload --port 8000

# Run tests
pip install pytest
pytest tests/
```
