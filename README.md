# Logistik LinkedIn Pipeline

Selbst gehostete Content-Pipeline, rund um die Uhr in Betrieb, die deutsche Logistik-News automatisch in LinkedIn-Entwürfe verwandelt.

**Täglich um 06:00 Uhr (Berliner Zeit):**
1. Liest kuratierte RSS-Feeds — deutsche Fachpresse (Logistik Heute, Logistra, Logistik News24) + institutionelle Quellen DE/EU (Destatis, Bundesnetzagentur, BALM, EUR-Lex, DG MOVE)
2. Entfernt Duplikate und wählt die relevantesten Artikel aus (TF-IDF-Ranking)
3. Filtert nach Relevanz mit einem Ollama-LLM-Agenten
4. Extrahiert den vollständigen Artikeltext (trafilatura, crawl4ai als Fallback)
5. Baut eine erzählerische Brücke zwischen der Nachricht und dem Alltag einer Spedition (Artikel ohne konkrete, belastbare Auswirkung werden verworfen)
6. Führt 2 Ollama-LLM-Agenten aus: deutscher Post-Writer + Bildprompt-Generator. Der Post-Writer nutzt RAG: ähnliche freigegebene Posts + Style-Guide-Abschnitte werden aus ChromaDB abgerufen
7. Prüft den erzeugten Post auf werblichen Ton gegenüber Drittunternehmen — wird automatisch verworfen, wenn er wie Werbung/PR für jemand anderen als Frachtwerk klingt (siehe „Anti-Propaganda / kritische Distanz“ unten)
8. Erzeugt LinkedIn-Headerbilder über Pollinations.ai (kostenlos, kein API-Key)
9. Speichert Entwürfe in SQLite → zeigt sie in einer Streamlit-Review-Queue an

---

## Architektur

```
n8n (Scheduler)
    └─► engine-api (FastAPI)
            ├─► RSS-Feeds (kuratierte Logistikquellen) ─ Artikelsuche
            ├─► Ollama — Haupt-LLM (Host-Rechner)       qwen2.5-coder:latest
            ├─► Ollama — Embedding-Modell (Docker)      nomic-embed-text
            ├─► ChromaDB ─ RAG (freigegebene Posts + Style Guide)
            ├─► Pollinations.ai (Bilder)
            └─► SQLite (posts.db)
review-ui (Streamlit)
    └─► engine-api
```

**Hinweis zur Ollama-Aufteilung:** Im Standard-Setup lokal zeigt `OLLAMA_HOST` auf ein nativ auf dem Host-Rechner laufendes Ollama (bessere GPU-Nutzung/Performance), während `OLLAMA_EMBED_HOST` auf den Ollama-Docker-Service für Embeddings zeigt. Auf einer Cloud-VM beide auf denselben Host setzen.

---

## Schnellstart (lokal)

```bash
git clone <repo>
cd logistik-linkedin-pipeline
cp .env.template .env
# .env bei Bedarf anpassen (die Standardwerte reichen für den ersten Start)

docker compose up -d

# Writer-Modell einmalig ins lokale Ollama laden
ollama pull qwen2.5-coder:latest

# nomic-embed-text wird beim ersten docker compose up automatisch von ollama-model-init geladen

# Prüfen, ob alle Services healthy sind
docker compose ps

# End-to-End testen (ohne auf den n8n-Zeitplan zu warten)
curl -X POST http://localhost:8000/run
```

Danach http://localhost:8503 öffnen, um die Entwürfe in der Review-Queue zu sehen.

### KB-Bootstrap (nur beim ersten Mal)

Sobald der Stack läuft, die Knowledge Base mit den 5 freigegebenen Beispielposts befüllen, damit RAG von Anfang an aktiv ist:

```bash
docker exec engine-api python ingest_seed_posts.py
```

Das legt 5 Posts im Frachtwerk-Stil als `approved` in SQLite an und bettet sie in ChromaDB ein. RAG wird automatisch aktiv, sobald `KB_MIN_EXAMPLES` (Standard: 3) freigegebene Posts vorhanden sind.

