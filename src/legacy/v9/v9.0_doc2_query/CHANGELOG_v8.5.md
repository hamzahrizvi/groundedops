# GroundedOps — v8.5 (production options + Phase 1 doc2query)

Five files: main.py, llm.py, router.py, retrieval_db.py, ingest.py.
Supersedes v8.4.3 (all prior fixes carried forward).

## 1. response_time_ms (main.py)
Every /query response now includes "response_time_ms" — answered,
clarify, extract, and refusal paths alike. Frontend can display it;
useful for comparing local vs api mode.

## 2. GENERATION_MODE=api — online/API-only switch (router.py, llm.py)
$env:GENERATION_MODE = "api"   -> ALL answering + condensation on
DeepSeek (2-8s answers). Ollama can be fully OFF in this mode:
embeddings/reranker/NLI are in-process sentence-transformer models, not
Ollama. Default "local" behaves exactly as v8.4.3. Role classification
still runs in api mode (drives extract/rethink downstream).
NOTE: requires DEEPSEEK_API_KEY set. Escalation is a no-op in api mode
(already on DeepSeek).

## 3. Preamble stripper (main.py)
The v8.4.2 prompt instruction reduced but did NOT stop "Based solely on
the provided context, ..." — deterministic post-processing now removes
these preambles from every answer, any provider, and re-capitalizes.
Unit-tested incl. mid-sentence "based on" NOT being touched.

## 4. Phase 1 — doc2query (ingest.py, retrieval_db.py)  ** RE-INGEST REQUIRED **
At ingest, 3-5 likely user questions are generated per chunk and stored
as embedded "query" entries carrying their parent chunk's id + text.
Retrieval matches question-to-question; hits on question entries are
credited to the PARENT chunk and deduped, so the answering pipeline only
ever sees real document text (verified: no question text can leak).
- Provider: DeepSeek default (minutes, pennies); local mistral fallback
  if no key; DOC2QUERY=off to disable (ingestion then matches v8.4.x).
- Backwards compatible with old collections (entries without "kind"
  are treated as chunks) — but to GET the feature you must wipe
  chroma_db/ and re-ingest.
- BM25 corpus now includes question entries too (helps lexical matching
  of question-phrased user queries).

## Apply
1. Copy all five files into src/. 2. Set DEEPSEEK_API_KEY (needed for
api mode and default doc2query). 3. Wipe chroma_db/ and re-ingest the
corpus (doc2query entries are created at ingest). 4. Restart backend.
5. python eval.py against the locked 16/16 baseline.

## Honest expectations
- Doc2query re-shuffles ALL retrieval rankings (new entries compete
  everywhere). A one-to-two case wobble on first eval is possible and
  is tuning signal, not necessarily regression — triage before reacting.
- Ingestion is slower (one LLM call per chunk). DeepSeek: minutes for
  this corpus. Local fallback: ~1hr. One-time per corpus.
- api mode changes ANSWER STYLE (DeepSeek phrasing differs from
  mistral). Run the eval once in each mode; grader tolerates phrasing
  but keyword cases should hold in both.
