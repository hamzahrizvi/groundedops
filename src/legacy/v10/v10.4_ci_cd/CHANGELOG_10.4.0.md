# GroundedOps — internal 10.4.0 (CI/CD, ingest verification, docs folder, FAQ)

GO_v2.0 line. Base folder groundedops/ — copy over repo root. Restart
backend + rebuild frontend. New files noted below.

## 4. Is ingest going through DeepSeek? — ANSWERED + FIXED
Finding: on Docker it probably was NOT. INGEST_PROVIDER defaults to
"auto", which only uses DeepSeek if a key is present — but the async
worker passed api_keys={}, so unless DEEPSEEK_API_KEY was set in the
BACKEND ENV, auto silently fell back to local mistral (the slow path
behind your original 504). Fixes:
  - _generate_questions now reads DEEPSEEK/OPENAI/ANTHROPIC keys from ENV
    when none are passed (headless ingest now finds the key).
  - It LOGS the provider actually used: "INFO:ingest:doc2query provider =
    deepseek". Watch `make logs` during an ingest to verify.
  - To force it: set INGEST_PROVIDER=deepseek + DEEPSEEK_API_KEY in the
    backend env (the dev override already sets INGEST_PROVIDER=deepseek).

## 1. CI/CD (.github/workflows/ci.yml + docker-compose.override.yml + Makefile)
- CI on push/PR to main: eval gate (`eval.py --selfcheck` — validates
  suite + baseline without the corpus) then Docker build of both images.
  Deploy stage is present but COMMENTED OUT pending a target (see
  DEPLOY_SETUP.md — this is the one decision I need from you).
- Local dev auto-update: docker-compose.override.yml bind-mounts ./src +
  uvicorn --reload + Vite dev server, so code changes reflect with NO
  rebuild. `make dev` / `make logs` / `make prod` / `make reload`.

## 2. Ingest-change detection (re-ingest prompt)
Backend fingerprints ingest-affecting code (INGEST_VERSION + hashes of
ingest.py/chunking.py) and stamps the DB after each ingest. GET
/ingest/version reports if they differ; the UI shows a re-ingest banner
when ingestion logic has changed since the docs were indexed. Bump
INGEST_VERSION in main.py when you change ingest logic.

## 3. Docs folder (./corpus)
Drop .pdf/.txt/.docx in ./corpus (mounted in Docker). POST
/ingest/reload_folder (admin) or `make reload` ingests everything not
already in the DB. Ideal after a wipe+reingest cycle.

## 5. FAQ page per product (NEW faq_store.py + FaqPage.jsx)
The doc2query questions generated at ingest are recorded per source with
their source chunk as the default answer. FAQ rail button -> page with a
product filter. Admins (password) can EDIT or delete answers; edits are
preserved across re-ingest (matched by question text). This also seeds
the future FAQ semantic cache. Endpoints: GET /faq, PATCH /faq/{id},
DELETE /faq/{id}.

## ⚠ Auth seams unchanged (admin password "admin"; user X-User-Id).
Both still placeholders — wire to innovative-technology.com before any
public deploy. The FAQ EDIT / folder-reload / admin endpoints all sit
behind the admin-password gate.

## Verify
1. `make dev`, ingest a doc, `make logs` -> see "doc2query provider =
   deepseek" (confirms #4). If it says "local", set DEEPSEEK_API_KEY in
   the backend env.
2. FAQ rail button -> pick a product -> see generated questions -> enter
   admin password -> edit an answer -> reload page, edit persists.
3. Change ingest.py trivially, restart -> re-ingest banner appears.
4. Put a PDF in ./corpus -> `make reload` -> it ingests.
5. Push to a branch -> GitHub Actions runs eval selfcheck + image builds.
