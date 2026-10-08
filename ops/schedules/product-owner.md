Product owner run for the GroundedOps agent office (Tuesday and Friday). Nobody is present: never ask questions.

REPO = C:\Users\hrizvi\Downloads\Git\groundedops (the main checkout)
OPS  = C:\Users\hrizvi\groundedops-ops

0. Move the session to REPO with the change_directory tool. Read REPO/ops/schedules/office-status.md and use it for every "status" and "event" below.
   If REPO/.claude/agents/product-owner.md or REPO/tools/gate_run.sh is missing, the office branch is not merged yet: status po idle "Waiting for the office branch to be merged", then stop.
1. Status po working "Reading this week's findings".
2. Gates. For each OPS/inbox/dev/<id>.md whose status is not BLOCKED and that has no OPS/inbox/dev/<id>.gate/gate.json yet:
   find its work order in OPS/decisions/*.md for the run folder and the hidden sibling file paths, and the worktree path in the hand-back.
   Status lab working "Checking Dev's fix against hidden questions". Event po -> lab "Check <id>".
   Run `bash tools/gate_run.sh "<worktree>" "<OPS>/runs/<stamp>" "<OPS>/inbox/dev/<id>.gate" <sibling files...>` with run_in_background and wait for its completion notice.
   Status lab idle with last "Gate PASS for <id>" or "Gate FAIL for <id>". Event lab -> po "Gate passed" or "Gate failed".
3. Spawn the product owner: Agent tool with subagent_type "product-owner"; if not listed, use general-purpose with model "fable" and tell it to read REPO/.claude/agents/product-owner.md and follow it exactly.
   Prompt: "Triage. REPO = <REPO>. OPS = <OPS>. Today = <YYYYMMDD>. Gates run this time: <ids or none>."
4. Read OPS/decisions/<today>.md. Build the CEO tray: every item with an unticked "- [ ] CEO approved" line, and every READY TO MERGE item whose branch is not yet merged (`git branch --merged HEAD` in REPO), at most 6, each {id, title, why} in plain words.
   Update board ceo with that list. Status po idle with last "<n> decided, <k> need you". If k > 0: status ceo waiting "<k> things need your yes", and event po -> ceo "Report: <k> need your yes"; otherwise event po -> ceo "Weekly report".
5. Boards and rewards: run `.venv/Scripts/python.exe tools/ops_ledger.py --ops "<OPS>" --repo "<REPO>" > "<OPS>/ledger.json"`.
   Upload it to the office: `get` collection "ledger", doc_id "current"; then `set` it with file_path "<OPS>/ledger.json" (pass if_version = the version you got, or none if it did not exist).
6. Finish with the product owner's final message.

CONSTRAINTS: never edit, stage or commit repo files; never reindex, rebuild an .exe, push, or use the DeepSeek key.
