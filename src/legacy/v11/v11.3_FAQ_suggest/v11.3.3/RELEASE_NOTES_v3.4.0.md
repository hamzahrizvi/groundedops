# GroundedOps v3.4.0

Embeddable website widget, a rebuilt FAQ that asks instead of guessing,
page-level source citations with downloads, and FAQ management.

**Requires a re-ingest** — see *Upgrading*.

---

## Added

### Embeddable website widget

`widget/groundedops-widget.js` — dependency-free, single file, one script
tag. Guides visitors through choosing a product before they can ask, so
every question is scoped. Persists the conversation across refreshes and
offers to continue or start over. Renders each answer's sources with page
numbers and a download link. Never holds an API key.

```html
<script src="/widget/groundedops-widget.js"
        data-api="https://your-backend.example.com"
        data-agent-name="Assistant"
        data-accent="#E4002B"></script>
```

### Page citations and source downloads

Answers now cite the page a fact came from ("pages 12, 14") and link to
the original document. Required two structural changes: `parsing.py` was
joining every page into one string, destroying page boundaries before
chunking ran, and originals were never retained past the temp file used
for parsing.

- `parsing.extract_pages()` preserves page boundaries; ingest chunks per
  page and tags each chunk with its number.
- Originals are kept in `SOURCE_FILE_DIR` and served by `GET /source_file/{name}`.
- `.docx` and `.txt` report page 1 rather than inventing pagination.

### FAQ management

- `POST /faq` — add a question by hand; duplicates return 409.
- `PATCH /faq/{id}` — edits the **question text** as well as the answer.
- `DELETE /faq?product=X&confirm=true` — bulk delete, refuses without the
  confirm flag.
- `GET /faq/gaps` — questions the FAQ couldn't answer, ranked by how often
  they were asked. This is the curation backlog, built from real demand.

### CORS

`WIDGET_ALLOWED_ORIGINS` (default `*`). Without this no cross-origin embed
can call the API at all.

---

## Changed

### FAQ matching: ask, don't guess

The FAQ short-circuit used to *decide* whether a question was equivalent
to a curated one and serve the answer if so. It shipped two failures:

- **v3.0** — lexical Jaccard at 0.6 only fired on near-verbatim repeats,
  so the FAQ was effectively dead.
- **v3.1** — bi-encoder cosine plus a cross-encoder "verifier" served
  *"No, it provides instant results with no requirement for internet"* in
  answer to *"Does the MyCheckr have WiFi or Ethernet ports?"* —
  semantically adjacent, factually opposite. The verifier was a passage-
  **relevance** ranker, for which a high score on that pair is correct:
  the texts genuinely are related. Relevance is not equivalence.

Judging equivalence is the hard part, and it's the part a person does
instantly. So the system now ranks candidates loosely and **asks**:

> These FAQs match your query — please select the one you meant:

Selecting one serves that entry **by id**, with no re-matching, so a
mismatch is structurally impossible. "None of these" logs a gap and
answers from the documents. Only a near-verbatim match (≥0.95 lexical)
still auto-serves, which keeps the common path instant.

The cross-encoder verifier is gone entirely.

### Generate no longer destroys curated content

`/faq/generate` merges rather than replaces. Questions already present in
the same product scope are skipped (compared case- and punctuation-
insensitively) and existing answers are never touched, so Generate is safe
to press repeatedly. Pass `replace: true` for a deliberate rebuild.

### Answers no longer describe their own evidence

The prompt forbade only the *opening* phrase "Based on the context", so
answers still leaked *"…as indicated by the 'WiFi Config' section"* and
*"The context does not specify the number of ports."* The reader can't see
the context, so this is meaningless to them. The prompt now bars source
references anywhere and requires a plain Yes/No opener for
existence questions, with `_strip_meta()` as a deterministic backstop.

### FAQ scope keys are matched tolerantly

Product keys arrive from the catalog UI, upload headers and the FAQ
payload. Exact string matching meant `MyCheckr` and `mycheckr` silently
matched nothing — an empty FAQ with no error anywhere. Matching is now
case- and format-insensitive.

---

## Removed

- **doc2query.** Ingest-time synthetic question generation cost one LLM
  call per chunk and inflated the vector count ~4x, while hybrid
  BM25+dense retrieval, breadcrumb enrichment and reranking already
  covered recall. `INGEST_PROVIDER` and `DOC2QUERY` are gone.
- **Automatic DeepSeek fallback in local mode.** A failed local generation
  now fails rather than silently sending the query and its retrieved
  context to a third-party API. DeepSeek remains available as a deliberate
  choice (Online provider, manual Rethink).

---

## Fixed

- **Uploads landed unassigned despite a category being chosen.** FastAPI
  maps `Header()` parameters to hyphenated names, so a frontend sending
  `category_key` produced `None`, the attach was skipped silently, and the
  document appeared in the "needs assignment" list. `/upload` now accepts
  either spelling and logs the headers it received.
- **Retired DeepSeek model.** `deepseek-chat` was hardcoded in the
  grounding-escalation path; DeepSeek retired that alias on 24 July 2026
  and no longer routes calls to it, leaving the path silently dead. Now
  driven by `ONLINE_DEEPSEEK_MODEL`, defaulting to `deepseek-v4-flash`.
- **Query condensation ignored the configured provider** — it was
  hardcoded to DeepSeek from before Online mode became multi-provider, so
  OpenAI and Anthropic deployments still sent condensation prompts to
  DeepSeek.
- **Conversation history was lost on every production rebuild.**
  `CONVO_DB_PATH` defaulted to `/app` inside the image; it now sits on the
  persistent volume.
- **FAQ answers claimed perfect grounding.** They reported
  `grounding_score: 1.0` despite never being grounded against anything.
  Now `None`, with the matched question returned so a mismatch is visible.
- **`.gitignore`** — a missing newline had merged two entries into
  `.idea/.deepseek_key.enc`, so `.idea/` was never ignored.

---

## Upgrading

Page citations and downloads are produced **at ingest**, so existing
chunks have neither.

```bash
cp .env.example .env        # then fill in, note the new SOURCE_FILE_DIR
docker compose up -d --build
docker compose stop backend
docker compose run --rm backend rm -rf /data/chroma_db
docker compose start backend
curl -X POST http://localhost:8000/ingest/reload_folder \
  -H "x-admin-password: $ADMIN_PASSWORD"
```

Remove `DOC2QUERY` and `INGEST_PROVIDER` from any config — both are
ignored. Drop the `ingest_provider` header from `/upload` calls.

---

## Known gaps

- No authentication beyond a shared admin password; single-tenant only.
- `/source_file` is unauthenticated so the widget can offer downloads —
  anything ingested is retrievable by filename (excluded sources aside).
- No rate limiting on the public query path.
- No data-retention or PII policy around `logs.json`.
- `main.py` still escalates to DeepSeek when a local answer fails
  grounding — the one remaining automatic escalation.
