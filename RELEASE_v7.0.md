# GroundedOps v7.0

Grounded, source-cited answers over your own documents — a local RAG system
(FastAPI + Ollama + ChromaDB, with an optional DeepSeek escalation) and a new
React frontend.

## Highlights in v7.0

- **New React frontend (Vite).** The Streamlit UI has been replaced by a
  Vite + React app for reliable, designed control of the interface. The
  FastAPI backend is **unchanged** — the frontend talks to it through a dev
  proxy, so there is no CORS setup and no new endpoints required.
- **Answer disambiguation.** Vague or in-domain-but-underspecified questions
  now ask a focused clarifying question (with pickable options) instead of
  guessing; out-of-domain questions are cleanly rejected.
- **Verified DeepSeek escalation.** When a local answer can't be grounded (or
  is an unusable template/boilerplate leak), the system can escalate to
  DeepSeek and reports whether it did (`escalated_to_deepseek`).
- **Suppressed ungrounded output.** Template-leak and total-generation-failure
  answers are no longer surfaced; they're replaced with a clear
  "I could not find that in the knowledge base."
- **Encrypted key storage (backend).** `keyvault.py` stores a DeepSeek key
  Fernet-encrypted at rest, derived from a machine key, and never re-displays
  it. (In the React frontend the key is instead kept in the browser and sent
  per request — see "DeepSeek key" below.)
- **Cleaner retrieval.** PDF running-headers and Title-Case section headings
  are filtered during ingestion for better chunk quality.

## Frontend (React)

Location: `frontend/`

Features: collapsible icon rail with hover tooltips, cream/copper editorial
theme with a full dark mode, user/assistant chat bubbles, selectable answers
that open a right-hand details panel (grounded score, model used, clickable
sources, "re-answer with another model", raw details), multi-file upload,
knowledge-base reset with confirmation, and page-load / thinking animations.

### Run (development)

```
# 1) Backend (from src/)
uvicorn main:app --reload            # http://localhost:8000

# 2) Frontend (from frontend/)
npm install
npm run dev                          # http://localhost:5173
```

Vite proxies `/api/*` to `http://localhost:8000` (prefix stripped), so the
backend needs no changes. Override the target with `API_TARGET` if needed.

### Logo

Drop your logo at `frontend/public/logo.png` (used top-left and in the rail;
rendered white in dark mode). Falls back to a "GroundedOps" wordmark.

### DeepSeek key (frontend)

Stored in the browser (localStorage) and sent with each query as
`deepseek_api_key`; never displayed again. Manage it in Settings.
To keep the key encrypted at rest on the server instead (reusing
`keyvault.py`), add `POST /set_key` + `GET /key_status` and point
`src/api.js` at them.

### Production build

```
npm run build                        # static files -> frontend/dist/
```

Serve `dist/` from FastAPI (`StaticFiles`) or host it separately and add CORS
to the backend for your origin.

## Backend API (unchanged)

- `GET  /status`, `/health`, `/stats`, `/rethink_options`
- `POST /query`, `/upload`, `/reset`, `/clear_session`, `/delete_source`,
  `/source_chunks`

`/query` returns: `answer, role, model, provider, fallback_used,
escalated_to_deepseek, grounding_score, flagged, retrieval_score,
resolved_query, timing{…}, sources[{source, chunk_ids, snippet}]`.

## Upgrade notes

- No database or API changes; existing ingested documents and sessions work
  as-is.
- The Streamlit app (`app.py`) is superseded by the React frontend. It can be
  kept for reference or removed.
- Add `node_modules/`, `frontend/node_modules/`, `dist/`, `.deepseek_key.enc`,
  `.deepseek_key.json`, `__pycache__/`, and `*.pyc` to `.gitignore`.

## Tests

```
python run_tests.py
```
Backend logic suite: 107 passed, 5 skipped (live-only), 0 failed.
