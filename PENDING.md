# Pending — GroundedOps

Working list of open items, kept current by the Monday status agent (see
`.github/` routine) and by hand. Each item: what it is, why it matters, and
what "done" looks like. Move an item to **Resolved** with the commit/PR that
closed it — don't delete history, since the Monday report reads this file to
know what changed since last week.

**Friday review.** Once a week this file gets a pass, not just additions:

1. Tick off anything now done — strike it through, name the commit or PR,
   and move it to **Resolved**.
2. Delete anything OBSOLETED rather than completed, saying what superseded
   it. An item that a better fix made irrelevant is worse than no item: it
   sends the next session to do work that would now be wrong. Two in this
   file already went that way — an absolute score floor and a largest-gap
   context cut, both measured and rejected (see `ea4320a`).
3. Add whatever was found-but-not-done that week, WITH the measurement that
   justifies it. An item with no evidence behind it cannot be triaged later
   and will be rediscovered from scratch.

Every item should be actionable by someone who was not in the session that
found it. If it needs a transcript to understand, it is not written yet.

Last hand-updated: 2026-09-21 — the answerability switchboard
(`src/answerability.py`) and the second grounding contract
(`grounding.check_inference`) both landed; see **Resolved**. The switchboard
replaced four independent question-sniffs with one classification; the
inference contract is **off by default** and, while its rules are tested
(15 unit tests, plus 8/8 sound vs 0/8 invented across every product on the
real index), no model has ever been asked its prompt — the gateway is still
NXDOMAIN. The advisory route (#5) is now classified but still not routed.
Tests 218/218, 1 skipped.

Also 2026-09-22 (later) — **`eval_baseline_retrieval.json` is ARMED**, the
single highest-value item on the measurement list: 79/90 runs passed (88%),
26/30 cases stable across 3 repeats (87%). Recorded against DeepSeek, not
the on-prem gateway — see the caveat in **Measurement**, and re-arm after
any provider or model change.

Also 2026-09-22 — three faults behind "I still can't get simple answers",
diagnosed from the widget transcript by replaying each turn stage by stage:
a typo defeating the capability match that retrieval had NOT been fooled by,
five missing prepositions in the capability pivot, and — the real one — the
steps of a procedure being the least retrievable part of it
(`retrieval_db.complete_procedures`). The MyCheckr/Linux fault was then
fixed too, and was not a ranking fault: BM25 tokenised the query and the
corpus inconsistently, so `linux?` scored 0.0000 and the most specific
term in the question was discarded. Reranked R@5 and R@8 0.880 → 0.920,
MRR 0.830 → 0.843. Tests **218/218**, 1 skipped; scenarios 79/79. (The total moved 219 → 215
→ 218 across the day: `test_rrf` gained a process boundary, where it runs
16 functions instead of the 5 that were silently being collected, and
`test_model_routing` added one more own-process entry. A falling total is
not automatically a regression here.)

Previously: 2026-09-19 (second pass) — the two remaining classifiers
were rewritten from lists of observed phrasings into general rules after ten
scripted customer conversations found four more gaps in an afternoon; those
conversations now score 58.2% -> 100% and live at `src/tests/run_scenarios.py`
as a standing check. Earlier the same day: the conversational deferrals from
the v16.5 run were closed: the follow-up misclassification found in the post-`de5a37f`
logs, the scope-blind refusal list, the off-by-one pronoun gate, and the
clarify options neither client read. `fastapi`/`httpx` are now in `.venv`,
so the 21 tests that used to skip run: **214/214 pass, 1 skipped** (the
browser UI test, which needs a live backend). Still nothing verified against
a real generation — the gateway is NXDOMAIN, re-checked today.

Previously: 2026-09-18 (Friday review) — pricing-question deflect
bug fixed (`15aae05`); reranker benchmarked and made an operator profile
choice (`0015564`, `11ead6a`), which stays unverified end-to-end pending
the LiteLLM gateway; v16.5 pipeline-hardening deferrals still open (see the
dated section below).

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

  **Pinned by tests 2026-09-22.** The rescue was inline in
  `retrieve_from_db` and nothing asserted it, in a function whose own
  comments record the candidate margin being silently thrown away by a
  later edit *twice*. Extracted to the pure `apply_arm_guarantee` and
  covered in `tests/test_rrf.py`: the rescue itself, that it extends rather
  than reorders the RRF head, that it stays cheap (a few extras, not 2x the
  window), the NV9S rank-20-of-46 case reconstructed from the measured arm
  ranks, and the two knob defaults -- so a revert to `ARM_GUARANTEE=0` or a
  raise to `CANDIDATE_MARGIN=2.0` fails the suite instead of quietly
  restoring the refusal.

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
    2. ~~It intercepted a **pricing** question (no pricing anywhere in the
       corpus) that should have been refused outright~~ — **FIXED
       2026-09-17** via `15aae05`. Root cause was upstream of the FAQ store:
       `is_sales_question`'s `_CROSS` regex carried only catalogue-navigation
       vocabulary and missed 9/10 real commercial questions (price, cost,
       quote, lead time, buy, purchasing, reseller), so `_sales_answer`
       returned `None` before `sales_mode` was ever consulted and the
       pipeline answered from the manual instead — "The NV9 Spectral is
       offered at a mid-range price, delivering casino-level security", with
       six pages cited behind it. New `sales.is_commercial_question`, kept
       separate from `is_sales_question`, deflects commercial questions
       under every `sales_mode` including the default. Tests 201/201 at the
       time.

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

## Deferred from the v16.5 pipeline-hardening run (opened 2026-09-17)

Everything below was found while fixing something else, measured, and then
NOT done — either because it was out of scope for the change in hand or
because the evidence said a different fix was better. Each carries the
measurement that justifies it, so a future session can act without redoing
the work. Branch `experimental/pipeline-hardening`, PR #13.

**Conversation**

- ~~Neither client reads `clarification_options`~~ — **FIXED 2026-09-19.**
  Forwarded from `/widget/ask` and from the `/ask/stream` meta event (a
  second, separate allowlist that would otherwise have disagreed with the
  blocking path), rendered as chips in the widget through the existing
  `chips()` helper and as buttons in the console through the same idiom as
  `product_options`. Tapping one sends it as the next message, which is the
  path a typed answer already takes. Verified in a browser against a stubbed
  backend: the chip row renders "Which did you mean? / NV9 Spectral /
  NV9USB+" and a tap fires a fresh `/widget/ask`.

  Two things came out of doing it. The refusal-path clarify now builds
  `ambiguous_in_domain` options (product labels) rather than `followup`
  ones (the visitor's own earlier questions), because the question it asks
  now ends "...or which model you mean?"; it falls back to `followup` when
  nothing maps. And `_product_label_for_source` had a hardcoded list of four
  products, so NV9, SMART Coin System and BV30 sources returned None and the
  chips were silently EMPTY for most of the range — it now reads the
  catalogue first, and every mapped manual now resolves — NV9 Spectral,
  NV9USB+, NV200S, BV30, SMART Coin System, MyCheckr, MyCheckr mini,
  MyConnect. (An earlier note here claimed BV30 still returned None. That was
  wrong: the check had used an invented filename, "BV30 Range User
  Manual-v1.pdf", where the real one is "BV30 User Manual-v1.pdf".) Two
  deliberate blanks remain: `note_val_general` and `nv22s` have no sources
  attached at all, and the shared-doc buckets are named "General …", which
  the lookup skips because a bucket is not a choice to offer.
- ~~The refusal's suggestion list is unranked and scope-blind~~ —
  **FIXED 2026-09-19** via `_refusal_suggestions`. The real fault was
  narrower than recorded: `list_for_product` filters correctly when a
  product IS in scope and returns the whole file when one is not, which is
  exactly the unscoped turn where the visitor has given us least. So an
  unscoped refusal now derives its scope from the documents retrieval just
  cited (`catalog.product_for_source`), then ranks what survives against the
  question. Checked against the real FAQ store: the 1969 case ("what is RMS"
  with the SCS manual cited) now offers three SMART Coin System questions.

  The floor is deliberately soft, and that is a decision rather than an
  oversight: by the time a refusal is being written, `suggest_candidates`
  has already declined everything at its 0.70 floor, so reusing that bar
  would empty the list on essentially every refusal. Ranking is lexical
  only — `faq_store._semantic_scores` embeds against the WHOLE store
  regardless of the pool it is passed (measured 10.7s cold, 2026-09-19),
  which is not a cost to add to a refusal. The consequence is pinned in
  `tests/test_refusal_suggestions.py`: a pure paraphrase ("works offline" vs
  "internet connection") is NOT reordered, and scope filtering is what
  protects that case.
- ~~A fresh question was read as a follow-up~~ — **FIXED 2026-09-19.**
  Found by reading the six turns logged after `de5a37f` shipped:
  `logs.jsonl` 2026-09-17 14:01, "how sturdy are nv9 st", was answered with
  a clarifying question about the PREVIOUS turn's pricing question.
  `is_followup_turn` reads `resolved_query != raw_query` as evidence the
  CONVERSATION rewrote the question — sound until `86d6728` started
  appending the selected product for retrieval, after which the two differ
  on nearly every scoped turn. `main.py` now freezes `condensed_query`
  before that rewrite and hands the gate that instead. A second fault in the
  same branch: the clarify text quoted `history[-1]["q"]`, so it named
  whatever was asked last rather than the turn that failed; it now quotes
  the question just asked, keeping a `_CLARIFY_MARKERS` phrase so the
  "never twice in a row" guard still recognises its own wording.

  Measured by replaying 19 turns — the logged failures plus unseen questions
  in both registers, an installer naming parts and a newcomer describing a
  fault: 11 turns newly classified correctly, 8 unchanged, 0 regressions.
  Also fixed in passing, found by the unseen questions: the "what about X"
  opener group was single-use, so "ok and what about the mini" matched
  nothing while "ok what about the mini" matched. People stack those openers
  constantly.

- ~~The follow-up classifier was a list of phrasings customers had already
  used~~ — **REPLACED 2026-09-19 with four general rules.** Ten scripted
  customer conversations (one per product, `src/tests/run_scenarios.py`)
  found four more gaps in an afternoon: bare "that" ("is THAT configurable
  from the host"), bare "one" ("which ONE would you recommend"), and the
  eight-word length gate failing in both directions — "does it need a
  separate supply from the host board" is nine words and read as standalone,
  "can my staff use it without any training" is eight and read as a
  follow-up. Patching four more entries into the list would have bought a
  fortnight.

  `has_reference_markers` is now four rules over CLOSED word classes, with
  `why_reference_markers` returning which one fired so a misjudgement can be
  argued about without re-deriving it from regexes:

    R1 opener            discourse connectives, stackable, plus the
                         elliptical "what about" frame
    R2 pro-form          a pro-form doing referential work, in a question
                         that does not name a product of its own
    R3 discourse deixis  "the above", "step 3" / "step three", "as mentioned"
    R4 continuation      "tell me more"

  R2 carries the work the word count was proxying for. A question that NAMES
  ITS SUBJECT is self-contained whatever pro-forms it also holds, and the
  product list comes from the catalogue, so adding a product in the console
  teaches the classifier too. Three structural tests keep the false
  positives down, each of which cost a debugging round: expletive "it" is
  not referential ("how long does IT take to..."), an antecedent in the same
  sentence resolves the pro-form ("...or does IT self-level", "IF the
  machine rejects a note, does IT..."), and "one" is only a pro-form when it
  heads an elided noun phrase ("which one", not "share one RS232 bus" and
  not "day one").

  Scored on the ten conversations, 79 checks: **58.2% → 100%**. Two of the
  79 labels were revised after the rule disagreed with them and the rule
  turned out to be right ("will this fit under a standard shop counter" and
  "what would one of these cost us at fifty units" both need the previous
  turn); both revisions are recorded in the files with their reasoning.
  `tests/test_general_classifiers.py` pins the rules and, mostly, the false
  positives.

  **Their job narrowed on 2026-09-22.** These rules no longer decide whether
  the rewriter runs — that gate is gone, and the prompt decides (see the
  condensation item under *Retrieval and ingest*). They still gate the
  deterministic combined-query fallback and feed `is_followup_turn`, where
  being wrong costs one phrasing rather than the whole rewrite. Worth
  knowing before extending them: a miss is no longer fatal, so the pressure
  that produced `de5a37f`'s marker patch is off.

- ~~`is_commercial_question` missed "fees"~~ — **FIXED 2026-09-19**, and
  generalised for the same reason. It was a list of observed words, so "are
  there any recurring fees for using it" went to the model — the same
  failure `15aae05` was written to stop, through a word nobody had typed
  yet. It is now the money/commerce field in four groups (what it costs, how
  to buy, when it arrives, who from), matched on stems so "fee" covers fees
  and "licen" covers licence/license/licensing.

  Words this corpus uses in a NON-commercial sense are excluded and pinned
  by test: a coin hopper PAYS OUT, a serial link has a baud RATE, a battery
  takes a CHARGE, "in ORDER to" is throughout the manuals, and a validator
  FEEDs notes — that last one is not hypothetical, the `fee` stem matched
  "feeds" and routed a note-handling question to sales until scenario 03
  caught it.

- ~~A refusal could not tell "we never had this" from "the manual sent us
  somewhere we do not hold"~~ — **FIXED 2026-09-21** (`crossrefs.py`,
  `GET /admin/crossrefs`). Found in a real transcript: "what is the screen
  size of the MyCheckr?" is refused, correctly, because MyCheckr User Manual
  p5 says *"Refer to MyCheckr Range Technical Data for the dimensions of the
  device"* and that sheet is not ingested — while "what is the weight?" is
  answered. The refusal now names the document, read from the chunks
  retrieval actually returned, so relevance is not guesswork.

  The scan over the live index finds five referenced-but-absent documents:
  **MyCheckr Range Technical Data** (both MyCheckr manuals, dimensions),
  **Service Guide** (NV200S p84, jam recovery), **BNF Path Guide** (NV200S
  p44), **Lock Specification** (NV200S p38), and one false positive,
  "Action Data" (an ICU API section name). Each row carries the sentence it
  came from so a false positive costs an operator seconds. Getting the
  Technical Data sheet ingested is the single highest-value corpus action
  on this list.

  Three filters earned their place, each against a real false positive: the
  title is matched CASE-SENSITIVELY (without that, "refer to the relevant
  manual" was a document, four times), internal pointers are excluded ("see
  the table below for screw specification"), and a reference that spells out
  its own document's name is a section pointer, not a gap.

- ~~"Does X work with Y" was refused with the answer on screen~~ — **FIXED
  2026-09-21.** "does ICU work with linux?" was refused while retrieval
  returned *Accessing my device in Linux Environment* at rank one, and the
  refusal then offered "How do I access my ICU device in a Linux
  environment?" as a suggestion.

  `text_utils.capability_target` reads the target off a closed set of frames
  (pivoting on the last companion preposition, so "can I use MyCheckr with
  linux" yields "linux" and not "MyCheckr with linux"), and
  `main._capability_reply` reports what the corpus holds about it in two
  tiers: a document TITLED for the target, or a NUMBERED PROCEDURE whose
  body names it — which is how Android is answered, since it appears only in
  the body of `ICU_Network_API` p14. A passing mention is neither and is not
  reported.

  Answered before the clarify gate, because a question we can answer must
  not be answered with a question. The sentence is composed rather than
  generated and claims only that a procedure exists, in this document, on
  this page — never that the product is compatible, which is not ours to say
  and is what a wrong answer here would cost a site visit. Where nothing is
  documented the refusal says so explicitly, as a gap rather than an answer
  either way.

  Deliberately NOT done: enumerating which platforms we do document ("I have
  Linux and Android"). Deriving that needs either a hand-kept list of
  platform names or a fuzzy guess at which documents are integration
  documents, and both are the kind of thing this codebase has just spent two
  days removing.

- ~~**No answerability turn type.**~~ — **PARTLY DONE 2026-09-21**, see the
  switchboard entry under **Resolved**. `src/answerability.py` now makes one
  classification per turn (stated / documented_elsewhere / inferable /
  advisory / unanswerable), `main.query` dispatches on it instead of four
  features each sniffing the question, and the kind is on the response as
  `answerability` so the console can tell a corpus gap from a question no
  corpus answers.

  **Still open: the ADVISORY route.** The outcome is classified and nothing
  acts on it — a recommendation ("I want to run a SCS with a note recycler,
  what do you recommend?") is now correctly *labelled* and still gets the
  refusal. Routing it needs slot-filling ("waiting on: note/coin, depth,
  cashless") resolved against the question that was asked, and memory holds
  only `{q, a}` strings. `sales.py` already has `build_index`, `find_by_spec`
  and `compare` over the spec tables, so the missing piece is the pending
  state, not the matching. Large, and unchanged in size by the switchboard.
- ~~`_SHORT_ONLY_PATTERNS = {6}` is off by one~~ — **FIXED 2026-09-19**
  (`{6}` → `{7}`). It gated the sentence-initial pronoun pattern while the
  comment beside it described gating the anywhere-pronoun one, so it was
  wrong in both directions: a long standalone question containing "it" read
  as a follow-up, and a sentence-initial pronoun stopped counting past eight
  words — which is how people actually describe a fault ("it keeps rejecting
  the same note even after I cleaned the note path").

**Retrieval and ingest**

- ~~**A document tagged to a product is not reached by that product's
  questions.**~~ — **FIXED 2026-09-22**, see **Resolved**. Two causes, and
  the first one was not about this case at all: BM25 was tokenising the
  query and the corpus inconsistently, so `linux?` scored 0.0000. The
  residue, after that fix, is that the cross-encoder still (correctly)
  drops a document that never names the product being asked about, and
  that is answered from the operator's own tagging rather than by forcing
  candidates past the rerank cut.

- ~~**Condensation can destroy a working query, and is gated by the wrong
  thing.**~~ — **FIXED 2026-09-22**, both faults, in the prescribed order.
  Two faults in one mechanism (`llm.condense_query`, the
  Rewrite-Retrieve-Read step over the last 2 turns). Fixing them together is
  the single highest-value retrieval change on this list.

  *It replaces rather than augments.* Measured on "what are the power
  requirements for this setup?": as typed it put the PSU page at #1 (0.9348)
  with a clean cliff to 0.0843; resolved to name both products, the PSU page
  was **not retrieved at all** and a firmware-programming page entered
  context instead. A good query can be rewritten into a worse one with no way
  back.

  *It is gated behind a regex.* `has_reference_markers()` must match or the
  rewrite is skipped entirely and the raw fragment hits retrieval. That gate
  is the non-standard part: the canonical pattern calls the rewriter
  unconditionally and lets the prompt decide, which
  `CONDENSE_PROMPT_TEMPLATE` already instructs ("if already self-contained,
  return it EXACTLY AS-IS"). The regex is a second, brittle classifier doing
  a job the model was already asked to do — it is what killed "what is the
  power required to run both at once", where no marker matched so no rewrite
  ran. `de5a37f` added set-anaphora markers, which patches the list rather
  than fixing the design.

  Done looks like: retrieve on the raw AND rewritten query and fuse (RRF is
  already there to do it), then drop the regex gate — safe only in that
  order, because fusing is what stops a bad rewrite losing the original's
  hits. Verify against `eval_cases_retrieval.json`, which needs no provider.

  **What shipped.** `retrieval_db.retrieve_fused` retrieves on each distinct
  phrasing and merges; `main.py` passes the rewritten query and the query as
  typed, and the gate is gone from `condense_query` — the prompt decides, as
  it was already asked to.

  *RRF was the wrong tool for the merge, twice.* "Fuse with RRF" was the
  plan and it loses the rewrite's hits, which is the same bug pointed the
  other way. A rewrite drops **9–13 of the 16** candidates the question had
  as typed (measured over the live index), so fusing two 16-lists into 16
  slots evicts about half of each. On *"what is the power required to run
  both at once"* that evicted the only chunk that answers it — NV200S p69,
  `24VDC / 3.5A ... whilst the SCS requires 24V DC 7.5A`, reranked 0.9975 —
  leaving generic power-supply boilerplate at 0.9936. Reserving an equal
  share of the window per query failed the same way: the reranker's best
  pick routinely sits at retrieval rank 9–16, outside any half-share.

  *What works is strictly additive*, and it is the 2026-08-28 lesson again:
  keep the primary query's candidates **in full** and add the top
  `RETRIEVAL_ARM_GUARANTEE` of each other phrasing, through the same
  `apply_arm_guarantee` helper as the bm25/dense arms. Ordering is not fused
  at all — `main.py` reranks the full candidate list and only then truncates
  to `CONTEXT_K`, so membership is the only thing that matters, and an
  ordering pass's one real effect would be deciding who gets trimmed.

  Deterministic measurement, 7 conversational queries over the live index:

      candidates the rewrite had and fusion lost : 0  (was ~8 under RRF+trim)
      candidates added                           : 0-3, avg 1.7 per query
      queries where a rescued candidate reached the prompt : 2 / 7
      queries where the reranked TOP changed     : 1 / 7  (0.9954 -> 0.9960)

  *Removing the gate needed a third change, not in the original plan.*
  `is_followup_turn` read `resolved_query != raw_query` as proof the
  conversation was needed. That was survivable while a regex gated the
  rewriter; with it running on every turn, any cosmetic difference — and
  `_normalize_query` alone lowercases `DEFAULT LOGIN???` — makes a fresh
  question read as a follow-up and earn a clarifying question about the
  previous topic. That is the 2026-09-19 fault arriving through a different
  door. The clause now asks whether the rewrite *added content words that
  came from the history*, so reflowing and repunctuating are correctly
  ignored. It also closes the trap in
  `test_product_context_must_not_be_what_makes_a_turn_a_follow_up` for an
  unrelated product — though **not** when the appended product is the one
  already under discussion, which is the normal case in a product chat, so
  freezing `condensed_query` remains the actual fix and the test now pins
  that residue honestly.

  **Not fixed by this, and worth separating out:** the cross-encoder still
  prefers boilerplate that merely mentions a term over the table that
  answers the question. `what is the pinout for the NV9USB+` ranks p18
  ("pin to pin compatible", mounting prose) at 0.9931 above p44, which *is*
  the MDB pin-assignment table. Fusion puts both in front of the reranker;
  it cannot make the reranker choose. Same family as the heading-list and
  content-free chunk items below.
- **Multi-entity questions are not decomposed.** "NV9 Spectral with Note
  Float" is two entities; one embedding blends them and favours chunks that
  weakly mention both over the best chunk for each. `sales.py` already does
  per-product decomposition for comparisons — same shape.
- **Nested product names collapse.** "Note Float" and "Multi Note Float" are
  different products (p25 of the NV9 manual lists them at 1.04 kg and
  1.2 kg) and the system blurs them. Same class as the MyCheckr / MyCheckr
  Mini item already recorded above.
- **50 duplicate chunks (2.9%).** Exact-body repeats — a liability notice
  5x, a command acknowledgement 5x, the Supply Voltage table 4x. Two of the
  eight context slots for a BV30 question went to the same escrow text.
- **7 heading-list chunks.** Bodies that are a page's own table of contents
  ("Additional Features / Typical Applications / Component Overview").
  Small, and harder to detect than the footers fixed in `9c138d3`.
- **A BV30 coverage miss.** "does the BV30 support polymer notes?" returns
  cashbox and escrow text in the top 3 — nothing about note handling. BV30
  also has the highest share of content-free chunks (15%), so this may be an
  extraction gap rather than a ranking one.
- **`nv9_spectral,biometrics_general` tagging.** Every chunk of the NV9
  Spectral manual carries a biometrics tag. A note validator is not a
  biometrics device; if this is wrong it widens retrieval into unrelated
  material.
- **Existing documents still carry running footers.** `9c138d3` strips them
  at ingest, so this only takes effect on a reindex
  (`python reindex.py --from-store`, ~40 min for 11 docs).

**Measurement**

- ~~**`eval_baseline_retrieval.json` does not exist.**~~ — **ARMED
  2026-09-22**, the first hour the LiteLLM gateway was reachable again.
  30 cases x 3 repeats = 90 real generations:

      79/90 individual runs passed (88%)
      26/30 cases stable across 3 runs (87%)   <- the recorded pass_rate

  It is a real capability and not an outage recorded as one: every case
  returned a genuine answer with a retrieval score in the 0.96-0.99 band.

  **THE BASELINE IS PROVIDER-SPECIFIC, and this is the caveat that matters.**
  It was recorded against **DeepSeek**, not the on-prem gateway —
  `provider=deepseek` on 69 of the 90 runs, because the answering path uses
  the DEFAULT role and `PROVIDER_ROLE_DEFAULT` is deepseek; the gateway is
  assigned to `advanced`/`reasoning` only. Console model selection (added
  the same day) now makes switching the default a one-click action, and
  doing so invalidates this baseline. **Re-arm after any provider or model
  change**, with the same command, or a comparison will attribute a change
  of model to a change of code.

  Four cases are unstable across the three repeats and are the ones to look
  at first, not a reason to distrust the number:

  | case | note |
  |---|---|
  | "...Twin SMART Coin System baseplate - how many screws" | the multi-constraint spec lookup |
  | "what are the pin assignments for that interface" | R3 deixis onto a near-all-numbers pin table — answered with a clarify, not the table |
  | "ok and what about the full size one?" | grader returned nothing; the entity SWITCH case the cases file already flags as gradeable only by hand |
  | "how do I reset the password on my Cisco router" | refused correctly; the case's expectation and the refusal wording disagree |

  Recorded against the code in this commit. The backend was restarted onto
  it first — the process serving :8000 was from the previous evening, and a
  baseline taken against it would have encoded the old BM25 tokenisation and
  no procedure completion as the reference, which is worse than no baseline
  because it looks authoritative.
- **No conversational layer in the eval suite.** — **CASES ADDED
  2026-09-22; still unarmed.** Every case was a well-formed standalone
  question (`new_session: true` on all 19), so the failure this whole run
  was about — a follow-up that dies — could not be caught. Wanted cases for
  follow-ups, anaphora, and the refusal path; `eval_cases_retrieval.json`
  now has 30 cases, six of them conversational follow-ups, added while
  fixing the condensation item above:

  | follow-up | what it pins |
  |---|---|
  | "what is the power required to run both at once" | set anaphora, PENDING's own example — matched no marker, so no rewrite ran |
  | "how much does it weigh on its own?" | R2 pro-form onto a spec-table row (`1.05`) |
  | "what are the pin assignments for that interface" | R3 deixis onto a near-all-numbers pin table |
  | "ok and what about the full size one?" | R1 stacked openers + an entity SWITCH (graded: `sources_any` cannot separate the two MyCheckr manuals) |
  | "how do I reset the password on my Cisco router" | the refusal path *inside* a conversation — an ungated rewriter must not drag it into the corpus, and fusion must not rescue enough junk to clear the gate |
  | "...operating temperature range for the MyCheckr" | a self-contained question with history present: the new door onto the 2026-09-19 fault |

  The runner already threaded a session across consecutive cases, so a
  follow-up is a case with `new_session` omitted — which makes this list
  **order-dependent**, noted in its `_README`. Every expectation is grounded
  in a chunk read out of the corpus first, not guessed. `--selfcheck`
  passes at 30 cases.

  **Armed 2026-09-22** with the item above — all 30 cases, including the six
  conversational follow-ups, are in the recorded baseline. Two of the four
  unstable cases are follow-ups (`what are the pin assignments for that
  interface`, `ok and what about the full size one?`), which is the
  conversational layer doing exactly the job it was added for: it is the
  part of the suite that does not yet hold still.
- **Nothing since `ea4320a` is verified end to end.** The numbered context
  passages, the prompt changes and the clarify gate are all unit-tested and
  none has seen a real generation, because the provider is unreachable.

**Serving**

- **The widget does not stream.** `/query/stream` exists with sentence-level
  `StreamGrounder` verification; `groundedops-widget.js:1610` posts to the
  blocking `/widget/ask`. The 260 chars/second reveal added in `152b960` is
  presentation only. Real token streaming needs the generation call pushed
  below the quota gates, which `widget_api.py` warns is not a change to make
  unverified.
- **`reanswer.py`'s passage-selection is unverified end-to-end.** Added
  2026-09-17 (`0015564`) after benchmarking three cross-encoders showed
  reranking was ordering, not losing, answers: r@8 was 100% across all three
  models over the 19-case retrieval suite (`tools/bench_reranker.py`), so the
  fix re-reads the already-retrieved passages with a model call
  (`reanswer.py`, wired to the console test chat as "That didn't answer it -
  read the sources again") instead of buying a more accurate reranker. The
  prompt construction, response parser and merge-top-3 fallback are
  unit-tested; what a real model actually picks when asked "which passages
  answer this" has never run, because the LiteLLM gateway is still NXDOMAIN
  (re-checked 2026-09-18, see the infrastructure item below). Done looks
  like: exercise the "read the sources again" button against a live NV9
  question once the gateway resolves, and confirm the selected-passage
  answer beats the merged-top-3 fallback on at least the case that motivated
  it ("can I use an nv9 spectral with note float?" — the disabling-firmware
  passage ranked #1 but the served answer came from passage #2).
- **`escalated_to_deepseek` is now a misleading field name.** `f51727c`
  moved escalation onto the assigned backup role; the response key still
  says DeepSeek. Kept for client compatibility.
- **A 0.0009-grounding answer was served.** `logs.jsonl:1970` — correct, and
  rescued by lexical containment, which is the rescue working as designed.
  Worth one look at how far that rescue can carry an answer the NLI model
  scored at essentially zero.

**Infrastructure**

- ~~**The LiteLLM gateway is gone from DNS.**~~ — **RESOLVED 2026-09-22**,
  by whoever owns that host; nothing in this repo was involved.
  `ukman-hsp-litellm.local.innovative-technology.co.uk` now resolves to
  **10.10.76.7** from ukmandc01, and `:4000/v1/models` answers in 16ms with
  `itl-gpt-pro` and `itl-gpt-flash`. The `OPENAI_BASE_URL`-to-an-IP
  workaround is no longer needed. The repo side was already in place —
  `OPENAI_BASE_URL` has been configurable since the note in `llm.py:20`,
  and `src/.env` already points at the gateway — so the only thing that
  was ever missing was the DNS record.

  It unblocked immediately, and the first end-to-end run of the inference
  contract found two defects in it that no unit test could have. See the
  entry under **Resolved**.
- **`release.py` cannot cut a drop while two READMEs share a basename.**
  `python release.py --minor` refuses with `README.md <-> tools/README.md`,
  because the legacy drop is FLAT and one would silently overwrite the
  other. The refusal is right; the flatness is the bug. v16.3 was therefore
  released by bumping `VERSION` by hand, with no `src/legacy/v16/v16.3`
  snapshot. Fix by snapshotting into subfolders that mirror the repo, not
  by renaming a README — the collision will recur for every `tools/` file
  that shares a name with a root one. Nothing is lost meanwhile: the drop
  is a convenience copy, and git holds the real history.

- **`backup/pre-build-drop` still pins 290MB in `.git`.** The safety net for
  the `f33b22b` rebase. Once the rebase is trusted:
  `git branch -D backup/pre-build-drop && git reflog expire --expire=now
  --all && git gc --prune=now`.

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

- **2026-09-22 — the gateway came back, and contract 2 met a real model for
  the first time.** DNS was fixed by whoever owns the host (see
  **Infrastructure**); nothing here caused or fixed it. What it unblocked
  was the verification every feature on this list has been waiting for, and
  the very first run found TWO defects in the inference contract that the
  15 unit tests could not have, because both concern what a real model
  actually writes:

  1. **A semicolon was demoting the attribution sentence to a premise.**
     `itl-gpt-flash` closed with *"This is my reading of the documentation
     and not a stated claim; the team can confirm."* — the exact sentence
     `build_inference_prompt` ASKS for. `_FRAME_TURNS` included `;` on the
     reasoning that a frame turning mid-sentence is smuggling. True of the
     grammar, false of the usage: scored as a premise it came in at 0.0004
     and sank an otherwise sound answer. The rule was rejecting the
     contract's own required output. The conjunctions stay — *"doesn't say,
     BUT it has a thermal printer"* is still caught.
  2. **The fact sentences described the passages instead of stating the
     facts.** *"The passages describe…"*, *"They also state that…"* —
     nothing entails a claim ABOUT a document, so they scored 0.0043.
     `build_answer_prompt` has forbidden that style for a long time
     ("NEVER refer to the source material"); the inference prompt had not
     inherited the rule. Added.

  With both fixed the same draft scores **0.9448** on its premise and both
  frames are recognised.

  **STILL TOO STRICT, and this is the honest state: 0 of 2 real drafts
  served.** The remaining rejections are on connectives and paraphrase, not
  invention — `introduces prevent, since, usable`. `since` was added to the
  connective vocabulary (same class as `within`, already justified).
  `usable` and `prevent` were deliberately NOT added: they carry meaning,
  and loosening the safety list until one sample passes is how a lock
  becomes decoration. Tuning past this point needs the inference eval cases
  this file already calls for, measured — not a judgement about whichever
  draft happened to be on screen. The lock does work: it caught a genuine
  invention on the BV30 draft (`introduces machine, meet`).

- **2026-09-22 — choosing a model in the console configures every route.**
  Providers were settable per job; MODELS were not settable anywhere.
  `ONLINE_<PROVIDER>_MODEL` was read from the environment at call time, so
  choosing which model answers meant hand-editing `.env` and restarting,
  and the console showed nothing about what was in force.

  The cost was live the day the gateway returned: the ADVANCED job was
  pointed at `itl-gpt-flash` on the on-prem gateway while DEFAULT and
  BACKUP were still routed at DeepSeek — so nearly every customer question
  went to a metered API while the local gateway sat idle. Nothing was
  misconfigured; half the configuration had never been written.

  Two levels, and the cascade is the point. A **baseline** per provider
  (`ONLINE_<PROVIDER>_MODEL`) that every job using that provider follows,
  and an optional **per-job override** (`MODEL_ROLE_<ROLE>`) where a job
  should differ — which is what keeps the deliberate split available
  (extraction on a fast model, the inference contract on a stronger one).
  Clearing an override is not the same as setting it to the baseline's
  current value: an inheriting job keeps following LATER changes, which is
  what makes choosing once keep working.

  Stored in `.env` via `keystore._rewrite_env_line`, exactly as the
  provider assignments already are, so a change applies to the next
  question with no restart and survives one. `llm._online_provider_model`
  asks `keystore.model_for_role` rather than reading the variable itself,
  so the model the console displays is the model that goes on the wire —
  two readings of one setting is how they drift.

  The picker offers the provider's OWN list where it can report one
  (`GET /admin/keys/models/{provider}`; the gateway returns `itl-gpt-flash`
  and `itl-gpt-pro`), and falls back to a typed box otherwise — a provider
  that cannot list its models must not become one whose model cannot be
  changed. Same reasoning that made the reranker a profile rather than a
  free-text model name. 9 tests in `tests/test_model_routing.py`, writing
  to a scratch env file so they can never touch the real `src/.env`.

- **2026-09-22 (second pass) — BM25 was reading a different alphabet from
  the corpus.** Chased from "Can I use MyCheckr with linux?" returning
  MyCheckr manuals; the actual fault has nothing to do with MyCheckr,
  Linux, ranking weights or the reranker.

  Both sides tokenised with a bare `text.lower().split()`, so a token kept
  whatever punctuation touched it. The query therefore asked BM25 for the
  term `linux?`:

      get_scores(["linux"])   max 9.5688   top 3 = the Linux document
      get_scores(["linux?"])  max 0.0000   not in the idf table at all

  An unseen term contributes nothing, so the question silently became
  "can i connect to mycheckr using" — and `idf("linux")` is **5.018**
  against `idf("mycheckr")` **2.233**, so with the term intact the right
  document wins comfortably. **This was never about Linux.** The last word
  of a question collects the "?" and is very often the most specific term
  in it: "does it support ccTalk?", "what is the weight of the NV200S?".
  The corpus side had the mirror image, where "environment:" and
  "environment" were two different terms.

  `_bm25_tokens` is now shared by both sides and strips only LEADING and
  TRAILING punctuation, keeping "+" everywhere. Internal structure is
  load-bearing here: `nv9usb+` must not collapse into `nv9usb` (different
  products), `192.168.137.8` must stay one token, `linux-based` must not
  become two — stripping to `\w+` would break all three.

  **Measured on the 19-case retrieval suite**, and the gain is at the
  stage that reaches context:

  | stage | R@1 | R@3 | R@5 | R@8 | MRR | misses |
  |---|---|---|---|---|---|---|
  | retrieved, before | 0.520 | 0.800 | 0.880 | 0.880 | 0.671 | 3 |
  | retrieved, after | 0.440 | 0.840 | 0.880 | **0.920** | 0.652 | **1** |
  | reranked, before | 0.800 | 0.840 | 0.880 | 0.880 | 0.830 | 3 |
  | reranked, after | 0.800 | **0.880** | **0.920** | **0.920** | **0.843** | **2** |

  Every reranked metric improves or holds. The pre-rerank R@1 dip is the
  expected trade: rare terms no longer score zero, so more genuine
  candidates compete for rank 1 while coverage rises, and the reranker —
  whose job that is — sorts it out.

  **Found on the way, and it matters more than the test count suggests:
  six tests in `test_rrf.py` were silently not running.** The file was
  collected in-process, and only the 5 functions defined BEFORE its
  module-level `from retrieval_db import apply_arm_guarantee` (line 72)
  were ever collected — no load error was printed, so the suite reported
  them as a clean pass. Everything after that import, including
  `test_nv9s_weight_case_survives_the_cut`, which pins the RRF fusion bug
  in the **Blocking** section above, had not run since it was written.
  `retrieval_db` is now in `run_tests.py`'s own-process list, on the same
  native-module grounds as `grounding`, `ingest` and `router`, and the
  file runs all 16. This is why the suite total goes 219 → **215** while
  coverage goes UP: 5 individually-counted entries became one
  own-process line that actually executes 16.

- **2026-09-22 (second pass) — a document the reranker is right to drop.**
  The residue after the tokenizer fix. The Linux document now reaches the
  candidate pool for "Can I use MyCheckr with linux?" and still does not
  survive the rerank cut, because it never uses the word "MyCheckr" and
  the cross-encoder correctly scores it off-topic against a question that
  does. The knowledge that it IS a MyCheckr document lives only in the
  tagging the operator set at upload.

  Forcing it past the cut is the experiment already run and rejected here
  (margin 2.0: two regressions, mean grounding 0.993 → 0.954), so
  `retrieval_db.sources_titled_for` answers from the tagging instead and
  touches neither ranking nor context. It is the LAST tier of the
  capability scan — consulted only when the retrieved chunks showed
  nothing — and scoped to the product, which is the whole safety of it:
  unscoped, the same lookup would claim a BV30 Linux procedure that does
  not exist. Verified against the real tagging: `mycheckr` and
  `mycheckr_mini` find it; `bv30` and `nv200s` do not.

  **Correction to the note this replaces:** the earlier entry said the
  document "IS filed under `mycheckr`" on the strength of chunk metadata.
  `catalog.product_for_source` reports it as `biometrics_general` alone.
  The two disagree because the catalogue maps filename substrings for
  ingest defaults, while the chunk metadata records what the operator
  actually chose at upload — and it is the operator's assignment that
  retrieval scopes on, so that is what this reads.

- **2026-09-22 — three faults behind "I still can't get simple answers",
  from the widget transcript of the previous evening.** Diagnosed by
  replaying the four failing turns stage by stage (retrieval → target
  extraction → switchboard) rather than by reading the code, which is the
  only reason they came apart into three different bugs.

  1. **A typo defeated the capability match, and retrieval had not been
     fooled by it.** "does ICU lite work with andorid?" — retrieval
     returned `ICU_Network_API` p14, the Android procedure, at rank 4.
     The literal substring test in `capability_evidence` was the only
     thing that failed, and the refusal then quoted the visitor's typo
     back at them. The target is now respelled from the words in the
     chunks retrieval already returned (`_as_documented`, difflib at 0.8,
     words of 5+ characters only). Safe because the candidate set is a
     few hundred words that are relevant by construction, not the corpus.
     The old comment argued a typo SHOULD fall through "to retrieval,
     which is tolerant of spelling" — retrieval was tolerant; this was
     not.

  2. **Five prepositions were missing from the capability pivot.** "Can I
     connect to MyCheckr using linux?" found no companion preposition, so
     the target read as "MyCheckr using linux", `_names_a_product`
     rejected it, and the turn got a bare refusal with no capability line
     at all. `using|via|through|over|in` added to `_COMPANION_PREP`.

  3. **THE STEPS OF A PROCEDURE ARE THE LEAST RETRIEVABLE PART OF IT** —
     the real one. "how to get RNDIS working with linux?" retrieved the
     sentence announcing the procedure and its Important Notes, and none
     of Steps 1-4: not ranked low, ranked outside the top SIXTEEN. A step
     reads `sudo touch /etc/udev/rules.d/80-local.rules`, which contains
     no word a person would type, so BM25 has nothing to match and its
     embedding is nothing like a question's. The parts of a document that
     talk ABOUT a task out-retrieve the parts that DO it, on both
     rankings at once, and widening the candidate window cannot help.

     `retrieval_db.complete_procedures` fetches them instead of ranking
     them: a chunk whose own `section` is the parent of a step heading is
     the introduction to those steps, so the document's step chunks are
     added in document order, below everything retrieved and marked
     `fetched_by`. All of the document's steps, not only the nested ones
     — the Linux document has Steps 1-2 under the introduction and Steps
     3-4 at the top level purely because of heading styling, and half a
     procedure strands the reader mid-way.

     Also fixed on the way: `section` was being dropped from
     `retrieve_from_db`'s results, exactly as `product`/`category` were
     in v15.2 and unnoticed for the same reason — nothing downstream had
     needed it yet.

     **Measured, because it runs on every query.** `eval_retrieval.py` is
     the wrong instrument (an additive fetch downstream of it cannot move
     recall@k; it stays at R@8 0.880 / MRR 0.830). What matters is blast
     radius: `tests/measure_procedure_completion.py` reports it fires on
     **2 of 37 questions (5%)**, only on the one document with a
     step-structured procedure, adding at most 4 chunks / 1972 chars
     against a 4000-char budget. The second of those two gains Step 3,
     which ranked 11th and was being cut at `CONTEXT_K=8`.

  One self-inflicted regression on the way, worth recording because the
  trap is documented and was walked into anyway: adding
  `complete_procedures` to `retrieval_db` without adding it to the
  `_harness` stub took out NINE test files at once. `_harness` replaces
  the module with a `types.ModuleType`, so `main`'s import of a name the
  stub lacks fails at import with `cannot import name ... (unknown
  location)`. The stub is a PASS-THROUGH, matching the real contract —
  the function is additive, and a stub returning `[]` would empty the
  context of every API test and fail far from the cause.

  Tests 219/219, 1 skipped; scenarios 79/79. **Turn 2 is still not fully
  fixed** and is listed under **In progress** — the Linux document is
  tagged to `mycheckr`, so a MyCheckr-scoped Linux question SHOULD reach
  it and does not.

- **2026-09-21 — the answerability switchboard and the inference contract.**
  Two changes, deliberately in this order.

  *The switchboard* (`src/answerability.py`, `tests/test_answerability.py`).
  Four features had each grown their own sniff at the question at four
  points in `main.query()`: crossrefs and the capability scan inside
  `_friendly_refusal`, the capability scan again in `query()`, sales ~4000
  lines earlier, and the clarify gate deciding without reference to any of
  them. `classify()` is now asked once and every branch reads it;
  `_capability_reply` is an alias for the one implementation rather than a
  second copy. The kind reaches clients as `answerability`.

  *The inference contract* (`grounding.check_inference`,
  `tests/test_inference_contract.py`). Contract 1 asks whether a passage
  entails the answer, which is why "it exposes HTTP on a static IP, so a
  Windows client on that subnet can reach it" scored 0.0039 and could never
  be said. Contract 2 splits the answer into premise / conclusion / frame
  and allows ONE hedged, attributed conclusion when every premise is
  entailed, no passage the answer was drawn from contradicts it, and it
  introduces no content word those passages do not contain.

  **Off by default** (`policy.inference_mode`, console → Answering from
  inference). Not verified end to end: the gateway is still NXDOMAIN, so
  no model has ever been asked `build_inference_prompt`. What IS verified
  is the gate — 15 rule tests with a faked scorer, plus
  `tests/sweep_inference_products.py`, which runs the same compatibility
  question shape across all eight products against the real index and the
  real NLI model: **8/8 sound inferences served, 0/8 inventions served.**

  Three faults were found by running that sweep rather than by reasoning,
  and all three made the contract look safer than it was:
  1. *No frame role.* "The documentation doesn't say" and "that is my
     reading" are claims about the corpus, not the product; nothing entails
     them (0.0015, 0.0071). Scored as premises they sank every answer —
     including the invented ones, so the rules meant to catch invention had
     never once run.
  2. *The vocabulary lock pooled every retrieved passage.* Asked "does the
     BV30 work with polymer notes", retrieval returns the BV30 cashbox
     section AND the NV200S media table listing "Polymer notes" — which
     licensed "polymer" in a conclusion about the BV30. Now scoped to the
     passages that actually supported a premise.
  3. *The contradiction check was vetoed by page furniture.* Given an
     unrelated pair this model returns high contradiction, not high
     neutral, so `max` over a chunk's sentences meant the footer "MyCheckr
     User Manual - 31" (0.995) and a section heading (0.991) could each
     veto a sound inference. Three of eight were being refused that way.
     Now checked only against the premises the conclusion was drawn from.

  Tests 218/218, 1 skipped. **Still unmeasured on answer quality for the
  inference path specifically.** `eval_baseline_retrieval.json` was armed
  later the same day, so there is now a reference to compare against — but
  it contains no inference-mode cases, and the contract was off while it
  was recorded. Writing those cases, with the HEDGE as part of the expected
  answer, is the remaining prerequisite. That is the reason for the
  default, not caution for its own sake.

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
