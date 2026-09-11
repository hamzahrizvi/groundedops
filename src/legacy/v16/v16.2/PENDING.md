# Pending — GroundedOps

Working list of open items, kept current by the Monday status agent (see
`.github/` routine) and by hand. Each item: what it is, why it matters, and
what "done" looks like. Move an item to **Resolved** with the commit/PR that
closed it — don't delete history, since the Monday report reads this file to
know what changed since last week.

Last hand-updated: 2026-08-28 — RRF retrieval bug fixed and A/B verified,
code scan and NV9 eval added; widget plugin export shipped.

---

## Blocking

- ~~RRF fusion silently discards the best chunk~~ — **FIXED 2026-08-28**
  via `RETRIEVAL_ARM_GUARANTEE`, after A/B'ing two candidate fixes and
  rejecting the obvious one on evidence. Found by testing questions from the two
  NV9 manuals already in the corpus. "How much does the NV9S validator weigh
  on its own?" is refused ("I could not find that in the knowledge base")
  even though `documents/NV9 Spectral Range User Manual-v1.pdf` p25 says
  `Validator NV9S: 1.05 Kg`.

  Traced end to end, and every stage in isolation is fine:

  | Stage | Result for the correct chunk |
  |---|---|
  | Indexed? | yes — `..._25`, `product=nv9_spectral`, page 25 |
  | Dense ranking | **#1** |
  | BM25 ranking | **absent** ("weigh" does not lexically match "Weights") |
  | RRF score | 1/(60+0+1) = **0.016393** |
  | Final RRF rank | **#20 of 46** — candidate cutoff is 16 |
  | Reranker, if given it | **0.9928** (vs 0.2165 for the ToC page that won) |

  The mechanism is textbook RRF: with `RRF_K=60` a chunk found by only ONE
  retriever caps at 1/61 = 0.0164, while any chunk both retrievers rank in
  their top ten scores ~0.028–0.030. The top five that beat it sat at
  bm25/dense ranks (7,6), (8,7), (9,10), (19,3), (2,22) — all mediocre in
  both, all winning on the strength of appearing twice. **A single-list #1
  can never beat a dual-list top-10.** This bites hardest on spec tables,
  where dense understands `weigh`→`Weights` and BM25 sees only numbers —
  i.e. precisely the content these manuals are made of.

  **The immediate cause is a comment/code mismatch** in
  `retrieval_db.retrieve_from_db`. The comment says:

      # Keep a margin of candidates (top_k*2) so the downstream reranker has
      # room to reorder before the answering pipeline trims to top_k.
      ranked_ids = sorted(scores, key=scores.get, reverse=True)[:top_k * 2]

  …but the loop that follows breaks at `len(results) >= top_k`, so the
  margin it just built is thrown away and the reranker receives 16, not 32.
  The correct chunk sits at RRF rank 20 — inside the margin the comment
  promises, outside the one the code delivers.

  **A/B'd over both suites, 2026-08-28. The obvious fix was wrong; a
  surgical one works.** Two servers, identical code, differing only by env
  var, every case forced through retrieval with `skip_faq`.

  *Rejected — widen the window (`RETRIEVAL_CANDIDATE_MARGIN=2.0`, i.e. give
  the reranker the full `top_k*2` the comment promises):*

  | | official 34 | NV9 17 | mean grounding |
  |---|---|---|---|
  | margin 1.0 | 25/34 | 15/17 | 0.9934 |
  | margin 2.0 | **23/34** | 16/17 | **0.9539** |

  Two regressions, no gains on the official suite, and grounding fell
  visibly. Sixteen extra mediocre candidates displace good ones when the
  reranker picks its `CONTEXT_K`. **Left at 1.0.**

  *Adopted — guarantee each ranking's own top 3 survives
  (`RETRIEVAL_ARM_GUARANTEE=3`):* rescues exactly the "one retriever is
  certain, the other has never heard of it" case without bulking the
  context. Deterministic measurement over all 51 questions:

      queries with >=1 rescued candidate : 12 / 51
      total extra candidates             : 23  (avg 0.45 per query)
      queries where the reranked TOP changed: 1  (0.216 -> 0.993, the NV9S case)

  End to end: 0 regressions on either suite, grounding flat (0.9966 ->
  0.9962 official, 0.9976 -> 0.9973 NV9), latency flat. **Applied**, both
  knobs env-tunable so the experiment is repeatable.

  **Caveat on the aggregate numbers.** Two runs of the *same* baseline gave
  25/34 and 22/34 — generation is stochastic even though the checks are not,
  so the suite carries roughly +/-3 cases of run-to-run noise and no
  end-to-end delta of that size means anything on its own. The evidence for
  this fix is the deterministic retrieval-layer measurement above, not the
  end-to-end tally.

  **Correction:** an earlier version of this entry said this was "strongly
  suspected" to be the cause of the MyCheckr 0.0051 case below. That was
  wrong. Under the widened window that case *regressed* to a refusal, and
  under the adopted fix it is unchanged. The 0.0051 case is still unexplained
  and still needs its own look.

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

