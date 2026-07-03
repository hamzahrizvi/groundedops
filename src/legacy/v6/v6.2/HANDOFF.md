# GroundedOps — Project Handoff Document
# For continuing development in a new chat session

---

## What this project is

GroundedOps is a local, offline-first RAG (Retrieval-Augmented Generation)
system built for a hardware installation domain. The corpus is a PDF manual
for a product called MyConnect / MyCheckr — a hub-and-tablet age-verification
system used in retail environments. The system answers installer questions
about setup, configuration, checklists, and troubleshooting.

The goal is grounded, auditable answers (no hallucination) that an installer
can follow verbatim on site. This means correctness and faithfulness to the
source document matter more than fluency or creativity.

---

## Stack

- **Backend**: FastAPI (main.py), runs via `uvicorn main:app --reload`
- **Frontend**: Streamlit (app.py), runs via `streamlit run app.py`
- **Local LLMs**: phi (fast, factual) and mistral (accurate, reasoning) via
  Ollama at http://localhost:11434
- **Fallback LLM**: DeepSeek API (deepseek-chat) — only used when local
  grounding check fails, or when user explicitly requests it via "Rethink"
- **Vector DB**: ChromaDB (PersistentClient, stored in ./chroma_db)
- **Embeddings**: sentence-transformers all-MiniLM-L6-v2 (via embeddings.py)
- **Reranker**: cross-encoder/ms-marco-MiniLM-L-6-v2 with sigmoid activation
  so scores are calibrated to [0,1] (0.5 = model's own relevance boundary)
- **Grounding**: cross-encoder/nli-deberta-v3-small (NLI entailment check on
  generated answers against retrieved chunks)
- **BM25**: rank_bm25 (BM25Okapi), run over full corpus independently of dense

---

## File map — every file and its exact role

```
main.py          FastAPI app. Startup warmup, query orchestration,
                 session-scoped memory wiring, confidence-band routing
                 (none/ambiguous/confident), clarifying questions,
                 rethink-with-model path, /source_chunks, /clear_session,
                 /delete_source, /rethink_options endpoints.

app.py           Streamlit chat UI. Generates one UUID session_id per
                 browser session, sends with every /query. "New
                 Conversation" button, clickable/expandable source
                 buttons, rethink model selector, resolved_query caption.

db.py            Shared ChromaDB client (PersistentClient). Both ingest.py
                 and retrieval_db.py import from here — fixes an earlier
                 bug where two separate in-memory clients meant uploaded
                 docs were invisible to retrieval. Adds delete_source()
                 and get_chunks_by_ids() for the clickable-source feature.

ingest.py        File parsing → chunking → embedding → ChromaDB storage.
                 Deduplication by source filename. Calls chunking.py,
                 embeddings.py, db.py.

chunking.py      Step-boundary-aware text chunking. Splits list-like blocks
                 at "Step N" headers and caps runs at MAX_LIST_UNIT_LINES=6
                 before packing into size-limited chunks. Fixes a bug where
                 the entire multi-step section (Steps 1-8 + Installer Notes)
                 merged into one indivisible chunk because the source PDF
                 had no blank lines between sections.

parsing.py       PDF/DOCX/TXT text extraction. Called by ingest.py.

embeddings.py    Loads all-MiniLM-L6-v2 once (singleton), exposes
                 embed_query() and embed_texts(). Returns normalized
                 numpy arrays so cosine similarity = dot product.

retrieval_db.py  Hybrid retrieval: full-corpus BM25 + dense (ChromaDB),
                 merged via RRF (Reciprocal Rank Fusion, k=60). BM25 runs
                 independently over the entire corpus (not just the dense
                 top-k — that was an earlier bug). Supports source_filter
                 for the "ask more about this document" scoped query path.
                 Returns chunk dicts with 'id' field for clickable sources.

bm25.py          Standalone BM25 helper used by ad-hoc scripts / tests.

reranker.py      cross-encoder/ms-marco-MiniLM-L-6-v2 with
                 torch.nn.Sigmoid() activation. Scores are in [0,1] where
                 0.5 is the model's own relevance boundary — makes the
                 score meaningful as a threshold (RETRIEVAL_GATE_THRESHOLD
                 in main.py).

router.py        SEMANTIC query router (not keyword-list). Embeds canonical
                 example queries per role once at startup (cached), then
                 classifies incoming queries by nearest-neighbor cosine
                 similarity using the same embedding model already loaded
                 for retrieval. Falls back to "accurate" if the embedding
                 model can't be loaded. Roles: extract / fast / accurate /
                 reasoning. Model assignments: extract→mistral, fast→phi,
                 accurate→mistral, reasoning→mistral (deepseek fallback).

structure.py     Structured checklist/procedure extractor. Scores each
                 retrieved chunk using rerank_score as the DOMINANT signal
                 (rerank_score * 25), with length-NORMALIZED structural
                 heuristics (verb-led lines, list density, consistent line
                 length) as secondary tie-breakers. Returns the best
                 chunk's content as a markdown list or None. Also filters
                 garbled PDF extraction artifacts (narrative prose mixed
                 into checklists, truncated table fragments, continuation
                 lines, etc.) via is_bad_line().

grounding.py     NLI entailment check (cross-encoder/nli-deberta-v3-small).
                 Splits generated answer into units (sentences/list items),
                 scores each against retrieved chunks, returns
                 (is_grounded, score). Fails open (returns grounded=True)
                 on model error.

llm.py           Ollama + DeepSeek calls. Fallback chains per role.
                 condense_query(): Rewrite-Retrieve-Read query condensation
                 — only called when has_reference_markers() returns True
                 (guards against rewriting standalone queries). Uses phi
                 with a 20s timeout and num_predict=64. parse_condense_
                 output() takes FIRST LINE ONLY to prevent phi from leaking
                 the rest of the prompt template into the output.

memory.py        Session-scoped conversation memory. Keyed by session_id
                 (caller-supplied UUID). TTL-based reaping (4h inactivity).
                 Thread-safe. get_history(session_id) returns list of
                 {q, a} dicts for use in condense_query. Refusals are
                 never stored. clear_memory(session_id=None) clears one
                 session or all (used by /reset).

text_utils.py    Pure stdlib helpers — no ML deps, fully unit-testable.
                 fix_camel_case(): term-protected camelCase fix (preserves
                   MyConnect, MyCheckr, WiFi, GPIO).
                 clean_table_artifacts(): checkbox glue, dangling parens.
                 split_units(): splits answers for NLI grounding checks.
                 truncate_after_refusal(): cuts phi/mistral rambling after
                   a refusal phrase (checks multiple variant phrasings).
                 rrf_merge(): Reciprocal Rank Fusion math.
                 passes_retrieval_gate(): checks rerank_score >= threshold.
                 retrieval_confidence_band(): classifies into none/
                   ambiguous/confident.
                 build_condense_prompt() / parse_condense_output():
                   pure pieces of the query condensation pipeline.
                 has_reference_markers(): targeted anaphora check — returns
                   True only for queries containing actual reference signals
                   (pronouns, "step N", "from that", "the above", etc.).
                   NOT a word-count proxy.
                 classify_by_similarity(): pure numpy nearest-neighbor
                   classifier used by router.py.

logger.py        Append-only JSONL interaction logger (logs.jsonl) with
                 threading lock, size-based rotation, sentence-aware
                 truncation.

run_tests.py     Pure-stdlib test runner. Discovers tests/test_*.py,
                 calls setup_function/teardown_function per pytest
                 convention, reports PASS/SKIP/FAIL with counts.
                 SkipTest exception injected into each module's globals.

test_queries.py  End-to-end smoke test against a running server. Generates
                 fresh SESSION_ID per run. Shows retrieval_score for every
                 query. EXPECTED_REJECTIONS set so query 9 ("capital of
                 france") counts as expected_rejected not a failure.
                 Includes a real follow-up query ("give me step 1 from
                 that") to exercise the condensation path.

tests/
  test_text_utils.py      split_units, truncate_after_refusal,
                          passes_retrieval_gate — pure logic.
  test_memory.py          Session isolation regression (the actual root-
                          cause bug), MAX_MEMORY truncation, TTL reaping,
                          clear_memory scoping. Uses setup_function.
  test_regression_bugs.py All bugs found in production transcripts:
                          camelCase fix, chunk scoring fix, narrative filter,
                          table artifacts, chunking split, confidence band,
                          condensation prompt/parse logic,
                          has_reference_markers (both should-fire and
                          should-not-fire cases).
  test_router.py          Tier 1 (pure): classify_by_similarity with
                          synthetic numpy vectors — runs unconditionally.
                          Tier 2 (integration): route_model with real
                          embeddings — SkipTest when sentence-transformers
                          not installed.
  test_chunking.py        chunk_text edge cases.
  test_structure.py       extract_structured_block, is_bad_line,
                          starts_with_verb, etc.
  test_rrf.py             rrf_merge, including the "BM25-only item survives
                          absence from dense results" regression.
```

---

## Complete bug history — every bug found, root-caused, and fixed

### Bug 1 — Shared ChromaDB client (fixed in v2)
**Symptom**: Uploaded documents were never retrieved.
**Root cause**: `ingest.py` and `retrieval_db.py` each created their own
`chromadb.Client()` — two separate in-memory databases, so documents
written by ingest were invisible to retrieval.
**Fix**: Shared `db.py` module with a single `PersistentClient` singleton.
**Verified by**: Direct code inspection; two separate client instantiations
were literally visible on adjacent lines.

### Bug 2 — BM25 running only on dense top-k, not full corpus (fixed in v3)
**Symptom**: Domain-specific terms (MyCheckr, MyConnect) occasionally not
retrieved even when present in the corpus.
**Root cause**: BM25 was re-ranking the dense retrieval's top-10 results
rather than running independently over the full corpus. A chunk that was
absent from dense results couldn't surface via BM25 either.
**Fix**: `retrieval_db.py` now runs BM25 and dense independently over the
full corpus and merges via RRF.
**Verified by**: test_rrf.py::test_bm25_only_item_is_included_even_if_absent_from_dense.

### Bug 3 — camelCase regex mangling brand names (fixed in v4)
**Symptom**: Production transcripts showed "My Connect App" and "My Checkr"
instead of "MyConnect App" and "MyCheckr" throughout all extracted answers.
**Root cause**: `normalize_line()` in structure.py applied
`re.sub(r"([a-z])([A-Z])", r"\1 \2", line)` with no exceptions — correctly
fixing PDF merge artifacts like "activityCategory" but blindly splitting
every brand name too.
**Fix**: `fix_camel_case()` in text_utils.py uses placeholder-swap technique
— replaces PROTECTED_TERMS with null-byte placeholders before the regex,
then restores them after. List: MyConnect, MyCheckr, WiFi, GPIO.
**Verified by**: Direct python3 -c reproduction confirmed the mangling;
test_regression_bugs.py::test_camelcase_* tests lock it in.

### Bug 4 — Extraction scoring not using rerank_score, no length normalization (fixed in v4)
**Symptom**: Three different queries ("give me the checklist before leaving
site", "how to connect tablet to hub", "give me steps to install and verify")
all returned the identical wrong chunk (a GPIO/relay installer notes section).
**Root cause**: structure.py's chunk scorer summed raw structural sub-scores
(verb_starts * 3, list_lines * 2.5, etc.) over all lines with NO length
normalization, and never used `rerank_score` at all. A longer chunk packed
with list-shaped lines would systematically beat a shorter, genuinely
relevant chunk purely by volume.
**Verified by**: Direct Python reproduction — the 9-line irrelevant relay
chunk scored 48 vs the relevant 5-line Steps 4-8 chunk scoring 33, despite
the relay chunk having a lower rerank_score and zero query overlap.
**Fix**: `rerank_score * 25` is now the dominant term. Structural sub-scores
are normalized per line (fractions, not raw counts). Backwards position_weight
term removed. MIN_EXTRACTION_SCORE = 15 threshold added.

### Bug 5 — Chunking merged entire multi-step sections into one indivisible unit (fixed in v4)
**Symptom**: Retrieval could never isolate a single step — a query about
Step 4 would retrieve a chunk containing Steps 1-8 plus Installer Notes.
**Root cause**: `chunking.py` treated any list-like block as ONE atomic unit
regardless of length. Source PDFs with no blank lines between steps (a
common PDF extraction artifact) produced a single giant block that the
size-based packer couldn't split.
**Fix**: List-like blocks are now split at "Step N" header boundaries first,
then capped at MAX_LIST_UNIT_LINES=6 per sub-group. The chunk packer can
then actually split between sections.
**Verified by**: test_regression_bugs.py::test_long_multi_step_section_gets_split.

### Bug 6 — Garbled checklist output: narrative prose and table fragments (fixed in v4)
**Symptom**: Query 6 ("Give installer checklist when leaving site") returned
"This final section is a short, single checklist ensuring nothing has been
forgotten." as if it were a checklist item, plus truncated fragments like
"Fail-safe NC wiring tested (if" and "All devices powered, connected, and".
**Root cause**: `is_bad_line()` only filtered short lines and section headers.
Long narrative prose, mid-sentence truncations, and table-cell merges all
passed through.
**Fix**: Added filters for (a) long non-verb-led lines ≥12 words (narrative
prose), (b) lines >6 words with no terminal punctuation AND not verb-led
(truncation signal), (c) lowercase-starting lines (PDF line-wrap
continuation), (d) lines ending in a trailing stopword (truncation signal).
Also `clean_table_artifacts()` for checkbox gluing and dangling parens.
**Verified by**: Direct testing of each actual garbled line from the
production transcript against is_bad_line().

### Bug 7 — Global, never-cleared, process-wide conversation memory (fixed in v6)
**Symptom**: Three unrelated queries (1, 6, 12) in a "fresh" test run all
returned the same wrong chunk. Query 1 was supposedly the first query but
was already contaminated.
**Root cause**: `memory.py` was a plain module-level list (`MEMORY: list[dict]
= []`) shared across every request regardless of which conversation or which
script run made it. `looks_like_followup()` used a `<= 10 words` heuristic
that flagged nearly every real query as a follow-up, causing
`build_retrieval_query()` to silently prepend whatever unrelated query ran
before it — including state left from the previous interactive Streamlit
session against the same server.
**Fix**: `memory.py` rewritten with session_id-keyed dict, TTL-based reaping
(4h), threading lock. `app.py` generates a persistent UUID per browser
session. `test_queries.py` generates a fresh UUID per script run.
**Verified by**: tests/test_memory.py::test_two_sessions_never_see_each_others_history
and test_fresh_session_id_starts_with_empty_history_even_if_others_are_populated.

### Bug 8 — Word-count follow-up heuristic misclassifying standalone queries (fixed in v6)
**Symptom**: Same as Bug 7 symptoms — self-contained queries being
concatenated with previous unrelated queries before retrieval.
**Root cause**: `looks_like_followup(query, max_standalone_words=10)` flagged
any query with ≤10 words as a follow-up. "How to connect tablet to hub" (6
words), "give me the checklist before leaving site after installation" (9
words), and "give me steps to install and verify system is working" (10 words)
are ALL complete, self-contained questions but all triggered the heuristic.
**Fix**: Replaced entirely by `llm.condense_query()` using the
Rewrite-Retrieve-Read pattern (Ma et al. 2023, arXiv:2305.14283). A fast phi
call is given the session's conversation history and instructed to return the
query UNCHANGED if it's self-contained, or rewritten into a standalone query
if it references prior context. The classification and the fix happen in the
same model call — no separate classifier. `looks_like_followup`,
`build_retrieval_query`, `should_use_memory`, and `get_memory_context` were
all deleted.
**Verified by**: test_regression_bugs.py condensation tests (pure logic).
End-to-end requires live Ollama.

### Bug 9 — phi leaking condensation prompt into resolved_query output (fixed in v6)
**Symptom**: test_queries.py output showed `resolved_query` containing the
entire condensation prompt template ("Rules: 1. The assistant can only
respond...") after the actual rewritten query.
**Root cause**: phi continued generating past the first line and output the
rest of the CONDENSE_PROMPT_TEMPLATE. `parse_condense_output()` stripped
quotes and labels but kept the entire multi-line string.
**Fix**: `parse_condense_output()` now takes only the FIRST non-empty line
(`next(l.strip() for l in raw.split("\n") if l.strip())`). Everything after
line 1 is discarded.
**Verified by**: Direct reproduction of the exact phi output then applying
the fix, confirmed in test_regression_bugs.py::test_parse_condense_output_takes_first_line_only.

### Bug 10 — condense_query rewriting standalone queries when history exists (fixed in v6)
**Symptom**: "post installation verification installer sign off" (query 10)
was being rewritten to "How to connect tablet to hub" — picking up the
topic from query 6 in the session history — even though query 10 has no
dependency on prior context whatsoever.
**Root cause**: `condense_query` called phi whenever history existed, with
no prior check on whether the query actually referenced anything from that
history. phi is not reliable enough at instruction-following to always return
a query unchanged when instructed to do so.
**Fix**: `has_reference_markers()` added to text_utils.py. Checks for actual
linguistic signals of anaphora/ellipsis (pronouns, "step N", "from that",
"the above", "give me that", "I need more context", continuation conjunctions
at start, etc.). `condense_query` returns immediately if no markers found —
no model call at all. ALL standalone queries from the failing transcript
(queries 1, 6, 8, 9, 10, 11, 12) return False. All genuine follow-ups
("give me that from step 1", "tell me more", "I need more context than above")
return True.
**Verified by**: test_regression_bugs.py::test_reference_markers_fires_on_genuine_followups
and test_reference_markers_does_not_fire_on_standalone_queries.

### Bug 11 — Keyword-list query router misclassifying paraphrased queries (fixed in v6)
**Symptom**: Queries that didn't happen to contain one of the hardcoded
keyword strings were misrouted regardless of intent. "What's the reason
device registration fails" contains none of _REASONING_KW; "list the steps
to power on the hub" contains none of _EXTRACT_KW.
**Root cause**: `router.py` used three static keyword lists (_REASONING_KW,
_EXTRACT_KW, _FAST_KW) — the same structural weakness as the word-count
follow-up heuristic.
**Fix**: Replaced with semantic routing. Canonical example queries per role
are embedded ONCE at startup using the same all-MiniLM-L6-v2 model already
loaded for retrieval (no extra model, no extra LLM call). Incoming queries
are classified by nearest-neighbor cosine similarity
(`text_utils.classify_by_similarity`, pure numpy). Falls back to "accurate"
gracefully if the embedding model can't be loaded.
**Verified by**: test_router.py Tier 1 (pure numpy logic, always runs).
Tier 2 (real route_model with sentence-transformers) is skipped with an
honest SKIP marker in this sandbox but runs on the actual deployment.

---

## Architecture decisions and why

**Rerank-score dominant extraction scoring**: rerank_score is the cross-
encoder's calibrated semantic relevance signal. It was already being computed
for every chunk. Not using it in extraction scoring was a clear oversight —
structural shape (list density, verb-led lines) should break ties between
similarly-relevant chunks, not override a real relevance gap.

**Session-scoped memory with TTL reaping**: process-global state is
inherently broken for any system that handles more than one conversation.
TTL reaping (4h) avoids unbounded memory growth without requiring a
background thread.

**Rewrite-Retrieve-Read with has_reference_markers guard**: calling a model
on every turn is wasteful and, as Bug 10 showed, produces wrong rewrites for
standalone queries when phi can't reliably distinguish "self-contained" from
"needs context". The guard is a cheap pre-filter: if there are no reference
markers, the model call is skipped entirely. This is not a return to the old
heuristic — it's specifically checking for linguistic signals of dependency,
not proxying with word count.

**First-line-only parse for condensation output**: phi generates past the
end of what it was asked to produce. This is a known characteristic of small
models. Taking the first line is the minimal, correct fix.

**Sigmoid-calibrated reranker scores**: raw cross-encoder logits are
unbounded and their scale varies by model. Squashing through sigmoid gives
scores in [0,1] where 0.5 is the model's own relevance/irrelevance boundary,
making RETRIEVAL_GATE_THRESHOLD=0.5 a principled choice rather than an
arbitrary number.

**Retrieval confidence band (none/ambiguous/confident)**: binary pass/fail
on retrieval misses the "borderline score + results scattered across many
unrelated sources" case, which is better handled by asking the user to
clarify than by picking one source and hoping.

**Semantic routing with canonical examples**: uses the embedding model that's
already loaded, adds no latency on the critical path (examples embedded once
at startup), and generalizes to paraphrasing in a way keyword lists
structurally cannot.

**BM25 over full corpus independently of dense**: a chunk absent from dense
results can still be retrieved via BM25 — important for domain-specific
terms like "MyCheckr" that the embedding model may not represent well.

---

## Current known issues (not yet fixed)

### Issue 1 — Queries 1 and 6 still returning wrong chunks
**Queries**: "give me the checklist before leaving site after installation"
and "how to connect tablet to hub"
**Symptom**: Both return page-number headers and unrelated sections
("MyConnect Environment – 9", "MyConnect Environment – 17").
**Current hypothesis**: One of two things. Either (a) the relevant chunks
(the actual "before leaving" checklist, the actual tablet-connection steps)
are not scoring above the RETRIEVAL_GATE_THRESHOLD=0.5 on the reranker —
meaning they're not in the top 5 at reranking time and the wrong chunk wins
extraction by default; or (b) they ARE being retrieved but losing to a
generic, densely-listed "Pre-requisites" or "Notifications" chunk during
extraction because those sections happen to score well structurally.
**What's needed to diagnose**: The retrieval_score is now shown in
test_queries.py output for all queries. Run test_queries.py and look at the
retrieval_score for queries 1 and 6. If it's above 0.65 (confident band),
the right chunk IS being retrieved but extraction is picking the wrong one —
inspect the sources field to see what's actually in the candidate set.
If retrieval_score is between 0.5 and 0.65, the gate is passing a borderline
chunk. The manual's "before leaving" checklist and tablet connection steps
may not be indexed as separate, clean chunks — re-ingesting after verifying
the chunking output would be the next step.

### Issue 2 — Queries 8 and 12 rejected as low_retrieval_confidence
**Queries**: "explain why device registration might fail" and "give me steps
to install and verify system is working"
**Symptom**: Reranker gives top chunk a score below 0.5, system refuses
before generating.
**Current hypothesis**: Either the relevant content genuinely isn't in the
corpus (not extracted well from the PDF), or it is present but the reranker
is not scoring it relevant to these specific phrasings.
**What's needed**: Lower RETRIEVAL_GATE_THRESHOLD from 0.5 to 0.45 and re-run
to see if these queries get through. Check the sources field if they do —
confirms whether the right content is present. If the content simply isn't
being extracted from the PDF properly, inspect parsing.py's output for the
relevant pages.

### Issue 3 — Query 7 ("why is multicast required for hub discovery") always returns not found
**Symptom**: mistral returns "I could not find that in the knowledge base."
**Current hypothesis**: The concept is in the corpus ("Fast and efficient
device discovery via multicast" appears in a benefits section in the PDF) but
that specific section may not be chunked as an independent retrievable unit,
or the phrasing doesn't match the embedding space well enough.
**What's needed**: Check whether the relevant text is present in ChromaDB at
all by querying the collection directly.

### Issue 4 — structure.py is_bad_line residual false positives
**Status**: Table-heavy PDF sections with severe column-merge artifacts can
still produce an occasional fragment that passes all filters. Documented as a
known limitation. The complete fix requires row-aware PDF table extraction
(pdfplumber table mode or unstructured.io), not more regex filters.

---

## Verification boundary — what's proven vs what needs live environment

**Proven by tests that run in any Python environment (no Ollama/ChromaDB):**
- Session isolation (test_memory.py — 8 tests, including the direct regression)
- Query condensation prompt building and output parsing (test_regression_bugs.py)
- has_reference_markers fires correctly / doesn't fire on standalone queries
- classify_by_similarity (nearest-neighbor classification math, test_router.py Tier 1)
- camelCase brand-name preservation (test_regression_bugs.py)
- Chunk scoring fix — relevant chunk beats irrelevant-but-longer chunk
  (test_regression_bugs.py)
- All is_bad_line filters including verb-led exemption
- All chunking / step-boundary splitting behavior
- RRF merge math including BM25-only item survival
- truncate_after_refusal with all variant phrasings

**Requires live environment (Ollama + ChromaDB + sentence-transformers):**
- condense_query actual rewrite quality with a real phi model
- route_model real routing accuracy with actual embeddings (test_router.py
  Tier 2 — currently skipped with honest SKIP marker)
- End-to-end pipeline behavior for queries 1, 6, 7, 8, 12

**Current test suite results in this sandbox:**
78/83 passed, 5 SKIP (route_model integration), 0 FAIL

---

## Norms established in this project — important for continuity

1. **Always reproduce bugs before claiming a root cause.** Every bug above
   was confirmed with a direct Python reproduction (python3 -c "..." or a
   scratch script) before the fix was written. Root causes asserted without
   reproduction have been wrong in this project (the relay/Steps4 scoring
   bug and the camelCase bug were both found this way — initial hypotheses
   were wrong until the numbers were actually run).

2. **Be explicit about the verification boundary.** What's proven by tests
   that actually ran vs what's correct-by-design-but-needs-live-environment
   is always stated separately. Never claim something is verified if only the
   pure-logic piece was tested.

3. **Respect sequencing.** When the user says "fix current issues first
   before anything else," that means exactly that. Don't slip in additional
   improvements or refactors alongside the bug fix.

4. **Don't return to heuristics after replacing them.** If a heuristic was
   replaced because it was structurally weak, the replacement should address
   the structural weakness — not just be a more complex heuristic. The router
   replacement (semantic) and the follow-up detection replacement
   (Rewrite-Retrieve-Read) both address the root structural problem.

5. **Tests must be honest about skips.** The run_tests.py SkipTest mechanism
   exists specifically so integration tests that need sentence-transformers
   don't silently pass as if they ran. A SKIP is not a PASS.

---

## How to run

```bash
# Install dependencies
pip install -r requirements.txt

# Pull local models
ollama pull phi
ollama pull mistral

# Start backend
uvicorn main:app --reload

# Start frontend (separate terminal)
streamlit run app.py

# Run test suite (no live services needed for pure tests)
python3 run_tests.py

# Run end-to-end smoke test (needs running server)
python3 test_queries.py
```

**Environment variables:**
- `OLLAMA_URL`: default `http://localhost:11434/api/generate`
- `CHROMA_DIR`: default `./chroma_db`
- `API_BASE`: default `http://localhost:8000` (used by app.py)
- `DEEPSEEK_API_KEY`: optional, enables DeepSeek escalation/rethink

---

## Version history summary

- **v1**: Initial keyword retrieval only
- **v2**: Added ChromaDB shared client fix, dense retrieval
- **v3**: Added full-corpus BM25 + RRF hybrid retrieval
- **v4**: Fixed extraction scoring (rerank-score dominant), camelCase fix,
  chunking step-boundary split, is_bad_line narrative/fragment filters,
  retrieval confidence gate (none/ambiguous/confident), grounding NLI check,
  DeepSeek escalation, clickable sources, rethink-with-model UI feature
- **v5**: Added session_id scaffolding (incomplete), clarifying questions path,
  query rewriting (heuristic version — later replaced)
- **v6 (CURRENT)**: Full session-scoped memory (Bug 7 fix), LLM-based query
  condensation with has_reference_markers guard replacing word-count heuristic
  (Bugs 8, 9, 10), semantic router replacing keyword-list router (Bug 11),
  first-line-only parse_condense_output (Bug 9), retrieval_score in all
  responses, improved test_queries.py

**The rag_v6.zip attached to this handoff is the current authoritative
version. Do not mix files from earlier versions.**
