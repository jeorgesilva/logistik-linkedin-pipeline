/**
 * Logistik Pipeline — dashboard (static, no build step).
 * Talks directly to engine-api's REST API from the browser. Requires
 * CORS_ALLOWED_ORIGINS on engine-api to include this page's origin.
 */

const STORAGE_KEY = "logistik_api_base_url";
// engine-api runs behind a free Cloudflare Quick Tunnel whose URL changes on
// every restart. This Worker (already used for the "Posts generieren" queue) also
// stores the current one in KV — tunnel_reporter.py (engine-api) keeps it
// updated. Auto-discovering it here means the link people are given for this
// dashboard never needs to change, even though the real API URL behind it does.
const QUEUE_WORKER_URL = "https://logistik-queue.jeorgecassil.workers.dev";
const STATUS_FILTERS = [
  { key: "all", label: "Alle" },
  { key: "draft", label: "Entwurf" },
  { key: "approved", label: "Genehmigt" },
  { key: "rejected", label: "Abgelehnt" },
  { key: "posted", label: "Veröffentlicht" },
];

let state = {
  filter: "all",
  posts: [],
  counts: {},
  drawerPostId: null,
  drawerRejecting: false,
  connected: null,
};

let refreshTimer = null;
let pipelineStatusTimer = null;

// ---------- API base URL (stored per-browser in localStorage) ----------

function getApiBase() {
  return (localStorage.getItem(STORAGE_KEY) || "").replace(/\/+$/, "");
}

function setApiBase(url) {
  localStorage.setItem(STORAGE_KEY, url.replace(/\/+$/, ""));
}

// Best-effort auto-discovery of the current engine-api URL — falls back to
// whatever's already in localStorage (or the manual setup screen) if the
// Worker is unreachable or hasn't heard from tunnel_reporter.py yet.
async function discoverApiBase() {
  try {
    const res = await fetch(`${QUEUE_WORKER_URL}/api/tunnel-url`);
    if (!res.ok) return null;
    const data = await res.json();
    return data.url || null;
  } catch (_) {
    return null;
  }
}

// ---------- Fetch helpers ----------

async function apiRequest(method, path, body) {
  const base = getApiBase();
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`${base}${path}`, opts);
  setConnectionStatus(true);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch (_) {}
    throw new Error(`${res.status}: ${detail}`);
  }
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

const apiGet = (path) => apiRequest("GET", path);
const apiPost = (path, body) => apiRequest("POST", path, body ?? {});
const apiPatch = (path, body) => apiRequest("PATCH", path, body);

function setConnectionStatus(ok) {
  if (state.connected === ok) return;
  state.connected = ok;
  const dot = document.getElementById("connection-dot");
  const label = document.getElementById("connection-label");
  dot.classList.remove("ok", "error");
  dot.classList.add(ok ? "ok" : "error");
  label.textContent = ok ? "Verbunden" : "Keine Verbindung zur API";
}

// ---------- Toast ----------

let toastTimer = null;
function toast(message, type = "info") {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = "toast" + (type === "error" ? " error" : type === "success" ? " success" : "");
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 4000);
}

// ---------- Setup screen ----------

function initSetupScreen() {
  const input = document.getElementById("setup-api-url");
  const saveBtn = document.getElementById("setup-save-btn");
  input.value = getApiBase();

  saveBtn.addEventListener("click", () => {
    const value = input.value.trim();
    if (!value) return;
    setApiBase(value);
    boot();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveBtn.click();
  });
}

function showSetupScreen() {
  document.getElementById("setup-screen").classList.remove("hidden");
  document.getElementById("main-screen").classList.add("hidden");
}

function showMainScreen() {
  document.getElementById("setup-screen").classList.add("hidden");
  document.getElementById("main-screen").classList.remove("hidden");
}

// ---------- Status nav (sidebar filters) ----------

function renderStatusNav() {
  const nav = document.getElementById("status-nav");
  nav.innerHTML = "";
  for (const f of STATUS_FILTERS) {
    const item = document.createElement("div");
    item.className = "status-nav-item" + (state.filter === f.key ? " active" : "");
    const count = f.key === "all"
      ? Object.values(state.counts).reduce((a, b) => a + b, 0)
      : (state.counts[f.key] || 0);
    item.innerHTML = `<span>${f.label}</span><span class="count">${count}</span>`;
    item.addEventListener("click", () => {
      state.filter = f.key;
      renderStatusNav();
      loadPosts();
    });
    nav.appendChild(item);
  }
}