---

## Services & Ports

| Service | Externer Port | URL |
|---|---|---|
| engine-api | 8000 | http://localhost:8000 |
| review-ui (Streamlit) | 8503 | http://localhost:8503 |
| n8n | 5678 | http://localhost:5678 |
| ollama (Embedding) | nur intern | — |

---

## n8n einrichten

1. http://localhost:5678 öffnen (Login: `N8N_USER` / `N8N_PASSWORD` aus `.env`)
2. **Workflows → Import from File** wählen
3. `n8n/content_pipeline.workflow.json` importieren
4. Den Workflow **aktivieren** — er läuft dann täglich um 06:00 Uhr Berliner Zeit
5. Zum sofortigen Testen: **Execute Workflow** klicken

---

## Konfiguration (`.env`)

Alle Einstellungen stehen in `.env` (Kopie von `.env.template`):

| Variable | Standard | Beschreibung |
|---|---|---|
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama für das Haupt-LLM (Host-Rechner in der lokalen Entwicklung) |
| `OLLAMA_EMBED_HOST` | `http://ollama:11434` | Ollama für Embeddings (Docker-Service) |
| `OLLAMA_WRITER_MODEL` | `qwen2.5-coder:latest` | LLM für das Schreiben der deutschen Posts |
| `OLLAMA_IMAGE_PROMPT_MODEL` | `qwen2.5-coder:latest` | LLM für die Bildprompt-Erzeugung |
| `OLLAMA_REQUEST_TIMEOUT` | `600` | Sekunden bis zum Timeout eines LLM-Aufrufs |
| `MAX_ARTICLES` | `10` | Maximale Anzahl ausgewählter Artikel pro Pipeline-Lauf |
| `EMBED_MODEL` | `nomic-embed-text` | Ollama-Modell für die ChromaDB-Embeddings |
| `KB_PATH` | `/data/chroma` | Persistenzpfad von ChromaDB |
| `KB_MIN_EXAMPLES` | `3` | Mindestanzahl freigegebener Posts, ab der RAG aktiv wird |
| `STYLE_GUIDE_PATH` | `/data/style_guide` | Pfad zu den `.md`-Dateien des Style Guides |
| `IMAGE_WIDTH/HEIGHT` | `1200/627` | Größe des LinkedIn-Headerbilds |
| `IMAGE_MODEL` | `flux` | Pollinations.ai-Modell |
| `LINKEDIN_ENABLED` | `false` | Automatische Veröffentlichung auf LinkedIn aktivieren (Phase 2) |

---

## Knowledge Base (RAG)

Posts werden per Retrieval-Augmented Generation mit zwei ChromaDB-Collections erzeugt:

| Collection | Inhalt | Aktualisiert durch |
|---|---|---|
| `style_examples` | Freigegebene Posts (eingebettet als Titel + Text) | Automatisch bei PATCH `status=approved`; Button „Genehmigte synchronisieren“ in der UI |
| `style_guidelines` | Style-Guide-Abschnitte (aufgeteilt nach `##`-Überschriften) | Automatisch beim Start; Button „Style Guide neu laden“ in der UI |

**RAG-Ablauf:**
```
Neuer Artikel → embed(Titel + Text[:300]) via nomic-embed-text
             → Top-2 ähnliche freigegebene Posts + Top-2 Style-Guide-Abschnitte
             → als Kontext in den qwen2.5-Prompt eingefügt
```

RAG wird stillschweigend übersprungen, wenn ChromaDB weniger als `KB_MIN_EXAMPLES` freigegebene Posts enthält, Ollama nicht erreichbar ist oder ChromaDB einen Fehler meldet. Die Pipeline läuft in jedem Fall normal weiter.

**Style-Guide-Dateien** (`data/style_guide/`) liegen auf einem Docker-Volume — direkt auf dem Host bearbeiten und in der Seitenleiste auf **Style Guide neu laden** klicken (oder `POST /kb/reload-guidelines`). Kein Rebuild nötig.

