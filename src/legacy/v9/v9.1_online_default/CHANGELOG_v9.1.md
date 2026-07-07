# GroundedOps — v9.1
 (UI mode toggle + manual local-model lifecycle)

Backend: main.py, llm.py, router.py, runtime_config.py (NEW).
Frontend: frontend/src/App.jsx, api.js, styles.css, components/Dialogs.jsx.
Supersedes v8.5 (doc2query etc. carried forward). No re-ingest for THESE
changes (v8.5's doc2query re-ingest requirement still applies if not done).

## 1. Online / Free toggle — top right of the chat
Pill toggle: **Online** (DeepSeek API — fast, needs key) vs **Free**
(local Ollama — private, slower, uses RAM). Switching is LIVE: new
runtime_config.py holds the mode; router + condensation read it per-call,
no restart. The env var GENERATION_MODE still sets the initial value.

## 2. Local models are NOT loaded at startup
Startup no longer warms mistral/phi (saves several GB RAM + startup time,
and they're unused in Online mode). Settings now has a "Local models"
section with **Load local models** / **Unload (free RAM)** buttons and a
disclaimer: Free mode takes noticeably longer per answer and holds
several GB of RAM. If the user asks in Free mode WITHOUT loading first,
the first answer simply pays cold-load (nothing breaks) — the UI shows a
note under the toggle explaining this.

## 3. Switching to Online offers to unload
If local models are loaded and the user flips to Online, a confirm dialog
offers to unload them to free memory (Ollama keep_alive=0). Also
available any time from Settings.

## New endpoints
GET  /settings                -> { generation_mode, local_models_loaded }
POST /settings/mode {mode}    -> live switch, "local" | "api"
POST /models/warmup           -> load phi+mistral (manual)
POST /models/unload           -> unload both, free RAM

## Apply
Backend: copy the 4 .py files into src/, restart backend once.
Frontend: copy the 4 frontend files into frontend/src/..., then
`npm run dev` (or `npm run build` for the built app).

## Notes / honest caveats
- "Online" requires the DeepSeek key (set in Settings as before). If the
  key is missing in Online mode, answers will fail — the key status dot
  in Settings is the tell.
- Eval runs: the preflight query will now pay cold-load in local mode
  (no auto-warmup). Either hit /models/warmup before eval, or accept a
  slow first case. eval preflight timeout (300s) already covers it.
- The eval suite should be run once after applying to confirm no
  behavioral drift (expected: none in local mode with models loaded).
