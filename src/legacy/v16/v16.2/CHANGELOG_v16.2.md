# GroundedOps v16.2

**Answers that were correct all along now actually reach the customer.**

Measured over 57 questions x 3 runs across every product range, 68% of
questions got an answer. The other 32% were not retrieval failures:
retrieval scored 0.93-1.00 and cited the right page, the model wrote the
right answer, and the verification step then scored it 0.0023 and threw it
away.

The cause is that these manuals keep their facts in tables, and a table row
reaches the NLI checker shredded:

    "Switch between the selected main protocol programmed to SSP |
     Powered ON | Press and hold more than 3 seconds"

An entailment model trained on prose cannot read that, and regenerating does
not help -- three retries returned 0.0113 to four decimal places every time.

v16.2 adds a second opinion for exactly that case: when the first check
cannot verify an answer, an LLM reads the page and decides. It fails closed,
so an error keeps the refusal.

    answered 39/57 (68%)  ->  55/57 (96%), zero regressions, slightly faster

Not a rubber stamp: it returned UNSUPPORTED on 2 of 24 calls, and on a
12-case prototype rejected 6/6 fabrications including two fault-code
MISPAIRINGS -- answers reusing a real row's wording against the wrong row.
A cheaper lexical rescue was tried first and rejected, because true answers
scored 0.93/0.80/0.71 on token overlap and a deliberately wrong one scored
0.78. The distributions overlap, and a mispaired fault code is the worst
thing this system could tell a customer.

## Also in this release

* **Support questions stop being answered with a product list.** "What size
  screws do I need to mount the X?" matched the cross-product sales pattern
  on "I need to" and was answered from the catalogue. 10 of 16 realistic
  support questions were misrouted; now 0 of 15, with all 8 genuine sales
  questions still caught. The sales path also ignored product scope.

* **Sales questions are now the operator's call** -- answer from the
  catalogue, reply with a fixed message, or treat as ordinary questions.

* **Retry before refusing**, bounded and configurable.

* **Duplicate page scraps dropped at ingest.** A page that introduces a
  table stored the introducing sentence twice; the copy without the table
  outranked the copy with it.

* **"Check your connection" no longer hides a quota limit.** The widget
  showed a network error for a 429 -- the real message, the reset time and
  "start a new chat" never reached the reader. Console gains a per-caller
  quota reset.

* **Console: an Advanced page** collects these switches with plain-English
  explanations, written for someone deciding whether to flip one with a
  customer waiting.

* **Docs**: README now shows the widget working, with screenshots and an
  animated walkthrough taken against real manuals.

All settings read through `policy.py` per request -- none of this needs a
restart. Tests: 179/179 pass.

## What deploys

