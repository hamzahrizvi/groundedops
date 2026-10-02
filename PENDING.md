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

**Friday review 2026-09-25 (automated, not yet reviewed by hand).** One
commit since the last hand update, `1f594c0` (the verifier thinks again),
recorded below. Ticked off: the `fix/wire-faq-choices` PR (merged as
hamzahrizvi/groundedops#7 on 2026-08-19), "nothing verified end to end
since `ea4320a`", and code-scan finding 1 (atomic writes, `f40ce3b`).
Corrected: the gateway is **NXDOMAIN again** (checked during this review, after the
night's measurements), code-scan finding 4 is half done, the backend-crash
item has a fix in the tree, and "thinking mode is not a quality lever" is
wrong for judgement calls. Added: three items under **Measurement** (the
blind set's gap to v16.3, the verifier's thinking cost, and eval.py not reading
`.env`). Checked in this review: both eval baselines pass `--selfcheck` (30 and 34
cases, baseline present). `run_tests.py`: **240/240, 1 skipped**
(`test_ui`), 0 failed. That is down from the 252 recorded below. The drop
is not a regression. `1f594c0` moved `test_llm` into its own process, so its
16 functions now count as one entry. The run was against the working tree,
which also holds another session's uncommitted OCR/table work
(`src/ocr.py`, `src/tables.py`, `src/tests/test_ocr.py`), not HEAD alone.

**2026-09-27 - the 7 -> 10 upgrade plan is written:
`docs/upgrade-plan-7-to-10.md`.** Built from nine subsystem readers, five
independent planners (59 proposals) and an adversarial verdict on each of 37
merged clusters (33 kept with changes, 4 rejected: a standalone-refusal
regeneration, lexical-rescue label adjacency, condenser slots, an advisory
stub). It carries a rubric with the instrument that reads each criterion, a
"Measure first" section (M1-M14: instrument fixes, the noise floor at
--repeats 3/5, the blind 3x3 against v16.3, log labelling, a run_live mode
through /widget/ask), three execution tiers, "Rejected and why", "Already
built - do not rebuild" with file:line, and the first three actions with
commands. Two findings worth reading before anything else: 440 chunks carry
a stale prod_biometrics_general flag that leaks NV9 Spectral rows into
MyCheckr-scoped retrieval (dense arm only), and /widget/ask never forwards
`role`, so the handoff fix recorded below as shipped has only ever worked on
/query. Every number in this file is a harness number until the bot has a
host and an origin field in the log.

Last hand-updated: 2026-09-25 (night) — the four follow-ups from the
v16.3 rating are done: graded eval **20/34 -> 32/34 (94%)** with the answer
baseline re-armed, the gateway models measured (like-for-like on quality,
7x on latency, thinking cannot be switched off), NLI kept on the evidence
of 792 logged turns, and the five deferred findings closed. See **Shipped
— the four follow-ups** below.

Previously 2026-09-25 (evening) — the conversation stress test
landed: 14 live scripted conversations, 46/59 -> 53/59 turns as expected,
five fixes and a ranked gap list in `docs/stress-test-2026-09-25.md`; see
**Shipped — conversation stress test** below. Earlier that day: **v16.3 is
tagged and published as GO_v3.2**; the work after it is on `experimental/v16.4-logic-and-latency`.
Two audits of the answer path (a latency map, and a logic review that
confirmed each finding against the code) landed together; see the dated
section **2026-09-25** below for what was fixed, what was measured, and the
five findings deferred with their evidence. Headline numbers: DeepSeek V4
was thinking by default on every call (3.15s -> 0.94s per call once
disabled, a tenth of the completion tokens); the same seven questions on an
idle box go 29.7s -> 30.9s end to end only because the new code ANSWERS
three of them the old code refused (each answer costs an NLI pass the
refusal skipped); the same short question repeated is 3.4s -> 2.6s. Tests
**248/248**, 1 skipped. eval.py --no-grade: 34/34 identical outcomes old
vs new; DeepSeek V4 Pro measured against Flash and rejected (below).

Previously: 2026-09-21 — the answerability switchboard
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

## 2026-09-27 - the 7 -> 10 plan, in plain English (all open)

The full plan, with file:line anchors, tests to keep green and the exact
commands, is `docs/upgrade-plan-7-to-10.md`. This section is the same list
in plain words, so anyone can see what is left and why. Item numbers match
the plan. Tick each off here, with its commit, as it lands.

**Why we are at 7.** The bot answers from the manuals, cites pages, and
refuses rather than guesses. But it still says a confident "No" about
things the manuals never mention, loses track of the conversation after a
refusal, cannot compare two products properly, and has no real customers
yet. Almost everything in the logs is our own testing, so every score we
have is a test score.

### Latest rating: 7/10, up from 6.5 (2026-09-27)

This is the first rise backed by questions nobody tuned against. The gain
is in behaviour customers notice: the bot now asks when a question is
vague and refuses when it is off-topic, instead of the reverse.

| Measure | v16.3 | Now | How much to trust it |
|---|---|---|---|
| Blind set 2, scoped chats | 25/34 | 30/34 | Moderate. About 28 on substance: three of its questions were seen, and two passes are grading noise. |
| Held-out unscoped set | 14/30 | 19/30 | High. Never used for development. |
| Vague questions asked "which product?" | 5/10 | 9/10 | High, held out. |
| Off-topic questions refused | 9/10 | 10/10 | High, held out. |
| Picture-only questions | 1/12 | 1/12 live, 2/12 with figure text | High. The feature is unfinished and not live. |

**What went up, and why:**
- The main customer path, a scoped widget chat, is roughly 82-88% right
  on unseen questions. Most remaining failures are safe ones: refusals,
  not wrong answers.
- The failure mix got safer. A mispaired fault code is caught again, a
  wrong age band became a refusal, and off-topic questions no longer get
  a product menu or an answer from the wrong manual.
- Measurement can now be trusted. Held-out sets exist for scoped,
  unscoped and image questions, so a claimed gain can be checked.

**What holds it back, by category.** Each item links to its plan step.
New items are numbered P1, 8.16, 8.17 and 9.14 in the table below.

*Accuracy (wrong or misleading answers)*
- [x] **A confident "No" on near-miss questions.** It says the MyCheckr has
  no backup battery when the manual says nothing. Plan step **8.3**
  (honest "not mentioned"). Done 2026-09-29: the battery, coins, Bluetooth
  and facial-recognition cases all answer "not mentioned" (see 8.3).
- [x] **Table conditions get lost.** The right row is found, but a
  condition such as "or 20 coins" is misstated. **Step 8.16.** Measured
  and half fixed 2026-09-29 (2/6 -> 4/6 cases stable; the "or 20 coins"
  row itself 3/3); two table shapes still open, see 8.16.

*Understanding the question*
- [x] **Describing a product instead of naming it: 0/10 -> 3/10.** "The
  screenless age-estimation camera" gets a menu or a refusal, not the
  MyCheckr Mini's answer. **Step 8.17.** Done 2026-09-29 (`ca94ebb`), in
  part. Where the "which did you mean?" menu would be shown, one short
  model call (clarify.described_product) is shown each candidate's own
  retrieved passages and asked whether the question describes exactly one;
  a clean name scopes the turn, anything else keeps the menu. The overview
  FAQs were too thin to match against (6 products, truncated). Also fixed:
  re-scoping turned a confident result "ambiguous" ("which part do you
  mean?") because the product's shared documents widen the source count.
  Developed on the probe set only: described 6/10 -> 9/10, vague 10/10
  kept (the model said NONE on every vague case it was shown). Held out,
  one run, measure-only: described 3/10, vague 9/10, off-topic 10/10,
  22/30 (was 19/30). **Open: 7/10 held-out descriptions still fail**; not
  inspected, to keep the set blind. The fix only covers the menu shape, so
  the rest are likely refusals or the nothing-found path. Next: have
  someone who has not seen the set read those 7 failures, or write a second
  probe set.

*Retrieval and images*
- [ ] **Picture-only questions: 2/12 even with figure text.** OCR reads the
  labels, but figure chunks carry no page heading, so search rarely finds
  them. **Step 9.14**, a retrieval fix alongside plan steps 9.7 (reindex) and
  10.6 (figures beside procedures). Done looks like figure chunks carrying
  their page's section heading, and the 12 image cases re-run.
  **Code done 2026-09-29** (`912239b`). The heading goes into the chunk's
  breadcrumb, its body and its `section` metadata, on ingest and on
  `index_figure_text`; a page with no heading takes the nearest earlier
  page's. Measured on scratch copies of documents/ and chroma_db
  (figures cut and OCR'd there, the live index untouched), with
  `retrieve_from_db` scoped to the product: the case's figure chunk is
  retrieved for **3/12 without headings and 4/12 with them** (SMART Hopper
  height newly found at rank 4). That is retrieval only, not answers.
  Headings are not the main barrier; the other 8 misses need
  query-to-label matching (dimension words vs bare numbers) or 10.6. Live
  only after the 9.7 rebuild with FIGURE_TEXT_INDEX=1 (currently 0
  figure-text chunks in the live index).

*Process*
- [x] **Two sessions editing one working tree broke the repository twice
  this session.** The code is sound now. **Step P1.** Rule: one session
  changes code at a time. A second session works in its own git worktree
  or branch and merges, and never runs `git add -A` on a shared tree. This
  matches the session-tooling memory note.

### Full plan in order, with the Claude model for each step

Switch model with `/model` at the start of a step, never midway through one.
Only one session changes code at a time (step P1).

**Which model, and why**
- **Fable 5.1:** changes to the answer pipeline in `main.py`, where many tests
  pin the exact code and a wrong edit can quietly lower answer quality. Also
  reading measurement results and deciding what they mean. Costs the most
  session limit, so keep it to these steps.
- **Opus 5.5:** most of the work. Normal feature changes with clear
  instructions in the plan.
- **Sonnet 5:** small, mechanical edits where the plan already says exactly
  what to change, plus doc and PENDING updates.
- **Haiku 4.5:** running things and reporting numbers, with no design
  decisions.
- **You:** decisions, credentials, documents and labels no model can supply.

| # | Step | What it does | Model |
|---|---|---|---|
| **Phase 0 - process** | | | |
| 1 | P1 | One session edits code at a time. Others use their own worktree or branch. Write the rule into CLAUDE.md. | Sonnet |
| 2 | Commit landing work | Commit the other session's OCR, figures and clarify work, staged by hunk, so later steps start clean. | Opus |
| **Phase 1 - measure first** | | | |
| 3 | M1 | Fix the test tool: word matching, polite refusals, grader setting, "blind" label on blind set 2. | Sonnet |
| 4 | M3 | Before/after comparison tool, blind-set guard, time per test case. | Opus |
| 5 | M2 | Label every log line: conversation, visitor or test, outcome, what checked the answer. | Opus |
| 6 | M12 | Run the live test conversations through the widget path too. | Opus |
| 7 | M14 | Tests for the new table and phrase-search code. | Sonnet |
| 8 | M10, M11 | Honest routing labels; routing tests in CI. | Sonnet |
| 9 | M9 | Pin spec-table test cases to the page; fix the reranker benchmark. | Sonnet |
| 10 | M6 (4th check) | Check whether vague fault reports now get "which product?". | Sonnet |
| 11 | M4 runs | Run each test set 3-5 times on unchanged code. | Haiku |
| 12 | M4 reading | Decide which flips are noise, reset the pass mark. | Fable |
| 13 | M5 | Blind comparison against v16.3, 3 runs each side (Haiku runs, Fable reads). Commission blind set 3 (you). | Haiku + Fable |
| 14 | M7, M13 | Re-measure checker cost and NLI value from the labelled log (Haiku runs, Fable reads). | Haiku + Fable |
| 15 | M8 | Rewire the checker test; try a second vendor as checker. A person labels ~50 answers. | Opus + you |
| **Phase 2 - Tier 7 -> 8** | | | |
| 16 | 8.1 | Widget passes on role, reason, request id, outage flag. | Sonnet |
| 17 | 8.2 | Repair the 440 wrong product tags. | Opus |
| 18 | 8.3 | Honest "not mentioned", including the MyCheckr backup-battery case. | Fable |
| 19 | 8.16 (new) | Keep table conditions such as "or 20 coins": measure first, then fix. | Fable |
| 20 | 8.4 | Stop treating fresh questions as follow-ups. | Opus |
| 21 | 8.5 | Remember refused turns. | Fable |
| 22 | 8.6 | Comparisons read both manuals. | Fable |
| 23 | 8.17 (new) | Recognise a product from a description ("the screenless age camera"). | Opus |
| 24 | 8.7 | "And step 3?" from the previous answer. | Opus |
| 25 | 8.8 | Prove "want the steps?" -> "yes" works on the widget. | Opus |
| 26 | 8.9 | Handoff reference number, email send, contact-form limit. You supply SMTP details. | Opus + you |
| 27 | 8.10 | Thumbs up/down and a review list. | Opus |
| 28 | 8.11 | Real progress while waiting. | Opus |
| 29 | 8.12 | Show "open the manual at page N", related questions, softer refusals. | Opus |
| 30 | 8.13 | Phone layout, screen reader, browser walk-through test. | Sonnet |
| 31 | 8.14 | First multilingual step. | Sonnet |
| 32 | 8.15 | Test cases for all of Tier 8. | Opus |
| **Phase 3 - Tier 8 -> 9** | | | |
| 33 | 9.1 | Always-on server and fixed address. You choose and provide the host; Opus writes the setup doc. | You + Opus |
| 34 | 9.2 | Restart on hang, alerts. | Opus |
| 35 | 9.3 | Backup AI vendor, outage drill. You supply the key. | Opus + you |
| 36 | 9.4 | Daily spending cap. You flip guest AI on. | Opus + you |
| 37 | 9.5 | Backups on a second disk, restore test. | Opus |
| 38 | 9.6 | Check unreviewed FAQs; honest "Reviewed" badge. | Opus |
| 39 | 9.14 (new) | Give figure chunks their page heading so image questions are found. | Opus |
| 40 | 9.7 | Rebuild the index: Fable checks the gates, Haiku runs it. | Fable + Haiku |
| 41 | 9.8 | Cross-references to a section of the same manual count as held ("MyCheckr Range Technical Data" is p36 / Mini p29, not a missing document). **Done 2026-09-30** (`d7ab9f5`). | Fable |
| 42 | 9.9 | Full multilingual answers behind a switch. | Fable |
| 43 | 9.10 | Explain every answer that flips run to run. | Fable |
| 44 | 9.11 | Label or hide the slow "accurate" reranker. | Sonnet |
| 45 | 9.12 | Draft FAQs for ~10 repeat questions; a person approves. | Opus + you |
| 46 | 9.13 | Test a stronger model on flagged answers (Haiku runs, Fable reads). | Haiku + Fable |
| **Phase 4 - Tier 9 -> 10** | | | |
| 47 | 10.1 | Weekly report: resolved, refused, handed off, speed. | Opus |
| 48 | 10.2 | Console review loop; OCR verified end to end. | Opus |
| 49 | 10.3 | Nightly automatic tests, Docker start-up test. | Sonnet |
| 50 | 10.4 | Full side-by-side comparisons, only if still needed after 8.6. | Fable |
| - | 10.5-10.9 | Deferred until needed. | - |
| **Extra - MCP server (outside the 7 -> 10 plan)** | | | |
| 51 | MCP0 | Decide who may call it and whether retrieved text may leave the network. | You |
| 52 | MCP1 | Read-only MCP server: `ask`, `list_products`, `search_faqs` on existing endpoints, stdio. | Opus |
| 53 | MCP2 | `search_docs` route, token auth, `get_source_excerpt`, HTTP transport, eval cases. | Opus |
| 54 | MCP3 | Optional: Jira support history; generic open-source version with no ITL code or data. | Opus + you |

### How to run the plan: sessions, not steps

The table above is the step reference. Run it as the sessions below.
Switching model per step restarts the prompt cache about 40 times, which
wastes more than the per-step model choice saves.

**Rules**
1. One session per block. Start with `/clear` or a new session, set the
   model once, never switch mid-block.
2. Eval runs are shell jobs, not model work. Steps 11, 13, 14, 40, 41 and 46
   ("Haiku runs") become background scripts that write a small summary
   JSON. Haiku is then unnecessary. The reading session opens only the
   summary, never raw logs.
3. A block with mixed models uses the stronger one throughout. A cache
   rebuild costs more than doing a few Opus steps in a Fable session.
4. Read little: only the block's step sections here (grep the ids), only the
   covering tests (`pytest -k`), full suite once at the end of the block,
   one commit per step.
5. Stop at any "you" step. An unattended session reports and stops; it
   never guesses.
6. Check `.venv` has fastapi before any test run, or 21 tests skip silently,
   including the ones covering these changes.
7. No Explore/subagent fan-out for these steps; they re-read files the main
   session already holds.

**Collect before starting (no Claude needed):** SMTP details (26), server
host choice (33), backup vendor key (35), guest-AI decision (36), commission
blind set 3 (13), the 5-document filing decision (40), time booked for
the ~50 human labels (15), the MCP access and data decision (51).

| S | Model | Steps | Notes |
|---|---|---|---|
| 1 | Opus | 1, 2 | Attended. Confirm the other session has stopped first. P1 is a one-line CLAUDE.md edit. |
| 2 | Sonnet | 3, 7, 8, 9, 10 | Edits the plan already spells out. M1 must land before any eval run. |
| 3 | Opus | 4, 5, 6 | Also write the eval batch script used in S4. |
| 4 | none (shell) | 11, runs for 13 and 14 | One overnight background job; writes summaries only. `nohup bash tools/overnight_eval.sh > /dev/null 2>&1 &` from the repo root (written in S3); S5 reads `eval_runs/<stamp>/summary.json`. |
| 5 | Fable | 12, reading for 13 and 14 | Reads the summaries; resets the pass mark. |
| 6 | Opus + you | 15 | |
| 7 | Sonnet | 16, 30, 31 | Check they are independent before grouping. |
| 8 | Opus | 17 | Tag repair. |
| 9 | Fable | 18, 19, 20, 21, 22 | Same conversation/answer code; 8.4 here avoids a second read. **Done 2026-09-29** (`dde62db`..`c4cc950` + two follow-ups); full suite 299/299 (1 skipped); retrieval 29/32 and tuned 33/34 unchanged. |
| 10 | Opus | 23, 24, 25 | Conversation follow-ups. **Done 2026-09-29** (`8b9fe65`..`c858ab7`); full suite 305 passed (1 skipped); run_live 25 3/3 on both paths, 13 4/4 x3; routing 152/164 (same 12 fails); retrieval old 32 unchanged but one 1/3 noise flip; held-out unscoped 22/30 (was 19). 8.17 is only partly done: 7/10 held-out descriptions still fail. |
| 11 | Opus | 26-29, then 32 | Features, then the Tier 8 tests. **Done 2026-09-29** (`872c394`..`a2125b0` + one follow-up); full suite 311/311 (1 skipped); run_scenarios 152/164 (same 12 fails). Left for you: SMTP settings + one real enquiry (8.9); the 8.15 baseline re-arm and a `run_live --path stream` N=3 run (both need a live backend). |
| 12 | Opus + you | 33-37 | Infrastructure; needs your inputs. **Code done 2026-09-29** (`4e8748a`..`73dbcc7` + this); full suite 311 passed (1 skipped); run_scenarios 156/168 (same 12 fails, 4 new probes pass). Restore drill 328s to ready, chunks match. Left for you: company server + tunnel (9.1), hang/outage acceptance + SMTP (9.2), backup via company gateway + drill ~10-01 (9.3), cap value + guest-AI flip (9.4), passphrase to password manager then drop BACKUP_ALLOW_PLAINTEXT (9.5). |
| 13 | Opus | 38, 39 | **Done 2026-09-29** (`b2cb5dc`, `912239b`, `ca0d6e2` + this); full suite 312 passed (1 skipped). 9.6: the "Reviewed answer" badge now appears on 168/406 entries, down from all 406. The audit checked the 157 unedited generated entries: 110 verified 3/3, 8 unstable, 39 stable rejects (list in `eval_runs/faq_audit_9.6.log`). 9.14: figure chunks carry their page heading. On a scratch index, the case's figure chunk is retrieved for 4/12 image cases, up from 3/12. The new chunks reach the live index only through the 9.7 rebuild (S14). Left for you: read the 39 rejects against the PDFs, then edit or delete them (9.6). |
| 14 | Fable, then shell | 40 (41 done) | Fable checks the rebuild is safe; a script runs it. Attended (your decisions gate it). **Gates for 9.7:** (1) figures.py/ocr.py committed: met; (2) `reindex.py --from-store --dry-run`, then you confirm the filing of the 5 documents whose catalogue entry differs from the live tags (the full MyCheckr manual must not drop out of Mini scope); (3) backend stopped, run on a copy of `src/chroma_db`; (4) M14's phrase-search and table tests pass; (5) decide whether to fix the 225 chunks under 80 chars and the 65 duplicate bodies in this pass; (6) new from 9.14: rebuild with `FIGURE_TEXT_INDEX=1` or not (4/12 image cases retrieved; figure chunks may outrank the prose that answers). **Done looks like:** `indexed_at` on 0 -> 1749 chunks; footer-tailed chunks 504 -> ~0; short/duplicate counts fixed or kept with a reason; `eval_retrieval.py --compare` unchanged or better; the NV200/SMART Payout footer case fixed; baselines re-armed. **9.8: done 2026-09-30** (`d7ab9f5`), ahead of this session: `crossrefs.missing()` 5 -> 0, since all five were sections of the citing manual. The MyCheckr dimensions are only in the drawing on p36/p29, so answering them rides on gate (6) and the image cases. **Collect first:** the 5-document filing decision, about an hour with the backend stopped. First prompt: dry run and report the 5 filing differences, the short/duplicate counts and a FIGURE_TEXT_INDEX recommendation, then stop for decisions. **Done 2026-09-30** (`b91a6a5`, `05fac34` + plan commits): decisions 1-4 taken and applied, index rebuilt 1749 -> 1668 chunks, footer-tailed 500 -> 0, retrieval R@1 up with no regression, full suite 315 passed (1 skipped); details under 9.7. Baselines re-armed from `eval_runs/20260930_2105` (tuned 45/49 x5, retrieval 32/35 x3). Left for you: rule on the one rebuild-caused flip ("how do I mount it" now answers instead of asking; keep `AMBIGUOUS_CEILING` 0.65 or raise it, see 9.7); delete `src/chroma_db.before-9.7-20260930_2100` when satisfied; try the upload form's new "Also file under" picker once. |
| 15 | Fable | 42, 43 | **Done 2026-10-01** (`c868dd6` + this). 9.9 was mostly built (8.14 found it); added the numeric translation check, the logged language, the 10-case es/de/fr parity suite (30/30 same document, 0.82 page overlap over 3 runs) and scenario 24 tightened. 9.10: 3 flips in 1,024 case-runs, all sampled-model (2 generation, 2 grader), none deterministic. |
| 16 | Sonnet | 44 | **Done 2026-10-02**: label only, one admin.html edit. |
| 17 | Opus + you | 45 | |
| 18 | shell, then Fable | 46 | |
| 19 | Opus | 47, 48 | |
| 20 | Sonnet | 49 | |
| 21 | Fable | 50 | Only if still needed after 8.6. |
| 22 | Opus + you | 51, 52, 53 (54 later) | Independent of S1-S21; run it any time in its own worktree (P1). Stop after MCP1 unless MCP0 is decided. |

**Opening prompt for each session**

```
Do steps <ids> from PENDING.md. Read only those sections (grep the ids).
Per step: implement, run the targeted tests, commit, tick the box.
Stop and report at anything marked "you", on a failing test you can't
fix in two tries, or if git status shows files you didn't touch.
Full test suite once at the end. Don't push.
```

**Auto mode:** fine for S2, S3, S7-S11, S13, S16, S19 and S20. Keep S1
(someone else's uncommitted work), S12, S17 and anything with credentials or
outside services attended. S4's overnight job uses the nohup recipe, since
`preview_start` is refused in scheduled runs.

### Step 0 - measure first (do these before changing any behaviour)

Our test scores wobble by about 3 cases out of 34 from run to run, even
with no code change. Until we fix the measuring tools and know that wobble
exactly, we cannot tell whether a fix helped.

- [x] **M1 Fix the test tool** (`21698b0`, 2026-09-27). Any-of keywords_all
  entries; the run_live.py offer_support rule ported into classify_outcome
  so an INFERABLE/ADVISORY friendly refusal scores "rejected"; "blind" is
  a valid layer and eval_cases_blind2.json now carries it on every case;
  two over-narrow blind2 keywords and blind.json:249's SD card speed-class
  fixed; preflight makes one real grader call and aborts before case 1 if
  it comes back empty; the unparsed-"pass" fallback deleted;
  eval_retrieval.py gets a --cases flag.
- [x] **M2 Label every log line.** Record which conversation, whether it
  came from a real visitor or from our tests, how the turn ended, and what
  checked the answer. Today we cannot tell customers from test runs.
  (2026-09-27, code and deterministic tests.) Every logs.jsonl row now
  carries session_id, origin, outcome (the trace's exit id, None when the
  last stage is not an outcome), verified_by (ground_via), verifier (the
  SUPPORT/RELEVANCE verdict text) and service_degraded, read by
  logger._trace_fields from a new pipeline_trace meta dict at write time.
  Origin (who asked) is server-side by session prefix (eval-/live-/
  preflight-), else "widget" for a visitor on the widget and "console" on
  /query; a separate `surface` field says which door ("widget"/"query").
  The widget now opens its own trace via main._traced_query. The prefix
  wins on the widget so M12's widget-path test runs are not counted as
  customers (a visitor could label only their own turns a test). Newly logged: steps,
  acknowledgement, more, document request, sales/catalogue, faq.ask, and
  every _curated_faq_reply (faq.answer and the now-marked faq.picked);
  request_id and timing added at the handoff, early-refusal, span.ask
  (now marked) and extract sites. eval.py session ids get "eval-". Not
  done here, needs a live backend: the Counter(origin) gate over a real
  `eval.py --no-grade` run plus one widget chat, and the ground_via split
  check. The pre-pipeline widget FAQ returns stay 9.4's.
- [x] **M3 A before/after comparison tool** for test results, and a guard
  that stops anyone locking in a blind set as a target. Also record how
  long each test case took (2026-09-27). `eval.py --compare-results A.json
  B.json` (pure `compare_results`, offline): headline is stable-pass-on-A /
  stable-fail-on-B and the reverse; within-side flips are listed, not
  counted; totals carry the +/-3 noise label. Any blind-layer case prints
  MEASURE-ONLY and `--update-baseline` exits 2 before preflight, writing
  nothing. Each result now carries `wall` (request seconds, grading
  excluded) and `role`, with an advisory p50/p90 per role. tools/README.md
  has the two-index-copy recipe and the three commands.
- [x] **M4 Measure the wobble.** Run each test set 3-5 times on unchanged
  code, write down which cases flip, then reset the pass mark to the cases
  that pass every time. Runs done (S4, 2026-09-27): `eval_runs/20260927_1825/`;
  m13 there was rerun by hand after a segfault. **Read 2026-09-28 (S5), HEAD
  7c58720, grader deepseek-v4-flash, one backend on :8010.** The wobble on
  this code is far below the +/-3 we had assumed: across 134 cases and 470
  case-runs (tuned x5, blind1 x3, blind2 x3, retrieval x3) exactly one case
  flipped, blind2's "installer checklist default relay activation duration"
  (2/3, the failing run refused with "I don't have that... about the
  MyConnect" at NLI 0.19, so a generation flip, not a grader one). That
  flip is noise until it repeats; it is not in any pass mark (blind).
  Every other failure is deterministic, so the honest reading is "0 to 1
  case per suite per 3 runs" -- the +/-3 figure came from the
  always-thinking verifier (1f594c0) and belongs to that code, not this.
  The tool's "+/-3" label stays as a conservative ceiling. The v16.3 side
  of M5 wobbled more (4 of 34 cases flaky, see M5), so the noise budget
  depends on the build under test and must be re-measured per build.
  Always-fail, none of them noise: tuned "What voltage is supported on the
  NV9ST?" (refuses, scoped to the NV9USB+ -- the NV9ST is not a product the
  catalogue knows, so this is a product-recognition case, 8.17); blind1
  "ICU REST API HTTPS port" (the FAQ picker fires instead of answering);
  blind2 MyCheckr backup battery ("No." -- the confident-No case, 8.3),
  MyCheckr ad-screen age band index 2 (refused, NLI unavailable), BV30
  Bluetooth diagnostics (served a gap statement as an answer at NLI 0.0008
  instead of a clean refusal); retrieval Twin SCS baseplate screws and the
  NV200 Spectral docked power supply (both refuse, scoped to the SMART
  Coin System: its product scoping wins over the second product in the
  question, so neither reaches the page), and "pin assignments for that
  interface" (answers the MDB/IF5 question instead of listing pins:
  keywords fail on a correct-outcome answer, a follow-up-resolution case
  for 8.7). **Pass mark reset:** eval.py gains `--baseline-from-results
  RESULTS.json [--baseline X]` (pure `baseline_from_results`, offline, same
  shape `--update-baseline` writes plus a `source` line; refuses blind
  results with exit 2 and writes nothing). eval_baseline.json is now the
  5-run tuned result, 33/34 (was 32/34 from one run; "does the SMART Coin
  System support facial recognition" is in, it passed 5/5).
  eval_baseline_retrieval.json is the 3-run result, 29/32 (was 26/30): the
  two M9 cases are regression-gated at last, "ok and what about the full
  size one?" and the Cisco-router refusal are in, and the NV200 Spectral
  docked power-supply case drops out of the mark (True -> False). That
  drop is real, not noise: it refuses 3/3, `sources_any` and
  `expected_page` (p69, pinned in M9) both fail, so either the old
  baseline passed it on a page that was not p69 or the SCS scoping has
  since taken it. It stays listed here so the mark does not hide it; 8.6
  (comparisons read both manuals) is the step that should win it back.
  (S9, 2026-09-29: it now answers from both manuals once the NV200S
  catalogue alias exists -- see 8.6; the p.69 pin is unverified.)
  Any future "N/34" quoted against these marks means stable-across-repeats.
  **Re-run after merging the thinking gate (0f08c73, batch
  `eval_runs/20260928_0807/`, same suites and repeats):** both marks came
  out case-for-case identical (tuned 33/34, retrieval 29/32, same
  always-fails), so the baselines now cite the new run and nothing else
  changed. Blind sets: blind2 30/34 again, with the relay-duration case
  now 3/3 and a different one at 2/3 ("BV30 one red flash and four blue
  flashes" -- grader passed all three, the failing run only missed the
  keyword phrasing "Sensor Covered", wording noise). Blind1 lost one:
  "can the NV9USB+ take coins as well as notes" refused 3/3 before the
  merge and now answers a confident "No. The NV9USB+ is a banknote
  validator and does not accept coins." 3/3 at NLI 0.0014. Same score
  both days; the always-thinking verifier rejected the "No", the gated
  one does not think on a plain yes/no and lets it through. So the gate
  bought its latency at the price of one more confident-No, the second
  such case with the MyCheckr battery. 8.3 now has two named cases and
  is not a "not mentioned" wording problem alone: an unsupported "No"
  passes a fast verifier that only thinks on flash, LED, colour and code
  answers. Whether "No"/"Yes" openers should also trigger thinking is
  the first thing to measure in 8.3.
- [ ] **M5 Blind comparison against the last release (v16.3)**, three runs
  each side. The first blind set is used up (we read it to fix things); a
  third one must be written fresh by someone who has not seen the fixes.
  **Runs and reading done on blind set 2 (S4/S5, 2026-09-28):** HEAD
  7c58720 on :8010 vs v16.3 (bd5e6b5) on :8004, each on its own copy of
  the same index, blind2 x3 per side, `m5_compare.txt` in
  `eval_runs/20260927_1825/`. Per-case, which is the only honest unit:
  **lost 1** -- "Does the MyCheckr have a backup battery" passed 3/3 on
  v16.3 and fails 3/3 on HEAD, which now answers a confident "No." So the
  confident-No (8.3) is a regression since v16.3, not an old gap, and it
  is the one case the release must win back before v16.4 ships.
  **gained 6** -- the three vague fault reports now get "which product?"
  (M6), the Euro 1 cent and Thai 50 Satang coin limits and the NV200
  Spectral MCBF now come out of their tables (8d9f5ca / M14). **Flaky,
  not counted, 5** -- four wobble on the v16.3 side only (MyCheckr solid
  yellow LED 2/3, "and 5 flashes?" 2/3, widest BV30 banknote 1/3, SCS MCBF
  2/3; all 3/3 on HEAD) and one on HEAD only (relay activation duration
  2/3, see M4). 22 unchanged. Totals, context only: v16.3 22/34 stable,
  HEAD 30/34. Speed: v16.3 p50 5.6s, p90 15.3s per turn against HEAD's
  3.3s/8-14s on the same set, and one v16.3 request hung for 11.6 hours
  (wall 41,912s), which is why the 102-turn run took all night instead
  of ten minutes; HEAD's provider cooldown and timeouts (0cd3b69) are
  what stop that, and it is the exact failure 9.2 (restart on hang) is
  for. Caveats: blind2 is only partly blind now (M1 corrected two of its
  keywords, M6 read three of its questions), both sides shared one box
  so the walls are advisory, and the grader was deepseek-v4-flash on both
  sides. **Still open, needs you:** commission blind set 3 from someone
  who has not seen the fixes; the comparison above is not repeated until
  it exists.
- [x] **M6 Root causes of the four named failures**, all four closed
  (2026-09-27): one was a wrong answer key (SD card class, fixed in M1),
  one was already fixed, two are the "confident No" problem. The fourth
  (vague fault reports with no product) is fixed, not a gap: against the
  LIVE catalogue vocabulary (not the fixture test_clarify_vague.py already
  used), `clarify.unscoped_clarify_products` on all three blind2 strings
  returns >= 2 products -- "My machine's showing an error" -> 6 products
  (top_score 0.0055), "How do I set the age limit?" -> 3 (0.094), "It
  won't turn on, help?" -> 5 (0.0509). Not widened to make them pass; this
  is what the landed code already does.
- [x] **M7 Cost of letting the checker "think"** (done): checking went from
  about 0.9s to 3.3s typical. **Re-measured from the labelled log
  (2026-09-28, S5):** the batch window holds 624 rows, every one labelled
  (origin: eval 501, live 118, preflight 5; surface: query 565, widget
  59), so M2 works. HEAD 7c58720 still has the always-thinking verifier
  (1f594c0); the thinking gate (15a776e, `fix/verifier-thinking-gate`,
  five commits, worktree `.claude/worktrees/dreamy-driscoll-eab095`) is
  NOT on this branch and PENDING never mentioned it -- **your call whether
  to merge it; if so the M4 marks must be re-run, they were measured
  without it.** Cost as it stands: when the LLM verifier rescues an
  answer, its call is p50 2.8s, p90 13.1s (n=154); when it rejects one it
  thinks longer, p50 8.8s, p90 28s (n=38). Total request time p50 by what
  checked the answer: faq 0.01s, verbatim 0.2s, lexical 2.7s, nli 2.9s,
  llm 6.3s. The NLI model's own tail is the other cost: p50 0.6s on
  answers under 400 characters but p50 10.9s / p90 21s on the 18 longer
  ones -- CPU time, not the vendor. Label gap found on the way: all 25
  suppressed answers carry `verified_by: nli`, because ground_via keeps
  its default when nothing rescues the answer; every one of them was in
  fact rejected by the LLM verifier (verifier_llm_time > 0). A suppressed
  row should name the checker that said no. Small main.py fix for the
  next session that touches the verify stage (S9), not done here.
  **Done in S9 (`dde62db`, 2026-09-29): `verified_by: llm` when the LLM
  verifier ran and the answer was still flagged.**
  **After the merge (0f08c73, batch 20260928_0807, M4 suites only, 460
  case-runs):** the LLM verifier's call on a rescue fell from p50 2.8s /
  p90 13.1s to p50 1.1s / p90 2.2s (n=112); total time for an
  LLM-verified answer p50 6.3s -> 4.8s, p90 20.9s -> 8.1s; NLI-verified
  p50 2.9s -> 3.1s (unchanged, the NLI itself did not move). Served
  answers under 0.1 on the NLI: 123/271 (45%). What it cost is in M4:
  one blind1 confident-No now passes the fast verifier.
- [ ] **M8 Test the answer-checker itself** on known right and wrong
  answers, and try a second AI vendor as checker. Have a person label ~50
  answers so we know whether the grader can be trusted. **Done except the
  second vendor (2026-09-28, S6).**
  *Labels* (`src/eval_labels.json`, the 50 served answers S5 sampled from
  batch 20260928_0807): 18 by the product owner (the MyCheckr, MyConnect
  and ICU rows, all "right", some with "should say more" notes), 32 by
  Claude, each checked against the manual text with the page in `note`
  (so these 32 are not an independent human reading). 49 right, 1 wrong.
  The one wrong is #20: "pin assignments for that interface" got the
  previous turn's IF5 description again, with no pinout (the MDB pinout
  is on NV9USB+ p.44). **The NLI cleared it at 0.747**: it judges support,
  not whether the question was answered; only the LLM verifier has the
  RELEVANCE line (the probe below rejects this answer 3/3). Served-answer
  false accepts by checker: nli 1/20, llm 0/20, lexical 0/10. No kappa:
  the sample holds only accepted answers, so it measures false accepts,
  not false rejects; a kappa needs refused answers labelled too.
  *Probe* (`tools/verifier_probe.py`, rewired to `main._llm_verified`
  with the production prompt and thinking gate; 26 cases, 3 runs, DeepSeek
  as configured, `eval_runs/m8/probe_production.*`): wrong 0/27 accepted
  (all four mispaired flash codes, fabrications, and the #20 answer),
  stated-"No" controls 9/9 passed, correct 21/24. The three misses are
  one case (NV9 Spectral SSP mode, 0/3). The probe's bare
  `retrieve_from_db` top-5 does not return the p.11 button table, so the
  verifier was right to reject it; the case needs the production
  retrieval, not a verifier fix. Absence "No"s (Bluetooth x2, coins,
  facial recognition, Mini ethernet/wifi) accepted 15/15. That is the
  8.3 baseline. **But the person marked the Mini ethernet/wifi "No" (#45)
  right, and the coins "No" (#30) is right in substance, so 8.3's "reject
  every unstated No" goes further than the owner's own judgement. Settle
  which absences 8.3 should refuse before S9 builds it (you).** Settled
  2026-09-29: only never-mentioned features; see 8.3.
  *Grader* (`tools/grader_ab.py`, 101 frozen graded answers, 3x each way,
  `eval_runs/20260928_0807/grader_ab.json`): as-is (DeepSeek thinking off)
  passes 303/303 and agrees with the labels 36/36 on the answers both
  cover; five deliberately wrong copies (exFAT, 450 mA, red x2 = feeder,
  15 mm, 5 s relay) failed 15/15. Inside `judging()` it failed 16 runs of
  correct answers that add true detail beyond the reference (370 mA peak,
  p.37; the SD card's "larger capacity is fine" note, p.19; #25's relay
  warning, which the owner marked right). **Decision: the grader stays
  thinking-off. The plan's "wrap llm_grade in judging()" is dropped on
  this A/B.**
  *Still open, needs you:* the second vendor. There is no
  ANTHROPIC_API_KEY in src/.env or the environment, and the ITL gateway
  (OPENAI_BASE_URL) does not answer off the office network. With either
  available: `python tools/verifier_probe.py --judge anthropic` (default
  claude-sonnet-5, which thinks adaptively with no thinking field, so
  `_call_anthropic` needs no change; its max_tokens 2048 includes the
  thinking tokens) or `--judge openai --model itl-gpt-pro`, and
  EVAL_GRADER_PROVIDER=anthropic for grader_ab.py.
  *Found on the way:* (1) `logger.MAX_ANSWER_LEN = 500` cuts every logged
  answer, so any sheet or review built from logs.jsonl shows #13, #15
  and #32 trailing off; the owner asked why #13 stops mid-word. The
  customer saw the full text. 10.2's review loop needs the full answer
  stored. (2) The manuals disagree on the SCS supply: NV200S p.69 says
  24V/7.5A with the NV200 Spectral, the SCS manual p.20 says 24V/6.5A
  (#22 quoted the NV200S figure). A documentation fix for ITL, not code.
  (3) Two served answers open with a stray "Yes." (#20, #28), a
  writer-prompt habit on non-yes/no questions.
- [x] **M9 Pin spec-table test cases to the exact page**, and fix the
  reranker benchmark (2026-09-27). eval.py gets an `expected_page` check
  beside `sources_any`, checked against `_build_sources`'s `pages` list;
  added to the NV9USB+ pinout (p44), NV9S weight (p25) and NV200S power
  (p69) cases, plus two new ones: the BV30 flash-code table's p31-32
  continuation (the exact case `complete_tables`, M14, exists for) and
  the NV9 Spectral SD card speed class (p19, the same fact M1 corrected
  in eval_cases_blind.json). eval_cases_retrieval.json 30 -> 32 cases.
  `tools/bench_reranker.py`'s env-override bug fixed: it set
  `reranker.RERANKER_MODEL` (the module attribute, read only at import)
  instead of `os.environ["RERANKER_MODEL"]`, which `_configured()`
  actually checks -- every row's `rerank()` call was silently reloading
  MiniLM the moment it ran, undoing the row's own explicit load. Verified
  live: MiniLM and bge-reranker-base now measure identical r@1/r@3/r@8
  (70%/96%/100%) and near-identical MRR (0.828 vs 0.822) but very
  different latency (1.07s vs 9.47s/query) -- confirming the earlier
  "identical scores" reading was the bug, and that `reranker_profile:
  fast` is the right default on latency, not on any accuracy gap.
  `eval_baseline_retrieval.json` still needs a live-backend re-arm before
  the two new cases are regression-gated (no backend running in this
  session to do it honestly); `--selfcheck` passes in the meantime.
- [x] **M10 Honest routing labels** (2026-09-27). `followup` on scenarios
  11-24 split into `needs_history` (nullable: None where a deflect or the
  intent gate answers first, main.py:1448/:2501) and `intent`
  ("handoff"/"greeting"). 14 T2 and 15 T3 relabelled against the live
  transcript's own resolved query; 15 T4 and 20 T2 kept as written. New
  keyless `run_scenarios.py --transcript` replays needs_history against
  the saved resolved queries with each scenario's real accumulated
  history: 52/54 agree, the two disagreements being the deliberately-kept
  15 T4 / 20 T2. `run_live.py` now scores the same thing against a live
  backend's own resolved_query. See docs/stress-test-2026-09-25.md's
  "Routing labels on the new scenarios" section.
- [x] **M11 Run the routing tests in CI** (2026-09-27). `run_scenarios.py
  --min-pass` (new flag) gates the unit job at the committed floor of
  148/160; eval-selfcheck runs over all four case files and is a real
  gate now (continue-on-error dropped: eval_baseline*.json are committed,
  not gitignored); CI now also triggers on experimental/** branches; a
  comment in docker-compose.yml warns against `--workers N` (every JSON
  store's threading.Lock() is process-local).
- [x] **M12 Run the live test conversations through the website widget
  path**, not only the internal one. This is how we found that a "fixed"
  handoff was never fixed for real visitors. (2026-09-27.) `run_live.py
  --path widget` posts each turn to /widget/ask as a member (token minted
  locally with WIDGET_TOKEN_SECRET from env or src/.env, one uid per
  scenario so the 25-credit member allowance never binds) and names every
  missing WIDGET_KEYS key per turn; `--path both` runs both paths on one
  backend and prints per-turn outcome agreement; exit 1 on any fail,
  dropped key or disagreement. The widget's public sources carry
  `page_label` not `pages`, so observed_outcome now reads either (without
  it every cited answer scored "manual"). The needs_history check stays
  /query-only: the widget dict has no resolved_query. Smoke run on the
  running :8000 backend, scenarios 17 and 19: 6/7 agree; the one
  disagreement is 19 T4, a handoff /query decided that the customer sees
  as a refusal because /widget/ask drops role -- 8.1, now measured. Every
  widget turn drops role, reason, request_id, service_degraded and
  product_options. The full 59-turn `--path both` reading is in the S4
  batch (tools/overnight_eval.sh). Pinned by
  tests/test_run_live_widget_path.py.
- [x] **M13 Check whether the fact-checking model is still earning its
  keep** - 58% of served answers now score under 0.1 on it. **Read
  2026-09-28 (S5)** from the labelled log window and `m13_sweep_report.txt`
  in `eval_runs/20260927_1825/`. It is earning its keep, but as a cheap
  accept, not as a gate. Of the 365 served answers in the window the NLI
  model cleared 174 (48%) on its own at p50 0.6s, the LLM verifier rescued
  154 (42%) and lexical containment 37 (10%); 159/327 served answers with
  a score sat under 0.1 (49%, was 58%). It never refuses alone: all 25
  suppressions in the window were LLM-verifier rejections (see the M7
  label gap). The sweep on the retrieval suite says the threshold value
  barely matters: scores are bimodal (median 0.048, widest gap 0.50 ->
  0.93), 18 of the 28 correct answers that reach the gate score under
  0.55 and 15 of them under 0.15, so lowering the bar to anywhere in
  0.15-0.45 would spare at most 3 LLM calls in 28. 0.55 stays. What it
  costs: about 0.7s p50 spent on the NLI before each of the 191 rescues,
  and the long-answer tail (p50 10.9s, p90 21s on the 18 answers over
  400 characters, on CPU). The one change worth measuring, not made
  here: send answers over ~400 characters straight to the LLM verifier
  the way tables already go (408fcae), then re-run the S4 batch and read
  `cost_by_verified_by` again. Anything that scores under 0.1 and gets
  rescued is a table or a long answer; the model is not wrong about
  short prose.
- [x] **M14 Add tests for the new table and phrase-search code** before it
  is baked into the index (`f703927`, 2026-09-27). _phrase_ranking and
  complete_tables now have tests; the chunk's lowercased text is
  precomputed once in the BM25 cache build instead of per query.

### Tier 7 -> 8 - fix what customers would notice

- [x] **8.1 Widget passes on what the server decided** (role, reason,
  request id, outage flag). Stops the handoff message contradicting itself.
  (2026-09-28.) `/widget/ask`'s hand-built response dict now forwards
  `role`, `reason`, `request_id` and `service_degraded` from the pipeline
  result (`widget_api.py`), fixing the exact bug M12 measured: the widget's
  own offer-support copy ("that is not something I have in my knowledge
  base") is only skipped for a handoff turn when `d.role === "handoff"`
  (`groundedops-widget.js:2116`), which was always false with the key
  missing. `service_degraded` also fixes an outage notice being typed out
  character-by-character like a real answer (`groundedops-widget.js:2059`).
  `product_options` stays deliberately dropped (K03). Re-pinned
  `test_run_live_widget_path.py`: the widget's missing-key set shrinks from
  5 keys to just `product_options`, and the handoff scenario's observed
  outcome now agrees with `/query`'s.
- [x] **8.2 Repair wrong product tags.** 440 chunks are tagged to the wrong
  product, so NV9 Spectral figures can appear in MyCheckr answers.
  (2026-09-29, S8.) Cause: every retag path popped the old prod_* flag and
  then called col.update(), and Chroma keeps a key that is merely absent
  (only None deletes). Fixed at the source: `db.with_product_tags` writes
  stale flags as None and is now used by `/admin/reassign_source`,
  `db.retag_product` and `db.delete_by_product` (the last two also left the
  old key in the singular `product` field). The dense arm now over-fetches
  2x, holds its hits to `_matches_scope`, and cuts back to `limit`, so a
  future stale flag cannot leak and RRF still sees the same candidate count.
  Live index repaired with `src/repair_product_flags.py --apply` (dry run
  first: exactly 440, all prod_biometrics_general: ICU_Network_API 160,
  NV9 Spectral 119, MyConnect 97, MyCheckr Mini 64); read-only recount 0
  left, 1749 chunks intact, `reindex.py --check` OK. Backup:
  `backups/before-flag-repair-20260929_1331.zip`. eval_retrieval.py on
  eval_cases_retrieval.json, results in `backups/retrieval_*_flags*.json`:
  old code + old index -> fixed code + repaired index, retrieved R@1
  0.613 -> 0.645, R@3 0.839 -> 0.871, MRR 0.749 -> 0.773; reranked unchanged
  (0.710 / 0.935 / 1.000); no case lower. The four Mini/MyCheckr-scoped
  probes (supply voltage x2, weight, operating temperature) return 0 NV9
  Spectral chunks in the dense 16 and the final 8. Tests:
  `test_product_flags.py` (real in-memory Chroma) and two dense-arm cases
  in `test_shared_documents.py`. **Still to do:** re-arm
  `eval_baseline_retrieval.json` (graded eval.py run, live backend,
  `--repeats 3`) on the repaired index, since M4 armed it before this.
- [x] **8.3 Honest "not mentioned".** When the manual never mentions
  Bluetooth, say so, instead of answering "No". **Scope decided
  (2026-09-29, you):** say "not mentioned" only when X appears nowhere in
  that product's manual (Bluetooth). Keep the plain "No" when the manual
  lists the full set (interfaces, accepted coins) and X is not in it.
  (2026-09-29, S9, `dde62db`.) New `mentions.py`: when the answer opens
  with "No", read what it denies (or, for a bare "No.", what the question
  asked about), drop product and qualifier words, and search the
  product's own chunks (same scope rule as retrieval, cached per product)
  for the feature words. Nowhere in them -> "The <product> documentation
  doesn't mention <feature>, so I can't confirm it either way", role
  `rejected`, `answerability: not_mentioned` (counted as rejected by
  eval.py), gap recorded, question kept in memory. Mentioned anywhere ->
  the No stands and is verified as before; Yes and prose answers are never
  touched. `NOT_MENTIONED_CHECK=off` restores the plain No. Measured on
  the labelled set: #45 (Mini Ethernet/Wi-Fi, both named in the manual)
  keeps its No; #30 flips, because "coin" is nowhere in the NV9USB+ manual
  -- which is also what blind set 1 expects for it, and what the rule as
  decided says (the note above used to say #30 stays; it does not). Live
  on :8010, 2 runs: battery, coins, BV30 Bluetooth and SCS facial
  recognition all "not mentioned" (12/12 with the two documented controls
  still answered). The tuned facial-recognition case now expects
  `rejected` (answer-key change following the decision); tuned mark still
  33/34, retrieval 29/32, same always-fails. Known wording quirk: a bare
  "No." to "connect to a phone over Bluetooth" reads "doesn't mention
  phone over Bluetooth". Also fixed here (M7 label gap): a suppressed
  answer logs `verified_by: llm` when the LLM verifier was the checker
  that said no.
- [x] **8.4 Stop treating fresh questions as follow-ups** just because the
  rewriter added the product name. (2026-09-29, S9, `ef64954`.)
  `is_followup_turn` takes an `ignore` set; `main._is_followup` passes
  every word of every catalogue product's key, name and aliases at all
  three call sites, so "how do I clean the note path" rewritten to "how do
  I clean the NV9USB+ note path" in an NV9USB+ chat is no longer a
  follow-up. "and the current draw?" still is (its own wording), and words
  pulled from history still count beside the name. Three pinned cases.
- [x] **8.5 Remember refused turns** (the question, not the refusal), so the
  next "which one?" still knows what was being asked. (2026-09-29, S9,
  `252a9a9`, `1187b6a`.) `add_to_memory` stores a refused turn as
  `{"q": ..., "a": "", "refused": True}`; the condense prompt and the
  answer prompt's <conversation> block show it as "(no answer was given:
  the documentation did not cover it)". A generation failure is still not
  a turn. The 8.3 reply is stored the same way (refused=True from main).
- [x] **8.6 Comparisons read both manuals.** A letter-case mismatch in table
  titles stopped the spec comparison finding any shared rows (0 -> 7 once
  fixed), and a product chat only searched one manual. (2026-09-29, S9,
  `9664e07`.) `sales._shared_differences` keys rows case- and
  space-folded: on the live spec index NV9 Spectral vs NV9USB+ 0 -> 7
  shared rows, vs NV200S 0 -> 5, vs BV30 0 -> 5, vs SCS 0 -> 3, every
  other pair unchanged, and `compare()` now returns the table. In a
  product chat a question that TYPES two products widens the scope to
  their shared category, or the whole corpus (`_scope_for_products`).
  The M4 case this should win back ("NV200 Spectral docked with the SCS")
  needed one more thing: the catalogue had no alias for "NV200 Spectral"
  (NV200S has none), so the question named only the SCS. Added
  `"aliases": ["NV200 Spectral", "NV200"]` to `src/catalog_config.json`
  -- **operator data, not in git**, so set it in the console on any other
  install. With it the question answers from both manuals in an SCS chat
  (24VDC/3.5A for the NV200S, 24V/6.5A for the SCS; cites NV200S p.66-68
  and SCS p.20). The retrieval case pins p.69, so check it on the next
  retrieval run rather than assuming.
- [x] **8.16 Keep table conditions** ("or 20 coins"): measure first, then
  fix. (2026-09-29, S9, `c4cc950`.) Six page-pinned cases in
  `eval_cases_tables.json`, each requiring the value AND its condition in
  the manual's words. Before (HEAD 8a1a8e9, 3 runs, keywords only): 7/18
  runs, 2/6 stable -- the MyCheckr max-load current lost "both USB Type-A
  ports drawing 0.5A each" 3/3, the Thai 50 Satang limit came back as a
  bare "15%" 2/3. Fix: one rule in the answer prompt beside "support is
  often conditional" -- a table value is conditional in the same way, and
  a figure without its condition is a wrong answer. After: 12/18, 4/6
  stable; both rows above 3/3. **Open, 0/3 before and after, kept in the
  case file:** the NV9 Spectral note length drops the Multi Note Float
  column (160 mm beside 167 mm -- a variant with its own column group),
  and the Euro 1-cent limit is answered from the WRONG row ("50 coins per
  roll" instead of 25% / max 20) although p.65 is cited -- a wrong answer,
  not a dropped condition; the model does map "1 cent" to "0,01€" and
  still picks the roll table. Next step for both: a table-shape fix
  (spell out variant column groups; give the small-coins row its header
  words), not more prompt.
- [x] **8.7 "And step 3?"** is answered from the previous answer, or the bot
  says honestly that the last answer had only two steps. Done 2026-09-29
  (`8b9fe65`, follow-up in `ca94ebb`). The last answered turn is kept per
  session with its full text and sources (memory keeps 300 characters), and
  popped at the top of every turn, so nothing stale survives a refusal. A
  bare step reference (text_utils.is_step_reference) serves item N verbatim
  with role `step` and no model call. "Step N" headings count, then
  top-level "1."/"1)" lines; plain "###" headings do not (live, it served a
  prose answer's third section, "Removal", as step 3). Out of range, it
  says how many steps there were, or that the answer was not a numbered
  procedure, and offers "Tell me more", which expands the real answer. That
  reply is kept out of memory for this reason. "anything else I should know"
  now expands. run_live 13: 4/4 on 3 runs after the ### fix, and T2 takes
  0.0s with role step (its "mention 3/step/three" check is trivial, as the
  plan said). eval_cases_retrieval.json gained a three-turn chain: the
  capability question, then "Yes, show me the steps", then "and step 3?",
  which serves "Step 3: Reboot or restart udev". It passed 9/9 over 3
  repeats, with the step turn in 0.01-0.03s. The first attempt used a model
  answer as turn 1 ("What are the steps to access my device in a Linux
  environment?"); the extract shortcut answered it with four stray bullets,
  so there was nothing numbered to point into. That extract answer is its
  own quality problem, not yet filed. Retrieval --repeats 3 on the old 32
  cases: the three known failures, plus "SSP programming mode" refused
  1/3 (product-scoped, which neither change touches, so noise).
- [x] **8.8 Prove "want the steps?" -> "yes" -> steps works end to end** on
  the widget. Done 2026-09-29 (`b08a1b3`).
  tests/test_steps_offer_widget_path.py posts "Yes, show me the steps" and
  "No thanks" to /widget/ask as a member with an offer outstanding. The
  steps are served and the offer is spent either way. Live scenario 25
  (capability question, then the offer's own button) passed 3/3 on both
  /query and /widget/ask, the "yes" turn in 0.0s. run_live gained a `reply`
  check (the offer's button must be in suggested_replies).
- [x] **8.9 Handoff gives the visitor a reference number, the enquiry is
  actually emailed** (needs SMTP details), and the contact form is limited
  so bots cannot flood or wipe enquiries. Done 2026-09-29 (S11), code
  side. /widget/lead returns `reference` (GO- + first 8 hex of the id); the
  widget says "Your reference is GO-XXXXXXXX", and the console shows it on
  each Enquiries row. After the response, the lead is emailed to the form's
  notify_email only (never the visitor-typed address: open relay) and
  marked notified; unconfigured mail = no attempt; a MailError keeps the
  lead, notified false. `quota.check_public_write` caps it at 5/visitor
  and 30/IP a day (env QUOTA_LEADS_PER_*; IP from widget_api._client_ip,
  and /widget/config now uses that too instead of trusting
  X-Forwarded-For). Transcript entries truncated to 4000 chars; a full
  store (MAX_LEADS) refuses with 503 instead of evicting. Enquiries page
  gained "Not emailed" and a store-full warning. One warning per process
  when posts arrive from loopback with no CF header and TRUST_PROXY unset.
  tests/test_lead_handoff.py (21 checks). **You:** fill in the mail server
  under API keys, send one real enquiry, and check it arrives and shows
  "Emailed to" in the console. The unsent count is on the Enquiries page,
  not on the nav badge.
- [x] **8.10 Thumbs up/down under answers**, feeding a review list in the
  console. Done 2026-09-29 (S11). POST /widget/feedback {request_id,
  session_id, vote up|down, note <= 280}: the id must be one /widget/ask
  returned in that conversation (bounded in-memory set, 5000; a restart
  forgets them, so a vote on an older answer is a 404), capped by 8.9's
  `check_public_write('feedback')` at 50/visitor, 300/IP a day. Stored in
  widget_feedback.json keyed by request_id, mirrored as an `event:
  feedback`, `outcome: voted_down|voted_up` row in logs.jsonl. A down vote
  calls record_gap(flag=True): `flagged_by_visitor` +1, times_asked
  unchanged, reason `visitor_flagged`. The widget shows two chips under
  generated answers only (not FAQ, refusal, clarify or outage turns); the
  vote is kept on the message so a resumed chat shows it. Shortcut replies
  (steps, tell-me-more) had no request_id at all; /widget/ask now mints one
  when main.query did not, so those can be voted on too, but have no log
  row to join to. The stream meta now carries request_id, role and reason.
  Console: a "Flagged by visitors (N)" filter on FAQs from customers, and
  flagged rows say so; Promote to FAQ is the existing draft button. No
  note box in the widget (the API takes one). tests/test_answer_feedback.py
  (6). Rates are for after go-live, origin=widget only.
- [x] **8.11 Show real progress while waiting** ("searching", "checking"...)
  instead of timed placeholder lines. Done 2026-09-29 (S11).
  `pipeline_trace.listen(loop, callback)` sets a context var that mark()
  reports each stage to via call_soon_threadsafe; /widget/ask/stream runs
  widget_ask as a task in a copied context (run_in_threadpool carries it
  into the worker thread) and sends each stage as a `status` event, mapped
  to visitor wording in `widget_api.PROGRESS_LINES` (a stage is marked when
  it finishes, so each names the step that starts next). Gates unchanged:
  the stream still calls widget_ask. The widget now asks through the
  stream (fetch + ReadableStream; EventSource is GET-only), shows the real
  stage once one arrives, and keeps the timed lines and clock as the
  fallback. The meta allowlist gained needs_sign_in, sign_in_url,
  service_degraded and more_context (the widget reads all four). The
  sentence splitter dropped a leading "." and a "?" after a newline; now
  lossless. **Found on the way:** the widget read a 429 body as
  `e.body.error`, but FastAPI wraps it as `{"detail": {...}}`, so every
  quota or session limit showed "check your connection". Fixed.
  `run_live.py --path stream` records seconds to first event, status
  events per turn and wall p50/p90 into the summary. Tests:
  test_progress_stream.py (4, real _traced_query + threadpool, faked
  pipeline) and test_widget_walk.py (3, Chromium with a stubbed backend:
  streamed answer + vote, the 429 wording, the form's GO- reference).
  **Not measured live:** no backend was started this session. Run
  `run_live.py --path stream` N=3 on an idle box before declaring an SLO.
- [x] **8.12 Show what the server already sends**: "open the manual at page
  N", related questions, and a softer refusal that does not quote the
  visitor's typo back. Done 2026-09-29 (S11). After an answered turn (not
  flagged, outage or sign-in) the widget shows, as chips: "Show more from
  the manual" when more_context is `detail` (the passages render as
  labelled quotes, "From <manual>, page N", and are NOT added to
  state.messages, so the next condense is unchanged); "Open <manual>, page
  N" (signed-in only: /source_file is token gated, so a guest's chip could
  only fail; opens the PDF blob at #page=N in a tab opened on the click);
  and up to 3 "Related questions": this product's curated FAQs the visitor
  has not asked, edited ones first (`edited` added to /widget/faq), tapped
  by faq_id. The starter chips now go by faq_id too. On a refusal, the same
  "Open <manual>, page N" chip sits beside Email support. One source on a
  generated answer reads "Verified against <manual>, page N"; otherwise
  "Sources (n)". The clarify line no longer quotes the question back ("I
  couldn't find that in the documentation I have here -- could you tell me
  more concretely..."); the never-ask-twice marker is kept, and
  test_clarify_gate now fails if a `{...}` returns to that string. Related
  is not ranked by similarity to the answer, only by edited-first. **Not
  done:** the section heading on refusals (optional in the plan), the
  more_context.kind histogram over the retrieval set (needs a live
  backend), and the console's Test chat still does not render
  more_context -- it diverges from the widget here (no shared build step).
  test_widget_walk.py gained 4 browser cases (detail, related by id,
  signed-in refusal page chip, guest gets none).
- [x] **8.13 Phone layout and screen-reader fixes**, plus an automated
  browser walk through the widget. (2026-09-28.) Three bugs found by
  walking the panel with Playwright/Chromium and fixed in
  `groundedops-widget.js`: (1) opening the panel hid the launcher
  (`display:none`) without moving focus anywhere, so a keyboard/screen
  reader user landed on `<body>` with no sense of where they were --
  `openPanel()` now focuses the panel itself (new `tabindex="-1"`), which
  also got `aria-modal="true"`; (2) the composer's 14px font-size is under
  the 16px Mobile Safari treats as "no need to zoom", so focusing it on a
  phone zoomed the whole page in and left it that way after blur; (3)
  below 480px the panel stayed the desktop 400x600 floating card pinned to
  a corner instead of filling the screen like a native sheet. (2) and (3),
  plus a 44px touch-target bump on the header icon buttons, are behind a
  new `@media (max-width:480px)` block so desktop is unaffected (pinned by
  `test_desktop_layout_is_unaffected`). New
  `tests/test_widget_phone_and_screen_reader.py`: 7 cases, no backend
  needed (`/widget/config`, `/widget/catalog`, `/widget/quota` stubbed --
  the widget already fails open on all three by design), skips rather than
  fails with no Chromium build installed. Verified against the pre-fix
  file (stashed, reran, restored): 5 of 7 cases fail without the fix, so
  the test is pinning real behaviour, not tautologies. No focus trap: a
  visitor can still Tab past the dialog into the host page behind it,
  which is out of scope here -- flagged, not built.
- [x] **8.14 First multilingual step**: spot French/Spanish/German price
  questions and refusals. (2026-09-28.) **Found already built and merged**
  (`f4c1399`, `language.py` + `main.query_any_language`, ahead of this
  branch's own PENDING bookkeeping -- neither this step nor 9.9 was ever
  ticked). The mechanism is not per-language spotting: every non-English
  question is translated to English at the edge (`looks_foreign` ->
  `to_english`), the whole pipeline including the commercial/price deflect
  and every refusal rule runs on the translation, and the finished
  answer -- refusal or not -- is translated back
  (`query_any_language`, `main.py:2196`). So a French, Spanish or German
  price question already reached the one commercial rule, and a refusal
  in any of the three already came back in the visitor's language, not
  English. `test_multilingual.py` already pinned the French case
  end-to-end; this session added the matching Spanish and German price
  cases and a German refusal round-trip case (13 tests total, all pass).
  One real gap found and fixed on the way: `language.py`'s foreign-word
  list did not have "kostet"/"kosten" (German for "cost(s)"), so "Was
  kostet der BV30?" -- the plain, ordinary way to ask a price in German --
  scored foreign=1 ("der") vs english=1 ("was" is ambiguously both), which
  `looks_foreign`'s `foreign <= english` guard reads as "not foreign" and
  leaves the question unrouted. Added those two words (plus "kostenlos");
  the cost-guard test (`test_no_english_question_in_the_eval_sets_looks_foreign`,
  364 English questions) still fires on none of them, so this did not buy
  a false positive. No new production code otherwise -- 9.9 ("full
  multilingual answers, behind a switch") looks substantially done by the
  same commit too (the `MULTILINGUAL` env var is the switch), but that is
  a separate step's box to tick, not read further here.
- [ ] **8.15 Add test cases for all of the above**, so the test suite looks
  like real conversations and not just FAQ lookups. **Partly done
  2026-09-29 (S11): code and cases in, baseline NOT re-armed.** eval.py:
  `fine_outcome` (handoff / deflect / manual, mirroring
  run_live.observed_outcome; a manual is a download_url with no cited
  page) is checked only when a case asks for one of those by name, so all
  34 older cases score exactly as before ("sales" is still "rejected");
  --selfcheck accepts the six outcomes; every run prints runs passed per
  layer and "answered by" per provider (the provider == 'faq' count is the
  curated share, no --skip-faq pass). eval_cases.json 34 -> 49: 15 new
  phrasings (none from the blind sets; the SCS warranty and MyCheckr
  battery drafts were dropped for overlapping them), covering stress items
  1 (handoff x2), 3 (BV30 vs NV200S supply voltage, unscoped), 4 (NV200S
  interfaces -> "and the BV30?"), 5 (manual x2), 6 (deflect x2), 7
  (greeting), 8 (absence x2, keywords_absent "Yes,"), 9 (Spanish price),
  plus SSP poll 0x07 and sync 0x11. The handoff, deflect, manual and
  greeting triggers were checked against the real detectors. Items 10 and
  11 are UI and are covered by test_widget_walk.py (related questions,
  votes); item 2 is --repeats. So 8/11 items have an eval case, 10/11 have
  a gating test. **Not done:** the unscoped fault-report case (needs
  clarify.unscoped_clarify_products run with a live top_score), and the
  re-arm itself, a shell job on a spare backend: `EVAL_URL=http://127.0.0.1:8004/query
  python eval.py --repeats 3 --results eval_runs/815_all.json`, then the same
  with `--layer faq --results eval_runs/815_faq.json`, then
  `--baseline-from-results eval_runs/815_all.json`. Expect the tuned number
  to FALL: the new cases were written to find failures, not to pass.

### Tier 8 -> 9 - reliable, reachable, safe

- [ ] **9.1 A proper always-on server with a fixed web address.** Today it
  runs on a PC with an address that changes on restart. **Host decided
  2026-09-29: a company server, not yet provisioned (you).** Done in S12:
  TUNNEL_SETUP.md (named tunnel steps + the go-live checklist). Left: the
  server, the tunnel/DNS, `serve.ps1 -Install` there, plugin re-export,
  and the 14-day /health gate.
- [ ] **9.2 Restart it if it hangs** (but not just because the AI provider
  is down), and alert someone when it is down. Code done 2026-09-29
  (`4e8748a`, `f77fedc`): serve.ps1 probes plain /health every 30s after a
  120s grace and kills the process tree after 3 failures (never ?deep=1);
  Dockerfile healthcheck start-period 120s; provider_unreachable_reason no
  longer carries the exception text (it named the internal gateway host on
  a public endpoint). Not done: the manual acceptance (suspend the pid ->
  restart in logs/service.log within 2 min; black-holed provider 10 min ->
  zero restarts; 3/3 cold starts), SMTP credentials + test-email (you), and
  the external pinger (waits for 9.1).
- [ ] **9.3 A backup AI provider from a different company**, tested by
  deliberately cutting off DeepSeek. Code done 2026-09-29 (`f77fedc`):
  deep health now probes the DEFAULT role (what answers customers) instead
  of the Settings picker (the gateway), and reports `roles.{default,
  advanced,backup}` each with provider + reachable (report-only; backup
  None when unassigned); _call_openai/_call_anthropic return model and
  provider. The escalation-model drift (main.py) was already fixed.
  **Waiting on you (~2026-10-01, at work):** no Anthropic key; plan is to
  route the backup through the company OpenAI-compatible server instead --
  assign it as backup in the console, check `roles.backup.reachable` on
  /health?deep=1, then the 30-minute DeepSeek black-hole drill with
  run_live. Open question from the plan: whether api-mode condensation
  should go through generate_with_fallback.
- [ ] **9.4 A daily spending cap, then turn AI answers on for guests.** The
  switch is the operator's decision. Code done 2026-09-29 (`b63ef28`):
  policy `global_llm_daily` (0 = off; console "Site-wide daily cap" under
  Account holders), counted under `global:llm` for every non-staff charge;
  a capped guest falls to the FAQ path, a capped member gets 429
  `global_quota`; /widget/draft_enquiry follows the console switch; guest
  FAQ turns now write a logs.jsonl row (origin/outcome/session_id);
  scenario 22 has 5 injection probes (run_scenarios 156/168, same 12
  fails). **Left for you:** pick the cap from a week of /admin/credits
  deltas, set it, then flip guest AI on in Access & limits.
- [ ] **9.5 Backups onto a second disk, and one timed restore test.**
  Done 2026-09-29: "GroundedOps Daily Backup" re-registered to
  `D:\GroundedOpsBackups` (second NVMe; off-disk, not off-box), one run
  now: groundedops-20260929-181436.gobk, 166 MB in 6s, envelope
  `encrypted: true`, scrypt n=65536, counts documents=24, index_files=83,
  stores=7. The 14 older archives stay in `backups\` on C:. **Timed
  restore:** `manage_backup.py restore -y --accounts` into a scratch dir
  with every store path exported: 1s; uvicorn on the restored data
  ready:true at 328s from restore start, index_chunks 1749 = live :8000.
  Drill lesson: also export `SPEC_INDEX_CACHE` and `DOC_VOCAB_CACHE` --
  the warm-up rebuilds src/spec_index.json and src/doc_vocab.json at those
  fixed paths (gitignored caches; rewritten from an identical index, so
  harmless this time). **Left for you:** put BACKUP_PASSPHRASE in the
  password manager, then delete `BACKUP_ALLOW_PLAINTEXT=1` from src/.env
  (gate: POST /admin/backup/export without a passphrase -> 400).
- [x] **9.6 Check the unreviewed FAQ answers** and only show "Reviewed
  answer" on ones a person actually checked. **Code done 2026-09-29**
  (`b2cb5dc`, `ca0d6e2`). `faq_store.is_reviewed` decides who gets the
  badge. Approved drafts are saved `reviewed: true`; manual, curated and
  edited entries count as reviewed, as do entries the audit verified 3/3.
  Everything else shows "From our FAQ". Badge-eligible: 168/406 (harvested
  entries are raw manual text and get no badge). `verify_faq_entries.py`
  ran 3x over the 157 unedited generated entries (backup of the store
  before the run: `eval_runs/faq_store.before-9.6.json`): 110 verified,
  8 unstable, 39 stable rejects.
  The rejects fall into three groups. (a) 10 ICU API entries answer "how
  do I X" with "X was added in version 1.0.N", which is a changelog line,
  not an answer: likely genuinely bad. (b) About 20 table-caption entries
  saved as "generated", where the verifier's top-3 chunks probably missed
  the table page: likely fine. (c) BV30/NV9USB+ overview blurbs.
  No serve gate was added (per the plan: only if the person finds real
  wrong entries). **Left for you:** read the 39 rejects in
  `eval_runs/faq_audit_9.6.log` against the PDFs, then edit or delete them.
  Editing an entry makes it reviewed.
- [x] **9.7 Rebuild the search index** (removes page footers, adds table
  context, stamps `indexed_at`). **Rebuilt 2026-09-30 21:00**, see the
  result bullet at the end. **Dry run done 2026-09-30** on a scratch
  copy of `src/chroma_db`, nothing live touched; gates 1 and 4 met (M14
  tests pass). Live: 1749 chunks, `indexed_at` on 0, figure-text chunks 0.
  - **Decision 1, filing: agreed, done (`b91a6a5`).** Five documents
    carry more product tags in the live index than the catalogue gives
    them. Cause: `/admin/reassign_source` writes the new tags into Chroma
    only and never into `catalog_config.json`, while `reindex.py` files
    every document from the catalogue, one product per source. A rebuild
    today would therefore silently re-file them:
    MyCheckr User Manual-v7: live mycheckr, mycheckr_mini,
    biometrics_general -> catalogue mycheckr (drops out of Mini scope);
    ICU_Network_API: live mycheckr, mycheckr_mini -> biometrics_general;
    Accessing my device in Linux Environment: live mycheckr,
    biometrics_general, mycheckr_mini -> biometrics_general;
    CS-MyCheckr Installation checklist and CS-MyConnect Quick Start:
    live myconnect, biometrics_general -> myconnect.
    Fix, three small edits once the rows are confirmed: (a) list each
    source in `catalog_config.json` under every product it belongs to;
    (b) `reindex.catalog_assignment` collects all products for a source
    instead of keeping the last one and passes them comma-joined to
    `ingest_file`, which already writes one `prod_*` flag per key;
    (c) `/admin/reassign_source` also updates the catalogue, so the two
    cannot drift again. Result: catalogue and index agree, and the
    rebuild reproduces today's scoping exactly.
  - **Decision 2, stray site-bot pages: done 2026-09-30** (`tools/drop_site_pages.py` run). The 9 GroundedOps help pages (.txt, added 09-25, chunk size
    450, never in the live index) are byte-identical copies of the
    website bot's knowledge, which already sits in the bot's own store
    `../groundedops-site-engine/documents` and its index. Nothing to
    move; the copies here go. The session's delete was blocked by the
    permission classifier, so run from the repo root:
    `python tools/drop_site_pages.py` (written this session; removes the
    9 .txt files and their manifest entries, plus the stale
    "MyCheckr User Manual-v7 (1).pdf" entry that has no file). Until
    then `--from-store` would add them unfiled.
  - **Decision 3, short and duplicate chunks: keep.** 225 chunk bodies
    under 80 chars (76 are footers the rebuild removes; the rest are
    table rows such as "Header Code: 0xF0" and captions) and 27
    duplicated bodies / 63 extra copies (genuine repeats: the SSP "no
    additional data" sentence x24 under different sections, spec tables
    shared across manuals, disclaimers). They cost nothing measurable:
    embedding happens once at ingest, a query searches 1749 vectors, and
    the reranker always scores 16 candidates. The one quality risk,
    BM25's length normalisation floating a short chunk into the 16, is
    already handled by the reranker, and the harmful case (a footer-only
    chunk at rank 1) is exactly what the footer strip removes. Fixing the
    rest means chunk-merge heuristics with their own failure modes.
  - **Decision 4, figure text: rebuild with `FIGURE_TEXT_INDEX=0`.**
    Figure text is added afterwards per document by `index_figure_text`
    and removable with a delete on `ocr=True`, so it is a separate,
    reversible step measured on its own. No `documents/figures` exists
    live, so this rebuild cuts figures for the first time and will run
    longer than the last one (40 min).
  - **Result 2026-09-30:** rebuilt with the backend stopped into a copy,
    verified, then swapped in; the previous index is kept beside it as
    `src/chroma_db.before-9.7-20260930_2100` (delete it once the M4
    re-arm below has passed). 1749 -> **1668 chunks** across 14
    documents (the 81 gone are footer-only chunks: BV30 123->112,
    ICU API 160->156, Mini 64->61, MyCheckr 70->67, MyConnect 97->89,
    NV200S 211->191, NV9 Spectral 119->113, NV9USB+ 133->125, SMART
    Coin 234->216; the SSP manual kept all 499). `indexed_at` on
    1668/1668, footer-tailed chunks **500 -> 0**, filing differences
    live vs catalogue **0** with every multi-product tag reproduced from
    the catalogue, figure-text chunks 0 (decision 4), figures cut for
    the first time into `documents/figures/`. Duplicate bodies rose
    27/63 -> 48/94 because chunks that differed only by a footer page
    number are now identical (same "keep" reasoning). Short whole
    chunks 24 -> 17. `eval_retrieval.py --compare` against
    `backups/retrieval_after_flags.json` (same 31 cases; 3 new unscoped
    follow-ups skip in both): retrieved R@1 0.645 -> **0.710**, MRR
    0.773 -> 0.805; reranked R@1 0.710 -> **0.774**, R@3 0.935 ->
    0.968, MRR 0.832 -> 0.872, no case lower; result in
    `backups/retrieval_after_rebuild_9.7.json`. Full suite 315 passed
    (1 skipped). The first attempt aborted before the reset:
    `snapshot_collection` choked on Chroma's numpy embeddings, fixed in
    `05fac34`. The rebuild took 100 minutes (figure cutting plus one
    page per embed batch), not 40. Baseline re-arm: the M4 batch
    (`STEPS="m4 summary" tools/overnight_eval.sh`) then
    `eval.py --baseline-from-results` for the tuned and retrieval
    suites. **Re-armed 2026-09-30 21:40** from `eval_runs/20260930_2105`
    (HEAD `d9f1924`, grader deepseek-v4-flash, the batch's own backend on
    :8010 against the rebuilt index): tuned 45/49 stable over 5 runs
    (was 33/34 on 09-28; 15 cases were added since), retrieval 32/35
    over 3 (was 29/32), blind1 31/34 (was 32), blind2 29/34 (was 30).
    Caveat: the 09-28 run was code `0f08c73`, so the graded diffs mix
    two days of code (S9-S13) with the rebuild. Stable regressions and
    what they are: **"how do I mount it" (tuned, 5/5 -> 0/5) is the one
    flip the rebuild caused**: unscoped, expected clarify; the Mini
    mounting chunk's rerank score rose 0.627 -> 0.677 once its footer
    went, crossing `AMBIGUOUS_CEILING` 0.65, so `near_top_products` saw
    one confident document and answered instead of asking. Measured
    across all 18 unscoped eval questions the top score moved by a
    median of 0.000 (mean +0.003), so this is one case, not a shift;
    raising the ceiling to 0.70 would fix only it. **Your call:** keep
    0.65 and accept the answer (it now describes the MyCheckr and Mini
    mounts without asking), or raise the ceiling. Left at 0.65. The
    other stable flips retrieve the same passages at the same scores on
    both indexes (checked), so they are answer/verifier-side, not the
    index: blind1 MyCheckr fingerprint and SMART Coin coins-per-second;
    blind2 Euro 1 cent limit (follow-up) and Thai 50 Satang percentage;
    retrieval "SSP programming mode" (unscoped, five manuals at 0.99).
    Newly stable passes: NV9USB+ takes coins (refusal), BV30 Bluetooth
    (refusal), MyCheckr backup battery (refusal), NV200 docked power
    supply. Blind sets are measure-only and were not tuned against.
  - **Run plan, as executed:** stop the backend on :8000 (started
    09-29 14:51, older than today's commits anyway); rebuild into a fresh
    copy with `CHROMA_DIR=<copy> python reindex.py --from-store`; on the
    copy run `eval_retrieval.py --compare ../backups/retrieval_after_flags.json`
    (09-29, 31 cases, same live index) and the full suite; swap the copy
    in, restart, `reindex.py --check`; re-arm baselines. Done looks like:
    `indexed_at` on every chunk, footer-tailed 500 -> ~0, compare
    unchanged or better, the four Mini/MyCheckr probes still free of
    NV9 Spectral chunks.
- [x] **9.8 Stop calling an internal section a missing document.**
  Rewritten 2026-09-30. "MyCheckr Range Technical Data" is not a separate
  document: it is the Technical Data section of each manual (MyCheckr
  User Manual-v7 contents p3, section p36; MyCheckr Mini User Manual-v5
  contents p3, section p29). MyCheckr p5 and Mini p6 say "Refer to MyCheckr
  Range Technical Data for the dimensions", pointing at that section.
  `crossrefs.missing()` lists it as absent, so a dimensions refusal
  tells the customer to find a document we "don't hold". Fix: a
  referenced title that matches a section heading in the citing document
  counts as held, and the refusal points to that page.
  **Done 2026-09-30** (`d7ab9f5`). `crossrefs._section_page` matches the
  title against the citing document's stored section headings (whole
  title contiguous, or 2+ content words all present; a misread heading
  that contains the pointer itself is skipped). `scan()` marks such
  entries held "(section, pN)". `deferral_for` carries `section_page`, so
  the refusal says "refers ... to its X section, on page N" and "our
  support team can help", no longer "which I don't hold" / "can send you
  that". On the live index **`missing()` went 5 -> 0**, not the 3 expected:
  every entry was a section. MyCheckr Range Technical Data is p36 (Mini
  p29). "Service Guide" is the NV200S manual's own chapter from p73 (the
  jam steps included). BNF Path Guide Inserts is NV200S p112. Lock
  Specification is a heading on NV200S p38, the citing page. Action Data
  Update is ICU API p39. Each citing sentence was read against its page.
  Full suite 312 passed (1 skipped); 2 new tests. The p36/p29 text layer
  holds only headings and weights; the dimensions are in the drawing, so
  answering them is the figure-text work (9.14, 9.7 gate 6, image cases
  "How tall is the MyCheckr?" and the Mini thread depth on p29).
- [x] **9.9 Full multilingual answers**, behind a switch. Done 2026-10-01
  (S15). The switch is `MULTILINGUAL` (default on since `f4c1399`; `=0`
  turns it off), the mechanism is `language.py` + `main.query_any_language`
  as 8.14 found: question to English at the edge, the unchanged English
  pipeline, the verified English answer translated back. The upgrade plan
  (docs/upgrade-plan-7-to-10.md 9.9) wanted three things on top, two were
  missing and are now in: (1) the English draft kept in the payload --
  already there as `answer_english`; (2) a numeric check between draft
  and translation -- `language.from_english` now rejects a translation
  whose numbers (two or more digits, separators ignored) differ from the
  draft, so the English is served instead, since the translation is by
  construction unverified (English NLI, embedder and reranker). One-digit
  numbers are exempt on purpose: the measured Spanish translation wrote
  "4 ... 1" as "cuatro ... un" in 2 of 3 runs, which is correct
  translation, not loss; (3) the detected language logged -- `language`
  is now a trace meta field and a logs.jsonl column (None = English).
  **Parity gate, measured** (backend at `d9f1924` on :8000, no grading):
  `src/eval_cases_multilingual.json`, ten es/de/fr renderings of ten
  `eval_cases_retrieval.json` cases, each expecting the English original's
  top document and cited pages (the English ran 3x first: 30/30 passed,
  identical top source and page set on every run for all ten). Translated,
  `--repeats 3`: **30/30 answered, 30/30 same top document as the English,
  30/30 cite at least one of the English pages, 12/30 the identical page
  set**; mean page overlap 0.82 (per case 1.0, 1.0, 0.8, 1.0, 0.75, 0.5,
  1.0, 0.43, 1.0, 0.75), and every case retrieved the same pages on all
  three of its own runs, so the difference is the translated wording, not
  noise. The two eval.py "fails" are the Spanish bezel case's keywords
  ("4"/"1" spelled out); its keywords are now empty and the page check
  carries it. **Scenario 24** (`run_live --only 24`, 3 runs): T1 Spanish
  BV30 cleaning answers 3/3, T3 French price deflects 3/3, T2 German
  NV9USB+ factory reset refuses 3/3 -- and so does the English original
  ("How do I reset the NV9USB+ to factory settings?", retrieval 0.98,
  the manual has no such procedure), so T2 now expects `refuse`, T1
  `answer`, both tightened from `any`. eval.py: results carry the top-3
  cited sources and pages, "multilingual" is a valid layer. Not done, as
  the plan said: widget localisation waits for the language column to
  show non-English traffic. Cost unchanged: two extra model calls per
  non-English turn.
- [x] **9.10 Explain every answer that flips run to run.** Done 2026-10-01
  (S15), a measurement, no pipeline change. The flip table is M4's, read
  from the two batch runs on unchanged code: `eval_runs/20260927_1825`
  (HEAD `7c58720`, tuned x5, blind1 x3, blind2 x3, retrieval x3) and
  `eval_runs/20260930_2105` (HEAD `d9f1924`, same shape, 49 tuned cases).
  **1,024 case-runs, 3 flipping cases, all in blind2**; every other case
  passed or failed every time. Root cause per flip, from the saved
  records (`m4_blind2.json`, fields retrieval / grounding / role / answer /
  `_grade_reason`):
  - *Installer checklist default relay activation duration* (09-27, 2/3;
    3/3 on 09-30). Retrieval 0.7575 and grounding 0.1942 identical on all
    three runs; runs 2 and 3 answered "1 second", run 1 the model refused
    ("I don't have that ... about the MyConnect"). **Sampled model**:
    same prompt, temperature 0, a different completion.
  - *DA3 error LED 4 short flashes during a firmware download* (09-30,
    1/3). Retrieval 0.9919 on all three runs, role reasoning. Runs 1 and
    2 gave the same claim ("1 long flash then 4 short flashes on the
    download LED = download failure"); the grader failed run 1 ("not the
    DA3 error LED's 4 short flashes") and passed run 2. Run 3 the model
    refused at grounding 0.0008 after 48.7 s. **Two sampled sources in one
    case**: the grader on runs 1 vs 2, the generator on run 3.
  - *MyConnect live alert timeout* (09-30, 2/3). Three near-identical
    answers ("10 minutes, configurable, logged as 'Left pending ...'");
    the grader passed runs 1 and 2 and failed run 3 for the log-line
    detail "not in the reference facts". **Sampled grader.** Across all
    1,024 case-runs this "extra detail" verdict occurred exactly once, so
    the grader prompt is left alone; a rule for a single occurrence would
    be tuning, not a fix.
  Nothing deterministic (no retrieval tie, no rerank-order dependence:
  retrieval and grounding scores repeat to four decimals on every
  flip). The generator (`llm.py` sends temperature 0; DeepSeek's chat
  API offers no seed) and the grader are the two sampled components,
  and both appear in the table, so Level 9a is capped there as the
  upgrade plan said (docs/upgrade-plan-7-to-10.md 9.10). Flip rate on
  this build: 3 cases in 1,024 case-runs; 2 of the 3 are generation
  flips at a confident retrieval, 2 of the 3 are grader flips (one case
  is both). The M4 "+/-3" label in `--compare-results` stays as the
  ceiling; the measured band is 0-2 cases per suite per 3 runs.
- [x] **9.11 Label or hide the "accurate" reranker option** - it makes every
  answer take about 28 seconds. (2026-10-02) Labelled, not hidden: the
  option text now reads "slow: ~28s per answer on CPU" and the hint says
  visitors wait and it is for a GPU server only. Kept because the policy
  value is a real profile (`reranker.PROFILES`) and `policy.json` is on
  "fast".
- [ ] **9.12 Write FAQ answers for about 10 questions** that keep being asked
  and refused.
- [ ] **9.13 Test whether a stronger model rescues flagged answers.**

### Tier 9 -> 10 - it improves itself from real use

- [ ] **10.1 A weekly report**: how many questions were resolved, refused,
  handed off, and how fast.
- [ ] **10.2 Console review loop**: see unanswered and thumbs-down questions
  and turn them into FAQs in one click; verify OCR end to end.
- [ ] **10.3 Nightly automatic test runs**, and a Docker start-up test.
- [ ] **10.4 Full side-by-side comparison answers** - only if 8.6 leaves
  comparisons still refused.
- [ ] 10.5-10.9 Deferred until needed: richer memory, figures beside
  procedures, true word-by-word streaming, translated widget text, test
  cases for the "inference" mode.

### Extra - MCP server (MCP0-MCP3, session S22)

Lets engineers and support staff ask GroundedOps from Claude Desktop,
Claude Code or VS Code and get cited answers without opening the widget.
Nothing MCP exists in the repo yet (checked 2026-09-29).

Design: a separate small process using the official Python `mcp` SDK
(FastMCP) that calls GroundedOps over HTTP. It must NOT import `main.py`:
that loads torch and Chroma, needs torch imported on the main thread first,
and would tie the two processes together. Read-only: no upload, delete,
admin or key tools, ever.

- [ ] **MCP0 (you) Decide access and data policy before anything goes past
  a local demo.**
  - Auth: `/query` takes only an `X-User-Id` header (`main.py` query_route).
    Check whether middleware protects it. The plan is a server token from
    `quota.issue_token()` with the `staff` tier.
  - Scope: the MCP path must go through the same shared-document and
    product-flag scoping as the widget, not around it.
  - Data: with a cloud client (Claude, Copilot) every retrieved chunk goes
    to that provider. Clear it with IT, or point clients at the on-prem
    LiteLLM hub.
- [ ] **MCP1 Read-only server over existing endpoints, stdio transport.**
  Tools: `ask(question, product?, language?)` -> `POST /query` (answer,
  sources with pages, answerability, request_id); `list_products()` ->
  public `GET /catalog`; `search_faqs(query, product?)` -> `GET /faq`.
  Tag the turn with `ptrace.set_meta(surface="mcp")` so the console shows
  MCP use separately. Test with MCP Inspector and Claude Desktop; demo to
  2-3 colleagues. About 2-3 days.
- [ ] **MCP2 Search, auth, shared deployment.** New thin route around
  `retrieval_db.retrieve_fused()` for `search_docs(query, product?, top_k)`
  (ranked chunks with source and page); `get_source_excerpt` via
  `POST /source_chunks`; the MCP0 token auth; streamable HTTP transport and
  one internal deployment; a few eval cases run through the MCP path.
  About a week.
- [ ] **MCP3 (optional) Jira support history and an open-source version.**
  Jira as a second source ("has this fault been reported before?"). A
  generic "RAG to MCP bridge" with a pluggable backend adapter, published
  only after checking ITL's IP and open-source policy (you).

Measure: weekly active users, share of `ask` calls answered vs refused,
lookup time saved for support staff (a small before/after sample).

### Rejected - do not redo without new evidence

- Asking the AI a second time when it refuses a first question: it would
  turn some correct refusals into wrong answers.
- Tightening the number-matching check: no wrong answer was ever traced
  to it.
- Giving the rewriter extra "slots": already measured, no effect.
- A canned reply for "what do you recommend?": almost no one asks it; the
  comparison work covers it.

### Waiting on things outside the code

SMTP login details (emails), a server and web address, a second AI
vendor's key, a person to label answers and write
the third blind set, and real visitors.

The two MyCheckr manuals disagree on the Mini's weight: MyCheckr User
Manual-v7 p36 says "MyCheckr Mini: 186 g", MyCheckr Mini User Manual-v5 p29
says 152 g. Needs the product team to say which is right and fix the
other manual; the bot will quote whichever manual it retrieves.

## 2026-09-25 — experimental/v16.4-logic-and-latency (opened after GO_v3.2)

Two read-only audits of the answer path, run in parallel, then fixes. Every
logic fix is pinned in `src/tests/test_logic_holes.py`; every latency claim
below was measured on this box (12 cores, CPU inference) with nothing else
running unless stated.

### Shipped — the verifier thinks again (`1f594c0`, 2026-09-25 14:56)

A blind eval (`src/eval_cases_blind.json`: 34 cases written from the PDFs by
an agent that saw no case, test or fix) found the branch answering "1 long,
2 short = Note Path Open" for an NV9USB+ bezel code the manual (p.56) lists
as Note Path Jam, and the LLM verifier approving it. Measured on that page:
thinking off, wrong code accepted **4/4** and right code **0/2**; thinking
on, 0/4 and 2/2. So `0cd3b69` switching thinking off everywhere had
inverted the verifier. `llm.judging()` now forces thinking on for the
verifier, the inference contract and the re-answer passage selection;
answer writing stays fast. After the fix: 0/4 wrong, 3/3 right. Blind set:
v16.3 28/34, branch 25-26/34 (see **Measurement** for why that gap is still
open). Original set 33/34. **The blind set stops being blind once anything
is tuned against it. Use it to measure, not to fix.**

### Shipped — the four follow-ups (night of 2026-09-25)

The rating of v16.3 named four things; all four were done, and the graded
eval moved **20/34 -> 32/34 (94%)** on the way. Tests **252/252**, 1 skipped.

**1. The graded eval was rerun, and the answer baseline was stale.**
`eval_baseline.json` held 14 case keys with ZERO overlap with the 34 cases
in `eval_cases.json` -- its 81% described a suite that no longer exists.
The grader also silently did nothing: `eval.py` reads `DEEPSEEK_API_KEY`
from the environment and never loads `.env`, so every graded check
reported "grader returned nothing". Export the key (or run from a shell
that has it) -- the preflight does not catch this. Graded, on DeepSeek
Flash with the DeepSeek grader: **20/34**. Of the 14 failures, three were
the instrument (`classify_outcome` counted the sales deflect and a
friendly refusal as "answered"), three were stale expectations (keyword
lists, and a "must refuse" for a question the manual answers with a
grounded "No"), one was a mis-ordered follow-up case, and **seven were
real**:

- *The commercial deflect ran before the curated FAQ.* "Are there
  recurring fees for MyCheckr?" has a curated answer and got "I can only
  answer technical questions". `_sales_answer` now asks the FAQ first
  (`suggest_candidates(..., record=False)`, no gap recorded).
- *FAQ candidates that share only the product name.* "How does the
  NV9USB+ communicate with a host?" was offered "What are typical
  applications for the NV9USB+?" (0.930), "What is the NV9USB+ Range?"
  (0.911) and "What is the NV9 USB+ Range and what does it do?" (0.899)
  -- a menu of three dead ends where retrieval had the answer. Measured
  over the eval: every wrong candidate shared no stemmed content word with
  its question once product and category names were excluded; every right
  one did, bar one close paraphrase at 0.974. Each candidate now needs a
  shared content word, or to be the top entry at >= 0.95 and not a product
  overview (`FAQ_SEMANTIC_SOLE_MIN`).
- *One candidate is not a choice.* With the rule above, "Can the NV9USB+
  recycle notes?" leaves exactly "Can the NV9USB+ Range provide note
  recycling?" (0.927, shares recycl/note). It is served, not offered
  (`FAQ_AUTO_SERVE_SOLE`, 0.92; harvested entries never).
- *The FAQ scored the question with the product name appended.* In a
  product chat the pipeline hands the FAQ "…? (MyCheckr mini)"; the pool
  is already scoped so the suffix adds nothing, and it dragged the
  paraphrase "What hardware components does MyCheckr include?" from 0.996
  to under the 0.98 bar. Both scores now see the bare question.
- *A family name overrode the picker.* "What hardware does MyCheckr
  include?" in a MyCheckr Mini chat re-scoped to MyCheckr (whose pool lacks
  the Mini's answer). A typed name that is a strict prefix of the selected
  product's name or aliases no longer overrides (`_is_family_parent`).
- *An alias matched inside a word.* Product forms were matched by
  substring on the de-spaced question, so the Spectral's "nv9s" was found
  inside "NV9ST" and the voltage question got the Spectral's table.
  `_token_runs`: a form must equal a run of whole words ("nv9 usb",
  "NV9USB+" and "nv9-usb" still all meet "NV9USB").
- *The MyCheckr-family cases and the stemmer.* `text_utils.stem` keeps a
  final "e", so "notes"/"note" and "recycle"/"recycling" never met; the
  FAQ gate has its own `_norm_word`.

The `run_scenarios.py` score moved 126/160 -> 142/160 on the same 24
scenarios from these changes alone (measured against HEAD in a worktree).
The baseline is re-armed from the final graded run (`--update-baseline`),
so `eval.py` gates on it again. Two cases still fail: `how fast is it?`
(the condenser sometimes rewrites the follow-up onto an older turn; the
case now sits directly after its predecessor, which is what its own
reference says it tests) and the NV9ST voltage question when the model
refuses on a name the manual does not contain -- both vary run to run.

**2. The gateway came back, and its models were measured.** `itl-gpt-pro`
answers 500 (its vLLM host is down: "Cannot connect to host"). `itl-gpt-
flash` works, at **6-9s per call** for a two-sentence answer against 0.9s
for DeepSeek Flash, and it is a reasoning model whose thinking cannot be
turned off: `reasoning_effort`, `thinking`, `chat_template_kwargs.enable_
thinking` and `extra_body` were all tried; `reasoning_content` comes back
every time. A full graded eval through it (default and advanced roles
both on flash): **20/34, identical outcomes to DeepSeek** bar two cases
that flipped one each way. So the gateway is a like-for-like fallback on
quality and a 7x cost on latency until its models expose a thinking
switch. The `reasoning` role still leads with `itl-gpt-pro`; while it is
down the cooldown skips it after the first failure.

**3. NLI on tables.** From `logs.jsonl`, 792 verified turns: NLI passed
429 (54%), the LLM verifier rescued 284 (36%), 79 were flagged. So NLI is
not a formality -- it settles half of all answers at ~0.14s -- and
replacing it wholesale would add a model call to those. What v16.4 does
(tables straight to the LLM verifier, early exit elsewhere) is the right
split; no further change.

**4. The five deferred findings, closed** (pinned in
`src/tests/test_deferred_fixes.py`):
- *"Deep" effort keeps its safety nets.* `QueryRequest.forced_by_effort`
  marks a model forced by an effort level (set only by `_widget_answer`);
  routing keeps the real role, the forced model answers first and falls
  back to the role's chain, and extraction, the backup escalation and the
  grounding retry all run. `top_k` from the effort spec is honoured
  (capped at 2x `CONTEXT_K`).
- *A reranker outage is a 503*, not a documentation gap: `rerank()` marks
  its fallback chunks `rerank_failed` and `query()` refuses as a system
  outage -- nothing charged, no gap recorded.
- *A wall-clock deadline on every provider call*
  (`PROVIDER_DEADLINE_SECONDS`, 120): the request runs on a worker and the
  caller stops waiting; the stream path checks it between chunks. Past it
  the provider counts as unreachable (cooldown).
- *"is there documentation on the MDB pinout?"* is a question: the object
  of a document request is checked for content words and for a
  capitalised code that is not a catalogue name (`_catalogue_terms`).
- *`/query/stream`* is left as the raw-path diagnostic it is: admin only,
  single turn, and its value is showing what the model does WITHOUT the
  pipeline. Documented rather than fixed.

### Shipped — conversation stress test (later still on 2026-09-25)

Fourteen new scripted customer conversations (59 turns,
`src/tests/scenarios/11_*`..`24_*`, driver `src/tests/run_live.py`) were run
through the LIVE `/query` on DeepSeek V4 Flash — the gateway was NXDOMAIN
again — classified, and used to drive fixes. Full report with the ranked
gap list against Intercom Fin / Zendesk / Ada / Sierra-class agents:
`docs/stress-test-2026-09-25.md`; verbatim transcripts before and after
sit beside it. Numbers, same scorer both sides: live turns as expected
**46/59 (78.0%) -> 53/59 (89.8%)**; tests 249 -> **250/250**, 1 skipped;
`run_scenarios.py` original ten 79/79, all 24 files 142/160 (the 18
misses are `followup` labels on the new files, all pronoun/short turns
the classifier calls standalone — left as the engineer wrote them).

- **"I want to talk to a person" / "open a support ticket" / "call me
  back" were answered with "could you tell me more concretely what you'd
  like me to check"** (19 T4, 21 T3/T4). `src/intents.py` recognises the
  request before any search; the turn returns `role: handoff` with
  `offer_support`, and the widget's existing support form is the ticket.
  The widget no longer says "not in my knowledge base" under it. 0.6-0.8s.
  A bare greeting ("hello, how are you today") got a refusal with three
  FAQ links; it is now a greeting. Regression caught in the run: the first
  regex matched "how do I connect **it** to my machine" — pinned.
- **Warranty reached retrieval and was refused** (15 T1; 0 corpus hits for
  "warrant"). `sales._MONEY` now takes `warranty`/`warranties` and the noun
  `guarantee` (the manuals use the verb: "to guarantee the best
  performance"). `test_new_routes` had used the warranty question as one of
  three corpus misses; its third miss is now the operating altitude.
- **A comparison's follow-ups asked "which did you mean?" with both
  products named** (18 T2-T4, 14 T2), and after the switch the next
  pronoun resolved to the OLD product (14 T3, cashbox answered for the NV9
  Spectral). `_COMPARISON` now covers "which one / of the two", "both /
  either", "the same", "faster ... or"; scenario 14 is 4/4. Scenario 18
  now reaches generation, where the model declines to synthesise two specs
  (below, deferred).
- **Second-document requests were summarised or refused** (17 T2 "and the
  pre-requisites checklist too", 17 T3 "send me the NV200 Spectral SSP
  manual as well"): `_OBJECT` rejected trailing "too / as well", `checklist`
  was not a document noun, and the rewrite was never tried. All three
  fixed; both now return download links in <1s.
- **The model refused follow-ups whose standalone form it answered** (16 T6
  "what interfaces does it support then" with the NV9USB+ interface pages
  cited; 11 T5; 23 T3). The only difference between the two prompts is the
  `<conversation>` block, so a refusal on a follow-up with confident
  retrieval is retried once without it. Recovered all three; costs one
  model call only on that shape.

### Deferred from the stress test, with the evidence

- **Comparison synthesis.** With the gate fixed, "which validates notes
  faster, the NV9 Spectral or the NV9USB+" and "do they both use the same
  SSP interface" reach the model and are refused (18 T2/T3 after-run):
  both manuals hold the figure, in different passages. A comparison prompt
  presenting the two products' passages side by side is the next path.
- **"and step 3?"** re-serves the whole procedure (13 T2, before and
  after). Wants the previous answer's numbered list, not retrieval —
  `_expand_previous`-style on `is_more_request`.
- **Non-English questions are refused at the retrieval gate** (24 T1/T2,
  Spanish and German); French price question answered in French by the
  model instead of deflecting (24 T3; `_COMMERCIAL` is English-only).
  Route: condense-to-English on a non-English first turn, "answer in the
  language of the question" in the answer prompt.
- **"Does the MyCheckr support Bluetooth" -> "No."** at grounding 0.002,
  unflagged (15 T2): inferred from the USB/Ethernet list; "bluetooth" has 0
  corpus hits. Inference-contract territory; no path today produces "the
  manual does not mention it".
- **Answer consistency.** The same resolved question was refused in one run
  and answered in the next (11 T5, 12 T1, 13 T4, 18 T1, 19 T2 each flipped
  across four runs) at temperature 0. Single-turn deltas are within this
  noise; the report only claims the reproducible ones.
- **No per-answer feedback from the widget** (thumbs), so "answered but
  wrong" is invisible unless a visitor writes in; no ticket ID shown on a
  handoff; no push to an external desk.
- **Freshly started backends crash after warm-up.** Four launches (nohup
  with `/status` polling, `Start-Process`, WMI, sandbox off) died seconds
  after "System warmup complete": WER logs `python.exe` APPCRASH in
  `RPCRT4.dll`, `0xc0000005`, no Python traceback, 2.9 GB free. `nohup
  python -X faulthandler -m uvicorn` with readiness read from the log
  survived twice. Suspect: today's "models imported on first use" moving
  a torch import off the main thread. Unresolved.
  **Friday review 2026-09-25: a fix for that exact suspect is in the tree.**
  `ccc84cb` imports `sentence_transformers` on the main thread in the
  startup hook, before the warmup thread starts (`src/main.py` ~875-880,
  confirmed in the code). Its own note says the unfixed code crashed on
  three of four starts. This review could not tell whether the four crashes
  above happened before or after `ccc84cb`, and did not launch a backend to
  check. Done looks like: three cold starts in a row on the current HEAD
  with a plain `uvicorn` (no faulthandler), each still answering `/health`
  60s after "System warmup complete". Then strike this.

### Shipped — cleanup and compaction (later on 2026-09-25)

An autonomous cleanup run; its commits on this branch are `5c7a2ef` (files),
`a5deca0` (split), `eac0699` (dead code), `3462559` (performance),
`153851e` (untrack the logs for real) and `ccc84cb` (the torch fix below).
Tests **249/249, 1 skipped** before and after every stage; `import main`
still works; the OpenAPI document has the same 118 operations and identical
component schemas before and after.

- **main.py 7,443 → 3,960 lines.** The route groups moved verbatim into
  nine `routes_*.py` modules (one `APIRouter` each), with `app_state.py`,
  `guards.py` and `providers.py` holding what they share. Nothing in the
  widget or console changed; two tests were repointed (one reads source text
  that now lives in `routes_faq.py`, one clears the emailed-link rate limit
  in `routes_admin_auth`). The answer pipeline (`query`, `query_stream` and
  ~2,900 lines of helpers) stays in main.py because seventeen test files
  patch its collaborators through `main.*`; moving it means moving those
  patches with it, in one change.
- **One source inventory, `docindex.py`.** `/catalog`, `/widget/catalog`,
  `/admin/sources`, `/stats` and the "give me the manual" path each fetched
  every chunk's metadata from Chroma per call. Measured `/catalog`, 30 runs
  over one keep-alive connection on an idle box: **45.4 ms → 2.0 ms** median.
  Every other timed route is within 0.5 ms of before. The two copies of the
  doc-count logic and the two read-a-source's-text loops are one each now.
- **`import main` 11 s → 3 s** for every test run and CLI tool:
  sentence_transformers is imported when the first model loads. Server
  time-to-ready is unchanged (15 s here, stage times identical) because the
  startup hook imports it before spawning the warmup thread -- and it MUST:
  imported first from the warmup thread, torch took the process down with an
  access violation when that thread exited, three of four starts.
- **Stores.** `faq_store.json` is parsed once per change (mtime+size key,
  callers get per-entry copies) instead of once per query and console load;
  `policy.py` now reads and writes through `jsonstore` like every other
  store -- the one deliberate behaviour change: a corrupt `policy.json` is
  refused with a 500 rather than overwritten. In-process FAQ-hit query
  9 → 8 ms; the rest of that path is the embedding forward pass.
- **Dead code removed** (each confirmed unreferenced across src, tests,
  tools, packaging): `embeddings.embedding_dim`, `faq_store.update_answer`,
  `match_answer`, `_gap_store_mtime`, logger's unused readers,
  `structures.harvest_into_faq` and `CHECKLIST_RE`, `answerability.ALL_KINDS`,
  `main._stamp_ingest` and its `SOURCE_FILE_DIR` copy, a second identical
  `keystore.providers()`, and every unused import and variable pyflakes
  reported. Fixed on the way: replacing a document under a NEW filename
  never removed the old chunks (`db.delete_source` with no `db` bound, the
  NameError swallowed by its own except).
- **Files.** Deleted: `brag-output/`, `promo-output/`, `demo/`, `scratchpad/`
  (renders and their node_modules), every `__pycache__`, the stale
  `HANDOFF_console_ux.md`. Untracked, kept on disk: `logs.json`, `logs.jsonl`,
  `eval_results.json`. Removed from git: `before.json`, `after.json`,
  `after_bge.json`, `minilm18.json` (August embedding comparison), and
  `eval_cases_16.json` + `eval_baseline_16.json` (a July superset copy of the
  extensive suite that nothing runs). Moved to `backups/`: `backup_snapshots/`,
  `before-nv9st-retag.zip`, the August full backup.
- **Left alone, deliberately:** `src/legacy/` (release.py writes its drops
  there, CodeQL excludes it); `sweep_grounding.json` and its two logs (a
  saved measurement); root `backend.log`/`tunnel.log`/`testpage.log` (open by
  the running launcher); `handover.txt` (run.ps1 output); the widget JS/PHP
  and `admin.html` (no repeated region of 8+ lines inside either -- sharing
  helpers between them would need a build step); `catalog_config.json`
  caching (3 KB, 0.18 ms a parse, six parses a query -- not worth the copy
  semantics); `widget_api._client_ip` vs `guards._external_ip` (different
  proxy-trust rules, not a duplicate).

### Shipped — latency

- **DeepSeek V4 thinks by default, and nothing turned it off.** `llm.py`
  sent `model` + `messages` + `temperature`; V4 Flash then writes a hidden
  chain of thought, bills it as completion tokens, and only then answers.
  Measured on a 1.3k-token manual prompt, 3 runs each: thinking on 3.15s,
  365-700 completion tokens of which 334-669 were reasoning; thinking off
  0.94s, 30-66 tokens; same answer. Every DeepSeek call — answer, condense,
  verifier, clarify draft, retries, the stream — now sends
  `thinking: {type: disabled}`; `DEEPSEEK_THINKING=on` restores it.
  End to end, `llm_time` per answer went 0.8-5.1s -> 0.6-0.9s and tokens
  per turn 2726 -> 1981 on the worst question. **The reasoning tokens were
  being charged to the visitor's quota.**
- **Provider cooldown** (`PROVIDER_COOLDOWN_SECONDS`, 60): a provider that
  failed to connect or answered 5xx is skipped while the chain has another
  entry. The role `reasoning` leads with the on-prem gateway, which is
  NXDOMAIN again today, so every such question and every grounding retry of
  it paid that failure first.
- **No forced Ollama in api mode.** `generate_with_fallback` appended a
  local/mistral attempt whenever the chain lacked one — in api mode that is
  always, and on a host without Ollama it is a 240s connect after every
  online provider has already failed. Contradicted the "Ollama is never
  touched" comment in `_chain_for`.
- One `requests.Session` with a pool for every provider call (was a fresh
  TCP+TLS per call, three to five calls a turn); query embeddings memoised
  (the same resolved query was embedded three times a turn: FAQ, retrieval,
  router); the router's 30 category examples embed at warmup instead of on
  the first question after a restart; the widget's `async` handler runs the
  quota sqlite calls and the FAQ lookup in the threadpool instead of on the
  event loop.
- **BM25 padded its arm with zero-score chunks** in corpus order up to
  `fetch_n`, and RRF rewarded them: dense #20 + zero-pad BM25 #5 outscored
  dense #1 alone. The arm now ends at the last lexical hit. (Logic and
  latency both.)
- **NLI grounding stops at the first failing unit.** Batching every pair
  into one `predict()` was tried first and measured at no gain — 8.8s vs
  10.2s on a 15-unit answer, noise; the cost is ~50ms per pair on CPU. Early
  exit changes no verdict (the callers branch on the boolean; no rescue
  reads the score). A markdown-table answer skips NLI entirely and goes to
  the LLM verifier, which is where every table answer ended up anyway after
  5-30s of scoring rows NLI cannot read.
- `timing` now logs `retrieval_time` and `verifier_llm_time`, and the
  response carries the verifier split too.

### Shipped — logic (each with the input that used to go wrong)

- **Table punctuation was a grounding unit.** `split_units` emitted
  `|---|---|---|` and `### Heading` as claims; NLI scores them ~0 and the
  gate takes the minimum, so every 3+-column table answer failed on its
  punctuation. Stripped now.
- **The number rescue used substring containment.** "30 notes" was
  supported by "300 notes", "12V" by "revised 2012", "1.2 kg" by "1.25 Kg"
  — the contradictions the gate exists for. Whole-number match now, and
  never on a table.
- **The picker was overridden by the rewrite, not the question.** With a
  product selected, the override read `_products_named_in(resolved_query)`,
  and the condense prompt tells the rewriter to carry the product over from
  history. Pick NV9USB+, ask its supply voltage, change to BV30, ask "and
  the current draw?" -> answered for the NV9USB+ under a BV30 scope bar.
  Now the typed text decides when a picker is set; the rewrite still counts
  when there is no picker (it is a follow-up's only product signal).
- **`_AFFIRMATIVE` had no word boundary.** "Yesterday the NV9 stopped
  accepting notes", "okay so what about android then?", "please tell me the
  weight" all served the pending steps. `\b`, <= 8 words, no question word.
- **Commercial classifier on technical vocabulary.** "quotes around the
  value", "subscribe to age result events", "availability of the RS232
  port", "third-party power supplier" all deflected to the sales reply.
- **A customer-facing refusal was remembered**, so "tell me more" expanded
  it into passages or "that is everything the documentation has" — the
  memory filter only knew the model's own refusal string.
- **An off-topic cross-reference claimed the refusal**: any NV200S refusal
  that retrieved p84 said "the manual refers jam recovery to the Service
  Guide, which I don't hold" and dropped the suggested questions. A
  deferral now needs word overlap with the question OR to come from the
  top-ranked passage ("screen size" vs "the dimensions of the device").
- **The widget charged a credit for our own outage** (`service_degraded`).
- **A curated question typed verbatim in a product chat was never
  auto-served**: the FAQ matcher saw the retrieval-expanded "…? (NV9
  Spectral)" and scored 0.50 against itself. Every widget chat is scoped,
  so every customer got "is this what you meant?" instead. Two eval FAQ
  cases flipped to pass on the backend that had this fix.
- Catalogue-derived classifier caches (`text_utils._PRODUCT_TERMS`,
  `retrieval_db._PRODUCT_CATEGORY`) reset on catalogue save; they lived
  until restart.
- `safe_generate` removed (dead; referenced an undefined name).

### Measured and rejected

- **DeepSeek V4 Pro as the default.** Same code, `ONLINE_DEEPSEEK_MODEL=
  deepseek-v4-pro`: `llm_time` 1.2-4.0s vs 0.6-0.9s, and on the five
  probe questions it refused three that Flash answered (bezel colour,
  cashbox capacity, protocol steps). eval --no-grade 20/34 vs 18/34, but
  both extra passes are the FAQ verbatim fix above, which only that backend
  had. No quality gain to pay 2-3x latency for. The gateway models
  (`itl-gpt-pro`, `itl-gpt-flash`) could not be tested: NXDOMAIN all day.
- **Thinking mode as a quality lever**: the old backend (thinking on) and
  the new (off) produced identical eval outcomes on all 34 cases.
  **CORRECTED (Friday review 2026-09-25): true for answer writing, false for
  judgement.** With thinking off the verifier approved a wrong flash code
  4/4 and rejected the right one 2/2 (`1f594c0`). The 34-case eval could not
  see this because none of its cases depended on the verifier catching an
  error. Judgement calls now think again.

### Deferred, with the evidence — **all closed on the night of 2026-09-25**,
see *Shipped — the four follow-ups* above; kept for the record.

- ~~**"Deep" effort buys fewer safety nets.**~~ `_widget_answer` maps deep to
  `force_provider/force_model`, which makes `role="rethink"`, and that role
  skips extraction, backup escalation and the grounding retry
  (`main.py` ~6145, ~6427, ~6491). Its `role`/`top_k` parameters are never
  read. Map deep to a real role instead of the rethink path.
- ~~**A reranker outage reads as a documentation gap.**~~ `reranker.py` returns
  chunks without `rerank_score` on failure -> confidence "none" ->
  "rejected" + `record_gap`. Should be a system refusal. Fail-closed, so no
  wrong answer, just a false gap.
- ~~**`/query/stream` skips**~~ condensation, name scoping, sales deflect, the
  template-leak check, the LLM verifier and procedure completion. Admin
  only; noted.
- ~~**No overall deadline on a provider call.**~~ `timeout=60` bounds connect
  and each read, not the total; one logged turn has `escalation_time`
  16544s. A worker-thread `future.result(timeout)` or a streaming cap.
- ~~**`doc_request` on "is there documentation on the MDB pinout?"**~~ returns
  the file list; `_CONTENT_WORDS` checks the prefix only. Needs the
  catalogue to tell a product from a topic in the object.
- **rerank_time 1.2s on the new process vs 0.9s on the old** for the same
  question, steady state. Not explained; same model, same candidates. Worth
  one look at torch thread settings when two backends share a box.

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

  *(Both superseded, 2026-09-29, 9.5: nightly archives go through
  manage_backup.py with BACKUP_PASSPHRASE and are encrypted (scrypt);
  BACKUP_ALLOW_PLAINTEXT now governs only the console export; the nightly
  task writes to D:\GroundedOpsBackups.)*

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
  **Still open, Friday review 2026-09-25:** the case is unchanged at
  `src/eval_cases.json:111` and was one of the two failures in the 32/34
  run.

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

  **Friday review 2026-09-25: the eval no longer exercises this.** In the
  latest graded run (`src/eval_results.json`, 14:52) the case passes, but
  `layer: faq`, `provider: faq`, `grounding: None`. A curated FAQ answers
  it now, so the generation path that scored 0.0051 never runs. Still
  worth the one look, but it needs `skip_faq: true` to reproduce.

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

  **2026-09-24: the per-IP ceiling did not exist in the default guest mode.**
  Checking the claim above found it false for FAQ-only guests, the mode that
  ships. `quota.consume_faq_lookup` counted every lookup against the IP, but
  `check_faq_lookup` read only the per-visitor count, and the visitor id
  comes from the browser too, so clearing site data reset everything. It now
  enforces `anon_ip_daily` on FAQ lookups as well (reason `ip_faq_quota`;
  the widget still shows its usual daily-limit message). `reset_visitor` also
  missed the per-IP credit counter, so a guest blocked on `ip_quota` stayed
  blocked after a console reset; fixed. Pinned by
  `tests/test_quota_ceilings.py`. The session caps are still a cost guard by
  design. No server-issued session id would change that, because a caller
  can just ask for a new one. What bounds a caller is identity: the IP for
  guests, the account for members.

- ~~**~50s of a 113s answered query is unattributed.**~~ — **RESOLVED
  2026-09-25, and the figure was stale.** It came from v15.1, when the
  `timing` dict had four keys. Re-traced against the 52 timed entries in
  `logs.jsonl`: on answered turns the gap between `total_time` and the
  logged stages is now ~0.8s median. The big gaps (201.9s, 67.9s, 29.0s)
  were all turns where the model refused or the answer was flagged, and the
  time went into `_structures_for(force_kind="table")` reading every page
  of each cited PDF for its vocabulary — untimed, per process, cache
  cleared wholesale at 16 files. That vocabulary is now persisted
  (`doc_vocab.json`, `DOC_VOCAB_CACHE`). And "NLI grounding 8.4s" was
  mostly the LLM verifier's second full-context call, which `grounding_time`
  hid; `verifier_llm_time` and `retrieval_time` are now logged separately.

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

- **Code scan 2026-08-28 — five findings: #1 fixed, #4 half done, #2,
  #3 and #5 still open** (re-checked against the code in the Friday review
  2026-09-25). A read-through of the live tree (`legacy/` excluded) for
  gaps not already on this list.

  1. ~~**Non-atomic writes on the only copy of customer data.**~~ —
     **FIXED `f40ce3b` (2026-09-02).** Leads, the FAQ store and the
     catalogue now write through `jsonstore.save` (temp file, fsync,
     `os.replace`). The gap log `faq_gaps.json` still uses bare
     `open(..., "w")` in `faq_store.py` (8 sites). That is a regenerable
     log, not customer data. Original text:
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
     past the cap rather than discarding history. **Fixed 2026-09-29
     (8.9):** a full store refuses with 503 and a warning, no eviction;
     the route is capped per visitor and per IP.

  3. **`transcript` is the one untrusted field with no size cap.** `q` 500,
     `notes` 2000, each `values` entry 2000, `enquiry` 4000 — all bounded.
     `transcript` is bounded only by COUNT (`[-20:]` in `add_lead`, `[-12:]`
     in draft), never by entry size, and `/widget/lead` stores entries
     verbatim. Twenty 10MB entries is ~200MB in one lead, in a file fully
     read and rewritten on every subsequent write; uvicorn sets no default
     body limit. (Draft is safe — `_assemble_enquiry` returns `[:4000]`.)
     **Fixed 2026-09-29 (8.9):** each entry is truncated to 4000 chars.

  4. **`/health` cannot detect the failure it is used to rule out.**
     `main.py:658` returns `{"status":"ok"}` unconditionally, touching
     neither Chroma nor a provider key. Both the Docker healthcheck and
     **STAGING.md step 4** treat it as the verification step, so
     `docker compose ps` reports healthy while the index is missing and
     every answer refuses — exactly the state a fresh deploy is in. A
     `?deep=1` variant that counts collection rows would make the runbook's
     check real.
     **HALF DONE:** `/health?deep=1` exists (since `1365920`, 2026-09-01)
     and returns 503 when the index is empty, no key is set or the models
     are not loaded. But neither consumer uses it:
     `docker/Dockerfile.backend:35` still probes plain `/health`, and so
     does `STAGING.md:139`. Done looks like: the runbook step uses
     `?deep=1`, and the container healthcheck either does too (with a
     `start-period` longer than the ~30s model load) or says why not.

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
  on this list. **Corrected 2026-09-30:** there is no separate sheet, and
  none of the five is a missing document. Each is a section of the manual
  that cites it (Technical Data p36/Mini p29, the NV200S Service Guide
  chapter p73, BNF Path Guide Inserts p112, Lock Specification p38, Action
  Data Update p39); see 9.8.

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
- **The blind set trails v16.3 by 2-3 cases, and nobody has shown that is
  noise.** `src/eval_cases_blind.json` (added in `1f594c0`), grader-judged:
  v16.3 **28/34**, this branch **25-26/34** after the verifier fix. The
  commit calls that "within run-to-run noise". The original suite's own noise
  (±3 cases between two runs of one baseline, 2026-08-28) makes that
  plausible, but that noise figure is from a different suite. Two blind runs per
  side cannot tell a regression from noise. It matters because the branch
  is about to be rated against v16.3, and the blind set is the only
  measure that nothing has been tuned against. Done looks like: 3 runs
  each of v16.3 and HEAD, per-case outcome diff, and every case that fails
  on HEAD in all 3 runs but passes on v16.3 in all 3 either explained or
  filed. **Measure only. Do not tune against this set.**
- **The verifier's thinking has a latency cost nobody has measured.**
  `1f594c0` turned DeepSeek thinking back on for the verifier, the
  inference contract and re-answer selection. On an answer-sized prompt,
  thinking cost 3.15s against 0.94s per call (see *Shipped — latency*).
  From 792 logged turns, 284 (36%) were settled by the LLM verifier, plus
  every table answer, which skips NLI. So roughly a third or more of answered
  turns may have gained ~2s. That is an estimate from two separate
  measurements, not a measurement. Done looks like: `verifier_llm_time` median and
  p90 from `logs.jsonl`, turns before `1f594c0` against turns after, and
  the figure recorded here.
- **`eval.py` grades nothing unless `DEEPSEEK_API_KEY` is exported, and
  the preflight does not notice.** Re-checked 2026-09-25 in the code:
  `_deepseek_key()` reads the environment, then falls back to
  `keyvault.load_key()`. Nothing loads `src/.env`, and `preflight()`
  checks only that `/query` generates. The night run lost a full graded
  pass to this: every check came back "grader returned nothing". Done
  looks like: the preflight makes one grader call and aborts on an empty
  verdict, or eval.py loads `src/.env` itself. The preflight check is safer, because it also
  catches a bad key. Pinned by a test that runs the preflight with no key
  in the environment.

**Serving**

- **The widget does not stream.** `/query/stream` exists with sentence-level
  `StreamGrounder` verification; `groundedops-widget.js:1939` (line re-checked 2026-09-25) posts to the
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

  **CORRECTION (Friday review 2026-09-25): it is gone from DNS again.**
  `Resolve-DnsName` returned "DNS name does not exist" during this review,
  after the night's measurements of `itl-gpt-flash` had worked. It has gone
  down three times on record: NXDOMAIN until 2026-09-22; back; NXDOMAIN
  during the 25th's stress test; back that night; NXDOMAIN again now. Anything that depends on the gateway should
  re-check DNS on the day and not trust this file. The owner of the host
  has never been named here, and naming them is the useful next step.
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

- **2026-09-25 (Friday review) — ticked off from the open list:**
  - ~~**PR for branch `fix/wire-faq-choices` → `main` not yet opened.**~~
    Merged as hamzahrizvi/groundedops#7 on 2026-08-19, with #8 after it.
    `gh` is authenticated, and the branch has no commits that are not in
    `main`. The item had been stale for five weeks.
  - ~~**Nothing since `ea4320a` is verified end to end.**~~ The numbered
    passages, prompt changes and clarify gate have all been through real
    generations since. Evidence: the 2026-09-25 live stress test (14
    conversations through `/query`), graded eval 32/34 with the baseline
    re-armed (`2ed5276`), and the blind set (`1f594c0`).
  - ~~**Code scan finding 1, non-atomic writes.**~~ `f40ce3b`, see the
    code-scan item.
- **2026-09-25 — the verifier thinks again** (`1f594c0`). With thinking
  off it had inverted on a flash-code table. See the dated section at the
  top.

- **2026-09-25 — conversation stress test: handoff, greeting, warranty,
  comparison follow-ups, second-document requests, follow-up refusal
  retry.** 14 live scenarios 46/59 -> 53/59; tests 250/250. Report
  `docs/stress-test-2026-09-25.md`. Commits: scenarios + runner + report,
  and the fixes, both on `experimental/v16.4-logic-and-latency` (see
  `git log --since=2026-09-25T09:00`).
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