---

## Anti-Propaganda / kritische Distanz

Wenn ein Artikel ein bestimmtes Drittunternehmen nennt, soll die Pipeline kritische Distanz
wahren, statt das Lob der Quelle zu übernehmen:

- **Prompt-Regeln** (`engine-api/agents.py`, `_BRIDGE_SYSTEM` und `_WRITER_SYSTEM`): Ein genanntes
  Drittunternehmen (nie Frachtwerk) dient nur als illustratives Beispiel für ein größeres
  Branchenmuster — nie als Protagonist oder „Erfolgsgeschichte“. Der Writer ist angewiesen,
  analytisch zu bleiben (Risiken, Zielkonflikte, Folgen für den Wettbewerb) statt lobend.
- **Automatische Prüfung** (`evaluate_promotional_tone()` in `agents.py`, eingebunden in
  `pipeline.py:_process_articles`): Nach der Erzeugung eines Posts klassifiziert ein eigener
  LLM-Aufruf, ob er wie eine unabhängige Analyse oder wie Werbung/PR für ein Drittunternehmen
  klingt. Die eigene (kurze, standardisierte) Erwähnung von Frachtwerk am Ende jedes Posts ist von
  dieser Prüfung ausdrücklich ausgenommen — nur werblicher Ton gegenüber *anderen* Unternehmen
  löst sie aus.
- **Bei einem Treffer wird der Post automatisch verworfen** — genauso wie bei einer ungültigen
  erzählerischen Brücke. Er erreicht weder die Datenbank noch die Review-Queue, und es wird kein
  Flag dafür gespeichert (keine Spalte `ad_risk`). Das war eine bewusste Produktentscheidung
  (16.07.2026): eine saubere Review-Queue ist wichtiger, als False Positives zur manuellen
  Sichtung vorzulegen.

---

## API-Endpunkte (engine-api :8000)

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/health` | Health-Check |
| GET | `/posts` | Posts auflisten (`?status=draft`) |
| GET | `/posts/{id}` | Einzelner Post |
| POST | `/posts` | Post manuell anlegen |
| PATCH | `/posts/{id}` | Felder aktualisieren; `status=approved` übernimmt den Post automatisch in die KB |
| POST | `/posts/{id}/regenerate-text` | Artikel neu extrahieren und Text per LLM neu erzeugen |
| POST | `/posts/{id}/regenerate-image` | Bild neu erzeugen |
| POST | `/posts/regenerate-missing-text` | Text für Posts ohne Text neu erzeugen (im Hintergrund) |
| POST | `/run` | Kompletten Pipeline-Batch ausführen |
| POST | `/collect` | Nur Artikel sammeln, keine Posts erzeugen |
| GET | `/kb/stats` | Dokumente in den KB-Collections zählen; zeigt, ob RAG aktiv ist |
| POST | `/kb/sync` | Alle freigegebenen Posts gesammelt in die KB synchronisieren (im Hintergrund) |
| POST | `/kb/reload-guidelines` | `/data/style_guide/` neu in die KB einlesen (im Hintergrund) |

---

## Deployment auf GCP

### 1. VM anlegen

```bash
gcloud compute instances create logistik-pipeline \
  --zone=europe-west3-a \
  --machine-type=e2-standard-4 \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=60GB \
  --boot-disk-type=pd-balanced
```

| Stufe | Maschinentyp | RAM | Empfohlenes Modell | ~Kosten/Monat |
|---|---|---|---|---|
| Beste Qualität | `e2-standard-8` | 32 GB | `qwen2.5:32b` | ~196 $ |
| **Empfohlen** | **`e2-standard-4`** | **16 GB** | **`qwen2.5:14b-instruct`** | **~98 $** |
| Budget | `e2-standard-2` | 8 GB | `llama3.1:8b` | ~49 $ |

Mit Spot-VMs oder Rabatten für 1 Jahr zugesagte Nutzung (Committed Use) lassen sich die Kosten um ~60–90 % senken.

### 2. Docker auf der VM installieren

```bash
gcloud compute ssh logistik-pipeline --zone=europe-west3-a