| File | Where it goes | Notes |
|---|---|---|
| `codeql-config.yml` | replaces `.github/codeql/codeql-config.yml` | |
| `ci.yml` | replaces `.github/workflows/ci.yml` | |
| `codeql.yml` | replaces `.github/workflows/codeql.yml` | |
| `.gitignore` | replaces `.gitignore` | |
| `PENDING.md` | replaces `PENDING.md` | |
| `PROJECT_MAP.md` | replaces `PROJECT_MAP.md` | |
| `QUICKSTART.md` | replaces `QUICKSTART.md` | |
| `README.md` | replaces `README.md` | |
| `STAGING.md` | replaces `STAGING.md` | |
| `USER_GUIDE.md` | replaces `USER_GUIDE.md` | |
| `backup_daily.ps1` | replaces `backup_daily.ps1` | |
| `.env.example` | replaces `docker/.env.example` | |
| `docker-compose.override.yml` | replaces `docker/docker-compose.override.yml` | |
| `docker-compose.yml` | replaces `docker/docker-compose.yml` | |
| `install.ps1` | replaces `install.ps1` | |
| `run.ps1` | replaces `run.ps1` | |
| `serve.ps1` | replaces `serve.ps1` | |
| `_harness.py` | replaces `src/_harness.py` | |
| `accounts.py` | replaces `src/accounts.py` | |
| `admin.html` | replaces `src/admin.html` | |
| `backfill_faq_tables.py` | replaces `src/backfill_faq_tables.py` | |
| `backup.py` | replaces `src/backup.py` | |
| `catalog.py` | replaces `src/catalog.py` | |
| `chunking.py` | replaces `src/chunking.py` | |
| `db.py` | replaces `src/db.py` | |
| `docstore.py` | replaces `src/docstore.py` | |
| `embeddings.py` | replaces `src/embeddings.py` | |
| `eval.py` | replaces `src/eval.py` | |
| `eval_cases.json` | replaces `src/eval_cases.json` | |
| `eval_results.json` | replaces `src/eval_results.json` | |
| `eval_retrieval.py` | replaces `src/eval_retrieval.py` | |
| `faq_store.py` | replaces `src/faq_store.py` | |
| `grounding.py` | replaces `src/grounding.py` | |
| `ingest.py` | replaces `src/ingest.py` | |
| `jsonstore.py` | replaces `src/jsonstore.py` | |
| `keystore.py` | replaces `src/keystore.py` | |
| `llm.py` | replaces `src/llm.py` | |
| `main.py` | replaces `src/main.py` | |
| `manage_accounts.py` | replaces `src/manage_accounts.py` | |
| `manage_backup.py` | replaces `src/manage_backup.py` | |
| `memory.py` | replaces `src/memory.py` | |
| `more_context.py` | replaces `src/more_context.py` | |
| `parsing.py` | replaces `src/parsing.py` | |
| `policy.py` | replaces `src/policy.py` | |
| `quota.py` | replaces `src/quota.py` | |
| `reindex.py` | replaces `src/reindex.py` | |
| `release.py` | replaces `src/release.py` | |
| `requirements.txt` | replaces `src/requirements.txt` | |
| `retrieval_db.py` | replaces `src/retrieval_db.py` | |
| `router.py` | replaces `src/router.py` | |
| `run_tests.py` | replaces `src/run_tests.py` | |
| `sales.py` | replaces `src/sales.py` | |
| `stream_test.html` | replaces `src/stream_test.html` | |
| `structures.py` | replaces `src/structures.py` | |
| `sweep_grounding.py` | replaces `src/sweep_grounding.py` | |
| `test_queries.py` | replaces `src/test_queries.py` | |
| `test_accounts.py` | replaces `src/tests/test_accounts.py` | |
| `test_backup.py` | replaces `src/tests/test_backup.py` | |
| `test_exposure.py` | replaces `src/tests/test_exposure.py` | |
| `test_faq_display.py` | replaces `src/tests/test_faq_display.py` | |
| `test_gap_clustering.py` | replaces `src/tests/test_gap_clustering.py` | |
| `test_jsonstore.py` | replaces `src/tests/test_jsonstore.py` | |
| `test_keystore.py` | replaces `src/tests/test_keystore.py` | |
| `test_llm.py` | replaces `src/tests/test_llm.py` | |
| `test_more_context.py` | replaces `src/tests/test_more_context.py` | |
| `test_new_routes.py` | replaces `src/tests/test_new_routes.py` | |
| `test_parsing.py` | replaces `src/tests/test_parsing.py` | |
| `test_policy.py` | replaces `src/tests/test_policy.py` | |
| `test_product_delete.py` | replaces `src/tests/test_product_delete.py` | |
| `test_streaming_and_tables.py` | replaces `src/tests/test_streaming_and_tables.py` | |
| `test_token_usage.py` | replaces `src/tests/test_token_usage.py` | |
| `test_widget_export.py` | replaces `src/tests/test_widget_export.py` | |
| `test_widget_forms.py` | replaces `src/tests/test_widget_forms.py` | |
| `text_utils.py` | replaces `src/text_utils.py` | |
| `groundedops-widget.js` | replaces `src/widget/groundedops-widget.js` | |
| `groundedops-widget.php` | replaces `src/widget/groundedops-widget.php` | |
| `preview.html` | replaces `src/widget/preview.html` | |
| `widget-nginx.conf` | replaces `src/widget/widget-nginx.conf` | |
| `widget_api.py` | replaces `src/widget_api.py` | |
| `widget_config.py` | replaces `src/widget_config.py` | |
| `widget_export.py` | replaces `src/widget_export.py` | |

## Commits in this release

