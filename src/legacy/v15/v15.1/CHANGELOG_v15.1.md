# GroundedOps v15.1 — three thresholds on uncalibrated scores

v15.1 is mostly one bug found three times. A question the corpus plainly
answered would be refused, then answered after a reword, and the cause each
time was a threshold applied to a score that is not a calibrated probability.
Identical retrieval, different wording, opposite outcome.

| Query | Top rerank score | Outcome |
|---|---|---|
| `what is the pinout for nv9?` | 0.836 | answered correctly |
| `give pinout for nv9?` | 0.268 | refused, model never called |

Both retrieved the pinout chunks into the top 8. Only the score differed.

## The three

**`RETRIEVAL_GATE_THRESHOLD` (0.35 → 0.10).** An absolute gate on a
cross-encoder score. A vaguely-phrased question scored under it and was
refused in 0.5s without the model ever being asked — which is how customers
phrase things. The real guards are downstream and unchanged: the prompt
refuses when context lacks the answer, and NLI grounding suppresses anything
ungrounded.

**`CONTEXT_FLOOR_RATIO`, relative to the top chunk.** Kept only chunks scoring
≥50% of the best one, so a peaky distribution collapsed the prompt to a single
chunk: top 0.771 put the floor at 0.385 while the two chunks *containing* the
pinout table scored 0.066 and 0.050 and were cut. Now bounded by `CONTEXT_MIN`
(4), so the floor can drop noise but never starve the prompt. The same line
also had a hard `results[:3]` cap, which meant the `CONTEXT_K` setting had
never done anything at all.

**FAQ candidate selection.** Only a *relative* cut existed, which says nothing
about whether the best match is any good — "give nv9 pinout?" was offered
"What is the NV9USB+ Range?" because nothing beat it. The discriminating signal
was already computed and discarded: lexical overlap was 0.000 while semantic
similarity was 0.720, because the embedder rates any two NV9 questions as
similar. Semantic agreement with no shared content word is exactly the "same
topic, different question" case, and is now rejected in favour of retrieval.

## Conversation

History reached the query rewriter but **never the answering prompt**, so the
model could not see what it had just said. "Is the pinout above for spectral or
usb?" was unanswerable in principle. The last four turns now go in as a
`<conversation>` block, separate from `<context>`, with facts still required to
come from retrieval. `MAX_MEMORY` 6 → 20, and the response reports
`turns`/`turn_limit`/`context_full` so a UI can suggest a fresh chat instead of
silently forgetting.

**Product scope is sticky.** A product chosen at the start of a chat is
remembered for the session, so an unscoped follow-up inherits it rather than
searching the whole corpus and failing.

## Answers and sources

- Value sets (pinouts, connectors, spec tables) are now requested as markdown
  lists or tables; they were arriving as unreadable run-on prose.
- The admin console renders that markdown — lists, tables, bold, code — with a
  ~60-line dependency-free renderer that escapes first and re-introduces only
  those constructs. **The React app and the widget do not yet render it.**
- Sources are clickable downloads with "see pages 18, 26, 44". `/source_file`
  resolves through `docstore`, so a link works wherever the original lives;
  previously it 404'd for anything in `<repo>/documents`.
- Citations are built from the chunks the model actually read. They were built
  from every reranked result, so an answer could cite pages 45 and 48 while
  saying it could not find the content — which is what made this look like
  retrieval flakiness.
- A refusal sets `offer_support`, and an unscoped question whose evidence spans
  several products returns `product_options` and asks which is meant instead of
  blending two products' pinouts into one answer.

## Tooling

`release.py` bumps `VERSION`, snapshots the release into `src/legacy/`,
generates the deploys table from git, and zips it. Version lives in its own
`VERSION` file — deliberately not `INGEST_VERSION`, which tells the app whether
to re-index and would nag users if bumped per release.

## Measured

18 scoped cases, chunk-level, via `eval_retrieval.py`:

| | 500/50 | 1200/200 |
|---|---|---|
| post-rerank recall@1 | 0.562 | **0.688** |
| post-rerank MRR | 0.688 | **0.760** |

The reranker earns its latency only at the larger chunk size — at 500/50 it
barely helped. Swapping the embedder to `bge-small-en-v1.5` (512-token window,
same 384 dims) changed post-rerank results **not at all**, but lifted
first-pass recall@1 from 0.389 to 0.556; kept because it removes the ceiling on
chunk size, not for today's numbers.

## Known open

- **Latency: ~113s on an answered query**, of which 47.8s is the provider call
  and ~15s local CPU (reranker + NLI). **~50s is unattributed** and needs
  per-stage timing to close. `CONTEXT_K=5 CHUNK_CHAR_CAP=1200` roughly halves
  the prompt if latency matters more than recall.
- **The grounding threshold (0.55) has never been swept**, so the
  false-refusal rate is unknown. Needs a provider key and `eval.py`.
- **Markdown is not rendered in the React app or the widget** — the two
  surfaces customers actually use.
- `documents/` is untracked and is the only copy of the corpus; three documents
  exist solely in the legacy `C:\data\source_files`.
- `ADMIN_PASSWORD` still defaults to `admin`; the browser can still send a
  competing API key that overrides the server's.

## Verification

126/126 unit tests, exit 0 (1 skipped: `test_ui` needs playwright). FAQ gate
verified in both directions. Retrieval gate and context floor verified against
the live index.
