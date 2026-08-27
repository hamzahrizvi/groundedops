# Pending — GroundedOps

Working list of open items, kept current by the Monday status agent (see
`.github/` routine) and by hand. Each item: what it is, why it matters, and
what "done" looks like. Move an item to **Resolved** with the commit/PR that
closed it — don't delete history, since the Monday report reads this file to
know what changed since last week.

Last hand-updated: 2026-08-27, after v16.0.

---

## Blocking

- ~~`accounts.json` has no backup and is not regenerable~~ — **RESOLVED as
  far as it can be pre-staging.** A first real archive was taken 2026-08-27
  (`manage_backup.py export --plain`, 107.7MB: 12 documents, 38 index files,
  all 6 stores, accounts included) and verified readable with
  `manage_backup.py inspect`. The mechanism is proven end to end, not just
  built.

  **Two decisions taken 2026-08-27, both deliberate, both with a condition:**

  1. **Encryption is OFF** (`BACKUP_ALLOW_PLAINTEXT=1` in `src/.env`). The
     lose-the-passphrase risk was judged worse than the at-rest risk while
     this is pre-staging and holds no real customer data. The encryption
     path is built, tested and unchanged — this is a config switch, not a
     removal. **REVISIT BEFORE STAGING CARRIES REAL DATA:** an archive holds
     staff password hashes and customer contact details in the clear, so
     until then archives are protected only by where they are put and by
     filesystem permissions.
  2. **No schedule yet** — backups stay manual until the staging host
     exists. Automating against this dev machine would be throwaway work;
     the cron job belongs in the staging deploy, next to a real destination.
     Tracked under the staging item below so it is not forgotten.

- **Password reset via email is banked, not built.** The user will supply
  SMTP credentials later. Until then the only reset path is a root using the
  console's "Reset password" button (Accounts page) or
  `manage_accounts.py passwd <email>` from the machine. Root-lockout
  question is settled: **no single account is permanently protected** — the
  existing "the only active root cannot be disabled/deleted/demoted" rule is
  sufficient, because promoting a second person to root before someone
  leaves is how root moves between people. Nothing to build there.

- ~~No working provider key on the server~~ — **STALE, verified working
  2026-08-27.** The `DEEPSEEK_API_KEY` in `src/.env` returns HTTP 200 on
  both `GET /models` and `POST /chat/completions`, and
  `ONLINE_DEEPSEEK_MODEL=deepseek-v4-flash` is present in the live model
  list. The 401 in this item predated the `deepseek-chat` alias retirement
  and was never re-checked after the key was replaced; it sat here blocking
  the grounding sweep below for no reason.

  Lesson worth keeping: this item blocked another item for weeks, and
  clearing it took one API call. **Re-verify a "blocked on credentials"
  item before planning around it.**

- ~~Grounding threshold (0.55) has never been swept~~ — **MEASURED
  2026-08-27, and it is not a risk.** `sweep_grounding.py` ran the 27
  answerable eval cases; 17 reached the grounding gate and scored between
  **0.9418 and 0.9994** (median 0.9970). At the live 0.55, **zero correct
  answers are discarded.** The gate would have to rise to 0.95 before it
  cost anything (1 case, 5.9%). It has ~0.39 of headroom — it is nowhere
  near the scores it is judging.

  Method worth keeping: `check_grounding` computes its score independently
  of the threshold it is passed (the threshold is only the final `>=`), so
  a sweep needs ONE generation pass, not one per candidate value.
  `sweep_grounding.py --report-only` re-reports from the saved run with no
  provider calls. `GROUNDING_THRESHOLD` is now env-tunable so acting on this
  needs no code edit.

  **Do not lower it on this evidence.** Every score sits above 0.94, so the
  measurement says the gate is loose, not that it is well-calibrated —
  nothing in the suite currently probes the 0.4–0.9 band where the
  threshold would actually bite. What it rules out is the thing that was
  feared: 0.55 is not silently eating correct answers.

- **7 eval cases test a product key that does not exist.** Found by the
  sweep above: cases tagged `product: "nv9st"` retrieve nothing and are
  refused BEFORE generation, so they never reach any of the pipeline they
  are meant to exercise. The index holds `nv9usb` (82 chunks) and
  `nv9_spectral` (82 chunks); the catalogue lists both. **There is no
  `nv9st` anywhere.**

  This is the "catalogue drift / duplicate product keys" item below, now
  with evidence. It means **26% of the answerable eval suite has been
  measuring nothing**, and it was hidden behind the stale "no working
  provider key" item that stopped anyone running the suite.

  Needs a domain call before fixing, which is why it is not already done:
  most of those cases ask about the NV9USB+ (→ `nv9usb`), but case 10 asks
  about "the NV9ST" and case 13 about the NV11+ Note Float recycler, and
  which key those belong to is a product question, not a code question.
  Retag them in `eval_cases.json` and the suite goes from 17 to 24 cases
  that actually exercise the pipeline.

  Three further cases never reach the gate for a different and legitimate
  reason: they carry no product scope at all (two MyConnect, one bare
  "how fast is it?"), and the pipeline requires a scope before answering.

## In progress / known gaps

