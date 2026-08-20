# GroundedOps v15.0 — the index becomes disposable

v15 exists because of one realisation: **the vector index had become the only
copy of the documents**, and everything else followed from that. Chunk geometry
could not be changed, because changing it meant re-ingesting, and re-ingesting
meant deleting the only copy. Evaluation could not isolate retrieval, because
the only harness was end-to-end and needed a working provider key.

Both are now fixed. The documents are the durable asset; the index is derived
and can be thrown away.

## What deploys

| File | Where it goes | Notes |
|---|---|---|
| `docstore.py` | new, `src/docstore.py` | document store resolution + ingest manifest |
| `reindex.py` | new, `src/reindex.py` | rebuild the index from the documents |
| `eval_retrieval.py` | new, `src/eval_retrieval.py` | recall@k / MRR, no LLM needed |
| `ingest.py` | replaces `ingest.py` | resolves the store via docstore, records provenance |
| `main.py` | replaces `main.py` | retrieval/context geometry constants |
| `llm.py` | replaces `llm.py` | local answer cap, env-sourced model defaults |
| `parsing.py` | replaces `parsing.py` | layout-aware PDF, docx tables, furniture stripping |
| `router.py` | replaces `router.py` | env-sourced deepseek model |
| `eval.py` | replaces `eval.py` | env-sourced force-model default |
| `eval_cases.json` | replaces `eval_cases.json` | 34 real cases, was template placeholders |
| `test_parsing.py` | replaces `tests/test_parsing.py` | 14 tests for extraction |
| `test_llm.py` | replaces `tests/test_llm.py` | mode pinned, assertions derived |
| `run_tests.py` | replaces `run_tests.py` | per-file sys.modules isolation |
| `test_queries.py` | replaces `test_queries.py` | env-sourced model |
| `requirements.txt` | replaces `requirements.txt` | adds pdfplumber |

## READ THIS FIRST — the live index is in a damaged state

At the time of this drop the on-disk vector store is **hollowed out**:

```
embeddings                      0 rows
embedding_metadata              0 rows
embedding_fulltext_search_data 81 rows
segment binary (data_level0)    intact, dated 16 Aug
```

The running backend still answers, because it loaded the index into memory at
startup and is serving from there. **A restart will very likely come up empty.**
Recovery, in order:

```
python reindex.py --from-store --dry-run     # confirm all 4 documents resolve
python reindex.py --migrate                  # copy out of C:\data\source_files
python reindex.py --from-store               # rebuild (backend stopped)
python eval_retrieval.py --json baseline.json
```

Do not delete `C:\data\source_files` until the rebuild is verified.

## Data management

**The store default was the actual bug.** Retained originals went to
`os.getenv("SOURCE_FILE_DIR", "/data/source_files")` — a Docker volume path
that on Windows silently resolves to `C:\data\source_files`: outside the repo,
outside version control, outside any backup, and in a place nobody looks. A
search of the project and the user's folders concluded the documents were lost.
They were not. They were exactly where that default put them.

`docstore.py` now resolves the store as: explicit `SOURCE_FILE_DIR`, else
`<repo>/documents`, else the legacy path **read-only** so an existing install
keeps working until it is migrated deliberately. `--migrate` copies rather than
moves; losing the only copy while tidying up is the precise failure this module
exists to prevent.

**A manifest** records, per document, the chunk geometry, parser and embedder it
was ingested with. Without it there is no way to know whether a chunk in the
index was built at 500 chars or 1200 — so `stale_documents()` reports which
documents no longer match current settings, which is the "is my index actually
built the way I think it is?" check.

**`reindex.py`** rebuilds from the store. It refuses to run if any indexed
source has no retained original, because a rebuild resets the collection and
discovering the gap afterwards is unrecoverable. `--from-store` ignores the
index entirely and rebuilds from the documents plus the catalogue's filing,
which is what makes it usable when the index is the thing that is broken.

## Evaluation

**`eval_retrieval.py` measures retrieval on its own.** The embedder and reranker
are local, so recall@k and MRR need no provider key, no server, and no cost.
That is the cheapest feedback loop in the system and it was not being collected
at all — every signal came through an end-to-end harness where a retrieval
regression, a generation regression and an over-strict grounding threshold are
indistinguishable.

It reports metrics **before and after reranking**, which isolates whether the
cross-encoder earns its latency, and it names two specific failure classes:
documents never retrieved, and documents the reranker pushed *down*.

Its first run is itself a demonstration: 16 cases scored, 16 misses, recall
0.000 — an independent confirmation that the index is empty, arrived at without
the LLM or the backend.

`eval_cases.json` previously shipped as the unedited template
(`<YOUR PRODUCT>`), so the committed baseline was meaningless and those
placeholder strings appear in `logs.jsonl` as though they were real traffic. It
now holds 34 cases derived from reviewed FAQ answers: 27 answered, 5 refusal,
2 clarification, 1 follow-up, and 6 adversarial near-twin pairs on part numbers
(`PA02051` vs `PA02698`, `PM00488` vs `PM01106`) asserted in both directions.

## Still open

- **The grounding threshold (0.55) has never been swept.** The system discards
  generated answers below it, so the false-refusal rate is unknown. A sweep
  needs a working provider key.
- **The embedder is the ceiling on chunk size.** `all-MiniLM-L6-v2` truncates
  at 256 tokens; 1200 chars of prose is ~242. Table-dense text loses its tail
  from the dense vector, though BM25 still sees it. Going larger requires a
  512-token embedder and a full re-embed — which `reindex.py` now makes
  possible.
- **No image or OCR handling.** Scanned pages are reported but not read.
- **37 FAQ entries** are tagged to a product key absent from the catalogue.
- **Two sources of truth for the provider key** — the browser may send its own,
  overriding the server's.

## Verification

126/126 unit tests, exit 0 (1 skipped: `test_ui` needs playwright).
`reindex.py --from-store --dry-run` resolves all four documents with correct
catalogue filing. `eval_retrieval.py` runs and reports honestly against the
current empty index.
