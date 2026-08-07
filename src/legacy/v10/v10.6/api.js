// Thin API client for the GroundedOps FastAPI backend.
// All calls go through the "/api" prefix, which Vite proxies to :8000.

const BASE = "/api";

// The DeepSeek key is kept in localStorage and sent with each query. It is
// never rendered back into any input (see SettingsDialog). If you'd prefer
// the key encrypted at rest on the server (the old Streamlit behaviour),
// add /set_key + /key_status endpoints that call keyvault.py — see README.
const KEY_STORAGE = "groundedops.deepseek_key";

export const getKey = () => localStorage.getItem(KEY_STORAGE) || null;
export const setKey = (k) => localStorage.setItem(KEY_STORAGE, k);
export const clearKey = () => localStorage.removeItem(KEY_STORAGE);
export const hasKey = () => !!getKey();

// v9.1.1: additional Online providers. Same storage pattern as DeepSeek:
// kept only in this browser, sent with each query, never rendered back.
const KEYS = {
  deepseek: KEY_STORAGE,
  openai: "groundedops.openai_key",
  anthropic: "groundedops.anthropic_key",
};
export const getProviderKey = (p) => localStorage.getItem(KEYS[p]) || null;
export const setProviderKey = (p, k) => localStorage.setItem(KEYS[p], k);
export const clearProviderKey = (p) => localStorage.removeItem(KEYS[p]);
export const hasProviderKey = (p) => !!getProviderKey(p);

async function jsonGet(path, headers) {
  const r = await fetch(`${BASE}${path}`, headers ? { headers } : undefined);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

async function jsonPatch(path, body, extraHeaders) {
  const r = await fetch(`${BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...(extraHeaders || {}) },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error((await r.text()) || `${path}: ${r.status}`);
  return r.json();
}

async function jsonDelete(path, headers) {
  const r = await fetch(`${BASE}${path}`, { method: "DELETE", headers: headers || {} });
  if (!r.ok) throw new Error((await r.text()) || `${path}: ${r.status}`);
  return r.json();
}

async function jsonPost(path, body, extraHeaders) {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(extraHeaders || {}) },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error((await r.text()) || `${path}: ${r.status}`);
  return r.json();
}

export const api = {
  status: () => jsonGet("/status"),
  stats: () => jsonGet("/stats"),
  rethinkOptions: () => jsonGet("/rethink_options"),

  // v8.6: runtime mode toggle (online/DeepSeek vs free/local) + manual
  // local-model lifecycle. Local LLMs are no longer auto-loaded at
  // startup; the settings panel loads them on demand.
  settings: () => jsonGet("/settings"),
  setMode: (mode) => jsonPost("/settings/mode", { mode }),
  setOnlineProvider: (provider) => jsonPost("/settings/online_provider", { provider }),
  warmupModels: (models) => jsonPost("/models/warmup", { models: models || null }),
  unloadModels: (models) => jsonPost("/models/unload", { models: models || null }),
  modelsStatus: () => jsonGet("/models/status"),
  pullModels: (models) => jsonPost("/models/pull", { models }),
  pullStatus: () => jsonGet("/models/pull_status"),

  query: ({ q, sessionId, forceProvider, forceModel, sourceFilter, product, category }) =>
    jsonPost("/query", {
      q,
      session_id: sessionId,
      deepseek_api_key: getKey(),
      openai_api_key: getProviderKey("openai"),
      anthropic_api_key: getProviderKey("anthropic"),
      force_provider: forceProvider || null,
      force_model: forceModel || null,
      source_filter: sourceFilter || null,
      product: product || null,
      category: category || null,
    }),

  catalog: () => jsonGet("/catalog"),
  ingestVersion: () => jsonGet("/ingest/version"),
  reloadFolder: (pw) => jsonPost("/ingest/reload_folder", {}, { "X-Admin-Password": pw }),
  faq: (product) => jsonGet(`/faq${product ? `?product=${encodeURIComponent(product)}` : ""}`),
  faqEdit: (pw, id, answer) => jsonPatch(`/faq/${id}`, { answer }, { "X-Admin-Password": pw }),
  faqDelete: (pw, id) => jsonDelete(`/faq/${id}`, { "X-Admin-Password": pw }),
  adminLogin: (pw) => jsonPost("/admin/login", {}, { "X-Admin-Password": pw }),
  adminAddCategory: (pw, key, name) => jsonPost("/admin/category", { key, name }, { "X-Admin-Password": pw }),
  adminDeleteCategory: (pw, key) => jsonDelete(`/admin/category/${key}`, { "X-Admin-Password": pw }),
  adminAddProduct: (pw, category_key, key, name, sources) =>
    jsonPost("/admin/product", { category_key, key, name, sources }, { "X-Admin-Password": pw }),
  adminDeleteProduct: (pw, ck, pk) => jsonDelete(`/admin/product/${ck}/${pk}`, { "X-Admin-Password": pw }),
  adminUploadToProduct: async (pw, ck, pk, file, onProgress, ingestProvider) => {
    const fd = new FormData(); fd.append("file", file, file.name);
    const headers = { "X-Admin-Password": pw, "category_key": ck, "product_key": pk };
    if (ingestProvider) headers["ingest_provider"] = ingestProvider;
    const r = await fetch(`${BASE}/upload`, { method: "POST", body: fd, headers });
    if (!r.ok) throw new Error((await r.text()) || `upload: ${r.status}`);
    const { job_id } = await r.json();
    while (true) {
      await new Promise((res) => setTimeout(res, 1500));
      const st = await jsonGet(`/upload/status/${job_id}`);
      if (onProgress) onProgress(st);
      if (st.done) { if (st.status === "error") throw new Error(st.error); return st; }
    }
  },
  // v2.1: server-side history for registered users. The user id header
  // is a PLACEHOLDER until the website token integration lands.
  listConversations: (userId) => jsonGet("/conversations", userId ? { "X-User-Id": userId } : {}),
  getConversation: (userId, id) => jsonGet(`/conversations/${id}`, { "X-User-Id": userId }),
  deleteConversation: (userId, id) => jsonDelete(`/conversations/${id}`, { "X-User-Id": userId }),

  sourceChunks: (chunkIds) => jsonPost("/source_chunks", { chunk_ids: chunkIds }),
  deleteSource: (source) => jsonPost("/delete_source", { source }),
  clearSession: (sessionId) => jsonPost("/clear_session", { session_id: sessionId }),
  reset: () => jsonPost("/reset", {}),

  upload: async (file, onProgress) => {
    // v10.x: async ingest. Start the job (returns immediately), then poll
    // status until done — no long-held request, so no nginx 504.
    const fd = new FormData();
    fd.append("file", file, file.name);
    const r = await fetch(`${BASE}/upload`, { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.text()) || `upload: ${r.status}`);
    const { job_id } = await r.json();
    // Poll until done.
    while (true) {
      await new Promise((res) => setTimeout(res, 1500));
      const s = await jsonGet(`/upload/status/${job_id}`);
      if (onProgress) onProgress(s);
      if (s.done) {
        if (s.status === "error") throw new Error(s.error || "Ingest failed");
        return s;
      }
    }
  },
};
