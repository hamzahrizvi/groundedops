# GroundedOps — React frontend

A Vite + React frontend for the existing GroundedOps FastAPI backend.
**The backend is unchanged** — this app talks to it through a dev proxy.

## Run (development)

1. Start your backend as usual (FastAPI on port 8000):
   ```
   uvicorn main:app --reload      # from your src/ directory
   ```
2. In this `frontend/` folder:
   ```
   npm install
   npm run dev
   ```
3. Open http://localhost:5173

Vite proxies every `/api/*` request to `http://localhost:8000` (stripping
the `/api` prefix), so no CORS setup or backend change is needed. If your
backend runs elsewhere, set `API_TARGET`, e.g.:
```
API_TARGET=http://127.0.0.1:9000 npm run dev
```

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
