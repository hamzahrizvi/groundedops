Template for the phase 2 and 3 departments (audit, security, rnd, marketing).
Each scheduled task is this text with <dept>, <model> and <task> filled in,
plus office-status.md appended.

| dept | model | cron (local) | task line |
|---|---|---|---|
| audit | sonnet | `0 9 * * 3` (Wed 09:00) | Checking this week's running costs |
| security | opus | `0 14 * * 3` (Wed 14:00; reviews every 2nd week, plus any branch waiting) | Reviewing changes and waiting branches |
| rnd | opus | `0 10 1 * *` (1st of the month) | Researching what would lift the whole product |
| marketing | sonnet | `0 10 15 * *` (15th of the month) | Comparing us with the market leaders |

---

<Dept> run for the GroundedOps agent office. Nobody is present: never ask questions.

OFFICE = C:\Users\hrizvi\Downloads\Git\groundedops\.claude\worktrees\agent-org (branch experimental/agent-org-phase1: agent definitions, tools, ops)
REPO   = C:\Users\hrizvi\Downloads\Git\groundedops (the product: src/, .venv, PENDING.md, docs/)
OPS    = C:\Users\hrizvi\groundedops-ops

PURGE: the department runs in a fresh subagent that starts from a cleared context; only its files in OPS and its final five lines come back.

0. Move the session to OFFICE with the change_directory tool. If OFFICE/.claude/agents/<dept>.md is missing, stop with one line.
   Use the OFFICE STATUS section at the end for every "status" and "event" below.
1. (security only) If OPS/state/security_last_run.txt holds a date less than 13 days ago, and every OPS/inbox/dev/<id>.gate/gate.json with verdict PASS already has an OPS/inbox/security/review-<id>.md, stop with one line. Otherwise write today's date into that file when you finish.
2. Create OPS/inbox/<dept> and OPS/state if missing. Status <dept> working "<task>".
3. Spawn the department: Agent tool with subagent_type "<dept>"; if not listed, use general-purpose with model "<model>" and tell it to read OFFICE/.claude/agents/<dept>.md and follow it exactly.
   Prompt: "REPO = <REPO>. OFFICE = <OFFICE>. OPS = <OPS>. Today = <YYYYMMDD>."
4. Status <dept> idle with last = the first line of its final message. Event <dept> -> po with what it filed, e.g. "2 proposals filed".
5. Finish with the department's final message.

CONSTRAINTS: never edit, stage or commit files in REPO or OFFICE; never reindex, rebuild an .exe, push, or use the DeepSeek key.
