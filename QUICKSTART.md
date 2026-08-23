# GroundedOps — Quick Start

Run GroundedOps locally against your own product documentation. Takes
about ten minutes, most of which is the first model download.

---

## 1. Prerequisites

- **Windows, macOS, or Linux**, **Python 3.11+** (the Windows installer
  fetches it via `winget` automatically if missing)
- **8GB RAM free** for API mode, 16GB if you want fully offline mode
- **An API key** for DeepSeek, OpenAI, or Anthropic — unless running offline
- Node/npm are **not** required to run the app — only if you're building
  the frontend from a fresh clone (the installer does this for you)

---

## 2. Install

```bash
git clone https://github.com/hamzahrizvi/groundedops.git
cd groundedops
install.cmd          # Windows — double-click, or run from a terminal
# ./install.sh       # macOS/Linux
```

The installer creates a local `.venv` (nothing installed system-wide),
prompts for Online-vs-Free mode, provider and API key, writes `src/.env`
for you, then pre-downloads the embedding/reranking/grounding models —
this is the ~500MB, few-minutes step.

**Fully offline instead?** Choose Free mode when asked, or edit
`src/.env` afterward: `GENERATION_MODE=local` with the keys left blank.
Nothing leaves your machine, but answers take considerably longer on CPU
and you'll need the extra RAM (needs [Ollama](https://ollama.com) too).

---

## 3. Start

```bash
run.cmd
```

Rebuilds the frontend if it's stale, starts the backend, and prints
everything you need:

```
This PC          http://127.0.0.1:8000
On the LAN       http://<your-ip>:8000
Admin console    http://127.0.0.1:8000/admin
```

Use `run.cmd -Local -NoTunnel -NoTestPage` for a plain local-only run
with nothing extra. On macOS/Linux (no launcher script yet — see
README.md's "Native install" section for the direct command).

---

## 4. Add your documents

1. Open the **Admin console** (`/admin`). On a fresh install it asks you to
   create the **root account** — that is the only level that can add other
   people. Needs `SESSION_SECRET` set in `src/.env` first, or sign-in refuses
   every attempt; generate one with
   `python -c "import secrets;print(secrets.token_hex(32))"`.
   Add colleagues later under **Accounts**: `support` for anyone doing this
   work, `basic` for someone who only needs the test chat.
2. Under **Documents**, pick a **Category** and **Product** — create them
   in the Categories/Products tabs first if the lists are empty.
3. Drop in `.pdf`, `.docx`, or `.txt` files.
4. Wait for ingestion to finish (progress shows per file).

> Selecting category **and** product before uploading matters — it's what
> scopes the document so it appears in product-specific chats. A document
> uploaded without both lands in the "needs assignment" list.

**Bulk alternative:** put files in `./corpus/` and run

```bash
curl -X POST http://127.0.0.1:8000/ingest/reload_folder \
  -H "x-admin-password: YOUR_PASSWORD"
```

On Windows PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/ingest/reload_folder" `
  -Headers @{ "x-admin-password" = "YOUR_PASSWORD" }
```

---

## 5. Build the FAQ (optional but recommended)

In the **admin console → Reviewed answers**:

- **Draft from a document** proposes questions from the ingested
  documents, generated server-side (no key needed in the browser). It
  *merges* — running it again adds only genuinely new questions and never
  overwrites answers you've written.
- **Write one yourself** adds a question by hand. These are protected
  from being overwritten by future drafting.
- **Answers by product** lists what you already have. Pick a product
  first — the page does not list every answer at once, because the full
  list runs to hundreds of rows and the wording is edited product by
  product anyway. Answers tagged with a product that is not in the
  catalogue are still offered in the picker, marked as such, so they can
  be found and retagged.
- Review every drafted answer before it goes live. Curated answers skip
  retrieval entirely, so they're served exactly as written.
- **Unanswered** lists questions people asked that the reviewed answers
  couldn't cover, ranked by frequency — your curation backlog. Answering
  one from here (or writing an entry with matching wording) resolves it
  automatically.

Not sure what a page is for? The **?** button at the top right walks you
through whichever page you are on, highlighting one thing at a time.

Write answers as complete, self-contained statements. "No, it doesn't
require internet — processing is local and results are instant" reads
correctly on its own; "No, instant results with no internet requirement"
reads like a fragment answering some other question.

---

## 6. Put the widget on a site

```html
<script
  src="http://127.0.0.1:8000/widget/groundedops-widget.js"
  data-api="http://127.0.0.1:8000"
  data-title="Product support"
  data-agent-name="Assistant"
  data-accent="#E4002B"></script>
```

Replace `127.0.0.1:8000` with your LAN address or tunnel URL (both
printed by `run.cmd`) for anyone off this machine to reach it.

The widget walks visitors through choosing a product before they can ask,
so every question is scoped. It never holds an API key — the backend
decides the model.

For a site on another origin, add it to `WIDGET_ALLOWED_ORIGINS` in
`src/.env` and restart.

---

## Everyday commands

```
run.cmd                              start everything, Ctrl+C to stop
python run_tests.py                  unit + route tests (from src/)
python eval.py                       regression gate against the baseline
```

Re-index everything from scratch: delete `src/chroma_db/`, then re-upload
or use `reload_folder` above.

---

## Troubleshooting

**Browser shows "Can't reach the backend on :8000"** — the backend is
still loading models (first start is the slow one) or isn't running; the
`run.cmd` window shows its log.

**Answers say "I could not find that in the knowledge base"** — usually
no documents ingested for the selected product. Check admin console →
Documents that the file is assigned to the product you're asking about.

**Uploaded document shows as needing assignment** — category and product
weren't both selected at upload. Use the Reassign control, or re-upload
with both chosen.

**No FAQ suggestions appear** — either the FAQ is empty for that product
(admin console → FAQ), or nothing scored above `FAQ_CANDIDATE_FLOOR`.
Check `backend.log` for `FAQ disambiguate` / `FAQ miss` lines with actual
scores, then tune the floor in `src/.env`.

**Widget shows nothing on your page** — check the browser console. A CORS
error means your page's origin isn't in `WIDGET_ALLOWED_ORIGINS`.

**Slow answers** — you're probably in `local` mode. Switch
`GENERATION_MODE=api` with a provider key in `src/.env`.

---

## Deploying this for real (Docker)

The native setup above is for local use and development. For an actual
deployment — a server, not your laptop — see `docker/` (Docker Compose
files for backend + frontend + Ollama) and `README.md`'s "Run with
Docker" section.

---

## Known limitations

Please read these before drawing conclusions from a test:

- **No authentication.** Admin endpoints are gated by a single shared
  password. Fine on a laptop, not fit for public deployment.
- **Single tenant.** One document collection, one FAQ, one configuration
  per instance. Two products can be scoped within an instance, but two
  *customers* cannot share one.
- **Source downloads are unauthenticated** so the widget can offer them —
  anything ingested is retrievable by filename.
- **Table-heavy PDFs** occasionally produce truncated checklist lines.
- **Local mode is slow on CPU** — usable for evaluation, not for
  interactive use at volume.
