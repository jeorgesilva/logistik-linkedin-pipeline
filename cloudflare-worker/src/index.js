/**
 * Logistik LinkedIn Pipeline — offline-friendly request queue.
 *
 * Lets a colleague click "Gerar Posts" on a page that is always online
 * (this Worker), even while the machine that actually runs the pipeline
 * (engine-api + Ollama, on someone's computer) is turned off. The click is
 * stored here in KV as a single pending record; engine-api's queue_poller.py
 * polls /api/pending whenever it's online, runs the pipeline, and reports
 * back via /api/complete.
 *
 * Only one request is tracked at a time (KV_KEY below) — this is meant for
 * "one colleague, occasional clicks", not a real multi-job queue. If that
 * ever changes, swap the single KV key for a D1 table.
 */

const KV_KEY = "latest_request";

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

// POST /api/request — colleague clicks the button. Public endpoint, same
// trust level as engine-api's own POST /run (no auth today). If you want to
// restrict who can trigger this, put Cloudflare Access in front of this
// Worker's route rather than adding auth here.
async function handleRequest(env) {
  const raw = await env.QUEUE_KV.get(KV_KEY);
  if (raw) {
    const existing = JSON.parse(raw);
    if (existing.status === "pending" || existing.status === "processing") {
      // Already an active request in flight — don't stack a second one.
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

// GET /api/status — polled by the button page to show current state.
async function handleStatus(env) {
  const raw = await env.QUEUE_KV.get(KV_KEY);
  if (!raw) return json({ status: "idle" });
  return json(JSON.parse(raw));
}

// GET /api/pending — polled by engine-api's queue_poller.py (secret-protected).
async function handlePending(env) {
  const raw = await env.QUEUE_KV.get(KV_KEY);
  if (!raw) return json({ pending: false });

  const record = JSON.parse(raw);
  if (record.status !== "pending") return json({ pending: false });

  // Flip to "processing" immediately so a second poll (engine-api restarting
  // mid-run, or a slow pipeline) doesn't pick up the same job twice.
  record.status = "processing";
  await env.QUEUE_KV.put(KV_KEY, JSON.stringify(record));
  return json({ pending: true, ...record });
}

// POST /api/complete — engine-api reports the result back (secret-protected).
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