```
729ba69 fix(answers): a correct answer is no longer thrown away by a checker that cannot read tables
5f53d29 feat(ui): Sources collapses to a button, and drops the prose snippet
13a53b6 feat(answers): a refusal now offers a way forward, in the customer's words
641b147 perf(faq): the first question cost 118 seconds; it now costs 64ms
f1178b6 fix(answers): a table answer is one captioned table, or none
b52097c fix(faq,structures): stop answering with what is merely nearby
8af7149 fix(faq): the semantic ranker was dead for every product but the first
56cf311 fix(query): expand product aliases even when a product is already scoped
dac18af fix(ci): linear caption regex, and let an own-process test skip
0d07566 docs: map the modules this branch added, and commit the run log
61bf793 feat(ingest): detect headings by layout, and store the section as metadata
1254af6 fix(chat): make follow-ups work, and resolve product acronyms like SCS
835ef46 feat(answer): offer more context, the document, or a person after every answer
6e74205 fix(faq): stop offering table captions as suggested questions
d7118ab test(backup): rehearse restoring the two payloads the backup exists for
6cc7281 chore: track the unattended launcher, keep 107MB of PDFs out of history
f40ce3b fix(state): break the chain that turns a bad read into permanent data loss
c1a1812 fix(db): re-acquire the collection handle when another process resets it
2701551 feat(sales): answer cross-product questions; fix the FAQ harvest regression
1365920 feat: stream in the widget, halve latency, answer discovery questions
7b71d8d fix(structures): don't answer with an irrelevant table; repair wrapped words
92701ce feat(faq): put every table and checklist in the FAQ store, verbatim
38f9212 fix(ingest,answers): table-aware chunking was off for 4 of 11 documents
d5fe07f fix(answers): serve the verbatim table instead of refusing, and rank it
326f761 feat(answers): return tables and checklists verbatim from the source
bfa5225 fix(grounding,catalog): read table rows with their header; know product short codes
7629e70 fix(console): /admin/sources actually counts chunks per source
c6e0a81 feat(dev): a page for hand-testing streamed answers
1e19c0d fix(streaming,grounding): three faults the first live call exposed
68c8eac feat(answers): stream, verified sentence by sentence
d4a8a8c feat(embeddings): bge-small-en-v1.5 -> gte-modernbert-base
ea95911 feat(ingest): keep spec tables whole through chunking
d4061ee fix(grounding): the gate was reading NEUTRAL and calling it entailment
ba24b6d docs: correct the staging runbook, record the weekend's findings
13ff2eb feat(widget): export a ready-to-install WordPress plugin from the console
56e8305 fix(widget): stop the guest sign-in notice burying the menu
5f56e74 fix(retrieval): stop RRF discarding the single best chunk
364dfb4 release: v16.1 — grounding measured, questions grouped, deletion cascades
6e6c926 chore: keep handoff notes out of a public repo, explicitly
f176126 fix(data): clear the nv9st phantom key, retag 18 questions and 7 eval cases
d488145 feat(catalog): cascade product deletion, and stop filing noise as questions
88b650f feat(gaps): group reworded questions, add filter/sort, gate the chat summary
a5cc3dc feat(eval): measure the grounding threshold; it discards nothing at 0.55
194428a fix(security): actually remove the second dead lgtm tag
2ad9b98 fix(security): dismiss alert #114, drop the dead lgtm tags
a634d94 fix(security): dismiss the 2 CodeQL alerts via the API, not inline comments
68d10d9 fix(security): suppress two CodeQL false positives on HMAC session signing
332990c fix(test): give the harness a WIDGET_TOKEN_SECRET
9ecb87d release: v16.0 — accounts, encrypted backup, runtime access policy, one widget
d9bcaa1 fix(run.ps1): show the LAN IP for the admin console, not 127.0.0.1
1431560 feat(backup): encrypt archives in an authenticated envelope
0c5018f feat(backup): export and restore everything, so nothing is re-ingested
4cf38a6 feat(deploy): make staging exposure a deliberate, tested decision
e628de8 feat: accounts, access policy, and one widget across every surface
eab36b0 Merge origin/main: keep GitHub's CodeQL workflow, add the path filter
a11108e ci: own CodeQL workflow so src/legacy can be excluded
9a37d9e fix(security): resolve documents by enumeration, not path construction
4c9ffa2 fix(build): drop the frontend image, repoint the widget service
b47915a fix(security): close the remaining live CodeQL findings
c9c97d1 fix(ci): run harness-based tests in their own process
c2de389 fix(ci): install the deps test_new_routes.py actually needs
542b27e release: v15.2 — retire the React SPA, fix CI, address CodeQL findings
b8205f7 docs: add PENDING.md, the working list of open items
2b52980 release: v15.1 — three thresholds on uncalibrated scores
2ebbee9 docs: archive v14.0 and v15.0 drops
4986c2c feat: make the index disposable and retrieval measurable
```

## Verification

<!-- e.g. 126/126 unit tests, exit 0. eval_retrieval.py deltas. -->
