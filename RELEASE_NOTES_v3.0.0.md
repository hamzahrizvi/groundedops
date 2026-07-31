# GroundedOps v3.0.0

Adds an embeddable website widget, and removes two pieces of machinery
that were no longer earning their keep: ingest-time doc2query generation
and the automatic DeepSeek fallback in local mode.

**This is a major version because it requires a re-ingest.** See
*Upgrading* below.

## Added

### Embeddable website widget

`widget/groundedops-widget.js` — a dependency-free, single-file widget
that adds grounded Q&A to any web page with one `<script>` tag. No build
step.

```html
<script
  src="https://your-cdn/groundedops-widget.js"
  data-api="https://your-backend.example.com"
  data-title="Product support"
  data-accent="#1F6F5C"></script>
```

- Calls the existing `POST /query`; no new backend endpoint.
- Generates a per-browser `session_id`, so multi-turn query condensation
  works for anonymous visitors.
- Renders each answer's verified sources (document name + snippet).
- Suppressed answers get a distinct "couldn't verify this" treatment
  rather than being dressed up as confident answers.
- **Never holds an API key** — the backend picks the model from its own
  config. Set the provider key in the server environment.
- Accessibility floor: keyboard focus, Esc to close, `aria-live` log,
  Enter to send / Shift+Enter for newline, `prefers-reduced-motion`
  respected, responsive to mobile.
- Optional scoping to a single product or category key.
- `widget/widget-demo.html` is a local host page for testing.

### CORS support

`main.py` now installs `CORSMiddleware`, without which no cross-origin
embed can call the API. Origins come from `WIDGET_ALLOWED_ORIGINS`
(comma-separated), defaulting to `*`.

> `*` is a development default. Restrict it to your real domains before
> exposing the backend publicly.

## Removed

### doc2query (ingest-time question generation)

doc2query generated ~4 synthetic questions per chunk at ingest and stored
them as extra embedded documents to boost retrieval recall. Since v10.15
it no longer fed the FAQ store (that became admin-curated), leaving
recall as its only job — a job the hybrid BM25 + dense/RRF retriever,
breadcrumb enrichment, and cross-encoder reranker already do.

What it cost: one LLM call per chunk at ingest (minutes-to-hours on a
large PDF), roughly 4x vector-count inflation, and a question-to-parent
mapping and dedupe pass on every query.

- `ingest.py`: `_generate_questions` and the query-entry storage block
  removed.
- `retrieval_db.py`: question-to-parent mapping and dedupe removed. Stale
  `kind="query"` entries from an older database are now filtered out at
  index build, so they can't surface before you re-ingest.
- `main.py`: the `INGEST_PROVIDER` / `ingest_provider` plumbing existed
  only to pick the doc2query model, and is gone. The `ingest_provider`
  upload header is no longer accepted.
- The `DOC2QUERY` env var is gone.

The FAQ store, `/faq/*` endpoints, and the curated-answer short-circuit
are unaffected.

### Automatic DeepSeek fallback in local mode

`llm.py`'s `FALLBACK_CHAIN` no longer appends DeepSeek to the `accurate`
and `reasoning` roles. Local ("Free") mode now stays local: if mistral
fails, the request fails rather than silently sending the query and its
retrieved context to a third-party API — which is the behaviour a
local-first, offline-capable tool should have.

DeepSeek is still fully available as a *deliberate* choice: as the
selected Online provider, and in the manual "Rethink" menu. Neither is a
fallback.

Also removed the dead `_API_ONLY_CHAIN` constant.

## Fixed

- **Condensation ignored the selected Online provider.** In Online mode,
  `condense_query` was hardcoded to DeepSeek — a leftover from before
  Online mode became multi-provider — so an OpenAI or Anthropic
  deployment still sent condensation prompts to DeepSeek. It now follows
  the configured provider.
- **`.gitignore`**: a missing newline had merged two entries into
  `.idea/.deepseek_key.enc`, so `.idea/` was never actually ignored.
- Removed a stray `cls` file committed at the repository root.
- Documentation corrected throughout: the README described `DOC2QUERY`,
  DeepSeek escalation, and a Streamlit UI that no longer reflect the
  code, and linked to files that weren't present.

## Upgrading

**A re-ingest is required.** `INGEST_VERSION` is bumped, so the app will
show a "re-ingest recommended" banner. Until you re-ingest, old
`kind="query"` vectors remain in ChromaDB — they're filtered at query
time and won't affect answers, but they're dead weight.

```bash
# with docs in ./corpus, wipe and rebuild
docker compose exec backend python -c "from db import get_collection; get_collection()"
curl -X POST localhost:8000/ingest/reload_folder -H "x-admin-password: $ADMIN_PASSWORD"
```

If you were setting `DOC2QUERY` or `INGEST_PROVIDER`, remove them; both
are ignored now. If you send the `ingest_provider` header on `/upload`,
drop it.

For the widget, set `WIDGET_ALLOWED_ORIGINS` and put your provider API
key in the server environment.

## Known gaps

Deferred deliberately, to get the widget working end-to-end first:

- No authentication or rate limiting on the public query path.
- `WIDGET_ALLOWED_ORIGINS` defaults to `*`.
- No data-retention or PII policy around `logs.json`.
- `main.py` still escalates to DeepSeek when a local answer fails
  grounding — the one remaining automatic escalation, slated for removal.