// ---------- Posts: load + render grid ----------

async function loadCounts() {
  try {
    const all = await apiGet("/posts");
    const counts = {};
    for (const p of all) counts[p.status] = (counts[p.status] || 0) + 1;
    state.counts = counts;
    renderStatusNav();
  } catch (e) {
    // Connection status already reflects the failure; nothing else to do here.
  }
}

async function loadPosts() {
  const grid = document.getElementById("post-grid");
  const loading = document.getElementById("loading-state");
  const empty = document.getElementById("empty-state");
  const title = document.getElementById("content-title");
  const countEl = document.getElementById("content-count");

  const filterMeta = STATUS_FILTERS.find((f) => f.key === state.filter);
  title.textContent = filterMeta ? filterMeta.label : "Posts";

  loading.classList.remove("hidden");
  empty.classList.add("hidden");
  grid.innerHTML = "";

  try {
    const path = state.filter === "all" ? "/posts" : `/posts?status=${state.filter}`;
    const posts = await apiGet(path);
    state.posts = posts;
    countEl.textContent = `${posts.length} Post${posts.length === 1 ? "" : "s"}`;
    loading.classList.add("hidden");

    if (posts.length === 0) {
      empty.classList.remove("hidden");
      return;
    }
    renderPostGrid(posts);
  } catch (e) {
    loading.classList.add("hidden");
    toast(`Posts konnten nicht geladen werden (${e.message})`, "error");
  }
}

function badgeClass(status) {
  return {
    draft: "badge-draft",
    approved: "badge-approved",
    rejected: "badge-rejected",
    posted: "badge-posted",
    candidate: "badge-candidate",
  }[status] || "badge-draft";
}

function statusLabel(status) {
  return {
    draft: "Entwurf",
    approved: "Genehmigt",
    rejected: "Abgelehnt",
    posted: "Veröffentlicht",
    candidate: "In Bearbeitung",
  }[status] || status;
}

function imageUrlFor(post) {
  if (!post.image_path) return null;
  if (/^https?:\/\//.test(post.image_path)) return post.image_path;
  const filename = post.image_path.split("/").pop();
  return `${getApiBase()}/images/${filename}`;
}

function fmtDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
  } catch (_) {
    return iso;
  }
}