- ~~7 eval cases test a product key that does not exist~~ — **RESOLVED
  2026-08-27.** `nv9st` was a phantom: it appears in no document, no
  catalogue entry, nothing. 18 customer questions and 7 eval cases were
  filed under it. All retagged to `nv9usb` (the NV9USB+ Range manual, which
  also covers the NV11+), decided by the user after the evidence was laid
  out.

  Measured effect: the answerable eval suite went from **17 cases reaching
  the pipeline to 22**, and the five recovered NV9 cases now score
  0.997–0.999 on grounding — they had been refused before generation ever
  ran.

  **The root cause is fixed too**, which matters more than the cleanup:
  `catalog.delete_product` used to remove only the catalogue row and orphan
  everything tagged to it. That is where `nv9st` and `coin_hoppers` came
  from. Deletion now requires the caller to say whether content is
  reassigned or deleted, and refuses to default.

- **One eval case asks about a product name that is in no document.**
  "What voltage is supported on the NV9ST?" still fails after the retag —
  correctly, because the string "NV9ST" appears nowhere in the corpus. The
  question itself is wrong, not its tag. Either reword it to name a real
  product, or drop it. Until then the suite has one permanently-failing
  case, which is worse than 26 cases because it trains people to ignore a
  red result.

- **One answer is genuinely ungrounded, and the gate is catching it.**
  "Does MyCheckr require integration with other systems?" scores **0.0051**
  — near-zero entailment against its retrieved context — while every other
  answered case scores above 0.99. The eval suite expects this one to be
  answered, so the sweep reports it as a false refusal, but a 0.005 is not
  a threshold problem: it would be refused at any threshold above zero.
  Either the documents do not actually answer it, retrieval is pulling the
  wrong chunks, or the model invented something. Worth one look; it is the
  only case in the suite behaving like this.

  This does not change the threshold conclusion above. 0.55 still discards
  nothing that scores well, and the one refusal is the gate doing its job.

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

- **Catalogue drift / duplicate product keys.** **Cause found and fixed**
  (2026-08-27): products could be deleted without dealing with their
  content, orphaning it under a key with no product in front of it. `nv9st`
  is cleaned up; **`coin_hoppers` (1 question) is the same shape and has
  not been touched** — decide where it belongs and use
  `POST /admin/product/retag`. `nv9usb` vs `nv9_spectral`,
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

- ~~"talk to sales" and one-word replies on the customer-questions list~~ —
  fixed 2026-08-27. `record_gap` now filters anything that is not a
  curatable question (button text, "no", bare product names) at the single
  choke point all six callers go through. Existing noise entries are still
  in the log and will need dismissing by hand, or ignoring — the filter is
  not retroactive.

