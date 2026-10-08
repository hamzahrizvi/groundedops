Weekly Customer run for the GroundedOps agent office. Nobody is present: never ask questions, never guess past a failure.

REPO = C:\Users\hrizvi\Downloads\Git\groundedops (the main checkout)
OPS  = C:\Users\hrizvi\groundedops-ops

0. Move the session to REPO with the change_directory tool. Read REPO/ops/schedules/office-status.md and use it for every "status" and "event" below.
   If REPO/.claude/agents/customer.md or REPO/tools/weekly_blind.sh is missing, the office branch is not merged yet: status customer idle "Waiting for the office branch to be merged", then stop.
1. STAMP = today as YYYYMMDD. RUN = OPS/runs/STAMP. Create RUN, OPS/inbox/customer and OPS/heldout/STAMP.
   Status customer working "Reading the manuals".
   Export the manuals: `cd src && HF_HUB_OFFLINE=1 ../.venv/Scripts/python.exe ../tools/export_manuals.py "<RUN>/manuals"`.
   If src/widget_feedback.json exists, copy it into RUN (real customer votes).
2. Status customer working "Writing 40 new customer questions".
   Spawn the customer agent: Agent tool with subagent_type "customer"; if that type is not listed, use general-purpose with model "sonnet" and tell it to read REPO/.claude/agents/customer.md and follow it exactly. Prompt: "Job WRITE. RUN = <RUN>."
   Check RUN/cases.json parses, has 40 cases, unique ids, and every case has id, type, expected, q, reference. If not, send it back once with the list of problems.
3. Status customer waiting "Waiting for the chatbot to answer all 40". Status lab working "Asking the chatbot 40 questions". Event customer -> lab "40 new questions".
   Run `bash tools/weekly_blind.sh "<RUN>"` with run_in_background and wait for its completion notice. Do not poll with sleep.
   If RUN/FAILED exists: status lab blocked "The test run failed; see the run log", event lab -> po "Test run failed at <phase>", status customer idle "Run failed, nothing to review", then stop.
4. Read only the "summary" key of RUN/cases_state.json. Status lab idle with last "Graded: <correct> right, <partial> partly, <wrong> wrong". Event lab -> customer "Graded answers".
5. Status customer working "Checking each miss against the manual". Spawn the customer agent again: "Job REVIEW. RUN = <RUN>."
6. Status customer idle with last = the first line of its summary. Event customer -> po "<n> findings filed".
   Finish with the agent's summary.

CONSTRAINTS: never edit, stage or commit repo files; never reindex, rebuild an .exe, push, or use the DeepSeek key. OPS is the only place this run writes.
