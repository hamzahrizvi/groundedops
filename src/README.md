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
Query → query rewriting (follow-up enrichment) → Hybrid Retrieval
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
| `router.py` | Keyword-based query classification (extract/fast/accurate/reasoning) |
| `structure.py` | Rerank-score-aware checklist/procedure extraction from chunks |
| `llm.py` | Ollama + DeepSeek calls, fallback chains, model warmup, rethink options |
| `grounding.py` | NLI-based answer verification |
| `memory.py` | Short conversational memory, follow-up detection |
| `text_utils.py` | Pure-stdlib helpers shared across modules (camelCase fix, refusal handling, retrieval gating/confidence, query rewriting) — fully unit-tested without ML dependencies |
| `logger.py` | JSON interaction logging |
| `app.py` | Streamlit chat UI |

## Conversational features

**Follow-up query rewriting.** A short, pronoun-heavy follow-up like
"give me that from step 1" has almost no retrieval signal on its own.
When `text_utils.looks_like_followup()` detects this pattern, the
previous turn's query is folded into the retrieval query (not the LLM
prompt) so search has something concrete to match against.

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
`text_utils.py`, `router.py`) have no ML dependencies and are fully
unit-tested:

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
- The query router is keyword-based and can misclassify edge-case
  phrasings.
- Table-heavy PDF sections can still produce an occasional truncated
  checklist line (`structure.py`'s `is_bad_line` catches most but not
  all truncation patterns — see its docstring). The complete fix is
  row-aware PDF table extraction, which needs the real source documents
  to build and verify against.
- No authentication — intended for local/single-user use.

## License

MIT