- **Staging deploy is prepared but has never been run.** `STAGING.md` is the
  runbook; `docker/.env.example` is the template. **Carries two deferred
  decisions from 2026-08-27:** the nightly backup cron belongs here (see the
  backup item above), and backup encryption should be switched back on
  (`BACKUP_ALLOW_PLAINTEXT` removed, a passphrase chosen and stored) before
  the host holds real customer data. What is done: the admin
  surface can be opened deliberately (`ADMIN_ALLOWED_IPS`), first-run root
  creation is protected in two layers, every runtime store is on the
  persistent volume, `documents/` is bind-mounted, and the WordPress plugin
  no longer overrides the console's branding. What is NOT done: **none of it
  has been exercised on a real host.** In particular `docker compose up` has
  not been run since the compose file changed, and the WordPress plugin edit
  was not PHP-linted (no php binary available here).

- **SMTP is the one thing standing between enquiries and a person.** Each
  form now also carries a phone number to offer, and every lead records
  `cc_email` (the visitor's own address) so the reply can copy them in the
  moment sending exists. Every other piece is built: the widget renders the console-configured sales and
  support forms, writes up the enquiry (from the chat or the visitor's own
  words), stores it with its destination address, and marks it
  `notified: false`. Wiring send means one function plus credentials — the
  destination is already on every lead as `notify_email`. Until then nobody
  is notified and someone must check the Enquiries page.

- **Guest AI access is now a console switch, and it is OFF.** "Access &
  limits" (root only) controls it, along with daily allowances and
  per-conversation caps. While it is off, guests get reviewed FAQs only and
  enquiry drafting returns an assembled body rather than a written one — so
  **most real enquiries will be the assembled kind until either visitors
  sign in or you switch guest AI on**. That switch has a bill and a
  prompt-injection surface attached, which is why it is not a default.
  `WIDGET_AI_DRAFT_ANONYMOUS=1` still works as the env-level initial value.

- **Per-conversation caps are a cost guard, not a security boundary.** The
  session id comes from the visitor's browser, so anyone can start a fresh
  conversation. The daily per-visitor and per-IP ceilings are what actually
  bound a determined caller; the session caps stop an honest runaway thread.
  Worth knowing before relying on them for anything adversarial.

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

- **RESOLVED: `offer_support` now opens the support form in the widget.** A
  refusal offers "Ask our support team", which opens the configured support
  form (fields from the console, optional write-up of the conversation) and
  files a real enquiry. It previously threw the visitor at a `mailto:` with
  no record kept. **`product_options` is still not rendered as buttons in
  the widget** — the widget's own flow asks for a range/product up front via
  `/widget/catalog`, so the mid-conversation disambiguation buttons the
  console's chat shows have no equivalent there yet.

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

- **Catalogue drift / duplicate product keys.** **Now evidenced** — see the
  `nv9st` finding under Blocking: 7 eval cases point at a key that exists
  nowhere. `nv9usb` vs `nv9_spectral`,
  `mini` vs `mycheckr_mini` existed as separate, inconsistently-filed product
  keys at points in this project; verify current `catalog_config.json` still
  matches what the FAQ answers are actually tagged to before trusting
  product-scoped retrieval blindly. 37 FAQ entries were at one point tagged
  `myconnect`, a key absent from the catalogue — confirm resolved.

- **`documents/` is untracked in git** and is the working document store
  (`docstore.py`). It is the only copy of some source PDFs — three exist
  solely under the legacy `C:\data\source_files` on the user's machine and
  have not been consolidated. The backup feature now covers it (an archive
  carries every document), which answers "how", but not "has anyone". The
  three legacy files still need consolidating with
  `reindex.py --migrate` before a backup can include them.

- **PR for branch `fix/wire-faq-choices` → `main` not yet opened.** `gh` CLI
  is installed (`winget install --id GitHub.cli`) but not authenticated as of
  last check (`gh auth status` → not logged in). Device-code login was
  started at least twice; confirm which attempt (if any) completed. Once
  authenticated, `gh pr create` against `main` with the body already drafted
  in this session's scratchpad.

## Lower priority

- ~~`ADMIN_PASSWORD` still defaults to the literal string `admin`~~ —
  resolved: the shared password is gone entirely, replaced by real accounts
  with levels (`accounts.py`, `manage_accounts.py`, `tests/test_accounts.py`).
  Three levels: `root` (everything, incl. account management), `support`
  (everything except account management), `basic` (test chat only). Sessions
  are HMAC tokens signed with `SESSION_SECRET`, revocable via a per-account
  token epoch. **Not yet verified in a browser** — the sign-in form,
  first-run root bootstrap, and the Accounts page are DOM-driven and want a
  real click-through. An SSO seam is documented in `accounts.py`: Entra/OIDC
  would replace `verify_password` only.
- ~~The browser can still hold a DeepSeek key in `localStorage` that
  overrides the server's own key (`api.js`)~~ — resolved as a side effect of
  the v15.2 React SPA retirement; `api.js` and its localStorage override only
  exist under `src/legacy/` now. `.env` (via the new `keystore.py`) is the
  sole source of provider keys in the live app.
- ~~Widget design page saves config the live widget does not read~~ —
  **resolved.** `groundedops-widget.js` now fetches `/widget/config` on load
  and applies the name, welcome, colour, icon, opening options and both
  contact forms. `data-*` attributes still win when explicitly present on
  the script tag, so an existing embed that hard-codes its accent does not
  change appearance; anything not set as an attribute is governed by the
  console. `data-api`/`data-token` stay attribute-only by necessity.
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
