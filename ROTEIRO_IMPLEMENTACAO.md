# Roteiro de Implementação — Fontes Oficiais + Anti-Propaganda + Limpeza

**Como usar este documento (instrução para o Claude Code):**

1. Execute **um STEP por vez, na ordem**. Nunca pule ou agrupe steps.
2. Ao final de cada STEP, rode o(s) comando(s) de "Verificação" listados e mostre o resultado.
3. Rode `git diff` (ou `git status` + `git diff`) e **pare**. Não faça commit nem avance para o próximo STEP sem aprovação explícita do usuário.
4. Depois da aprovação, faça `git add -A && git commit -m "<mensagem do step>"` com a mensagem sugerida no próprio step, e só então siga para o próximo.
5. Se qualquer verificação falhar, **não tente consertar automaticamente** — pare e relate o erro.

Cada STEP foi desenhado para ser pequeno e reversível (`git revert` do commit isolado desfaz só aquele step).

---

## FASE 0 — Preparação e rede de segurança

### STEP 0.1 — Inicializar controle de versão
**Motivo:** o projeto não tem repositório Git inicializado hoje. Sem isso, nenhum passo abaixo é auditável ou reversível.

- Rodar: `git init` (na raiz de `logistik-linkedin-pipeline/`)
- Confirmar que `.gitignore` já existe e cobre `.env`, `data/*.db`, `data/images/*`, `n8n_data/`, `__pycache__/` etc. (já existe — só confirmar)
- `git add -A && git commit -m "baseline: estado atual antes das mudanças de fontes e anti-propaganda"`

**Verificação:** `git log --oneline` mostra 1 commit; `git status` limpo.

### STEP 0.2 — Criar branch de trabalho
- `git checkout -b feature/fontes-oficiais-anti-ad`

**Verificação:** `git branch --show-current` → `feature/fontes-oficiais-anti-ad`

### STEP 0.3 — Backup dos dados operacionais
**Motivo:** mudanças de fonte e de schema vão tocar o pipeline; queremos poder restaurar o estado atual de posts/RAG se algo sair errado.

- Copiar `data/posts.db`, `data/chroma/`, `data/images/` para `backup_pre_migracao_$(date +%Y%m%d)/` **fora** do diretório versionado (ex.: um nível acima do repo, ou pasta local não rastreada).

**Verificação:** listar a pasta de backup e confirmar que `posts.db` tem o mesmo tamanho do original.

---

## FASE 1 — Limpeza de código órfão, duplicatas e arquivos desnecessários

> Cada item abaixo foi confirmado por busca no código antes de entrar neste roteiro (não são suposições).

### STEP 1.1 — Remover módulo de busca não utilizado (`search.py`) e stack SearXNG
**Motivo:** `search.py` (SearXNG + DuckDuckGo scraping) nunca é chamado por `pipeline.py` — é código morto ("pendente integração"). Como a decisão foi migrar para fontes RSS oficiais/associações (não busca), este módulo não vai ser integrado — é órfão definitivo.

Arquivos a remover:
- `engine-api/search.py`
- `searxng/` (pasta inteira, incluindo `settings.yml`)

Arquivos a editar (remover blocos relacionados a searxng):
- `docker-compose.yml` — remover o serviço `searxng` inteiro (bloco `services.searxng`) e a entrada `depends_on.searxng` dentro de `engine-api`
- `.gitignore` — remover a linha `searxng/uwsgi.ini` (arquivo não existirá mais)

**Verificação:**
- `grep -ri searxng engine-api/ docker-compose.yml` → nenhum resultado
- `docker compose config` roda sem erro (confirma que o compose ainda é válido sem o serviço removido)

**Commit sugerido:** `remove: search.py e stack SearXNG (código morto, substituído por fontes RSS oficiais)`

### STEP 1.2 — Remover dependência não utilizada `beautifulsoup4`
**Motivo:** `beautifulsoup4` só era importado em `search.py` (removido no STEP 1.1).

- Remover a linha `beautifulsoup4==4.12.3` de `engine-api/requirements.txt`
- **Não remover `lxml`** — é usado como parser interno do `trafilatura` (dependência transitiva); confirmar isso antes de remover qualquer coisa.

**Verificação:**
- `grep -r "beautifulsoup\|BeautifulSoup" engine-api/` → nenhum resultado
- Rebuild da imagem `engine-api` não falha por dependência ausente: `docker compose build engine-api`

