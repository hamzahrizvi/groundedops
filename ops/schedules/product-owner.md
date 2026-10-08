Product owner run for the GroundedOps agent office (Tuesday and Friday). Nobody is present: never ask questions.

OFFICE = C:\Users\hrizvi\Downloads\Git\groundedops\.claude\worktrees\agent-org (branch experimental/agent-org-phase1: agent definitions, tools, ops)
REPO   = C:\Users\hrizvi\Downloads\Git\groundedops (the product: src/, .venv, PENDING.md, the live index)
OPS    = C:\Users\hrizvi\groundedops-ops

PURGE: the triage runs in a fresh subagent that starts from a cleared context; the gate runs are shell jobs. Read only summaries and gate.json yourself.

0. Move the session to OFFICE with the change_directory tool. Use the OFFICE STATUS section at the end for every "status" and "event" below.
   If OFFICE/.claude/agents/product-owner.md or OFFICE/tools/gate_run.sh is missing: status po idle "The office checkout is missing", then stop.
1. Status po working "Reading this week's findings".
2. Gates. For each OPS/inbox/dev/<id>.md whose status is not BLOCKED, that has no OPS/inbox/dev/<id>.gate/gate.json yet, and whose branch agents/dev-<id> is not already merged (`git -C "<REPO>" branch --merged HEAD` or `--merged experimental/agent-org-phase1`):
   find its work order in OPS/decisions/*.md for the run folder and the hidden sibling file paths, and the worktree path in the hand-back.
   Status lab working "Checking Dev's fix against hidden questions". Event po -> lab "Check <id>".
   Run `PY="<REPO>/.venv/Scripts/python.exe" GO_DATA="<REPO>/src" bash "<OFFICE>/tools/gate_run.sh" "<worktree>" "<OPS>/runs/<stamp>" "<OPS>/inbox/dev/<id>.gate" <sibling files...>` with run_in_background and wait for its completion notice.
   Status lab idle with last "Gate PASS for <id>" or "Gate FAIL for <id>". Event lab -> po "Gate passed" or "Gate failed".
3. Spawn the product owner: Agent tool with subagent_type "product-owner"; if not listed, use general-purpose with model "fable" and tell it to read OFFICE/.claude/agents/product-owner.md and follow it exactly.
   Prompt: "Triage. REPO = <REPO>. OFFICE = <OFFICE>. OPS = <OPS>. Today = <YYYYMMDD>. Gates run this time: <ids or none>."
4. Read OPS/decisions/<today>.md. Build the CEO tray: every item with an unticked "- [ ] CEO approved" line, and every READY TO MERGE item whose branch is not yet merged (`git -C "<REPO>" branch --merged HEAD`), at most 6, each {id, title, why} in plain words.
   Update board ceo with that list. Status po idle with last "<n> decided, <k> need you". If k > 0: status ceo waiting "<k> things need your yes", and event po -> ceo "Report: <k> need your yes"; otherwise event po -> ceo "Weekly report".
5. Boards and rewards: run `"<REPO>/.venv/Scripts/python.exe" "<OFFICE>/tools/ops_ledger.py" --ops "<OPS>" --repo "<REPO>" > "<OPS>/ledger.json"`.
   Upload it to the office: `get` collection "ledger", doc_id "current"; then `set` it with file_path "<OPS>/ledger.json" (pass if_version = the version you got, or none if it did not exist).
6. Finish with the product owner's final message.

CONSTRAINTS: never edit, stage or commit files in REPO or OFFICE; never reindex, rebuild an .exe, push, or use the DeepSeek key.