function renderPostGrid(posts) {
  const grid = document.getElementById("post-grid");
  grid.innerHTML = "";
  for (const post of posts) {
    const card = document.createElement("div");
    card.className = "post-card";
    const imgUrl = imageUrlFor(post);
    card.innerHTML = `
      <div class="post-card-image">${imgUrl ? `<img src="${imgUrl}" alt="" loading="lazy" onerror="this.parentElement.textContent='🖼️'">` : "🖼️"}</div>
      <div class="post-card-body">
        <span class="badge ${badgeClass(post.status)}">${statusLabel(post.status)}</span>
        <p class="post-card-title">${escapeHtml(post.title || "(kein Titel)")}</p>
        <span class="post-card-topic">${escapeHtml(post.topic || "")}</span>
        <p class="post-card-snippet">${escapeHtml((post.post_text || "Noch kein Text generiert.").slice(0, 220))}</p>
        <div class="post-card-footer">#${post.id} · ${fmtDate(post.created_at)}</div>
      </div>
    `;
    card.addEventListener("click", () => openDrawer(post.id));
    grid.appendChild(card);
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ---------- KB panel ----------

async function loadKbStats() {
  const el = document.getElementById("kb-indicator");
  try {
    const stats = await apiGet("/kb/stats");
    if (stats.rag_active) {
      el.className = "kb-indicator kb-active";
      el.textContent = `✓ RAG aktiv — ${stats.style_examples} Beispiele / ${stats.style_guidelines} Abschnitte`;
    } else {
      el.className = "kb-indicator kb-inactive";
      el.textContent = `RAG inaktiv — ${stats.style_examples}/${stats.min_examples_for_rag} genehmigte Beispiele`;
    }
  } catch (e) {
    el.className = "kb-indicator kb-inactive";
    el.textContent = "KB-Status konnte nicht geladen werden.";
  }
}

// ---------- Drawer (post detail / edit) ----------

async function openDrawer(postId) {
  state.drawerPostId = postId;
  state.drawerRejecting = false;
  document.getElementById("drawer-overlay").classList.remove("hidden");
  requestAnimationFrame(() => {
    document.getElementById("drawer-overlay").classList.add("visible");
    document.getElementById("drawer").classList.add("open");
  });
  await refreshDrawer();
}

function closeDrawer() {
  document.getElementById("drawer-overlay").classList.remove("visible");
  document.getElementById("drawer").classList.remove("open");
  setTimeout(() => {
    document.getElementById("drawer-overlay").classList.add("hidden");
  }, 250);
  state.drawerPostId = null;
}

async function refreshDrawer() {
  if (!state.drawerPostId) return;
  try {
    const post = await apiGet(`/posts/${state.drawerPostId}`);
    renderDrawer(post);
  } catch (e) {
    toast(`Post konnte nicht geladen werden (${e.message})`, "error");
  }
}

function renderDrawer(post) {
  const container = document.getElementById("drawer-content");
  const imgUrl = imageUrlFor(post);

  container.innerHTML = `
    <div class="drawer-header">
      <span class="badge ${badgeClass(post.status)}">${statusLabel(post.status)}</span>
      <button class="icon-btn" id="drawer-close-btn" title="Schließen">✕</button>
    </div>
    <div class="drawer-body">
      <div class="drawer-image">
        ${imgUrl ? `<img src="${imgUrl}" alt="">` : `<div style="padding:40px;text-align:center;color:var(--text-faint)">Kein Bild</div>`}
      </div>
      <button class="btn btn-outline btn-sm" id="drawer-regen-image-btn">🖼 Bild neu generieren</button>

      <div>
        <label class="field-label">Post-Text</label>
        <textarea id="drawer-text" rows="9">${escapeHtml(post.post_text || "")}</textarea>
      </div>

      ${post.image_prompt ? `
        <details>
          <summary class="details-toggle">Bild-Prompt anzeigen</summary>
          <div class="image-prompt-box">${escapeHtml(post.image_prompt)}</div>
        </details>
      ` : ""}

      <div class="drawer-meta">
        <span><strong>Thema:</strong> ${escapeHtml(post.topic || "—")}</span>
        ${post.source_url ? `<span><a href="${post.source_url}" target="_blank" rel="noopener">Quellartikel ansehen ↗</a></span>` : ""}
        <span>Erstellt am ${fmtDate(post.created_at)}</span>
      </div>

      <div class="action-row">
        <button class="btn btn-outline" id="drawer-save-btn">💾 Speichern</button>
        <button class="btn btn-primary" id="drawer-approve-btn">✅ Genehmigen</button>
        <button class="btn btn-danger-outline" id="drawer-reject-btn">❌ Ablehnen</button>
        ${post.status === "rejected" ? `<button class="btn btn-outline" id="drawer-restore-btn">↩️ Wiederherstellen</button>` : ""}
        ${post.source_url ? `<button class="btn btn-outline" id="drawer-regen-text-btn">🔄 Text neu generieren</button>` : ""}
      </div>

      <div id="drawer-reject-box"></div>
    </div>
  `;

  document.getElementById("drawer-close-btn").addEventListener("click", closeDrawer);

  document.getElementById("drawer-save-btn").addEventListener("click", () =>
    doSave(post.id, document.getElementById("drawer-text").value)
  );
  document.getElementById("drawer-approve-btn").addEventListener("click", () =>
    doApprove(post.id, document.getElementById("drawer-text").value)
  );
  document.getElementById("drawer-reject-btn").addEventListener("click", () => showRejectBox(post.id));
  document.getElementById("drawer-regen-image-btn").addEventListener("click", () => doRegenerateImage(post.id));

  const restoreBtn = document.getElementById("drawer-restore-btn");
  if (restoreBtn) restoreBtn.addEventListener("click", () => doRestore(post.id));

  const regenTextBtn = document.getElementById("drawer-regen-text-btn");
  if (regenTextBtn) regenTextBtn.addEventListener("click", () => doRegenerateText(post.id));

  if (state.drawerRejecting) showRejectBox(post.id, /* fromRender */ true);
}

function showRejectBox(postId, fromRender) {
  state.drawerRejecting = true;
  const box = document.getElementById("drawer-reject-box");
  box.innerHTML = `
    <div class="reject-box">
      <p>Warum wird dieser Post abgelehnt? (Pflichtfeld — bitte konkret)</p>
      <textarea id="reject-reason" rows="3" placeholder="Z. B.: werblicher Ton, schwacher Hook, falsche Angabe..."></textarea>
      <div class="action-row">
        <button class="btn btn-primary btn-sm" id="reject-confirm-btn">Ablehnung bestätigen</button>
        <button class="btn btn-ghost btn-sm" id="reject-cancel-btn">Abbrechen</button>
      </div>
    </div>
  `;
  document.getElementById("reject-confirm-btn").addEventListener("click", () => doReject(postId));
  document.getElementById("reject-cancel-btn").addEventListener("click", () => {
    state.drawerRejecting = false;
    box.innerHTML = "";
  });
}

// ---------- Post actions ----------

async function doSave(postId, text) {
  try {
    await apiPatch(`/posts/${postId}`, { post_text: text });
    toast("Gespeichert.", "success");
    loadPosts();
  } catch (e) {
    toast(`Fehler beim Speichern (${e.message})`, "error");
  }
}

async function doApprove(postId, text) {
  try {
    await apiPatch(`/posts/${postId}`, { post_text: text, status: "approved" });
    toast("Genehmigt.", "success");
    closeDrawer();
    loadPosts();
    loadCounts();
    loadKbStats();
  } catch (e) {
    toast(`Fehler bei der Genehmigung (${e.message})`, "error");
  }
}

async function doReject(postId) {
  const reason = document.getElementById("reject-reason").value.trim();
  if (!reason) {
    toast("Bitte einen Grund für die Ablehnung angeben.", "error");
    return;
  }
  try {
    await apiPost(`/posts/${postId}/reject`, { reason });
    toast("Abgelehnt.", "success");
    state.drawerRejecting = false;
    closeDrawer();
    loadPosts();
    loadCounts();
  } catch (e) {
    toast(`Fehler bei der Ablehnung (${e.message})`, "error");
  }
}

async function doRestore(postId) {
  try {
    await apiPatch(`/posts/${postId}`, { status: "draft" });
    toast("Als Entwurf wiederhergestellt.", "success");
    closeDrawer();
    loadPosts();
    loadCounts();
  } catch (e) {
    toast(`Fehler beim Wiederherstellen (${e.message})`, "error");
  }
}

async function doRegenerateImage(postId) {
  toast("Neues Bild wird generiert…");
  try {
    await apiPost(`/posts/${postId}/regenerate-image`);
    await refreshDrawer();
    loadPosts();
    toast("Bild neu generiert.", "success");
  } catch (e) {
    toast(`Fehler beim Neugenerieren des Bilds (${e.message})`, "error");
  }
}

async function doRegenerateText(postId) {
  try {
    await apiPost(`/posts/${postId}/regenerate-text`);
    toast("Text wird im Hintergrund neu generiert — das kann einige Minuten dauern.");
    pollForTextUpdate(postId);
  } catch (e) {
    toast(`Fehler beim Starten der Neugenerierung (${e.message})`, "error");
  }
}

async function pollForTextUpdate(postId, attempt = 0) {
  if (attempt > 20 || state.drawerPostId !== postId) return; // ~2min max, or drawer closed/changed
  await new Promise((r) => setTimeout(r, 6000));
  if (state.drawerPostId !== postId) return;
  try {
    const post = await apiGet(`/posts/${postId}`);
    const currentText = document.getElementById("drawer-text");
    if (currentText && currentText.value !== post.post_text) {
      renderDrawer(post);
      loadPosts();
      toast("Text aktualisiert.", "success");
      return;
    }
  } catch (_) {}
  pollForTextUpdate(postId, attempt + 1);
}

// ---------- Sidebar actions ----------

async function doRunPipeline() {
  try {
    const res = await apiPost("/run");
    if (res.status === "already_running") {
      toast("Pipeline läuft bereits — bitte warten.");
    } else {
      toast("Pipeline im Hintergrund gestartet…");
    }
    startPipelineStatusPolling();
  } catch (e) {
    toast(`Fehler beim Starten der Pipeline (${e.message})`, "error");
  }
}

// ---------- Pipeline progress banner ----------

const PIPELINE_PHASE_LABELS = {
  collecting: "Artikel werden gesammelt…",
  processing: "Artikel werden verarbeitet…",
};

function renderPipelineStatus(status) {
  const banner = document.getElementById("pipeline-status-banner");
  const text = document.getElementById("pipeline-status-text");
  if (!status || !status.running) {
    banner.classList.add("hidden");
    return;
  }
  banner.classList.remove("hidden");
  let label = PIPELINE_PHASE_LABELS[status.phase] || "Pipeline läuft…";
  if (status.phase === "processing" && status.articles_target > 0) {
    label = `${status.posts_created}/${status.articles_target} Post(s) erstellt (${status.articles_relevant} relevante Artikel getestet, ${status.articles_scanned} durchsucht)`;
  }
  text.textContent = `⏳ ${label}`;
}

async function pollPipelineStatusOnce() {
  let status;
  try {
    status = await apiGet("/pipeline/status");
  } catch (_) {
    return; // API unreachable — leave banner/state as-is, next tick retries
  }
  renderPipelineStatus(status);
  if (!status.running) {
    clearInterval(pipelineStatusTimer);
    pipelineStatusTimer = null;
    loadPosts();
    loadCounts();
    if (status.phase === "error") {
      toast(`Pipeline mit Fehler beendet (${status.last_error || "unbekannt"})`, "error");
    } else if (status.phase === "done") {
      const reachedTarget = status.posts_created >= status.articles_target;
      let msg = `Pipeline abgeschlossen: ${status.posts_created}/${status.articles_target} Post(s) erstellt`;
      if (!reachedTarget) msg += ` — RSS-Pool erschöpft (${status.articles_scanned} Artikel durchsucht).`;
      toast(msg, status.posts_created > 0 ? "success" : "info");
    }
  }
}

function startPipelineStatusPolling() {
  if (pipelineStatusTimer) return;
  pollPipelineStatusOnce();
  pipelineStatusTimer = setInterval(pollPipelineStatusOnce, 3000);
}

async function doGenMissingTexts() {
  try {
    const res = await apiPost("/posts/regenerate-missing-text");
    toast(res.count ? `Text für ${res.count} Post(s) wird im Hintergrund generiert.` : "Kein Post mit fehlendem Text gefunden.");
  } catch (e) {
    toast(`Fehler (${e.message})`, "error");
  }
}

async function doKbSync() {
  try {
    const res = await apiPost("/kb/sync");
    toast(res.count ? `${res.count} genehmigte(r) Post(s) werden synchronisiert.` : "Kein genehmigter Post zum Synchronisieren.");
  } catch (e) {
    toast(`Fehler (${e.message})`, "error");
  }
}

async function doKbReload() {
  try {
    await apiPost("/kb/reload-guidelines");
    toast("Style Guide wird im Hintergrund neu geladen.");
    setTimeout(loadKbStats, 4000);
  } catch (e) {
    toast(`Fehler (${e.message})`, "error");
  }
}

// ---------- Settings modal ----------

function openSettings() {
  document.getElementById("settings-api-url").value = getApiBase();
  document.getElementById("settings-overlay").classList.remove("hidden");
  document.getElementById("settings-modal").classList.remove("hidden");
}

function closeSettings() {
  document.getElementById("settings-overlay").classList.add("hidden");
  document.getElementById("settings-modal").classList.add("hidden");
}

// ---------- Wiring + boot ----------

function wireStaticControls() {
  document.getElementById("btn-run-pipeline").addEventListener("click", doRunPipeline);
  document.getElementById("btn-empty-run").addEventListener("click", doRunPipeline);
  document.getElementById("btn-gen-missing").addEventListener("click", doGenMissingTexts);
  document.getElementById("btn-refresh").addEventListener("click", () => {
    loadPosts();
    loadCounts();
    loadKbStats();
  });
  document.getElementById("btn-kb-sync").addEventListener("click", doKbSync);
  document.getElementById("btn-kb-reload").addEventListener("click", doKbReload);

  document.getElementById("btn-settings").addEventListener("click", openSettings);
  document.getElementById("settings-cancel-btn").addEventListener("click", closeSettings);
  document.getElementById("settings-save-btn").addEventListener("click", () => {
    const value = document.getElementById("settings-api-url").value.trim();
    if (!value) return;
    setApiBase(value);
    closeSettings();
    boot();
  });

  document.getElementById("drawer-overlay").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeDrawer();
      closeSettings();
    }
  });
}

async function boot() {
  const discovered = await discoverApiBase();
  if (discovered) setApiBase(discovered);

  if (!getApiBase()) {
    showSetupScreen();
    return;
  }
  showMainScreen();
  renderStatusNav();
  await Promise.all([loadPosts(), loadCounts(), loadKbStats()]);

  try {
    const status = await apiGet("/pipeline/status");
    if (status.running) startPipelineStatusPolling();
  } catch (_) {}

  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(() => {
    loadCounts();
    if (!state.drawerPostId) loadPosts();
    loadKbStats();
  }, 20000);
}

document.addEventListener("DOMContentLoaded", () => {
  initSetupScreen();
  wireStaticControls();
  boot();
});
