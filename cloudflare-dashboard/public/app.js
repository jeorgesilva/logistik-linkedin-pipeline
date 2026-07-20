/**
 * Logistik Pipeline — dashboard (static, no build step).
 * Talks directly to engine-api's REST API from the browser. Requires
 * CORS_ALLOWED_ORIGINS on engine-api to include this page's origin.
 */

const STORAGE_KEY = "logistik_api_base_url";
const STATUS_FILTERS = [
  { key: "all", label: "Todos" },
  { key: "draft", label: "Rascunho" },
  { key: "approved", label: "Aprovado" },
  { key: "rejected", label: "Rejeitado" },
  { key: "posted", label: "Publicado" },
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

// ---------- API base URL (stored per-browser in localStorage) ----------

function getApiBase() {
  return (localStorage.getItem(STORAGE_KEY) || "").replace(/\/+$/, "");
}

function setApiBase(url) {
  localStorage.setItem(STORAGE_KEY, url.replace(/\/+$/, ""));
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
  label.textContent = ok ? "Conectado" : "Sem conexão com a API";
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
    countEl.textContent = `${posts.length} post${posts.length === 1 ? "" : "s"}`;
    loading.classList.add("hidden");

    if (posts.length === 0) {
      empty.classList.remove("hidden");
      return;
    }
    renderPostGrid(posts);
  } catch (e) {
    loading.classList.add("hidden");
    toast(`Não foi possível carregar os posts (${e.message})`, "error");
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
    draft: "Rascunho",
    approved: "Aprovado",
    rejected: "Rejeitado",
    posted: "Publicado",
    candidate: "Processando",
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
    return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
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
        <p class="post-card-title">${escapeHtml(post.title || "(sem título)")}</p>
        <span class="post-card-topic">${escapeHtml(post.topic || "")}</span>
        <p class="post-card-snippet">${escapeHtml((post.post_text || "Sem texto gerado ainda.").slice(0, 220))}</p>
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
      el.textContent = `✓ RAG ativo — ${stats.style_examples} exemplos / ${stats.style_guidelines} trechos`;
    } else {
      el.className = "kb-indicator kb-inactive";
      el.textContent = `RAG inativo — ${stats.style_examples}/${stats.min_examples_for_rag} exemplos aprovados`;
    }
  } catch (e) {
    el.className = "kb-indicator kb-inactive";
    el.textContent = "Não foi possível carregar o status do KB.";
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
    toast(`Não foi possível carregar o post (${e.message})`, "error");
  }
}

