# GroundedOps — Conversational RAG with Grounding Verification

A retrieval-augmented question-answering system with a verification
layer: every answer is checked against its retrieved sources before it's
shown, and the system refuses (or asks for clarification) rather than
guessing. Runs in two modes — **Online** (DeepSeek / OpenAI / Claude, via
your own API key) or **Free/offline** (local models via Ollama) —
switchable at runtime. Chat-style UI with clickable per-answer sources,
persistent chat history, and manual model re-answering.

Also ships an **embeddable website widget** (`widget/`) that puts the
same grounded Q&A on any web page with a single `<script>` tag.

> Run it with Docker in one command — see [Run with Docker](#run-with-docker-recommended).
> New to the app? See [USER_GUIDE.md](USER_GUIDE.md).

## Why this exists

Small local models (phi, mistral) often hallucinate when retrieved
context is thin or irrelevant. This project adds a verification layer —
every generated answer is checked against retrieved chunks using an NLI
model, low-confidence retrieval is refused *before* generation rather
than after, and ambiguous queries trigger a clarifying question instead
of a guess.

## Architecture

```
Upload → Parse → Chunk (step-boundary aware) → Breadcrumb enrich → Embed → ChromaDB
                                                    │
Query → query condensation (Rewrite-Retrieve-Read, session-scoped) → Hybrid Retrieval
        (BM25 + dense, full corpus, RRF-merged) → Rerank (cross-encoder)
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
                            Answer + clickable sources
```

### Components

| File | Role |
|---|---|
| `main.py` | FastAPI app, CORS, async startup warmup, query orchestration, clarifying-question and rethink logic |
| `db.py` | Shared persistent ChromaDB client, per-source deletion, chunk-by-id lookup |
| `ingest.py` | File parsing → chunking → breadcrumb enrichment → embedding → storage |
| `chunking.py` | Step-boundary-aware chunking (prevents unrelated sections merging into one chunk) |
| `retrieval_db.py` | Hybrid full-corpus BM25 + dense retrieval (RRF-merged), optional source/product scoping |
| `reranker.py` | Sigmoid-calibrated cross-encoder reranking |
| `router.py` | Semantic (embedding-based) query classification (extract/fast/accurate/reasoning) |
| `structure.py` | Rerank-score-aware checklist/procedure extraction from chunks |
| `llm.py` | Ollama + DeepSeek/OpenAI/Anthropic calls, model warmup, rethink options |
| `grounding.py` | NLI-based answer verification |
| `memory.py` | Session-scoped conversational memory (keyed by `session_id`, TTL-reaped) |
| `faq_store.py` | Admin-curated Q/A pairs + lexical match cache for repeat questions |
| `catalog.py` | Category / product catalog and source-to-product attachment |
| `conversations.py` | Server-side conversation persistence for signed-in users |
| `text_utils.py` | Pure-stdlib helpers shared across modules (camelCase fix, refusal handling, retrieval gating/confidence, query-condensation prompt building/output parsing) — fully unit-tested without ML dependencies |
| `logger.py` | JSON interaction logging |
| `frontend/` | React + Vite chat UI (chat, sources, admin, FAQ, settings) |
| `widget/` | Embeddable single-file website widget (see below) |

## Embeddable website widget

`widget/groundedops-widget.js` is a dependency-free, single-file widget
that adds a floating "ask a question" launcher to any site. It calls the
backend's `POST /query` and renders the answer with its verified sources.

```html
<script
  src="https://your-cdn/groundedops-widget.js"
  data-api="https://your-backend.example.com"
  data-title="Product support"
  data-scope-product=""
  data-accent="#1F6F5C"></script>
```

| Attribute | Purpose |
|---|---|
| `data-api` | **Required.** Backend base URL |
| `data-title` | Panel header text |
| `data-scope-product` | Restrict retrieval to one product key |
| `data-scope-category` | Restrict retrieval to one category key |
| `data-accent` | Accent colour |
| `data-greeting` | Opening message |

The widget generates a per-browser `session_id` (so multi-turn query
condensation works), never holds an API key — the backend chooses the
model from its own config — and renders suppressed answers in a distinct
"couldn't verify this" style rather than inventing sources.
`widget/widget-demo.html` is a local host page for testing.

Because the widget runs on a different origin, the backend needs CORS —
set `WIDGET_ALLOWED_ORIGINS` (see below).

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
a "Rethink" control offering phi, mistral, or DeepSeek. This bypasses the
router's automatic model selection, calling the chosen model directly
with the same retrieved context, so answers are directly comparable.

**Curated FAQ short-circuit.** Admin-curated Q/A pairs (`faq_store.py`)
are matched lexically against incoming questions; a close match with a
human-reviewed answer is served directly, with no LLM call.

**Clickable sources.** Each answer's `sources` field includes the
underlying chunk ids and a short snippet per source document. The UI
renders these as buttons; clicking one fetches the full retrieved chunk
via `/source_chunks`, with an inline "ask more about this document" field
that scopes the next query to that one source.

## Setup

### Run with Docker (recommended)

```bash
docker compose up -d --build      # first build downloads models (a few minutes)
# open http://localhost:8080
```

Brings up backend, frontend, and Ollama (for Free mode) together.

### Or run natively

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File install.ps1
.\start.ps1
```

```bash
# macOS / Linux
./install.sh
./start.sh
```

The installer checks Python 3.11+ and Node 18+, installs backend and
frontend dependencies, and (optionally) downloads the local models via
Ollama. The app opens at http://localhost:5173.

### First run — choosing a mode

GroundedOps starts in **Online mode**: answers come from an API provider
(DeepSeek, OpenAI, or Claude) using a key you supply in the startup popup
or Settings. Keys entered in the app are stored only in your browser and
sent per request.

**Free mode** runs entirely on your machine via Ollama (private, no API
cost; slower and holds several GB of RAM). The Free-mode dialog checks
whether the models are installed, downloads any that are missing with a
progress bar, and loads them. Ollama must be installed:
https://ollama.com/download

> For the **widget**, set the provider key in the *server* environment
> instead — the widget deliberately never holds a key.

### Manual install

```bash
pip install -r requirements.txt
cd frontend && npm install && cd ..
uvicorn main:app --port 8000        # terminal 1
cd frontend && npm run dev          # terminal 2
```

### Configuration (env vars)

| Variable | Default | Purpose |
|---|---|---|
| `GENERATION_MODE` | `api` | `api` (Online) or `local` (Free) at boot |
| `ONLINE_PROVIDER` | `deepseek` | `deepseek` / `openai` / `anthropic` |
| `ONLINE_*_MODEL` | per provider | Override the Online answering model |
| `WIDGET_ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins for the widget |
| `EXCLUDED_SOURCES` | `icu_network_api` | Internal docs hidden from answering |
| `CONTEXT_FLOOR_RATIO` | `0.5` | Relative score floor for context chunks |
| `CORPUS_DIR` | `corpus` | Folder ingested by `/ingest/reload_folder` |
| `EVAL_GRADER_PROVIDER` | `local` | Set `deepseek` for faster eval grading |

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
- **The widget is functional but not yet hardened.** No rate limiting,
  `WIDGET_ALLOWED_ORIGINS` defaults to `*`, and there's no data-retention
  or PII policy around `logs.json`. Lock these down before public use.
- **Local model latency is high on CPU.** Acceptable for offline/low-volume
  use, not for interactive chat at scale. The widget realistically wants
  Online mode.
- **Local mode has no model fallback.** As of v3.0.0 a failed local
  generation fails the request rather than silently escalating to a
  third-party API. DeepSeek remains available as a deliberate choice
  (Online provider, or manual "Rethink").
- **Grounding-failure escalation still lives in `main.py`.** When a local
  answer is flagged and a key is present, it retries on DeepSeek. This is
  the one remaining automatic escalation path and is slated for removal
  for consistency with the `llm.py` change.
- **Ingest is single-worker**, and re-ingesting a large corpus is slow.
- **Table-heavy PDF sections** can still produce an occasional truncated
  checklist line. The complete fix is row-aware PDF table extraction.
- **`condense_query`'s rewrite quality** can only be verified against a
  live Ollama instance; there's no mocked-model test for it, by design.

## License

MIT
