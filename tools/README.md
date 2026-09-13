# tools/

Verification scripts. These were written during the v16.2/v16.3 work and
kept because each one caught something that reading the code did not.

All of them expect a backend already running; none of them start one.

| Script | What it is for |
|---|---|
| `eval_battery.py` | 19 grounded questions × 3 repeats against `/query`, across all four product ranges. **Repeats are the point** — a single pass cannot tell a fix from noise (see HANDOFF.md). |
| `verifier_probe.py` | Scores the LLM verifier against known-good answers and deliberate fabrications, including fault-code mispairings. Use before changing the verifier prompt. |
| `console_audit.py` | Logs into a throwaway console and screenshots every page in both themes, reporting JS errors. Catches render breakage no unit test sees. |
| `capture_screenshots.py` | Regenerates `docs/img/*` for the README, driving the real widget against the real backend. |

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

## Two traps these scripts exist because of

**Run from `src/`, always.** Every store path is relative. From the repo
root you get an *empty* Chroma and a *different* catalogue built from
defaults, with different product keys. It looks like catastrophic data loss
and is nothing of the sort. This cost an hour.

**`force_model` changes the code path.** It sets `role="rethink"`, which
performs worse than the normal path. An A/B run through it measures the
rethink path, not production.