function renderDrawer(post) {
  const container = document.getElementById("drawer-content");
  const imgUrl = imageUrlFor(post);

  container.innerHTML = `
    <div class="drawer-header">
      <span class="badge ${badgeClass(post.status)}">${statusLabel(post.status)}</span>
      <button class="icon-btn" id="drawer-close-btn" title="Fechar">✕</button>
    </div>
    <div class="drawer-body">
      <div class="drawer-image">
        ${imgUrl ? `<img src="${imgUrl}" alt="">` : `<div style="padding:40px;text-align:center;color:var(--text-faint)">Sem imagem</div>`}
      </div>
      <button class="btn btn-outline btn-sm" id="drawer-regen-image-btn">🖼 Regenerar Imagem</button>

      <div>
        <label class="field-label">Texto do post</label>
        <textarea id="drawer-text" rows="9">${escapeHtml(post.post_text || "")}</textarea>
      </div>

      ${post.image_prompt ? `
        <details>
          <summary class="details-toggle">Ver prompt da imagem</summary>
          <div class="image-prompt-box">${escapeHtml(post.image_prompt)}</div>
        </details>
      ` : ""}

      <div class="drawer-meta">
        <span><strong>Tópico:</strong> ${escapeHtml(post.topic || "—")}</span>
        ${post.source_url ? `<span><a href="${post.source_url}" target="_blank" rel="noopener">Ver artigo-fonte ↗</a></span>` : ""}
        <span>Criado em ${fmtDate(post.created_at)}</span>
      </div>

      <div class="action-row">
        <button class="btn btn-outline" id="drawer-save-btn">💾 Salvar</button>
        <button class="btn btn-primary" id="drawer-approve-btn">✅ Aprovar</button>
        <button class="btn btn-danger-outline" id="drawer-reject-btn">❌ Rejeitar</button>
        ${post.status === "rejected" ? `<button class="btn btn-outline" id="drawer-restore-btn">↩️ Restaurar</button>` : ""}
        ${post.source_url ? `<button class="btn btn-outline" id="drawer-regen-text-btn">🔄 Regenerar Texto</button>` : ""}
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
      <p>Por que este post está sendo rejeitado? (obrigatório — seja específico)</p>
      <textarea id="reject-reason" rows="3" placeholder="Ex.: tom promocional, hook fraco, dado incorreto..."></textarea>
      <div class="action-row">
        <button class="btn btn-primary btn-sm" id="reject-confirm-btn">Confirmar rejeição</button>
        <button class="btn btn-ghost btn-sm" id="reject-cancel-btn">Cancelar</button>
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
    toast("Salvo.", "success");
    loadPosts();
  } catch (e) {
    toast(`Erro ao salvar (${e.message})`, "error");
  }
}

async function doApprove(postId, text) {
  try {
    await apiPatch(`/posts/${postId}`, { post_text: text, status: "approved" });
    toast("Aprovado.", "success");
    closeDrawer();
    loadPosts();
    loadCounts();
    loadKbStats();
  } catch (e) {
    toast(`Erro ao aprovar (${e.message})`, "error");
  }
}

async function doReject(postId) {
  const reason = document.getElementById("reject-reason").value.trim();
  if (!reason) {
    toast("Informe o motivo da rejeição.", "error");
    return;
  }
  try {
    await apiPost(`/posts/${postId}/reject`, { reason });
    toast("Rejeitado.", "success");
    state.drawerRejecting = false;
    closeDrawer();
    loadPosts();
    loadCounts();
  } catch (e) {
    toast(`Erro ao rejeitar (${e.message})`, "error");
  }
}

async function doRestore(postId) {
  try {
    await apiPatch(`/posts/${postId}`, { status: "draft" });
    toast("Restaurado para rascunho.", "success");
    closeDrawer();
    loadPosts();
    loadCounts();
  } catch (e) {
    toast(`Erro ao restaurar (${e.message})`, "error");
  }
}

async function doRegenerateImage(postId) {
  toast("Gerando nova imagem…");
  try {
    await apiPost(`/posts/${postId}/regenerate-image`);
    await refreshDrawer();
    loadPosts();
    toast("Imagem regenerada.", "success");
  } catch (e) {
    toast(`Erro ao regenerar imagem (${e.message})`, "error");
  }
}

async function doRegenerateText(postId) {
  try {
    await apiPost(`/posts/${postId}/regenerate-text`);
    toast("Texto sendo regenerado em segundo plano — isso pode levar alguns minutos.");
    pollForTextUpdate(postId);
  } catch (e) {
    toast(`Erro ao iniciar regeneração (${e.message})`, "error");
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
      toast("Texto atualizado.", "success");
      return;
    }
  } catch (_) {}
  pollForTextUpdate(postId, attempt + 1);
}

// ---------- Sidebar actions ----------

async function doRunPipeline() {
  try {
    await apiPost("/run");
    toast("Pipeline iniciado em segundo plano. A lista atualiza sozinha em breve.");
  } catch (e) {
    toast(`Erro ao rodar o pipeline (${e.message})`, "error");
  }
}

async function doGenMissingTexts() {
  try {
    const res = await apiPost("/posts/regenerate-missing-text");
    toast(res.count ? `Gerando texto para ${res.count} post(s) em segundo plano.` : "Nenhum post com texto faltando.");
  } catch (e) {
    toast(`Erro (${e.message})`, "error");
  }
}

async function doKbSync() {
  try {
    const res = await apiPost("/kb/sync");
    toast(res.count ? `Sincronizando ${res.count} post(s) aprovado(s).` : "Nenhum post aprovado para sincronizar.");
  } catch (e) {
    toast(`Erro (${e.message})`, "error");
  }
}

async function doKbReload() {
  try {
    await apiPost("/kb/reload-guidelines");
    toast("Style guide sendo recarregado em segundo plano.");
    setTimeout(loadKbStats, 4000);
  } catch (e) {
    toast(`Erro (${e.message})`, "error");
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
  if (!getApiBase()) {
    showSetupScreen();
    return;
  }
  showMainScreen();
  renderStatusNav();
  await Promise.all([loadPosts(), loadCounts(), loadKbStats()]);

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
