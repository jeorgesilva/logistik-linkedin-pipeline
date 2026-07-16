# Logistik LinkedIn Pipeline — CLAUDE.md

## O que é este projeto

Pipeline self-hosted que busca notícias de logística alemã, gera posts para LinkedIn com LLM local (Ollama), cria imagens e expõe uma fila de revisão no navegador. Tudo roda via Docker Compose. Os posts são gerados no estilo e voz da marca **LogAid** usando uma Knowledge Base com RAG (ChromaDB + nomic-embed-text).

## Arquitetura

```
rss.py (feeds curados)  → fonte ativa de artigos (Logistik Heute, Logistra, Destatis, BALM, EUR-Lex, etc.)
ollama                  → LLM local (qwen2.5 + llama3.2:3b + nomic-embed-text)
engine-api              → FastAPI, orquestra pipeline e expõe REST API
review-ui               → Streamlit, fila de revisão humana
n8n                     → agendamento do pipeline (webhook trigger)
data/posts.db           → SQLite com todos os posts
data/images/            → imagens geradas
data/chroma/            → ChromaDB persistido (vector store dos posts aprovados + style guide)
data/style_guide/       → Arquivos .md do style guide LogAid (editáveis sem rebuild)
```

## Mapa de arquivos relevantes

| Arquivo | Papel |
|---|---|
| `engine-api/pipeline.py` | Orquestra busca → extração → LLM → imagem |
| `engine-api/agents.py` | Agent 1: gera post (qwen2.5) · Agent 2: gera image prompt (llama3.2:3b) |
| `engine-api/kb.py` | Knowledge Base: ChromaDB client, embedding via Ollama, ingest e retrieval |
| `engine-api/main.py` | FastAPI routes + auto-sync KB ao aprovar post |
| `engine-api/db.py` | SQLite helpers |
| `engine-api/rss.py` | **Fonte ativa de artigos** — feeds RSS curados: trade press alemão (Logistik Heute, Logistra, Logistik News24) + institucional DE/EU (Destatis, Bundesnetzagentur, BALM, EUR-Lex, DG MOVE). FreightWaves/TTNews removidos em 2026-07-16 (decisão do usuário, escopo DE/EU) |
| `engine-api/extract.py` | Extração de artigos (trafilatura + crawl4ai fallback) |
| `engine-api/rank.py` | Dedup e ranking de artigos por TF-IDF |
| `engine-api/images.py` | Geração de imagens (Pollinations.ai) |
| `engine-api/linkedin.py` | Publicação no LinkedIn via API (desabilitado por padrão — `LINKEDIN_ENABLED=false`) |
| `engine-api/ingest_seed_posts.py` | Script de bootstrap: insere 5 posts seed no KB (rodar uma vez) |
| `review-ui/app.py` | Streamlit UI com painel KB na sidebar |
| `docker-compose.yml` | Stack completa + ollama-model-init para nomic-embed-text |
| `.env` | Variáveis de ambiente (não versionar) |
| `data/style_guide/*.md` | Style guide da LogAid (editável sem rebuild, recarregado via UI) |

## Variáveis de ambiente (.env)

```
# Ollama
OLLAMA_HOST=http://ollama:11434
OLLAMA_WRITER_MODEL=qwen2.5:7b-instruct
OLLAMA_IMAGE_PROMPT_MODEL=llama3.2:3b
OLLAMA_REQUEST_TIMEOUT=600

# Busca
MAX_ARTICLES=10

# Knowledge Base (RAG)
EMBED_MODEL=nomic-embed-text
KB_PATH=/data/chroma
KB_MIN_EXAMPLES=3          # RAG só ativa se houver >= N posts aprovados
STYLE_GUIDE_PATH=/data/style_guide
```

## API endpoints (engine-api :8000)

| Método | Path | Descrição |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/posts` | Lista posts (query param: `?status=draft`) |
| GET | `/posts/{id}` | Post individual |
| POST | `/posts` | Cria post manual |
| PATCH | `/posts/{id}` | Atualiza campos; se `status=approved` → auto-ingere no KB |
| POST | `/posts/{id}/regenerate-text` | Re-extrai artigo e regera texto via LLM (síncrono) |
| POST | `/posts/{id}/regenerate-image` | Regera imagem (síncrono) |
| POST | `/posts/regenerate-missing-text` | Regera texto de posts sem text em background |
| POST | `/run` | Roda pipeline batch completo |
| POST | `/collect` | Só coleta artigos sem criar posts |
| GET | `/kb/stats` | Conta docs em `style_examples` e `style_guidelines`; indica se RAG está ativo |
| POST | `/kb/sync` | Bulk-sync todos os posts aprovados com texto para o KB (background) |
| POST | `/kb/reload-guidelines` | Re-ingere `/data/style_guide/` no KB (background) |

## Knowledge Base — como funciona

### Collections ChromaDB

| Collection | Conteúdo | Alimentada por |
|---|---|---|
| `style_examples` | Posts aprovados (embedados por título + texto) | Auto-sync ao aprovar via PATCH; botão "Sync" na UI |
| `style_guidelines` | Chunks do style guide (por seção `##`) | Startup automático + botão "Reload Style Guide" na UI |

### Fluxo de geração com RAG

