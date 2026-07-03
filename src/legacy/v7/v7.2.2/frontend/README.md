# GroundedOps — React frontend

A Vite + React frontend for the existing GroundedOps FastAPI backend.
**The backend is unchanged** — this app talks to it through a dev proxy.

## Run (development)

One command starts **both** the backend and the frontend, and stops the
backend automatically when you quit (Ctrl+C, close the terminal, or if Vite
exits). The backend runs without `--reload`, so it's a single process that
gets killed cleanly instead of leaving an orphan.

```
npm install
npm run dev
```

Open http://localhost:5173

The launcher (`dev.mjs`) assumes your backend `main.py` is in a sibling
`../src` folder and that `python` is on your PATH. Override with env vars if
not:

```
# Use a virtualenv's Python:
PYTHON=.venv/Scripts/python npm run dev      # Windows
PYTHON=.venv/bin/python npm run dev          # macOS / Linux

# Backend lives elsewhere / different port:
BACKEND_DIR=../server BACKEND_PORT=8001 npm run dev
```

Prefer to run them separately (e.g. backend in its own terminal)?
```
npm run web        # frontend only (Vite)
npm run backend    # backend only (uvicorn, no reload)
```

Vite proxies `/api/*` to the backend, so the backend needs no CORS and no
changes. If nothing is listening yet, the app sits on the splash and the
console shows `ECONNREFUSED` until the backend is up — that's expected.

## Your logo

Drop your logo at `frontend/public/logo.png`. It's used at the top-left and
in the rail; in dark mode it's rendered white automatically. If the file is
absent, the app falls back to a "GroundedOps" wordmark.

## DeepSeek key

The key is stored in this browser (localStorage) and sent with each query as
`deepseek_api_key`. It is never displayed again. Manage it in Settings (the
gear in the rail or the details panel).

> If you later want the key encrypted at rest on the server (the old
> Streamlit behaviour via `keyvault.py`), add two small endpoints —
> `POST /set_key` and `GET /key_status` — and switch `src/api.js` to call
> them instead of localStorage. Ask and I'll wire it.

## Production build

```
npm run build      # outputs static files to dist/
```
Then either:
- Serve `dist/` from FastAPI with `StaticFiles`, or
- Host `dist/` separately and add CORS to the backend (allow your origin),
  pointing the app at the backend URL.

## Where things live

- `src/api.js` — all backend calls
- `src/App.jsx` — app state, ready-polling, query submission, layout
- `src/components/Rail.jsx` — collapsible icon rail + tooltips
- `src/components/Message.jsx` — chat bubbles / selectable answer cards
- `src/components/DetailsPanel.jsx` — sources, rethink, grounded/model, details
- `src/components/Dialogs.jsx` — Settings + Documents
- `src/styles.css` — theme + layout (edit the `:root` knobs at the top)
