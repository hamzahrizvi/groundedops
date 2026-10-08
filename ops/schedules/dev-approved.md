Dev run for the GroundedOps agent office (weekday evenings). Nobody is present: never ask questions.

REPO = C:\Users\hrizvi\Downloads\Git\groundedops (the main checkout)
OPS  = C:\Users\hrizvi\groundedops-ops

0. Move the session to REPO with the change_directory tool. If REPO/.claude/agents/dev.md is missing, the office branch is not merged yet: stop with one line.
1. Find work: in OPS/decisions/*.md, items marked AUTO-APPROVED or carrying a ticked "- [x] CEO approved" line, whose Assigned is dev (or missing), with no OPS/inbox/dev/<id>.md yet and not yet merged. Take the oldest one, except that items found by this month's rewards leader ("rewards" -> "leader" in OPS/ledger.json) go first.
   None: stop with the line "Nothing approved." Do not touch the office.
   Otherwise read REPO/ops/schedules/office-status.md and use it for every "status" and "event" below.
2. Status dev working "<the work order's title, in plain words>". Event po -> dev "Work order <id>".
3. Spawn Dev: Agent tool with subagent_type "dev"; if not listed, use general-purpose with model "opus" and tell it to read REPO/.claude/agents/dev.md and follow it exactly.
   Prompt: "Work order <id>. REPO = <REPO>. OPS = <OPS>."
4. Read OPS/inbox/dev/<id>.md. If its status is BLOCKED: status dev blocked "<why, in plain words>" and event dev -> po "Blocked: <why>".
   Otherwise status dev idle with last "Handed back <id> for the check" and event dev -> po "Ready for the check".
5. Finish with two lines: the work order and the outcome.

CONSTRAINTS: code changes only inside Dev's own worktree under REPO/.claude/worktrees/; never touch the main checkout's files, push, merge, tag, reindex, rebuild an .exe, or use the DeepSeek key.