**Commit sugerido:** `chore: remover dependência beautifulsoup4 não utilizada`

### STEP 1.3 — Remover variáveis de ambiente órfãs
**Motivo:** `NEWS_KEYWORDS`, `NEWS_HL`, `NEWS_GL`, `NEWS_CEID` são relíquias do antigo Google News RSS (já removido, ver `CLAUDE.md`) e não são lidas por nenhum código atual (`rss.py` só usa `RSS_FEEDS`/`RSS_MAX_PER_FEED`).

Remover as 4 linhas acima de:
- `.env`
- `.env.template` (se presentes — conferir antes de editar)
- Tabela de configuração no `README.md`
- Tabela de configuração no `CLAUDE.md`

**Decisão pendente do usuário — `ARTICLE_MAX_AGE_DAYS`:** está documentado em `.env`/README como "Max article age in days" mas **não é usado em nenhum lugar do código** (`rank.py`, `rss.py`). Duas opções — Claude Code deve perguntar qual antes de agir:
- (a) Remover a variável (deixar de prometer um filtro que não existe), ou
- (b) Implementar de fato o filtro (descartar artigos com `pub_date` mais velho que N dias em `rss.py` antes do `dedup_and_rank`).

**Verificação:** `grep -r "NEWS_KEYWORDS\|NEWS_HL\|NEWS_GL\|NEWS_CEID" .` → nenhum resultado.

**Commit sugerido:** `chore: remover variáveis de ambiente órfãs do antigo Google News RSS`

### STEP 1.4 — Consolidar duplicata `_make_seed`
**Motivo:** existe uma função `_make_seed` quase idêntica em `pipeline.py` (linha ~22, baseada em `source_url`) e em `images.py` (linha ~21, baseada em `post_id` + `extra`). São variações do mesmo conceito (seed determinístico via SHA-256) mantidas em dois lugares.

- Criar `engine-api/seed_utils.py` com uma função única e genérica, por exemplo:
  ```python
  import hashlib

  def make_seed(*parts: str) -> int:
      raw = "-".join(str(p) for p in parts)
      return int(hashlib.sha256(raw.encode()).hexdigest(), 16) % (2**31)
  ```
- Em `pipeline.py`: substituir `_make_seed(source_url)` por `make_seed(source_url)` (import de `seed_utils`)
- Em `images.py`: substituir `_make_seed(post_id, extra)` por `make_seed(post_id, extra)`

**Verificação:** `pytest tests/` continua passando (nenhum teste depende do nome interno das funções removidas, mas confirmar).

**Commit sugerido:** `refactor: consolidar _make_seed duplicado em seed_utils.make_seed`

### STEP 1.5 — Reorganizar `engine-api/tests/`
**Motivo:** a pasta `tests/` mistura testes automatizados reais (`test_extract.py`, `test_rank.py`) com scripts manuais de debug (`manual_test.py`, `manual_batch.py`, `manual_articles.json`, `fetch_article.py`) que não são coletados pelo pytest mas confundem a convenção da pasta.

- Criar `engine-api/scripts/debug/`
- Mover para lá: `tests/manual_test.py`, `tests/manual_batch.py`, `tests/manual_articles.json`, `tests/fetch_article.py`
- Ajustar os `sys.path.insert(...)` desses scripts se necessário (o caminho relativo muda um nível)
- `tests/` deve conter apenas: `__init__.py`, `test_extract.py`, `test_rank.py`

**Verificação:**
- `pytest engine-api/tests/` continua passando (7 testes)
- `python engine-api/scripts/debug/manual_batch.py --help` (ou execução equivalente) ainda roda sem erro de import

**Commit sugerido:** `refactor: separar scripts de debug manual dos testes automatizados`

### STEP 1.6 — Adicionar `.dockerignore`
**Motivo:** nenhum dos dois serviços (`engine-api`, `review-ui`) tem `.dockerignore`; o build copia `__pycache__`, `.venv`, `dev.db`, `dev_images/` etc. para dentro da imagem.

- Criar `engine-api/.dockerignore`:
  ```
  __pycache__/
  *.pyc
  .venv/
  dev.db
  dev_images/
  dev_chroma/
  .pytest_cache/
  scripts/debug/manual_articles.json
  ```
- Criar `review-ui/.dockerignore`:
  ```
  __pycache__/
  *.pyc
  .venv/
  ```

**Verificação:** `docker compose build engine-api review-ui` sem erro; `docker compose run --rm engine-api find / -maxdepth 1 -name "__pycache__"` (ou inspeção similar) confirma ausência de lixo.

