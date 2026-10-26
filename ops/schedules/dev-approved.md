Dev run for the GroundedOps agent office (weekday evenings). Nobody is present: never ask questions.

OFFICE = C:\Users\hrizvi\Downloads\Git\groundedops\.claude\worktrees\agent-org (branch experimental/agent-org-phase1: agent definitions, tools, ops)
REPO   = C:\Users\hrizvi\Downloads\Git\groundedops (the product: src/, .venv, PENDING.md)
OPS    = C:\Users\hrizvi\groundedops-ops

PURGE: Dev works in three phases, PLAN, BUILD and CHECK, each in a fresh subagent that starts from a cleared context. Only the hand-off files Dev writes (OPS/inbox/dev/<id>.plan.md, <id>.build.md, <id>.md) cross between phases.

SENIOR AND JUNIOR: the senior Dev (subagent_type "dev", Opus) always plans and checks. The plan's first line says who builds: "Builder: junior" means subagent_type "dev-junior" (Sonnet, cheaper) builds, "Builder: senior" means "dev" builds. Junior builds save cost; Audit watches that quality holds.

0. Move the session to OFFICE with the change_directory tool. If OFFICE/.claude/agents/dev.md is missing, stop with one line.
1. Find work: the dispatcher's RUN line for this department reads "build <id>": take that id. Run on your own, run `"<REPO>/.venv/Scripts/python.exe" "<OFFICE>/tools/office_next.py" --five 0 --week 0` and take the id after "build" on its dev line. office_next.py orders the queue (security findings first, then the oldest) and reads every later verdict, such as a CEO approval of a deferred item or a later DEFER, so never pick an item by hand.
   None: stop with the line "Nothing approved." Do not touch the office.
   Otherwise use the OFFICE STATUS section at the end for every "status" and "event" below.
2. Event po -> dev "Work order <id>".
3. For PHASE in PLAN, BUILD, CHECK, one after the other:
   status dev working "<Planning | Building | Testing>: <the work order's title, in plain words>" (for a junior build: "Junior building: ...");
   MODEL PICK (agent: the type below, dev or dev-junior; job <phase in lower case>; inputs the work order and the OPS/inbox/dev/<id>.*.md hand-off files so far), then spawn the phase fresh with the Agent tool, with that model and effort. PLAN and CHECK: subagent_type "dev". BUILD: read the first line of OPS/inbox/dev/<id>.plan.md; "Builder: junior" -> subagent_type "dev-junior", anything else (or no such line) -> "dev". If an office type is not listed, use general-purpose with model "opus" (dev) or "sonnet" (dev-junior) and tell it to read OFFICE/.claude/agents/<type>.md and follow it exactly.
   Prompt: "Work order <id>. Phase <PHASE>. REPO = <REPO>. OFFICE = <OFFICE>. OPS = <OPS>."
   After a junior BUILD, if the first line of OPS/inbox/dev/<id>.build.md starts "Stuck:", spawn "dev" once for Phase BUILD with the prompt "Work order <id>. Phase BUILD. The junior got stuck: take it over. REPO = <REPO>. OFFICE = <OFFICE>. OPS = <OPS>." and event dev -> dev "Junior stuck, senior taking over".
   After each phase, if OPS/inbox/dev/<id>.md exists with "Status: BLOCKED", stop the loop.
4. Read the first lines of OPS/inbox/dev/<id>.md. If BLOCKED: status dev blocked "<why, in plain words>" and event dev -> po "Blocked: <why>".
   Otherwise status dev idle with last "Handed back <id> for the check" and event dev -> po "Ready for the check".
5. Finish with two lines: the work order and the outcome (include its Builder line).

CONSTRAINTS: code changes only inside Dev's own worktree under REPO/.claude/worktrees/dev-<id>; never touch the main checkout's or OFFICE's files, push, merge, tag, reindex, rebuild an .exe, or use the DeepSeek key outside the lab's own scripts (tools/blind_eval.py and the scripts that run it).
