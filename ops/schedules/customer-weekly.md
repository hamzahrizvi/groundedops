Weekly Customer run for the GroundedOps agent office. Nobody is present: never ask questions, never guess past a failure.

OFFICE = C:\Users\hrizvi\Downloads\Git\groundedops\.claude\worktrees\agent-org (branch experimental/agent-org-phase1: agent definitions, tools, ops)
REPO   = C:\Users\hrizvi\Downloads\Git\groundedops (the product under test: src/, .venv, the live index)
OPS    = C:\Users\hrizvi\groundedops-ops

PURGE: every phase below runs in a fresh subagent that starts from a cleared context. Give it only the paths it needs and take back only its final five lines. Never read raw answers, logs or manuals yourself.

0. Move the session to OFFICE with the change_directory tool. Use the OFFICE STATUS section at the end for every "status" and "event" below.
   WORK ORDER: if the dispatcher's why is "work order <id>", follow the WORK ORDER section near the end instead of the numbered steps below.
   If OFFICE/.claude/agents/customer.md or OFFICE/tools/weekly_blind.sh is missing: status customer idle "The office checkout is missing", then stop.
1. STAMP = today as YYYYMMDD; if OPS/runs/STAMP already exists (a second run the same day), add the first letter from b on that makes it new (YYYYMMDDb, YYYYMMDDc, ...). RUN = OPS/runs/STAMP. Create RUN/parts, OPS/inbox/customer and OPS/heldout/STAMP.
   Status customer working "Reading the manuals".
   Export the manuals: `cd "<REPO>/src" && HF_HUB_OFFLINE=1 ../.venv/Scripts/python.exe "<OFFICE>/tools/export_manuals.py" "<RUN>/manuals"`.
   Retention first (D-20261010-22): run `"<REPO>/.venv/Scripts/python.exe" "<OFFICE>/tools/ops_pivot.py" --prune --ops "<OPS>" --repo "<REPO>"`. It deletes widget_feedback.json, real_questions.json and real_categorised.json from runs older than RETENTION_DAYS (90) and keeps their counts, and older votes from newer runs.
   Export the week's website questions and the week's widget votes (without session ids): `"<REPO>/.venv/Scripts/python.exe" "<OFFICE>/tools/ops_pivot.py" --export-real "<RUN>" --src "<REPO>/src"`.
2. RE-ASK: if the dispatcher started this run with a why that begins "re-ask" (a fix merged while Customer's other fixes are in flight; tools/office_next.py), copy cases.json from the newest OPS/runs/*/ that has DONE into RUN, status customer working "Re-asking the last 40 questions after a fix", and go to step 3. The rest of step 2, and step 5, are skipped for a re-ask: the questions and their findings already exist.
   Otherwise status customer working "Writing 40 new customer questions".
   Plan the 40: the 10 types (lookup, yes_no_documented, table_condition, procedure_or_troubleshoot, comparison, followup, multi_document, absent_feature, phantom_or_offtopic, vague) 4 each, spread so every manual in RUN/manuals gets at least 3, with 2 or 3 phantom/vague questions under product "none".
   MODEL PICK once (agent customer, job write, inputs RUN/manuals), then spawn one customer subagent PER PRODUCT with that model and effort, all in one message so they run in parallel: Agent tool with subagent_type "customer"; if not listed, general-purpose with model "sonnet" told to read OFFICE/.claude/agents/customer.md and follow it exactly. Prompt: "Job WRITE-PART. RUN = <RUN>. OFFICE = <OFFICE>. PRODUCT = <p>. TYPES = <type: count, ...>. ID PREFIX = P<STAMP>-<p>."
   Merge RUN/parts/*.json into RUN/cases.json as {"cases": [...]} with a short python one-liner, renumbering the ids C<STAMP>-01 to -40, and add every part's "new_categories" to OPS/categories.json. Check: 40 cases, unique ids, every case has id, type, category, expected, q, reference. Re-run a failing part once.
3. Status customer waiting "Waiting for the chatbot to answer all 40". Status lab working "Asking the chatbot 40 questions". Event customer -> lab "40 new questions".
   Run `PY="<REPO>/.venv/Scripts/python.exe" GO_CODE="<REPO>/src" GO_DATA="<REPO>/src" bash "<OFFICE>/tools/weekly_blind.sh" "<RUN>"` with run_in_background and wait for its completion notice. Do not poll with sleep.
   If RUN/FAILED exists: status lab blocked "The test run failed; see the run log", event lab -> po "Test run failed at <phase>", status customer idle "Run failed, nothing to review", then stop.
4. Read only the "summary" key of RUN/cases_state.json. Status lab idle with last "Graded: <correct> right, <partial> partly, <wrong> wrong". Event lab -> customer "Graded answers".
5. (Skipped for a re-ask.) Status customer working "Checking each miss against the manual". MODEL PICK (agent customer, job review, inputs RUN/cases_state.json summary), then spawn a fresh customer subagent with it: "Job REVIEW. RUN = <RUN>. OFFICE = <OFFICE>."
6. Product status figures: run `"<REPO>/.venv/Scripts/python.exe" "<OFFICE>/tools/ops_pivot.py" --ops "<OPS>" > "<OPS>/pivot.json"`, then upload: `get` collection "pivot", doc_id "current"; `set` it with file_path "<OPS>/pivot.json" (if_version = the version you got, or none if it did not exist).
7. Status customer idle with last = the first line of its summary. Event customer -> po "<n> findings filed".
   Finish with the agent's summary.

CONSTRAINTS: never edit, stage or commit files in REPO or OFFICE; never reindex, rebuild an .exe, push, or use the DeepSeek key outside the lab's own scripts (tools/blind_eval.py and the scripts that run it). OPS is the only place this run writes.