# Auf der VM:
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# Swap anlegen (wichtig für große Modelle)
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 3. Deployen

```bash
git clone <repo> logistik-linkedin-pipeline
cd logistik-linkedin-pipeline
cp .env.template .env
nano .env  # OLLAMA_HOST=http://ollama:11434 setzen (gleiches Docker-Netzwerk auf der VM), N8N_PASSWORD ändern

docker compose up -d

# Modelle laden (dauert 10–30 Min.)
docker compose exec ollama ollama pull qwen2.5:14b-instruct
# nomic-embed-text wird automatisch geladen

docker compose ps  # Alle sollten „healthy“ sein

# KB befüllen
docker exec engine-api python ingest_seed_posts.py
```

### 4. Zugriff per SSH-Tunnel (empfohlen — Ports bleiben geschlossen)

```bash
gcloud compute ssh logistik-pipeline --zone=europe-west3-a \
  -- -L 5678:localhost:5678 -L 8503:localhost:8503 -L 8000:localhost:8000
```

Danach lokal http://localhost:8503 (Streamlit) und http://localhost:5678 (n8n) öffnen.

### 5. VM stoppen, um Kosten zu sparen

```bash
gcloud compute instances stop logistik-pipeline --zone=europe-west3-a
# Neu starten:
gcloud compute instances start logistik-pipeline --zone=europe-west3-a
```

---

## Phase 2 — automatische Veröffentlichung auf LinkedIn

Der LinkedIn-Publisher (`engine-api/linkedin.py`) ist implementiert, aber **standardmäßig deaktiviert** (`LINKEDIN_ENABLED=false`).

**Aktivieren:**
1. Unter https://www.linkedin.com/developers/ eine LinkedIn-Developer-App anlegen
2. Den Scope `w_member_social` beantragen
3. Den OAuth-2.0-Flow durchlaufen, um ein Access Token zu erhalten (Gültigkeit ~60 Tage)
4. In `.env` setzen:
   ```
   LINKEDIN_ENABLED=true
   LINKEDIN_ACCESS_TOKEN=your_token_here
   LINKEDIN_AUTHOR_URN=urn:li:person:your_id
   ```
5. Freigegebene Posts werden als **ENTWURF** (DRAFT) an LinkedIn übertragen (veröffentlicht wird manuell)

**Rate-Limits:** ~10–100 Posts/Tag. Die Veröffentlichung als Entwurf ist im Hinblick auf die LinkedIn-Nutzungsbedingungen die sichere Variante.
**Token-Erneuerung:** Access Tokens laufen ab; etwa alle 60 Tage manuell erneuern.

---

## Fernauslösung auch bei ausgeschaltetem Rechner

Eine Warteschlange aus Cloudflare Worker + KV (`cloudflare-worker/`) ermöglicht es einem
Teammitglied, die Pipeline auszulösen (Klick auf „Posts generieren“), auch wenn der Host-Rechner
ausgeschaltet ist. Die Anfrage wartet im KV-Store des Workers, bis der Poller von `engine-api`
(`queue_poller.py`) sie abholt, sobald der Host wieder online ist, den Batch ausführt und das
Ergebnis zurückmeldet. Die Routen zwischen engine-api und Worker
(`/api/pending`, `/api/complete`) sind über einen Header mit gemeinsamem Geheimnis
(`X-Queue-Secret`) authentifiziert.

Ein schlankes statisches Dashboard (`cloudflare-dashboard/`, ohne Build-Schritt) bietet einem
nicht-technischen Teammitglied eine Review-Queue-Oberfläche — filtern, bearbeiten, freigeben,
ablehnen, Lauf auslösen — ohne lokalen Zugriff auf die Streamlit-App. Es spricht über einen
kostenlosen Cloudflare Quick Tunnel (`cloudflared` in `docker-compose.yml`) direkt mit
`engine-api` und braucht daher — anders als der Auslöse-Button — einen laufenden Host. Da sich
der Hostname des Quick Tunnels bei jedem Neustart ändert, überträgt `tunnel_reporter.py` die
aktuelle URL in den KV-Store desselben Workers, und das Dashboard ermittelt sie beim Laden — der
Link, den das Teammitglied bekommt, ändert sich nie.

