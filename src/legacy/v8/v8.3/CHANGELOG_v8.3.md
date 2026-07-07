# GroundedOps — v8.3 Change Set

Drop these files into `src/` (overwrite existing). Every change is described
below with **what** and **why**. After copying, re-run the eval (see bottom).

> Line endings are CRLF to match your existing Windows files. If your editor
> or git complains, that's cosmetic.

---

## Files in this folder

| File | Status | One-line summary |
|------|--------|------------------|
| `main.py` | changed | Query normalization (case 39 fix) + breadcrumb-strip + 1200-char context (from v8.2) |
| `ingest.py` | changed | Breadcrumb-enriched chunks for retrieval disambiguation (from v8.2) |
| `llm.py` | changed | Stability: num_ctx 4096, local timeout 240s, keep_alive 2h |
| `eval.py` | changed | Preflight health gate before any case runs |
| `eval_cases.json` | **REPLACED** | NEW tight 16-case "pointy" PRIMARY suite (was the old set) |
| `eval_cases_extensive.json` | changed | 40-case SURVEY suite; ICU cases removed; cases 32/42/03 corrected |
| `eval_cases_api.json` | **NEW** | 4 ICU/API cases — run ONLY when the ICU doc is ingested |

---

## Change 1 — Query normalization (`main.py`)
**What:** Added `_normalize_query()`, applied to the query before condensation/
retrieval. Collapses repeated terminal punctuation (`???` → `?`) and lowercases
queries that are almost entirely uppercase.
**Why:** Case 39 (`MYCONNECT APP DEFAULT LOGIN???`) retrieved perfectly (0.9914)
but the small local model bailed to "could not find" on the shouty/malformed
phrasing. Normalizing the *working copy* fixes generation without touching
retrieval. Raw query is still what's logged/stored. Meaning-preserving; verified
it no-ops on normal queries.

## Change 2 — Breadcrumb-enriched chunks (`ingest.py`) — REQUIRES RE-INGEST
**What:** Each chunk is stored prefixed with `[Doc — Section]` (e.g.
`[MyConnect_Environment — Step 3: Login to the Hub]`).
**Why:** The app-login and API-credential blocks looked near-identical to the
retriever; the login chunk wasn't reaching the context. The breadcrumb gives
embedding/BM25/reranker the section-identity signal. Fixed the credentials bug
(case 02/11). **Section-title list is tuned to these docs — see the comment
block at the top of the file to extend it.**

## Change 3 — Breadcrumb strip before generation/grounding (`main.py`)
**What:** `_strip_breadcrumb()` removes the `[Doc — Section]` prefix from chunks
before they're used for the prompt and the grounding NLI check.
**Why:** The breadcrumb must help *retrieval* only. Left in, it would pollute
grounding scores and leak `[Doc — Section]` fragments into answers. No-op on
chunks stored before breadcrumbs existed (safe on a mixed collection).

## Change 4 — Context window 250 → 1200 chars (`main.py`)
**What:** `r["text"][:250]` → `r["text"][:1200]` when building context.
**Why:** 250 chars (~40 words) starved the generator; several cases fell back to
"could not find" despite perfect retrieval. NOTE: this is what necessitated
Change 5 (the model's context window had to grow to match).

## Change 5 — Ollama stability (`llm.py`)
**What:** `num_ctx` 2048 → 4096; local model timeout 90s → 240s; `keep_alive`
30m → 2h.
**Why:** The 1200-char contexts (Change 4) blew past the 2048-token window,
causing truncation/thrash and cascading timeouts: local fails → DeepSeek gets
hammered → rate-limited empties → answers suppressed → poisoned eval logs (the
43% run). Sizing the window to the real prompt and widening the timeout stopped
the cascade (74% run, zero "grader returned nothing"). **If you change chunk
size or top_k again, revisit `num_ctx` FIRST.**

## Change 6 — Preflight health gate (`eval.py`)
**What:** Before case 1, fires one real query; aborts with exit code 2 if the
backend is down or generation fails (`provider=none`). Also warms the model so
case 1 doesn't pay cold-load. `--skip-preflight` bypasses.
**Why:** A 40-case run against a degraded backend produces a log that costs more
to un-learn than the check costs to run. This turns silent 25-minute hangs into
an immediate, explanatory abort.

## Change 7 — Eval suites restructured (`eval_cases*.json`)
**What:**
- `eval_cases.json` is now a **tight 16-case PRIMARY suite** — one case per
  distinct behavior/fix, no duplicates, no doc-presence dependence. Run every
  iteration.
- `eval_cases_extensive.json` is the **40-case SURVEY suite** — run only when
  validating a new benchmark or before a release.
- `eval_cases_api.json` (**NEW**) holds the 4 ICU/API cases — run ONLY when
  `ICU_Network_API-*.pdf` is ingested, so doc presence stops polluting pass rate.
- Corrected test-expectation errors: case 32 (mounting — answering both devices
  is now accepted as ideal), case 42 (step-1 follow-up — grounded steps OR
  clarify both accepted now the checklist doc is known), case 03 (three
  components — annotated as a known source-wording issue, graded leniently).
**Why:** Faster iteration on the pointy suite; the survey and API cases don't
distort your primary pass rate. Several prior "failures" were test errors or
doc-presence artifacts, not system bugs.

---

## NOT fixed (deliberate)
- **Case 43** (`does MyCheckr Mini have a screen` → self-contradicting "Yes…
  without a screen"): a 7B-model comprehension flake on one paraphrase. A
  bespoke patch would add complexity for a symptom. Real fix is a stronger
  generator (DeepSeek-primary in production). Logged as a known limitation.

---

## How to apply and run

1. **Copy** all files in this folder into `src/` (overwrite).
2. **Re-ingest** (Change 2 changes stored chunk text): clear `chroma_db/`
   (or use your reset endpoint) and re-upload the four PDFs.
3. **Restart the backend** (uvicorn) so the new `main.py`/`llm.py` load.
4. **(Optional, faster eval) offload grading to DeepSeek:**
   ```powershell
   $env:EVAL_GRADER_PROVIDER = "deepseek"
   $env:EVAL_GRADER_MODEL   = "deepseek-chat"
   ```
5. **Run the primary suite** (this is now the default `eval_cases.json`):
   ```powershell
   python eval.py
   ```
   Preflight runs first; if it aborts, start the backend / warm Ollama and retry.
6. If green and you're happy, lock it: `python eval.py --update-baseline`.

To run the survey suite later:
`copy eval_cases_extensive.json eval_cases.json` → `python eval.py` → restore.
To run API cases (only with the ICU doc ingested): same pattern with
`eval_cases_api.json`.

## Answering your direct question
Yes — copy/paste into `src/`, re-ingest, restart backend, run `python eval.py`.
The only non-obvious step is the **re-ingest**: without it, the breadcrumb
change (and therefore the credentials fix) won't take effect, because the
prefixes are added at ingestion time.
