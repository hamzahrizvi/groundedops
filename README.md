# GroundedOps — Conversational RAG with Grounding Verification

A retrieval-augmented question-answering system with a verification
layer: every answer is checked against its retrieved sources before it's
shown, and the system refuses (or asks for clarification) rather than
guessing. Runs in two modes — **Online** (DeepSeek / OpenAI / Claude, via
your own API key) or **Free/offline** (local models via Ollama) —
switchable at runtime. Chat-style UI with per-answer sources that cite the
page they came from and link to the original document, persistent chat
history, and manual model re-answering.

Also ships an **embeddable website widget** that puts the same grounded
Q&A on any web page with a single `<script>` tag.

> **Just want to run it?** See [QUICKSTART.md](QUICKSTART.md) — installs
> with a double-click, no Docker required.
> New to the app once it's running? See [USER_GUIDE.md](USER_GUIDE.md).

## Why this exists

Small local models (phi, mistral) often hallucinate when retrieved
context is thin or irrelevant. This project adds a verification layer —
every generated answer is checked against retrieved chunks using an NLI
model, low-confidence retrieval is refused *before* generation rather
than after, and ambiguous queries trigger a clarifying question instead
of a guess.

The same principle now extends to the curated FAQ: where judging whether
two questions mean the same thing is a call a person makes better than a
model, the system asks instead of deciding. See
[Curated FAQ](#curated-faq).

## Architecture

```
Upload → Parse (per page) → Chunk (step-boundary aware) → Breadcrumb enrich
       → Embed → ChromaDB   (originals retained for download)
                                                    │
Query → query condensation (Rewrite-Retrieve-Read, session-scoped)
                                                    │
                            curated FAQ check
                     ┌──────────┬──────────┬──────────┐
                 verbatim    plausible    nothing close
                  serve       ASK user      continue
                                                    │
        Hybrid Retrieval (BM25 + dense, full corpus, RRF-merged)
                       → Rerank (cross-encoder)
                                                    │
                          retrieval confidence band
                     ┌──────────┬──────────┬──────────┐
                  "none"   "ambiguous"   "confident"
                  refuse    ask to        proceed
                            clarify
                                                    │
                          ┌──────────┴──────────┐
                          │   Structured path    │  → checklist/list
                          │  (rerank-score-aware │     extraction
                          │   regex extraction)  │
                          └──────────┬───────────┘
                                     │
                          Generative path (routed by query type,
                          or a manually chosen model via "Rethink")
                                     │
                          Grounding check (NLI cross-encoder)
                                     │
                    failed? → suppress ("not in the knowledge base")
                                     │
                    Answer + sources (page number + download link)
```

### Components

| File | Role |
|---|---|
| `main.py` | FastAPI app, CORS, `/api` prefix rewrite, static frontend serving, async startup warmup, query orchestration, clarifying-question and rethink logic |
| `db.py` | Shared persistent ChromaDB client, per-source deletion, chunk-by-id lookup |
| `parsing.py` | Page-preserving text extraction (PDF / DOCX / TXT) |
| `ingest.py` | Per-page parsing → chunking → breadcrumb enrichment → embedding → storage; retains original files for download |
| `chunking.py` | Step-boundary-aware chunking (prevents unrelated sections merging into one chunk) |
| `retrieval_db.py` | Hybrid full-corpus BM25 + dense retrieval (RRF-merged), optional source/product scoping |
| `reranker.py` | Sigmoid-calibrated cross-encoder reranking |
| `router.py` | Semantic (embedding-based) query classification (extract/fast/accurate/reasoning) |
| `structure.py` | Rerank-score-aware checklist/procedure extraction from chunks |
| `llm.py` | Ollama + DeepSeek/OpenAI/Anthropic calls, model warmup, query condensation, rethink options |
| `grounding.py` | NLI-based answer verification |
| `memory.py` | Session-scoped conversational memory (keyed by `session_id`, TTL-reaped) |
| `faq_store.py` | Curated Q/A pairs, candidate ranking, and the FAQ gap log |
| `catalog.py` | Category / product catalog and source-to-product attachment |
| `conversations.py` | Server-side conversation persistence for signed-in users |
| `text_utils.py` | Pure-stdlib helpers shared across modules — fully unit-tested without ML dependencies |
| `logger.py` | JSON interaction logging |
| `frontend/` | React + Vite chat UI (chat, sources, admin, FAQ, settings) |
| `frontend/public/widget/` | Embeddable single-file website widget |

## Curated FAQ

Admin-curated Q/A pairs are checked before retrieval — instant, no LLM
call, and exactly what the admin approved.

The hard part is deciding what "matches" means, and two attempts at
deciding it automatically failed in production. Lexical matching only
fired on near-verbatim repeats, leaving the FAQ effectively dead. A
semantic matcher with a cross-encoder verifier then answered *"Does the
MyCheckr have WiFi or Ethernet ports?"* with the curated answer to *"Does
MyCheckr require an internet connection?"* — semantically adjacent,
factually opposite. The verifier was a passage-**relevance** ranker, for
which a high score on that pair is correct: the texts genuinely are about
the same topic. Relevance is not equivalence.

So the system now ranks candidates and asks:

> These FAQs match your query — please select the one you meant:

- Selecting one serves that entry **by id** — no re-matching, so a
  mismatch is structurally impossible.
- "None of these" logs a gap and answers from the documents instead.
- Only a near-verbatim match (≥0.95 lexical) auto-serves, which keeps
  repeat questions and tapped suggestion chips instant.

FAQ answers report `grounding_score: null` rather than claiming a
verification that never happened, and the response carries the matched
question so the UI can show it.

### FAQ management

| Endpoint | Purpose |
|---|---|
| `POST /faq` | Add a question by hand (409 on duplicate) |
| `PATCH /faq/{id}` | Edit the question text and/or the answer |
| `DELETE /faq/{id}` | Delete one entry |
| `DELETE /faq?product=X&confirm=true` | Bulk delete (refuses without `confirm`) |
| `POST /faq/generate` | Generate from documents — **merges**, never overwrites |
| `GET /faq/gaps` | Questions the FAQ couldn't answer, ranked by frequency |

`/faq/generate` is non-destructive by default: questions already present
in the same product scope are skipped (compared case- and
punctuation-insensitively) and existing answers are never touched, so it
is safe to press repeatedly. Pass `replace: true` for a deliberate
rebuild.

The **gap log** is the useful by-product of asking rather than guessing:
a user-confirmed list of questions your documentation is being asked but
your FAQ doesn't cover, prioritised by real demand.

> **Writing curated answers:** make them self-contained statements. "No,
> instant results with no internet requirement" reads as a fragment
> answering some other question. "MyCheckr does not require an internet
> connection; processing is local and results are instant" reads
> correctly on its own.

## Embeddable website widget

`frontend/public/widget/groundedops-widget.js` is a dependency-free,
single-file widget that adds a floating "ask a question" launcher to any
site. It calls the backend's `POST /query` and renders the answer with
its verified sources.

```html
<script
  src="https://your-host/widget/groundedops-widget.js"
  data-api="https://your-backend.example.com"
  data-title="Product support"
  data-agent-name="Assistant"
  data-accent="#1F6F5C"></script>
```

| Attribute | Purpose |
|---|---|
| `data-api` | **Required.** Backend base URL |
| `data-title` | Panel header text |
| `data-agent-name` | Name shown in the header |
| `data-avatar-url` | Header avatar image |
| `data-scope-product` | Restrict retrieval to one product key |
| `data-scope-category` | Restrict retrieval to one category key |
| `data-accent` | Accent colour |
| `data-greeting` | Opening message |
| `data-sales-email` | Shown when a visitor asks to speak to sales |

**Behaviour**

- **Guided onboarding.** Visitors pick an intent, then a category, then a
  product before free text is enabled — so every query is scoped and the
  backend never answers from the whole corpus by accident.
- **Suggested questions** are pulled from the curated FAQ for the chosen
  product. Tapping one is a verbatim match, so it answers instantly with
  no LLM call.
- **Disambiguation.** When the backend returns FAQ candidates, they're
  rendered as tappable options with a "none of these" fallback.
- **Session persistence.** Messages, scope and `session_id` survive a
  page refresh, with a continue-or-restart prompt.
- **Sources** show the document name, page numbers, and a download link.
- **Never holds an API key** — the backend chooses the model from its own
  config.

Because the widget runs on a different origin, the backend needs CORS —
set `WIDGET_ALLOWED_ORIGINS`.

> A page served over `https://` cannot call an `http://` API — browsers
> block it as mixed content. A public embed needs TLS on the backend.

## Source citations

Answers cite the page a fact came from and link to the original file:

> **MyCheckr User Manual v7** — *pages 12, 14*
> Download source

This required capturing pages at parse time: text extraction previously
joined every page into one string, destroying page boundaries before
chunking ran, so no page number could exist downstream at any price.
Chunking is now per page, and originals are retained in
`SOURCE_FILE_DIR` and served by `GET /source_file/{filename}`.

`.docx` and `.txt` report page 1 rather than inventing pagination that
wouldn't match what the reader sees.

> **Requires a re-ingest.** Both page metadata and retained originals are
> produced at ingest time; documents indexed earlier have neither.

## Query routing

`router.py` classifies each query into a role (extract/fast/accurate/
reasoning) that determines which model handles it. This used to be
keyword-list matching, which had a structural weakness: a query that
didn't happen to contain one of the listed words got misclassified
regardless of actual intent ("list the steps to power on the hub" matched
none of the old checklist keywords despite obviously being one).

It's now semantic: a small set of canonical example queries per role is
embedded once using the same `sentence-transformers` model already
loaded for retrieval (no extra model, no extra LLM call), and incoming
queries are classified by nearest-neighbor cosine similarity
(`text_utils.classify_by_similarity`). This generalizes to paraphrasing
in a way a keyword list can't, and falls back to the general-purpose
"accurate" role — rather than crashing — if the embedding model can't be
loaded for any reason.

## Conversational features

**Query condensation (Rewrite-Retrieve-Read).** Multi-turn conversations
need a way to resolve follow-ups like "give me that from step 1" into
something retrieval can match against. Earlier versions tried to *guess*
whether a query was a follow-up using surface heuristics (word count, a
fixed pronoun keyword list) — this reliably misfired, since most
complete, self-contained questions are also short ("how to connect tablet
to hub" is 6 words). It's been replaced with the documented industry
pattern (Ma et al. 2023, [arXiv:2305.14283](https://arxiv.org/abs/2305.14283);
LangChain's "history-aware retriever"): every turn beyond the first, a
fast model is given the conversation history and the new message, and
explicitly instructed to return it **unchanged** if it's already
self-contained, or rewritten into a standalone query if it depends on
prior context. See `llm.condense_query`.

**Session-scoped memory.** Conversation history is keyed by a
caller-supplied `session_id` (`memory.py`), so one conversation's history
can't leak into an unrelated conversation's follow-up resolution. Every
real caller (the frontend, the widget, the test script) generates and
sends its own UUID; omitting `session_id` falls back to a shared
anonymous bucket, fine for one-off manual testing but with no session
isolation.

**Clarifying questions.** Retrieval confidence is classified into three
bands (`text_utils.retrieval_confidence_band`): `none` (refuse outright),
`ambiguous` (borderline score AND results scattered across 3+ distinct
sources — ask which section the user means), or `confident` (proceed).

**Rethink with a different model.** Every assistant answer in the UI has
a "Rethink" control offering phi, mistral, or an API provider. This
bypasses the router's automatic model selection, calling the chosen model
directly with the same retrieved context, so answers are directly
comparable.

**Answers state facts, not evidence.** The generation prompt forbids
referring to the source material at all — no "as indicated by the WiFi
Config section", no "the context does not specify". The reader can't see
the retrieval context, so describing it is meaningless to them and
undercuts the answer; sources are attached separately. A deterministic
cleanup pass strips anything that slips through.

**Clickable sources.** Each answer's `sources` field includes the
underlying chunk ids, a snippet, page numbers and a download URL per
source document. The UI renders these as buttons; clicking one fetches
the full retrieved chunk via `/source_chunks`, with an inline "ask more
about this document" field that scopes the next query to that one source.

## Setup

### Native install (recommended)

```
install.cmd      one-time setup
start.cmd        run on this PC        http://127.0.0.1:8000
start-lan.cmd    share on your network http://<your-ip>:8000
```

macOS / Linux:

```bash
./install.sh
./start.sh          # or ./start.sh --lan
```

The installer needs only **Python 3.11+**, and fetches it via `winget` on
Windows if missing. Everything lands in a local `.venv`; nothing is
installed system-wide. It prompts for cloud-vs-offline mode, provider and
API key, writes `.env` for you, then pre-downloads the models so the
first question doesn't stall.

**Node is not required at runtime** — the release ships a pre-built
frontend, which FastAPI serves directly. One process, one port, one URL.
(Working from a clone rather than the release? Run `npm run build` in
`frontend/` once.)

Colleagues on the same network need **nothing installed** — just the
address `start-lan.cmd` prints.

### Run with Docker

```bash
docker compose up -d --build      # first build downloads models
# app on http://localhost:8080, API on http://localhost:8000
```

Brings up backend, frontend, and Ollama (for Free mode) together.

> Compose publishes the backend on port **8000**, the same port the
> native launcher uses. Run `docker compose down` before `start.cmd`.

### Manual / development

```bash
pip install -r requirements.txt
cd frontend && npm install && cd ..
uvicorn main:app --port 8000        # terminal 1
cd frontend && npm run dev          # terminal 2 (Vite on :5173)
```

The React app calls `/api/*`, which Vite proxies in development. When the
built app is served by FastAPI there is no proxy, so a middleware strips
the `/api` prefix before routing — both spellings work, and the frontend
needs no change between modes.

### First run — choosing a mode

GroundedOps starts in **Online mode**: answers come from an API provider
(DeepSeek, OpenAI, or Claude). The installer writes your key to `.env`;
the in-app Settings dialog can also supply one, stored only in your
browser.

**Free mode** runs entirely on your machine via Ollama (private, no API
cost; slower and holds several GB of RAM). Ollama must be installed:
https://ollama.com/download

> For the **widget**, the key must be in the *server* environment — the
> widget deliberately never holds one.

### Configuration (env vars)

See [.env.example](.env.example) for the annotated full list.

| Variable | Default | Purpose |
|---|---|---|
| `ADMIN_PASSWORD` | `admin` | Gates every admin endpoint. **Change it.** |
| `GENERATION_MODE` | `api` | `api` (Online) or `local` (Free) at boot |
| `ONLINE_PROVIDER` | `deepseek` | `deepseek` / `openai` / `anthropic` |
| `ONLINE_DEEPSEEK_MODEL` | `deepseek-v4-flash` | DeepSeek retired the `deepseek-chat` alias on 24 July 2026 |
| `ONLINE_OPENAI_MODEL` | `gpt-4o-mini` | Override the OpenAI answering model |
| `ONLINE_ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Override the Anthropic answering model |
| `WIDGET_ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins for the widget |
| `SOURCE_FILE_DIR` | `/data/source_files` | Where original documents are retained |
| `CHROMA_DIR` | `./chroma_db` | Vector store location |
| `CONVO_DB_PATH` | `conversations.db` | Keep on a persistent volume under Docker |
| `FAQ_STORE_PATH` | `faq_store.json` | Curated FAQ |
| `FAQ_GAP_PATH` | `faq_gaps.json` | Unanswered-question log |
| `FAQ_ENABLED` | `on` | `off` sends every question through retrieval |
| `FAQ_AUTO_SERVE` | `0.95` | Serve without asking (near-verbatim only) |
| `FAQ_CANDIDATE_FLOOR` | `0.70` | Cosine floor to offer a candidate |
| `FAQ_CANDIDATE_LEX_FLOOR` | `0.70` | Lexical floor to offer a candidate |
| `FAQ_CANDIDATE_MARGIN` | `0.12` | Drop candidates far below the best match |
| `EXCLUDED_SOURCES` | `icu_network_api` | Internal docs hidden from answering and download |
| `CONTEXT_FLOOR_RATIO` | `0.5` | Relative score floor for context chunks |
| `CORPUS_DIR` | `corpus` | Folder ingested by `/ingest/reload_folder` |
| `FRONTEND_DIST` | `frontend/dist` | Built UI to serve; API-only if absent |
| `EVAL_GRADER_PROVIDER` | `local` | Set to a provider for faster eval grading |
| `HF_HUB_OFFLINE` | unset | Set to `1` after first run to skip model revalidation |

Tune the FAQ floors from the logs — `FAQ serve` / `FAQ disambiguate` /
`FAQ miss` lines carry the actual scores.

## Testing

The pure-logic modules (`chunking.py`, `structure.py`, `text_utils.py`,
`memory.py`) have no ML dependencies and are fully unit-tested.
`router.py`'s classification *math* (`text_utils.classify_by_similarity`)
is also pure and tested unconditionally, but `router.py` itself depends on
sentence-transformers to produce real query vectors — those integration
tests are skipped with a clear `SKIP` (not silently passed) when that
dependency isn't installed.

```bash
python3 run_tests.py        # no pytest required
pytest tests/               # or, if you have pytest
python test_queries.py      # end-to-end smoke test against a running server
```

`tests/test_regression_bugs.py` locks in fixes for bugs found via
production transcript analysis.

## Evaluation

`eval.py` is a regression harness that gates changes: outcome checks
(answered / clarify / rejected), required and forbidden keywords,
LLM-graded correctness against reference facts, and a baseline diff that
fails the run on any regression.

The reference corpus (four vendor hardware manuals plus an internal API
specification, ~200 pages) is not included. With your own corpus: ingest
documents, write cases in `eval_cases.json`, lock a baseline
(`python eval.py --update-baseline`), then gate every change with
`python eval.py`.

Highlights of what the harness caught: silently wrong ground truth that
was masking a retrieval-disambiguation bug (fixed with breadcrumb chunk
enrichment); a fail-open safety hole where an off-domain query produced a
confident answer scoring 0.055 on grounding and was served anyway (the
grounding verdict is now enforced); and answer suppression of a *correct*
table lookup, fixed with a narrow lexical-containment rescue.

## Known limitations

- **No authentication.** Admin endpoints are gated only by a shared
  password header. Not suitable for untrusted public traffic as-is.
- **`/source_file` is unauthenticated** so the public widget can offer
  downloads, which means any ingested document is retrievable by
  filename. `EXCLUDED_SOURCES` keeps internal documents out; gate it per
  product before exposing this publicly.
- **No rate limiting** on `/query`. A public endpoint backed by a metered
  API key needs it.
- **No data-retention or PII policy** around `logs.json`, which is also
  append-only and unbounded — add rotation.
- **Single tenant.** One collection, one FAQ, one configuration per
  instance, and `runtime_config` is process-wide. Two products can be
  scoped within an instance; two customers cannot share one.
- **The main React chat UI does not yet render FAQ candidates.** The
  backend returns them and the widget displays them, so disambiguation
  currently appears as a prompt with no options in the web app.
  `FaqChoices.jsx` is written and needs wiring into the chat component.
- **Local model latency is high on CPU.** Acceptable for offline/low-volume
  use, not for interactive chat at scale. The widget realistically wants
  Online mode.
- **Local mode has no model fallback.** As of v3.0.0 a failed local
  generation fails the request rather than silently escalating to a
  third-party API. DeepSeek remains available as a deliberate choice
  (Online provider, or manual "Rethink").
- **Grounding-failure escalation still lives in `main.py`.** When a local
  answer is flagged and a key is present, it retries on an API provider.
  This is the one remaining automatic escalation path and is slated for
  removal for consistency with the `llm.py` change.
- **Ingest is single-worker**, and re-ingesting a large corpus is slow.
- **Page-crossing passages are split** at the boundary, since chunking is
  per page. Retrieval usually returns both halves.
- **Table-heavy PDF sections** can still produce an occasional truncated
  checklist line. The complete fix is row-aware PDF table extraction.
- **`condense_query`'s rewrite quality** can only be verified against a
  live Ollama instance; there's no mocked-model test for it, by design.

## License

MIT
