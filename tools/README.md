# tools/

Verification scripts. These were written during the v16.2/v16.3 work and
kept because each one caught something that reading the code did not.

All of them expect a backend already running, except `overnight_eval.sh`,
which starts (and stops) its own.

For a release-facing gate, use `src/eval.py --repeats 3`. Cases can be marked
as `faq`, `retrieval`, `comparison`, `refusal`, or `grounding`; use
`--layer retrieval` with `skip_faq: true` to measure retrieval rather than the
curated-answer shortcut. A repeated case is stable only when every run passes.
The starter retrieval suite is `src/eval_cases_retrieval.json`; review three
runs, then lock it separately with `--baseline eval_baseline_retrieval.json
--update-baseline`.

| Script | What it is for |
|---|---|
| `eval_battery.py` | 19 grounded questions × 3 repeats against `/query`, across all four product ranges. **Repeats are the point** — a single pass cannot tell a fix from noise (see HANDOFF.md). |
| `verifier_probe.py` | Scores the production LLM verifier (`main._llm_verified`: its prompt, thinking gate and parsing) on 26 cases in four groups (correct, wrong incl. fault-code mispairings, absence "No"s, stated-"No" controls), N runs each. `--judge deepseek\|anthropic\|openai` swaps the vendor. Use before changing the verifier prompt. |
| `grader_ab.py` | Re-grades one batch's frozen graded answers N times with the eval grader as-is and N times inside `llm.judging()`, and checks both against `src/eval_labels.json`. Writes `<batch>/grader_ab.json`. |
| `console_audit.py` | Logs into a throwaway console and screenshots every page in both themes, reporting JS errors. Catches render breakage no unit test sees. |
| `capture_screenshots.py` | Regenerates `docs/img/*` for the README, driving the real widget against the real backend. |
| `overnight_eval.sh` | PENDING.md session 4 as one unattended job: starts its **own** HEAD backend and proves it is HEAD, runs the M4 repeats, `run_live --path both`, the v16.3 blind2 side on a copied index, and the M13 sweep, then writes `eval_runs/<stamp>/summary.json`. `STEPS="m4 summary"` runs a subset. |
| `eval_batch_summary.py` | Turns one batch directory into that `summary.json`: flip tables, blind per-case flips, widget agreement, and the M2-labelled log window. Offline. |
| `export_manuals.py` | Writes each product's manual text from the index, one file per product (run from `src/`). The blind question writer reads only these; the whole-manual baseline pastes them in. |
| `blind_eval.py` | The blind runner (was copied into each `eval_runs/` folder): this bot vs a whole-manual gpt-5-mini baseline, graded blind by gpt-5. `GO_CODE`/`GO_DATA` run a Dev worktree's code on the live index; `reset` re-measures a copied run. The grader also sees the full text of each case's cited pages; `prompt <case_id>` prints a grading prompt without calling the API. |
| `retrofit_gate.py` | Pass/fail for a fix: hidden sibling questions >= 2/3, nothing right became wrong, no drop beyond noise. `--selftest`. |
| `weekly_blind.sh`, `gate_run.sh` | The agent office's shell jobs (`ops/README.md`): the weekly blind run, and the gate on a Dev branch. |

## The throwaway console

`console_audit.py` and the UI test need a backend with its stores pointed at
a temp directory, so nothing touches a real install:

```bash
T=/c/Users/hrizvi/AppData/Local/Temp/gotest && rm -rf $T && mkdir -p $T/docs $T/src_files
cd src && ACCOUNTS_PATH=$T/accounts.json FAQ_STORE_PATH=$T/faq.json \
  FAQ_GAP_PATH=$T/gaps.json WIDGET_CONFIG_PATH=$T/widget.json \
  WIDGET_LEADS_PATH=$T/leads.json CONVO_DB_PATH=$T/convo.db \
  QUOTA_DB_PATH=$T/quota.db POLICY_PATH=$T/policy.json \
  CHROMA_DIR=$T/chroma CATALOG_CONFIG=$T/catalog.json \
  SOURCE_FILE_DIR=$T/src_files ALLOWED_EMAIL_DOMAIN= \
  ../.venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8099
```

`ALLOWED_EMAIL_DOMAIN=` must be cleared or the first sign-in — which
bootstraps the root account — is rejected for using an outside address.

## Before/after comparison (eval.py --compare-results)

Two backends, one per side, each on its **own copy** of the index and
stores. A bare `uvicorn` launch of the second side opens the same
`src/chroma_db` and JSON stores as `:8000`, so its writes (FAQ gaps,
memory, quota) leak into the first side's run. Copy the index first
(`cp -r src/chroma_db $T/chroma`), start side B with the store variables
above pointed at `$T` and `CHROMA_DIR=$T/chroma`, and record which
`chroma_db` each side used: the index changes under re-ingests and table
backfills, and a comparison across two indexes measures the index, not
the code.

Then three commands, from `src/`:

```bash
EVAL_URL=http://127.0.0.1:8000/query python eval.py --cases eval_cases_blind2.json --repeats 3 --results results_A.json
EVAL_URL=http://127.0.0.1:8099/query python eval.py --cases eval_cases_blind2.json --repeats 3 --results results_B.json
python eval.py --compare-results results_A.json results_B.json
```

To reset a pass mark from a repeated run on unchanged code (M4), build it
from the saved results instead of re-running against a backend:

```bash
python eval.py --baseline-from-results ../eval_runs/<stamp>/m4_tuned.json --baseline eval_baseline.json
```

Only cases that passed on every attempt go in; blind results are refused.

The comparison is offline. Quote its per-case lines (stable pass on one
side, stable fail on the other), never the totals: the suite carries about
+/-3 cases of run-to-run noise as a ceiling (measured 2026-09-28 on HEAD:
one flip in 470 case-runs; the v16.3 build wobbled 4 cases in 34, so
re-measure per build). A blind file prints MEASURE-ONLY and
`--update-baseline` refuses it (exit 2, nothing written). The `wall`
p50/p90 per role is advisory: two backends on one box skew each other
(PENDING.md records the same question reranking in 1.2s on one process
and 0.9s on the other). For the retrieval layer alone, use
`eval_retrieval.py --compare`, which is deterministic and needs no grader.

## Two traps these scripts exist because of

**Run from `src/`, always.** Every store path is relative. From the repo
root you get an *empty* Chroma and a *different* catalogue built from
defaults, with different product keys. It looks like catastrophic data loss
and is nothing of the sort. This cost an hour.

**`force_model` changes the code path.** It sets `role="rethink"`, which
performs worse than the normal path. An A/B run through it measures the
rethink path, not production.