- **Desktop shell for the admin console — decided 2026-08-28, not started.**
  The goal is a company admin (ITL, initially Hamzah) installing one thing,
  uploading documents, curating FAQs, creating accounts and exporting the
  WordPress widget, without meeting a terminal.

  **The backend cannot move into the .exe.** The widget runs in a customer's
  browser on the WordPress site and calls the backend directly for every
  answer, so the backend needs a public, always-on address. An admin PC has
  neither: laptop shut means the site's widget is dead, and a residential IP
  moves. The direction chosen is therefore **hosted backend + a thin desktop
  shell** — the .exe is a window onto the hosted console, not a copy of it.
  (A Cloudflare Tunnel would let a local backend serve the public widget. It
  works, and it was rejected: uptime of a customer-facing widget should not
  depend on whether someone's laptop is open.)

  Infrastructure this needs that does not exist yet:

  1. **A permanent host.** Staging is the first one; production is a second.
     Until there is a box with a stable hostname and TLS, the desktop shell
     has nothing to point at.
  2. **A packaging pipeline.** Tauri (small, needs Rust) or pywebview
     (trivial, ships a Python runtime). Either way: an icon, a versioned
     installer, and a place to host the download.
  3. **Code signing.** Unsigned, Windows SmartScreen warns every ITL
     colleague on first run. An OV/EV certificate is a purchase and a
     renewal, so it wants deciding early rather than at ship time.
  4. **An update path.** A shell that pins a URL is fine until the URL
     changes. Decide now whether it self-updates or is simply re-downloaded.
  5. **A server-address setup screen**, so the same build works against
     staging and production without a rebuild.

  Worth being honest about the value: over "open the console in a browser
  and bookmark it", the shell buys an icon, a fixed window, and not having
  to remember a URL. That is not nothing for a non-technical admin, but it
  is packaging rather than capability — so it should come after the console
  itself is proven in a browser (see the browser-verification gap above),
  not before.

- **Code scan 2026-08-28 — five findings, none yet fixed.** A read-through
  of the live tree (`legacy/` excluded) for gaps not already on this list.

  1. **Non-atomic writes on the only copy of customer data.**
     `accounts.py`, `policy.py`, `keystore.py` and `docstore.py` write via
     temp-file + `os.replace`. `widget_config.py:314` (leads),
     `faq_store.py:113` (curated answers) and `catalog.py:81` do not — they
     use `open(path,"w")`, which truncates before writing, so a kill between
     the two leaves an empty or partial file. `accounts.py:101` says *"Unlike
     the FAQ store, a half-written accounts file locks everyone out"* — the
     reasoning was about **lockout**, never about **loss**, which is why
     leads were left out. With SMTP unwired (`notified: false`) and backups
     unscheduled, `widget_leads.json` is the single copy of every enquiry.
     Fix is ~6 lines copying the existing `accounts.py` pattern.

  2. **Leads are evicted silently, and the endpoint filling them is
     public.** `widget_config.py:402` keeps the newest `MAX_LEADS=5000` and
     drops the oldest with no log line. `/widget/lead` is public,
     unauthenticated and unrate-limited — already noted below as a *spam*
     problem, but it is really a *data destruction* problem: 5000
     submissions permanently erase every real enquiry behind them, with no
     trace and nothing emailed. At minimum log the eviction; better, refuse
     past the cap rather than discarding history.

  3. **`transcript` is the one untrusted field with no size cap.** `q` 500,
     `notes` 2000, each `values` entry 2000, `enquiry` 4000 — all bounded.
     `transcript` is bounded only by COUNT (`[-20:]` in `add_lead`, `[-12:]`
     in draft), never by entry size, and `/widget/lead` stores entries
     verbatim. Twenty 10MB entries is ~200MB in one lead, in a file fully
     read and rewritten on every subsequent write; uvicorn sets no default
     body limit. (Draft is safe — `_assemble_enquiry` returns `[:4000]`.)

  4. **`/health` cannot detect the failure it is used to rule out.**
     `main.py:658` returns `{"status":"ok"}` unconditionally, touching
     neither Chroma nor a provider key. Both the Docker healthcheck and
     **STAGING.md step 4** treat it as the verification step, so
     `docker compose ps` reports healthy while the index is missing and
     every answer refuses — exactly the state a fresh deploy is in. A
     `?deep=1` variant that counts collection rows would make the runbook's
     check real.

  5. **Worker scaling would silently corrupt every store.** All seven JSON
     stores rely on `threading.Lock()`, which is process-local. Correct
     today (single-worker uvicorn), but the open ~50s latency item makes
     `--workers N` the obvious reach, and that breaks locking everywhere at
     once with no error. Wants a comment in the compose file before someone
     tries it.

  **Checked and clean**, so nobody re-audits them: widget XSS (`esc`/`mdEsc`
  correct, every `innerHTML` sink escaped, markdown escapes-then-wraps);
  backup zip-slip (`backup.py:396` rejects `..`, absolute paths and drive
  letters, plus a prefix allowlist and a containment check); token
  comparison (`hmac.compare_digest` on both paths); log rotation (10MB); no
  secrets in log lines; `add_lead`'s `values` allowlist.

- **Ad-hoc NV9 eval, 2026-08-28 — 11/17, and the failures are informative.**
  17 questions written from the two NV9 manuals already in the corpus
  (`scratchpad/nv9_eval.py`), including deliberate discriminators where the
  two manuals disagree. Grounding on everything answered: 0.9945–0.9992.

  - **Product scoping is solid.** All three discriminators passed: asked the
    same question under each scope, it returned +3°C for NV9USB+ and
    +5°C/37.4°F for NV9 Spectral, and correctly said only Spectral takes 24V
    natively. No cross-product bleed.
  - **1 genuine false refusal** — the NV9S weight case. Root-caused to the
    RRF single-list problem and now fixed; the suite gives 16/17 after it.
  - **5 questions were answered from the FAQ store, not the documents** —
    `/query` returned curated candidates ("These FAQs match your query —
    please select the one you meant") for peak current, unit weight, host
    comms and a bezel part number. **This is by design, not a bug**: guests
    get reviewed FAQs only, so for the signed-out tier that menu IS the
    product, and it is the whole reason guest AI can stay switched off.
    Two narrower things are still worth a look, though:
    1. A part-number lookup with one obvious answer arguably wants serving
       directly rather than as a one-item multiple choice — the menu earns
       its place when the question is genuinely ambiguous.
    2. It intercepted a **pricing** question (no pricing anywhere in the
       corpus) that should have been refused outright, so whatever a visitor
       gets after picking a candidate there is untested and might imply the
       corpus covers pricing.

    Measurement consequence, and the more important point: **65% of the
    official suite (22/34) returns in under a second without touching
    retrieval at all.** The suite is mostly testing the FAQ store. Any future
    retrieval work must run with `skip_faq: true` or it is measuring the
    wrong layer — that is why the A/B above had to be re-run.
  - **Latency**: median 3.4s, max 20.2s — much better than the 113s figure
    in the latency item below, which may itself be stale.
  - Minor data-quality note: the Spectral weights table OCRs `NV11S: 2,04 Kg`
    with a comma for the decimal point.

- ~~No way to hand someone a ready-to-install WordPress plugin~~ — shipped
  2026-08-28. **Widget design → Install on WordPress** builds the zip with
  the backend address substituted into the real `define()`
  (`widget_export.py`, `POST /admin/widget/export_plugin`,
  `tests/test_widget_export.py`, 34 checks). Root can additionally embed the
  signing secret for a zero-config install; support cannot, because that zip
  is a credential that mints signed-in visitors — it is named
  `…-CONFIDENTIAL.zip` and its `INSTALL.txt` leads with the warning.
  **Not yet clicked in a browser** — the card is DOM-driven like the rest of
  the console, and the download path (blob + synthetic `<a download>`) is
  covered only by backend tests.

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