```
Artigo novo → embed(título + texto[:300]) via nomic-embed-text
           ↓
  ChromaDB → top-2 posts mais similares + top-2 chunks de style guide
           ↓
  User message = [contexto KB] + artigo → qwen2.5 gera post
```

RAG é silenciosamente ignorado se: KB tiver < `KB_MIN_EXAMPLES` exemplos aprovados, Ollama indisponível, ou ChromaDB com erro. O pipeline continua normalmente.

### Style guide (`data/style_guide/`)

Os arquivos `.md` são editáveis diretamente no host — estão no volume `./data`. Após editar, recarregue via botão **Reload Style Guide** na sidebar ou `POST /kb/reload-guidelines`. **Não é necessário rebuild**.

Arquivos atuais:
- `00_overview.md` — visão geral e language split policy
- `01_tone_and_voice.md` — os 4 adjetivos, princípios, frases proibidas
- `02_post_structure.md` — 5 padrões de post (A–E) com exemplos anotados
- `03_vocabulary_and_glossary.md` — vocabulário LogAid e glossário de logística alemã
- `04_topic_patterns.md` — regras por tópico (crise, dados, regulação, produto, aviação)
- `05_hashtag_strategy.md` — conjuntos de hashtags por tópico e idioma

## Review UI (review-ui :8503)

Sidebar:
- **Refresh** — recarrega posts
- **Generate Missing Texts** — `POST /posts/regenerate-missing-text` (não-bloqueante)
- **Run Pipeline Now** — `POST /run` (bloqueante, aguarda spinner)
- **Knowledge Base** (seção):
  - Indicador RAG ativo/inativo (verde/amarelo)
  - **Sync Approved Posts** — `POST /kb/sync` (background)
  - **Reload Style Guide** — `POST /kb/reload-guidelines` (background)

Card de cada post:
- Imagem + botão Regenerate Image
- Text area editável com o texto do post
- Checkbox "Show image prompt" (não usar `st.expander` — proibido aninhar expanders)
- Botão "✍️ Generate Text" aparece só se `post_text` estiver vazio
- Botões: Save / Approve / Reject / Restore

## Regras importantes

- **Fonte de artigos é `rss.py`** (feeds curados) — um módulo de busca web alternativo existiu no passado e foi removido por ser código morto, nunca chamado por `pipeline.py`; não reintroduzir sem uma decisão explícita de arquitetura.
- **Nunca aninhar `st.expander` dentro de outro** — o Streamlit lança `StreamlitAPIException`. Usar `st.checkbox` para conteúdo colapsável dentro de cards.
- **`/posts/regenerate-missing-text` e endpoints KB são assíncronos** — usam `BackgroundTasks` do FastAPI e retornam imediatamente. Timeout do Streamlit é 15s; geração e embedding levam minutos.
- **Rebuild obrigatório após mudanças de código** — o código fica baked na imagem Docker. `docker-compose restart` não aplica mudanças; usar `docker-compose build <serviço> && docker-compose up -d <serviço>`.
- **Style guide não precisa de rebuild** — os `.md` em `data/style_guide/` são lidos do volume. Editar e clicar "Reload Style Guide" é suficiente.
- **RAG threshold** — com menos de `KB_MIN_EXAMPLES` (padrão: 3) posts aprovados, o contexto KB é omitido do prompt. O pipeline funciona normalmente; aprovando os primeiros posts o RAG ativa automaticamente.

## Como rodar

```bash
# Subir stack completa (primeira vez: baixa nomic-embed-text automaticamente)
docker-compose up -d

# Rebuild de um serviço após mudança de código
docker-compose build engine-api && docker-compose up -d engine-api

# Ver logs em tempo real
docker logs engine-api -f

# Inspecionar banco
sqlite3 data/posts.db "SELECT id, title, status, length(post_text) FROM posts;"

# Inspecionar KB
docker exec engine-api python -c "import kb; print(kb.get_stats())"

# Forçar re-ingestão do style guide manualmente
curl -X POST http://localhost:8000/kb/reload-guidelines
```

## Onboarding do KB (primeira vez)

1. `docker-compose up -d` — stack sobe, `nomic-embed-text` é baixado, style guide é ingerido automaticamente no startup.
2. Rodar o script de bootstrap com os 5 posts seed aprovados:
   ```bash
   docker exec engine-api python ingest_seed_posts.py
   ```
   Isso insere os posts no SQLite como `approved` e os embeda no ChromaDB. O RAG fica ativo imediatamente.
3. A partir daí, cada post que você aprovar na review-ui é automaticamente adicionado ao KB.

### Posts seed incluídos (`engine-api/ingest_seed_posts.py`)

| # | Título / Tema | Padrão |
|---|---|---|
| 1 | Helikopter Freileitungsbau (bilingue DE+EN) | Pattern D — Capability Showcase |
| 2 | KI-Modell abgeschaltet / Datenhoheit | Pattern B — News Event Pivot |
| 3 | 75% Straßengüterverkehr vor dem Kollaps | Pattern C — Early Warning |
| 4 | Microsoft AI Report 2026 / Dispo KI | Pattern A — Data Hook |
| 5 | 2,907 insolvencies in Germany 2025 (EN) | Pattern E — Insolvency/Obituary |

## Portas expostas

| Serviço | Porta |
|---|---|
| review-ui (Streamlit) | http://localhost:8503 |
| engine-api (FastAPI) | http://localhost:8000 |
| n8n | http://localhost:5678 |