Die Deploy-Schritte stehen in `cloudflare-worker/README.md` (Cloudflare-Konto erforderlich).

---

## Produktentscheidungen

- **Nur Quellen aus DE/EU.** Internationale Feeds (FreightWaves, Transport Topics) wurden
  entfernt, um den Fokus auf den deutschen/europäischen Markt zu legen; die Feeds werden im
  Round-Robin-Verfahren verschränkt, damit `MAX_ARTICLES` nicht schon von der Fachpresse
  ausgeschöpft wird, bevor institutionelle Quellen (Destatis, EUR-Lex, …) an der Reihe sind.
- **Werbliche Posts verwerfen statt markieren.** Posts, die wie PR für ein Drittunternehmen
  klingen, werden verworfen, bevor sie die Review-Queue erreichen, statt zur manuellen Sichtung
  markiert zu werden — eine saubere Queue wurde dem Prüfen von False Positives vorgezogen (siehe
  „Anti-Propaganda / kritische Distanz“).
- **Ablehnungen trainieren das Modell.** Wer einen Post ablehnt, muss einen Grund angeben;
  abgelehnte Posts werden in eine eigene ChromaDB-Collection eingebettet und bei ähnlichen
  Artikeln als „zu vermeidende Muster“ abgerufen.
- **Keine öffentlichen Ports.** Alle Services binden an `127.0.0.1`; öffentlicher Zugriff läuft
  ausschließlich über den `cloudflared`-Tunnel.
- **Bei LinkedIn bleibt der Mensch in der Verantwortung.** Die automatische Veröffentlichung ist
  standardmäßig aus und überträgt freigegebene Posts, wenn aktiviert, als LinkedIn-*Entwürfe* —
  auf „Veröffentlichen“ klickt immer ein Mensch.

---

## Fehlerbehebung

**Ollama antwortet nicht:**
```bash
docker compose logs ollama
docker compose exec ollama ollama list
```

**Keine Artikel gesammelt:**
```bash
docker compose logs engine-api | grep -E "RSS|articles"
# Erreichbarkeit eines Feeds manuell prüfen
curl -s https://logistik-heute.de/rss.xml | head -50
```

**Artikel mit leerem full_text:**
- Manche Seiten mit Paywall lassen sich nicht extrahieren; dann wird die RSS-Zusammenfassung als Fallback verwendet
- Prüfen: `docker compose logs engine-api | grep "Full text extracted"`

**Bilder werden nicht erzeugt:**
- Pollinations.ai ist kostenlos, aber rate-limitiert; Wiederholungsversuche sind eingebaut
- Prüfen: `docker compose logs engine-api | grep -i image`

**RAG nicht aktiv (gelbe Anzeige in der UI):**
- Es werden mindestens `KB_MIN_EXAMPLES` (Standard: 3) freigegebene Posts in ChromaDB benötigt
- `docker exec engine-api python ingest_seed_posts.py` ausführen, um 5 Beispielposts anzulegen
- Prüfen: `curl http://localhost:8000/kb/stats`

**n8n-Workflow startet nicht:**
- Sicherstellen, dass der Workflow **aktiviert** ist (grüner Schalter in der n8n-UI)
- Prüfen, ob in den n8n-Einstellungen die Zeitzone `Europe/Berlin` gesetzt ist

**Alles zurücksetzen:**
```bash
docker compose down
rm -f data/posts.db data/images/*.jpg
docker volume rm logistik-linkedin-pipeline_chroma_data
docker compose up -d
docker exec engine-api python ingest_seed_posts.py
```

---

## Entwicklung (ohne Docker)

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

# Tests ausführen
pip install pytest
pytest tests/
```
