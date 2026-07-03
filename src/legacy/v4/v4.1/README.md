# GroundedOps — Local-First RAG with Grounding Verification

A retrieval-augmented question-answering system that runs primarily on
local LLMs (via Ollama), with optional escalation to a cloud model when
the local answer fails a grounding check.

## Why this exists

Most RAG demos assume the LLM will follow instructions and only answer
from retrieved context. Small local models (phi, mistral) often don't —
they hallucinate plausible-sounding answers when context is thin. This
project adds a verification layer: every generated answer is checked
against the retrieved chunks using an NLI (natural language inference)
model, and answers that fail this check are automatically escalated to
a stronger model before being returned.

## Architecture

```
Upload → Parse → Chunk → Embed → ChromaDB
                                     │
Query → Hybrid Retrieval (dense + BM25) → Rerank (cross-encoder)
                                     │
                          ┌──────────┴──────────┐
                          │   Structured path     │  → checklist/list
                          │   (regex extraction)  │     extraction
                          └──────────┬──────────┘
                                     │
                          Generative path (routed by query type)
                          → local model (phi/mistral via Ollama)
                                     │
                          Grounding check (NLI cross-encoder)
                                     │
                    failed? → escalate to DeepSeek (optional)
                                     │
                                  Answer
```

### Components

| File              | Role |
|-------------------|------|
| `main.py`         | FastAPI app, async startup warmup, query orchestration |
| `db.py`           | Shared persistent ChromaDB client |
| `ingest.py`       | File parsing → chunking → embedding → storage |
| `retrieval_db.py` | Hybrid dense + BM25 retrieval from ChromaDB |
| `bm25.py`         | Keyword-based retrieval (BM25Okapi) |
| `reranker.py`     | Cross-encoder reranking of retrieved chunks |
| `router.py`       | Keyword-based query classification (extract/fast/accurate/reasoning) |
| `structure.py`    | Heuristic checklist/procedure extraction from chunks |
| `llm.py`          | Ollama + DeepSeek calls, fallback chains, model warmup |
| `grounding.py`    | NLI-based answer verification |
| `memory.py`       | Short conversational memory across queries |
| `logger.py`       | JSON interaction logging for offline review |
| `app.py`          | Streamlit frontend |

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Install and start Ollama, then pull models
ollama pull phi
ollama pull mistral

# 3. (Optional) configure DeepSeek fallback
cp .env.example .env
# edit .env and add DEEPSEEK_API_KEY

# 4. Start the backend
uvicorn main:app --reload

# 5. Start the frontend (separate terminal)
streamlit run app.py
```

The backend loads all models (embeddings, reranker, NLI, local LLMs) on
startup in a background thread. Check progress at `GET /status`.

## Testing

```bash
python test_queries.py
```

Runs a fixed set of queries covering each routing path (extraction, fast
lookup, multi-hop reasoning, out-of-domain rejection) and prints grounding
scores, model used, fallback status, and timing for each.

## Known limitations

- Local model latency is high on CPU (60–110s for `reasoning`-routed
  queries with mistral). Acceptable for an offline/batch use case, not
  for interactive chat at scale.
- The query router is keyword-based and will misclassify some queries
  (e.g. procedural "how to" questions sometimes route to `reasoning`
  instead of `extract`).
- Grounding scores on numbered-list answers can still be noisy; the NLI
  model was trained on prose, not structured lists.
- No authentication — intended for local/single-user use.

## License

MIT
