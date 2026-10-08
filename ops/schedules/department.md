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

REPO = C:\Users\hrizvi\Downloads\Git\groundedops (the main checkout)
OPS  = C:\Users\hrizvi\groundedops-ops

0. Move the session to REPO with the change_directory tool. If REPO/.claude/agents/<dept>.md is missing, the office branch is not merged yet: stop with one line.
   Use the OFFICE STATUS section at the end for every "status" and "event" below.
1. (security only) If OPS/state/security_last_run.txt holds a date less than 13 days ago, and no OPS/inbox/dev/<id>.gate/gate.json with verdict PASS lacks an OPS/inbox/security/review-<id>.md, stop with one line. Otherwise write today's date into that file when you finish.
2. Create OPS/inbox/<dept> and OPS/state if missing. Status <dept> working "<task>".
3. Spawn the department: Agent tool with subagent_type "<dept>"; if not listed, use general-purpose with model "<model>" and tell it to read REPO/.claude/agents/<dept>.md and follow it exactly.
   Prompt: "REPO = <REPO>. OPS = <OPS>. Today = <YYYYMMDD>."
4. Status <dept> idle with last = the first line of its final message. Event <dept> -> po with what it filed, e.g. "2 proposals filed".
5. Finish with the department's final message.

CONSTRAINTS: never edit, stage or commit repo files; never reindex, rebuild an .exe, push, or use the DeepSeek key.
