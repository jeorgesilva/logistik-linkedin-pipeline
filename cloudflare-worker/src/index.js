/**
 * Logistik LinkedIn Pipeline — Warteschlange für Anfragen bei ausgeschaltetem Rechner.
 *
 * Ermöglicht einem Teammitglied, auf einer immer erreichbaren Seite (diesem Worker) auf
 * „Posts generieren“ zu klicken, auch wenn der Rechner, auf dem die Pipeline tatsächlich
 * läuft (engine-api + Ollama, auf jemandes Computer), ausgeschaltet ist. Der Klick wird
 * hier in KV als ein einzelner offener Eintrag gespeichert; queue_poller.py der engine-api
 * fragt /api/pending ab, sobald sie online ist, führt die Pipeline aus und meldet das
 * Ergebnis über /api/complete zurück.
 *
 * Es wird immer nur eine Anfrage gleichzeitig verfolgt (KV_KEY unten) — gedacht für
 * „ein Teammitglied, gelegentliche Klicks“, nicht für eine echte Mehrfach-Job-Queue. Falls
 * sich das ändert, den einzelnen KV-Schlüssel durch eine D1-Tabelle ersetzen.
 *
 * Dient außerdem als Verzeichnis für die aktuelle öffentliche URL der engine-api: Der
 * Rechner mit der engine-api liegt hinter einem kostenlosen Cloudflare Quick Tunnel, dessen
 * Hostname bei jedem Neustart zufällig neu vergeben wird. tunnel_reporter.py (engine-api)
 * überträgt den aktuellen bei jeder Änderung hierher; das cloudflare-dashboard ruft ihn beim
 * Laden ab, statt dass jedes Mal jemand eine neue URL von Hand einfügen muss.
 */

const KV_KEY = "latest_request";
const TUNNEL_KV_KEY = "current_tunnel_url";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === "/api/request" && request.method === "POST") {
      return handleRequest(env);
    }
    if (path === "/api/status" && request.method === "GET") {
      return handleStatus(env);
    }
    if (path === "/api/pending" && request.method === "GET") {
      if (!isAuthorized(request, env)) return unauthorized();
      return handlePending(env);
    }
    if (path === "/api/complete" && request.method === "POST") {
      if (!isAuthorized(request, env)) return unauthorized();
      return handleComplete(request, env);
    }
    if (path === "/api/tunnel-url" && request.method === "GET") {
      return handleGetTunnelUrl(env);
    }
    if (path === "/api/tunnel-url" && request.method === "POST") {
      if (!isAuthorized(request, env)) return unauthorized();
      return handleSetTunnelUrl(request, env);
    }

    return new Response("Not found", { status: 404 });
  },
};

function isAuthorized(request, env) {
  const secret = request.headers.get("X-Queue-Secret");
  return Boolean(secret) && Boolean(env.QUEUE_SHARED_SECRET) && secret === env.QUEUE_SHARED_SECRET;
}

function unauthorized() {
  return json({ error: "unauthorized" }, 401);
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// POST /api/request — das Teammitglied klickt den Button. Öffentlicher Endpunkt, dieselbe
// Vertrauensstufe wie POST /run der engine-api (derzeit ohne Authentifizierung). Um
// einzuschränken, wer das auslösen kann, Cloudflare Access vor die Route dieses Workers
// schalten, statt hier eine Authentifizierung einzubauen.
async function handleRequest(env) {
  const raw = await env.QUEUE_KV.get(KV_KEY);
  if (raw) {
    const existing = JSON.parse(raw);
    if (existing.status === "pending" || existing.status === "processing") {
      // Es läuft bereits eine aktive Anfrage — keine zweite stapeln.
      return json({ ...existing, already_queued: true });
    }
  }

  const record = {
    status: "pending",
    requested_at: new Date().toISOString(),
    completed_at: null,
    result_summary: null,
  };
  await env.QUEUE_KV.put(KV_KEY, JSON.stringify(record));
  return json(record);
}

// GET /api/status — wird von der Button-Seite abgefragt, um den aktuellen Zustand anzuzeigen.
async function handleStatus(env) {
  const raw = await env.QUEUE_KV.get(KV_KEY);
  if (!raw) return json({ status: "idle" });
  return json(JSON.parse(raw));
}

// GET /api/pending — wird von queue_poller.py der engine-api abgefragt (per Secret geschützt).
async function handlePending(env) {
  const raw = await env.QUEUE_KV.get(KV_KEY);
  if (!raw) return json({ pending: false });

  const record = JSON.parse(raw);
  if (record.status !== "pending") return json({ pending: false });

  // Sofort auf "processing" umstellen, damit eine zweite Abfrage (Neustart der engine-api
  // mitten im Lauf oder eine langsame Pipeline) denselben Job nicht doppelt abholt.
  record.status = "processing";
  await env.QUEUE_KV.put(KV_KEY, JSON.stringify(record));
  return json({ pending: true, ...record });
}

// POST /api/complete — die engine-api meldet das Ergebnis zurück (per Secret geschützt).
async function handleComplete(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON body" }, 400);
  }

  const record = {
    status: body.status === "error" ? "error" : "done",
    requested_at: body.requested_at || null,
    completed_at: new Date().toISOString(),
    result_summary: body.result_summary || "",
  };
  await env.QUEUE_KV.put(KV_KEY, JSON.stringify(record));
  return json({ ok: true });
}

// GET /api/tunnel-url — wird von cloudflare-dashboard beim Laden abgefragt, um die aktuelle
// URL der engine-api zu finden. Öffentlich: Die URL selbst ist kein Geheimnis, das Dashboard
// braucht sie überhaupt erst, um mit der API zu sprechen. Wird Cross-Origin aufgerufen (vom
// eigenen Origin von logistik-dashboard) und braucht daher einen expliziten CORS-Header —
// anders als /api/request und /api/status, die die Button-Seite selbst Same-Origin aufruft,
// und /api/pending und /api/complete, die die engine-api serverseitig aufruft (kein Browser,
// kein CORS beteiligt).
async function handleGetTunnelUrl(env) {
  const url = await env.QUEUE_KV.get(TUNNEL_KV_KEY);
  return new Response(JSON.stringify({ url: url || null }), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
    },
  });
}

// POST /api/tunnel-url — wird von tunnel_reporter.py der engine-api gemeldet, sobald sich
// der Hostname des Quick Tunnels ändert (per Secret geschützt, dieselbe Vertrauensstufe wie
// /api/pending und /api/complete).
async function handleSetTunnelUrl(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON body" }, 400);
  }
  if (!body.url || typeof body.url !== "string") {
    return json({ error: "missing url" }, 400);
  }
  await env.QUEUE_KV.put(TUNNEL_KV_KEY, body.url);
  return json({ ok: true });
}
