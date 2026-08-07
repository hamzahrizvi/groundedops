# GroundedOps — Quick Start

Run GroundedOps locally against your own product documentation. Takes
about ten minutes, most of which is the first Docker build.

---

## 1. Prerequisites

- **Docker Desktop** (Windows/macOS) or Docker Engine + Compose (Linux)
- **8GB RAM free** for API mode, 16GB if you want fully offline mode
- **An API key** for DeepSeek, OpenAI, or Anthropic — unless running offline

---

## 2. Set up

```bash
git clone https://github.com/hamzahrizvi/groundedops.git
cd groundedops
cp .env.example .env
```

Open `.env` and set two things:

```bash
ADMIN_PASSWORD=pick-something
DEEPSEEK_API_KEY=sk-...        # or OPENAI_API_KEY / ANTHROPIC_API_KEY
```

If you use OpenAI or Anthropic, also change `ONLINE_PROVIDER` to match.

**Fully offline instead?** Set `GENERATION_MODE=local` and leave the keys
blank. Nothing leaves your machine, but answers take considerably longer
on CPU and you'll need the extra RAM.

---

## 3. Start

```bash
docker compose up -d --build
```

First build downloads the embedding, reranking and grounding models — a
few minutes. After that, startup is quick.

- Web app: <http://localhost:8080>
- API: <http://localhost:8000>

Check it's healthy:

```bash
docker compose logs -f backend
```

Wait for `Application startup complete`.

---

## 4. Add your documents

1. Open <http://localhost:8080> and go to **Admin** (your `ADMIN_PASSWORD`).
2. Under **Documents**, pick a **Category** and **Product** — create them in
   the Categories and Products tabs first if the lists are empty.
3. Drop in `.pdf`, `.docx`, or `.txt` files.
4. Wait for ingestion to finish (progress shows per file).

> Selecting category **and** product before uploading matters — it's what
> scopes the document so it appears in product-specific chats. A document
> uploaded without both lands in the "needs assignment" list.

**Bulk alternative:** put files in `./corpus/` and run

```bash
curl -X POST http://localhost:8000/ingest/reload_folder \
  -H "x-admin-password: YOUR_PASSWORD"
```

On Windows PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/ingest/reload_folder" `
  -Headers @{ "x-admin-password" = "YOUR_PASSWORD" }
```

---

## 5. Build the FAQ (optional but recommended)

In **Admin → FAQ**:

- **Generate** proposes questions from the ingested documents. It *merges* —
  pressing it again adds only genuinely new questions and never overwrites
  answers you've written.
- **Add question** writes one by hand. These are protected from being
  overwritten by future generation.
- Review every generated answer before it goes live. Curated answers skip
  retrieval entirely, so they're served exactly as written.
- **Gaps** lists questions people asked that the FAQ couldn't answer,
  ranked by frequency. This is your curation backlog.

Write answers as complete, self-contained statements. "No, it doesn't
require internet — processing is local and results are instant" reads
correctly on its own; "No, instant results with no internet requirement"
reads like a fragment answering some other question.

---

## 6. Put the widget on a site

```html
<script
  src="http://localhost:8080/widget/groundedops-widget.js"
  data-api="http://localhost:8000"
  data-title="Product support"
  data-agent-name="Assistant"
  data-accent="#E4002B"></script>
```

The widget walks visitors through choosing a product before they can ask,
so every question is scoped. It never holds an API key — the backend
decides the model.

For a site on another origin, add it to `WIDGET_ALLOWED_ORIGINS` in `.env`
and `docker compose up -d`.

---

## Everyday commands

```bash
docker compose logs -f backend      # watch logs
docker compose restart backend      # after changing .env
docker compose down                 # stop
docker compose down -v              # stop AND delete all data
```

Re-index everything from scratch:

```bash
docker compose stop backend
docker compose run --rm backend rm -rf /data/chroma_db
docker compose start backend
# then re-upload, or use reload_folder above
```

---

## Troubleshooting

**"System is still loading" (503)** — models are still warming up. Watch
`docker compose logs -f backend`; first start is the slow one.

**Answers say "I could not find that in the knowledge base"** — usually no
documents ingested for the selected product. Check Admin → Documents that
the file is assigned to the product you're asking about.

**Uploaded document shows as needing assignment** — category and product
weren't both selected at upload. Use the Reassign control, or re-upload
with both chosen.

**No FAQ suggestions appear** — either the FAQ is empty for that product
(Admin → FAQ), or nothing scored above `FAQ_CANDIDATE_FLOOR`. Check the
backend log for `FAQ disambiguate` / `FAQ miss` lines with actual scores,
then tune the floor in `.env`.

**Widget shows nothing on your page** — check the browser console. A CORS
error means your page's origin isn't in `WIDGET_ALLOWED_ORIGINS`.

**Slow answers** — you're probably in `local` mode. Switch
`GENERATION_MODE=api` with a provider key.

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
