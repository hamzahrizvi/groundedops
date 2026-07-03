# GroundedOps — Conversational RAG with Grounding Verification

A retrieval-augmented question-answering system that runs primarily on
local LLMs (via Ollama), with optional escalation to DeepSeek when a
local answer fails a grounding check, and a chat-style UI with clickable
sources and manual model re-answering.

## Why this exists

Small local models (phi, mistral) often hallucinate when retrieved
context is thin or irrelevant. This project adds a verification layer —
every generated answer is checked against retrieved chunks using an NLI
model, low-confidence retrieval is refused *before* generation rather
than after, and ambiguous queries trigger a clarifying question instead
of a guess.

## Architecture

```
Upload → Parse → Chunk (Step-boundary aware) → Embed → ChromaDB
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
                          │   Structured path     │  → checklist/list
                          │  (rerank-score-aware  │     extraction
                          │   regex extraction)   │
                          └──────────┬──────────┘
                                     │
                          Generative path (routed by query type,
                          or a manually chosen model via "Rethink")
                          → local model (phi/mistral) or DeepSeek
                                     │
                          Grounding check (NLI cross-encoder)
                                     │
                    failed? → escalate to DeepSeek (optional)
                                     │
                            Answer + clickable sources
```

### Components

| File | Role |
|---|---|
| `main.py` | FastAPI app, async startup warmup, query orchestration, clarifying-question and rethink logic |
| `db.py` | Shared persistent ChromaDB client, per-source deletion, chunk-by-id lookup |
| `ingest.py` | File parsing → chunking → embedding → storage |
| `chunking.py` | Step-boundary-aware chunking (prevents unrelated sections merging into one chunk) |
| `retrieval_db.py` | Hybrid full-corpus BM25 + dense retrieval (RRF-merged), optional source scoping |
| `bm25.py` | Standalone BM25 helper (used by tests / ad-hoc scripts) |
| `reranker.py` | Sigmoid-calibrated cross-encoder reranking |
| `router.py` | Semantic (embedding-based) query classification (extract/fast/accurate/reasoning) |
| `structure.py` | Rerank-score-aware checklist/procedure extraction from chunks |
| `llm.py` | Ollama + DeepSeek calls, fallback chains, model warmup, rethink options |
| `grounding.py` | NLI-based answer verification |
| `memory.py` | Session-scoped conversational memory (keyed by `session_id`, TTL-reaped) |
| `text_utils.py` | Pure-stdlib helpers shared across modules (camelCase fix, refusal handling, retrieval gating/confidence, query-condensation prompt building/output parsing) — fully unit-tested without ML dependencies |
| `logger.py` | JSON interaction logging |
| `app.py` | Streamlit chat UI |

## Query routing

`router.py` classifies each query into a role (extract/fast/accurate/
reasoning) that determines which model handles it. This used to be
keyword-list matching, which has the same structural weakness the
follow-up detector had: a query that doesn't happen to contain one of
the listed words gets misclassified regardless of actual intent ("list
the steps to power on the hub" matched none of the old checklist
keywords despite obviously being one).

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
something retrieval can actually match against. Earlier versions tried
to *guess* whether a query was a follow-up using surface heuristics
(word count, a fixed pronoun keyword list) — this reliably misfired,
since most complete, self-contained questions are also short ("how to
connect tablet to hub" is 6 words). It's been replaced with the
documented industry pattern (Ma et al. 2023,
[arXiv:2305.14283](https://arxiv.org/abs/2305.14283); LangChain's
"history-aware retriever"): every turn beyond the first, a fast local
model (phi) is given the conversation history and the new message, and
explicitly instructed to return it **unchanged** if it's already
self-contained, or rewritten into a standalone query if it depends on
prior context. The decision and the fix happen in the same call — see
`llm.condense_query` and the "conversational query condensation" section
of `text_utils.py`.

**Session-scoped memory.** Conversation history is keyed by a
caller-supplied `session_id` (`memory.py`). This matters independently
of the point above: a single shared, never-cleared memory buffer meant
one conversation's history could leak into a completely unrelated
conversation's follow-up resolution — including a fresh `test_queries.py`
run picking up leftover state from a previous interactive session
against the same server. Every real caller (the Streamlit app, the test
script) generates and sends its own UUID; omitting `session_id` falls
back to a shared anonymous bucket, which is fine for one-off manual
testing but loses session isolation.

**Clarifying questions.** Retrieval confidence is classified into three
bands (`text_utils.retrieval_confidence_band`): `none` (refuse outright),
`ambiguous` (borderline score AND results scattered across 3+ distinct
sources — ask which section the user means), or `confident` (proceed
normally).

**Rethink with a different model.** Every assistant answer in the UI has
a "Rethink" control offering phi, mistral, or DeepSeek. This bypasses the
router's automatic model selection and fallback/escalation chain,
calling the chosen model directly with the same retrieved context, so
answers are directly comparable.

**Clickable sources.** Each answer's `sources` field includes the
underlying chunk ids and a short snippet per source document. The UI
renders these as buttons; clicking one fetches and displays the full
retrieved chunk text via `/source_chunks`, with an inline "ask more about
this document" field that scopes the next query to that one source.

## Setup

```bash
pip install -r requirements.txt

ollama pull phi
ollama pull mistral

cp .env.example .env   # add DEEPSEEK_API_KEY if you want escalation/rethink

uvicorn main:app --reload
streamlit run app.py   # separate terminal
```

## Testing

This project's pure-logic modules (`chunking.py`, `structure.py`,
`text_utils.py`, `memory.py`) have no ML dependencies and are fully
unit-tested. `router.py`'s classification *math*
(`text_utils.classify_by_similarity`) is also pure and tested
unconditionally, but `router.py` itself depends on sentence-transformers
(via `embeddings.py`) to produce real query vectors — those integration
tests are skipped with a clear `SKIP` (not silently passed) when that
dependency isn't installed; see `tests/test_router.py`'s docstring.

```bash
python3 run_tests.py        # no pytest required
# or, if you have pytest installed:
pytest tests/
```

`tests/test_regression_bugs.py` specifically locks in fixes for bugs
found via production transcript analysis — see the module docstring and
individual test names for what each one reproduces.

```bash
python test_queries.py      # end-to-end smoke test against a running server
```

## Known limitations

- Local model latency is high on CPU. Acceptable for offline/low-volume
  use, not for interactive chat at scale.
- Query condensation adds one extra fast local-model call on every turn
  beyond the first. This is the cost the documented Rewrite-Retrieve-Read
  pattern accepts in exchange for not misclassifying complete questions
  as follow-ups (see "Conversational features" above) — skipping it
  would be faster but brings back the original bug.
- Query routing degrades to the general-purpose "accurate" role if the
  embedding model can't be loaded (rather than crashing), but in that
  degraded mode every query gets the same generic handling regardless of
  type — confirm `sentence-transformers` is actually installed and
  loading successfully if routing seems to be ignoring query type.
- Table-heavy PDF sections can still produce an occasional truncated
  checklist line (`structure.py`'s `is_bad_line` catches most but not
  all truncation patterns — see its docstring). The complete fix is
  row-aware PDF table extraction, which needs the real source documents
  to build and verify against.
- `condense_query`'s actual rewrite quality (as opposed to the
  prompt-building/output-parsing logic around it) can only be verified
  against a live Ollama instance — there's no mocked-model test for it
  here, by design, rather than asserting it works without checking.
- No authentication — intended for local/single-user use.

## License

MIT
