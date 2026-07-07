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

async function jsonGet(path) {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

async function jsonPost(path, body) {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
  warmupModels: (models) => jsonPost("/models/warmup", { models: models || null }),
  unloadModels: (models) => jsonPost("/models/unload", { models: models || null }),

  query: ({ q, sessionId, forceProvider, forceModel, sourceFilter }) =>
    jsonPost("/query", {
      q,
      session_id: sessionId,
      deepseek_api_key: getKey(),
      force_provider: forceProvider || null,
      force_model: forceModel || null,
      source_filter: sourceFilter || null,
    }),

  sourceChunks: (chunkIds) => jsonPost("/source_chunks", { chunk_ids: chunkIds }),
  deleteSource: (source) => jsonPost("/delete_source", { source }),
  clearSession: (sessionId) => jsonPost("/clear_session", { session_id: sessionId }),
  reset: () => jsonPost("/reset", {}),

  upload: async (file) => {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const r = await fetch(`${BASE}/upload`, { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.text()) || `upload: ${r.status}`);
    return r.json();
  },
};
