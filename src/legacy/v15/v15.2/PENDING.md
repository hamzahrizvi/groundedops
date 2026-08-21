# Pending — GroundedOps

Working list of open items, kept current by the Monday status agent (see
`.github/` routine) and by hand. Each item: what it is, why it matters, and
what "done" looks like. Move an item to **Resolved** with the commit/PR that
closed it — don't delete history, since the Monday report reads this file to
know what changed since last week.

Last hand-updated: 2026-08-20, after v15.1.

---

## Blocking

- **No working provider key on the server.** `DEEPSEEK_API_KEY` in `src/.env`
  returns HTTP 401. Blocks: `eval.py --update-baseline` (end-to-end
  evaluation), and sweeping the grounding threshold below. Owner: user (needs
  a valid key pasted in, then a restart).

- **Grounding threshold (0.55) has never been swept.** The system discards a
  correct generated answer if NLI grounding scores it below this — an
  uncalibrated gate, same family as the three threshold bugs fixed in v15.1.
  The false-refusal rate is completely unknown. This is the single biggest
  unmeasured risk in the pipeline. Needs the provider key above, then
  `eval.py` run across a range of thresholds against the 34-case suite.

## In progress / known gaps

- **~50s of a 113s answered query is unattributed.** Measured breakdown:
  DeepSeek call 47.8s, NLI grounding 8.4s, reranker 3.9s, retrieval 3.0s — sums
  to ~63s, wall clock was 113s. Needs per-stage timing instrumentation around
  the request path to close (today's `timing` dict only covers part of it).
  `CONTEXT_K=5 CHUNK_CHAR_CAP=1200` roughly halves prompt size as a stopgap if
  latency matters more than recall — untested trade-off, measure with
  `eval_retrieval.py --compare`.

- **RESOLVED v15.2: markdown now renders in both surfaces.** The admin
  console's test chat and the widget both render lists, tables, bold and code.
  The React app was retired rather than fixed (see below).

- **The widget does not render `product_options` or `offer_support` yet.**
  The backend returns both on every relevant response; the admin test chat
  renders them. So an ambiguous question in the WIDGET shows the "which
  product did you mean?" prose with no buttons to click, and a refusal shows
  no contact-support option. This is now the last remaining surface gap and
  it is the customer-facing one.

- **The React SPA was retired in v15.2.** It was a third chat surface
  duplicating the admin console's test chat and the widget, so every fix had
  to be made three times and the widget was consistently last. Consequences
  worth tracking:
  - `/` now 307-redirects to `/admin`.
  - The widget moved to `src/widget/` and is served by explicit exact-path
    routes (`/widget/groundedops-widget.js`, `.php`). The customer-facing URL
    is unchanged. It previously depended on vite copying it into
    `frontend/dist/` and that being mounted at `/` — a customer asset
    depending on an internal dev app being built.
  - **No UI remains for the generation mode / provider toggle.** The React
    app's Settings page was the only one. The endpoints still exist
    (`POST /settings/mode`, `POST /settings/online_provider`) but are
    curl-only. Worth adding to the admin console.
  - Saved chat history (was browser localStorage in the React app) is gone.
  - Node/npm is no longer needed at all: no build step anywhere.

- **Catalogue drift / duplicate product keys.** `nv9usb` vs `nv9_spectral`,
  `mini` vs `mycheckr_mini` existed as separate, inconsistently-filed product
  keys at points in this project; verify current `catalog_config.json` still
  matches what the FAQ answers are actually tagged to before trusting
  product-scoped retrieval blindly. 37 FAQ entries were at one point tagged
  `myconnect`, a key absent from the catalogue — confirm resolved.

- **`documents/` is untracked in git** and is the working document store
  (`docstore.py`). It is the only copy of some source PDFs — three exist
  solely under the legacy `C:\data\source_files` on the user's machine and
  have not been consolidated. Decide: commit `documents/` (repo becomes the
  backup) or back it up elsewhere, deliberately — don't leave it as the only
  copy with no backup of either kind.

- **PR for branch `fix/wire-faq-choices` → `main` not yet opened.** `gh` CLI
  is installed (`winget install --id GitHub.cli`) but not authenticated as of
  last check (`gh auth status` → not logged in). Device-code login was
  started at least twice; confirm which attempt (if any) completed. Once
  authenticated, `gh pr create` against `main` with the body already drafted
  in this session's scratchpad.

## Lower priority

- `ADMIN_PASSWORD` still defaults to the literal string `admin` in
  `src/.env`.
- The browser can still hold a DeepSeek key in `localStorage` that overrides
  the server's own key (`api.js`) — decided to leave as-is; `.env` is the
  intended single source of truth, but the client override path still exists
  in code.
- Widget design page in the admin console saves config that the live widget
  (`groundedops-widget.js`) does not read — it uses `data-*` attributes only.
  Cosmetic-looking bug that misleads whoever configures it.
- No image/diagram/OCR handling in ingestion — explicitly deferred, not
  forgotten.
- 4 pre-existing `test_llm.py`-adjacent issues were cleared in v15.0; if new
  ones appear, check `run_tests.py`'s per-file `sys.modules` isolation is
  still doing its job (it's what unskipped 5 files that were silently not
  running before).

## Resolved (kept for the Monday diff, trim periodically)

- v15.1: three uncalibrated-threshold refusal bugs (retrieval gate, context
  floor, FAQ candidate cut), conversation context not reaching the answering
  prompt, sticky product scope, source citations naming pages actually read,
  `release.py` tooling.
- v15.0: document store made disposable (`docstore.py`, `reindex.py`),
  retrieval-only evaluation (`eval_retrieval.py`), 34 real eval cases
  replacing template placeholders, product-metadata key mismatch
  (`product` vs `products`) fixed at ingest.
- v14.0: console walkthrough/help/sub-nav, product-scoped FAQ answers,
  draft-row layout fix, LAN address detection fix in `run.ps1`, retired
  `deepseek-chat` alias cleared from 8 call sites.

---

*How to use this file (for the Monday agent or a human):* diff the "Resolved"
section against last week's version of this file (via git history) to see
what closed. Check "Blocking" and "In progress" for anything a `git log`
since last Monday shows movement on. Report drift honestly — an item that
looks resolved in code but has no test/measurement behind it stays
"in progress," not "resolved."