**Commit sugerido:** `chore: adicionar .dockerignore em engine-api e review-ui`

### STEP 1.7 — Atualizar documentação (README.md, CLAUDE.md)
**Motivo:** ambos os documentos ainda descrevem `search.py`/SearXNG como "pendente integração" e citam as variáveis removidas no STEP 1.3 — a documentação precisa refletir a arquitetura real após a limpeza.

- Remover toda menção a `search.py`, SearXNG, `SEARXNG_URL`, "Google News RSS pendente" em `README.md` e `CLAUDE.md`
- Remover a linha da tabela de portas referente a `searxng`
- Atualizar o diagrama de arquitetura no `README.md` (bloco ` ``` `) removendo o ramo `SearXNG + DuckDuckGo`

**Verificação:** `grep -ri searxng README.md CLAUDE.md` → nenhum resultado.

**Commit sugerido:** `docs: remover referências a search.py/SearXNG da documentação`

---

## FASE 2 — Migração para fontes oficiais e associações do setor

> **Atenção Claude Code:** este bloco depende de URLs de feed reais que precisam ser **validadas manualmente antes de codificar** (rodando `curl -I <url>` e confirmando que retorna XML/Atom válido). Não invente ou assuma URLs — se não conseguir validar uma fonte, marque como `# TODO: validar URL` e avise o usuário em vez de codificar um valor não verificado.

### STEP 2.1 — Levantar e validar feeds candidatos
Candidatos a verificar (governo/institucional e associações do setor):
- Destatis (Statistisches Bundesamt) — feed de comunicados de imprensa
- Bundesamt für Güterverkehr (BAG)
- Bundesnetzagentur — comunicados
- Comissão Europeia, DG MOVE — sala de imprensa / RSS de transporte
- EUR-Lex — feed de novos atos legislativos na área de transporte
- BGL (Bundesverband Güterkraftverkehr Logistik und Entsorgung) — imprensa/posicionamentos
- DSLV (Deutscher Speditions- und Logistikverband) — imprensa/posicionamentos

Para cada um: `curl -sI <url-candidata>` → confirmar HTTP 200 e `Content-Type` XML/RSS/Atom. Descartar da lista qualquer fonte que não tenha feed público real (usar contato institucional / import manual pontual nesse caso, fora do escopo deste roteiro automatizado).

**Saída esperada deste step:** uma lista curta (5–8 itens) de pares `{url, topic}` validados, no mesmo formato que `_DEFAULT_FEEDS` em `rss.py`.

**Não commitar nada neste step** — é levantamento, não mudança de código.

### STEP 2.2 — Atualizar `_DEFAULT_FEEDS` em `rss.py`
- Substituir (ou complementar, a decidir com o usuário) a lista `_DEFAULT_FEEDS` pelas fontes validadas no STEP 2.1
- Manter ou remover FreightWaves/TTNews (fontes internacionais) — **perguntar ao usuário** antes de remover, já que cobrem eventos globais (ex.: bloqueios de porto) que as fontes alemãs institucionais não cobrem.
- Atualizar o comentário acima de `_DEFAULT_FEEDS` refletindo a nova composição de fontes.

**Verificação:**
- `pytest engine-api/tests/test_rank.py` continua passando (não depende dos feeds reais)
- Teste manual: rodar `collect_articles()` (via `POST /collect` ou script) e inspecionar se artigos das novas fontes aparecem com `topic` correto

**Commit sugerido:** `feat: migrar fontes de artigos para RSS oficiais/institucionais e associações do setor`

### STEP 2.3 — Atualizar README/CLAUDE.md com as novas fontes
- Substituir a lista de fontes descrita no README (`FreightWaves, TTNews, Logistik Heute, Logistra, Logistik News24`) pela composição final decidida no STEP 2.2

**Commit sugerido:** `docs: atualizar lista de fontes de notícias`

---

## FASE 3 — Regras anti-propaganda / distância crítica

### STEP 3.1 — Adicionar regra de distância crítica ao `_BRIDGE_SYSTEM`
Arquivo: `engine-api/agents.py`

Adicionar ao final do prompt `_BRIDGE_SYSTEM` (antes da instrução final de formato JSON):

```
REGRA SOBRE EMPRESAS DE TERCEIROS:
Se o artigo cita uma empresa privada específica (que não seja a LogAid), trate-a apenas
como um exemplo ilustrativo de um padrão maior do setor — nunca como protagonista ou
"case de sucesso". Nunca reproduza elogios, superlativos ou linguagem promocional do
artigo-fonte em relação a essa empresa. Sempre que possível, generalize: em vez de
"a Empresa X fez Y", escreva "algumas Speditionen estão fazendo Y" ou "o setor está se
movendo para Y" — a menos que o nome da empresa seja estritamente necessário para a
credibilidade do fato.
```

Adicionar um novo campo opcional ao schema JSON de saída do bridge: `"empresa_mencionada"` (nome da empresa citada no artigo, ou string vazia).

**Verificação:** `pytest engine-api/tests/` continua passando (nenhum teste atual valida o conteúdo exato do prompt, então isso não deve quebrar nada — mas confirmar).

**Commit sugerido:** `feat: adicionar regra de distância crítica ao prompt de bridge`

### STEP 3.2 — Regra simétrica no `_WRITER_SYSTEM`
Arquivo: `engine-api/agents.py`

Adicionar à lista de regras do `_WRITER_SYSTEM`:

```
Nunca elogie ou destaque conquistas, méritos ou soluções de uma empresa privada nomeada
no artigo (exceto a LogAid, seguindo as regras já definidas). Se uma empresa terceira for
mencionada, adote postura analítica e questionadora — riscos, trade-offs, o que isso
significa para os concorrentes — nunca tom de conquista ou propaganda.
```

**Commit sugerido:** `feat: adicionar regra anti-propaganda ao prompt do writer`

### STEP 3.3 — Nova etapa de verificação: `evaluate_promotional_tone()`
Arquivo: `engine-api/agents.py`

Adicionar um novo system prompt `_AD_CHECK_SYSTEM` e função, seguindo exatamente o padrão de `evaluate_relevance()`:

```python
_AD_CHECK_SYSTEM = """\
Du bewertest, ob ein LinkedIn-Beitrag wie eine unabhängige, kritische Analyse eines \
Branchenthemas klingt — oder wie Werbung/PR für ein bestimmtes Unternehmen oder Produkt. \
Kriterien für "soa_como_ad=true": der Beitrag lobt ein Unternehmen, hebt dessen Produkt/Lösung \
positiv hervor, oder liest sich wie eine Erfolgsgeschichte/Case Study eines Dritten. \
Antworte NUR als JSON: {"soa_como_ad": true, "motivo": "Begründung auf Deutsch"} \
oder {"soa_como_ad": false, "motivo": "Begründung auf Deutsch"}."""


def evaluate_promotional_tone(post_text: str) -> Optional[dict]:
    """Step 4 (post-generation): flag posts that read as advertising for a third party."""
    required = ["soa_como_ad", "motivo"]
    for attempt in range(2):
        try:
            raw = _chat(WRITER_MODEL, _AD_CHECK_SYSTEM, post_text)
            data = _parse_json_obj(raw, required)
            if data is not None:
                return data
            logger.warning("Ad-check parse failed (attempt %d): %s", attempt + 1, raw[:200])
        except Exception as e:
            logger.error("Ad-check error (attempt %d): %s", attempt + 1, e)
    return None
```

**Verificação:** nenhuma ainda (função isolada, sem uso — o uso é conectado no STEP 3.4).

**Commit sugerido:** `feat: adicionar agente de verificação de tom promocional`

### STEP 3.4 — Adicionar coluna `ad_risk` e conectar a verificação ao pipeline
Arquivos: `engine-api/db.py`, `engine-api/pipeline.py`, `engine-api/main.py`

1. Em `db.py`, adicionar migração idempotente (SQLite não tem `ADD COLUMN IF NOT EXISTS` nativo — verificar via `PRAGMA table_info`):
   ```python
   def _ensure_column(con, table: str, column: str, coltype: str) -> None:
       cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
       if column not in cols:
           con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
   ```
   Chamar dentro de `init_db()`: `_ensure_column(con, "posts", "ad_risk", "INTEGER DEFAULT 0")` e `_ensure_column(con, "posts", "ad_risk_motivo", "TEXT")`.

2. Em `pipeline.py`, dentro de `_process_articles`, logo após `post_text = generate_post(...)` e antes de `insert_post(...)`, chamar `evaluate_promotional_tone(post_text)` e guardar o resultado para passar a `insert_post`/`update_post`.

3. Em `main.py`, garantir que `PostUpdate`/`PostCreate` conseguem expor `ad_risk` (leitura, não necessariamente edição manual).

**Decisão de produto a confirmar com o usuário:** quando `soa_como_ad=true`, o post é **descartado automaticamente** (como acontece hoje com bridge inválida) ou **marcado e enviado à fila mesmo assim** para o revisor decidir? Recomendação: marcar e enviar (não descartar), para não perder falsos positivos — a decisão final fica com o humano.

**Verificação:**
- `sqlite3 data/posts.db "PRAGMA table_info(posts);"` mostra as novas colunas
- Rodar pipeline manualmente e confirmar que posts recebem valor em `ad_risk`

**Commit sugerido:** `feat: persistir e conectar verificação de tom promocional ao pipeline`

### STEP 3.5 — Aviso visual na fila de revisão (Streamlit)
Arquivo: `review-ui/app.py`

No título de cada card de post (linha do `st.expander(...)`), adicionar prefixo de aviso quando `post.get("ad_risk")` for verdadeiro, por exemplo:
```python
warn = "⚠️ POSSÍVEL TOM PROMOCIONAL — " if post.get("ad_risk") else ""
```
e usar `warn` na string do título do expander.

**Verificação:** rodar `review-ui` localmente (ou `docker compose up review-ui`) e confirmar visualmente que o aviso aparece nos posts marcados.

**Commit sugerido:** `feat: exibir aviso de tom promocional na fila de revisão`

### STEP 3.6 — Testes automatizados para a nova verificação
Arquivo: `engine-api/tests/test_agents.py` (novo arquivo)

Seguir o padrão de `test_extract.py` (mock de `_chat` via `unittest.mock.patch`), cobrindo:
- `evaluate_promotional_tone` retorna `soa_como_ad=True` quando o mock simula resposta positiva
- `evaluate_promotional_tone` retorna `None` após 2 tentativas de parse falho
- (opcional, se o tempo permitir) testes equivalentes para `evaluate_relevance` e `build_bridge`, que hoje não têm nenhuma cobertura

**Verificação:** `pytest engine-api/tests/` — todos os testes (antigos + novos) passam.

**Commit sugerido:** `test: adicionar cobertura para evaluate_promotional_tone`

---

## FASE 4 — Verificação final e fechamento

### STEP 4.1 — Suíte de testes completa
- `pytest engine-api/tests/ -v`

**Critério de sucesso:** 100% dos testes passam (contagem deve ter crescido em relação ao início, por causa do STEP 3.6).

### STEP 4.2 — Rebuild e smoke test end-to-end
- `docker compose build engine-api review-ui`
- `docker compose up -d`
- `docker compose ps` → todos os serviços `healthy`
- `curl -X POST http://localhost:8000/run` e acompanhar `docker compose logs -f engine-api`
- Abrir `http://localhost:8503` e conferir manualmente 2–3 posts gerados: (a) vieram de fontes oficiais/associação, (b) nenhum soa como propaganda de terceiro, (c) o aviso `ad_risk` aparece quando esperado

### STEP 4.3 — Atualização final da documentação
- Revisar `README.md` e `CLAUDE.md` por completo, garantindo que refletem: fontes atuais, ausência de `search.py`/SearXNG, novo campo `ad_risk` e seu significado, nova localização dos scripts de debug

**Commit sugerido:** `docs: atualização final pós-migração`

### STEP 4.4 — Fechamento
- `git log --oneline` — revisar a lista completa de commits com o usuário
- Merge da branch `feature/fontes-oficiais-anti-ad` para a branch principal **somente após aprovação explícita do usuário**

---

## Resumo do que muda (para referência rápida do usuário)

| Área | Antes | Depois |
|---|---|---|
| Descoberta de artigos | RSS de veículos de imprensa (+ `search.py` morto/não usado) | RSS de fontes oficiais/institucionais e associações do setor |
| Stack | Inclui SearXNG (não utilizado) | SearXNG removido |
| Tom sobre terceiros | Sem regra — herda tom da fonte | Regras explícitas de distância crítica + verificação automática de tom promocional |
| Schema do banco | `posts` sem sinalização de risco | `posts.ad_risk` + `ad_risk_motivo` |
| Testes | 7 testes (extract, rank) | + testes de `agents.py` (verificação de tom) |
| Organização de `tests/` | Mistura testes reais e scripts manuais | Scripts manuais movidos para `scripts/debug/` |
| Controle de versão | Nenhum (`.git` inexistente) | Git inicializado, branch de trabalho, commits por step |
