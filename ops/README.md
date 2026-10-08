# The agent office

Departments of Claude agents that test GroundedOps every week, propose fixes,
build the approved ones and report to the CEO. Phase 1 (this folder) runs
the loop that matters most: **test -> decide -> build -> check -> report**.

## Who does what (phase 1)

| Department | Agent | Model, effort | When | Writes |
|---|---|---|---|---|
| Customer | `.claude/agents/customer.md` | Sonnet 5.5, medium | Mon 01:00 | new blind questions, findings, hidden sibling questions |
| Test lab | `tools/weekly_blind.sh`, `tools/gate_run.sh` | no model (shell) | after Customer; before each PO run | graded runs, gate results |
| Product owner | `.claude/agents/product-owner.md` | Fable 5.1, high | Tue and Fri 08:00 | decisions, the CEO report |
| Dev | `.claude/agents/dev.md` | Opus 5.5, high | weekdays 20:00, only when something is approved | a branch per work order, hand-back note |
| CEO (you) | - | - | when the report lands | ticks approvals, merges branches |

The exact prompts the scheduled tasks run are in `ops/schedules/`. Edit them
there and copy the change into the scheduled task.

## The week

1. **Mon 01:00, Customer.** Exports each product's manual text from the index
   (`tools/export_manuals.py`), writes 40 new blind questions from that text
   alone, the test lab asks the bot and grades the answers against a
   whole-manual baseline, then Customer re-checks every miss and files one
   finding per *type* of failure, plus 4 hidden sibling questions per type.
2. **Tue 08:00, Product owner.** Scores each finding, rejects patches for one
   question, checks the PENDING.md "Rejected" list, approves at most 3 at once,
   writes the CEO report.
3. **Your turn.** Read the report. Approve by ticking `- [x] CEO approved` in
   the decisions file, or tell any Claude session "approve D-...".
4. **Weekdays 20:00, Dev.** Takes one approved work order, builds it in its own
   worktree on `agents/dev-<id>`, runs the tests, hands back. Never merges.
5. **Fri 08:00, Product owner.** The test lab runs the gate on each hand-back;
   the product owner reads `gate.json` and marks it READY TO MERGE or sends it
   back. You merge.

## The retrofit gate

A fix passes only if (`tools/retrofit_gate.py`):
1. every **hidden sibling question** (same weakness, other products and
   wording, written by Customer, never shown to Dev) scores at least 2 of 3;
2. no question that was right before is now **wrong**;
3. the overall score does not drop by more than the noise (3 points).

The siblings are the point. Dev sees the failing questions, so passing them
proves little; passing questions it never saw proves the fix generalises.

## Where things live

`OPS = C:/Users/hrizvi/groundedops-ops` (outside the repo, so agents never
touch the shared working tree, and the hidden questions are not in git):

```
OPS/runs/<YYYYMMDD>/       manuals/, cases.json, cases_state.json, run.log, DONE
OPS/heldout/<YYYYMMDD>/    siblings_<type>.json   (Dev must never read)
OPS/inbox/<department>/    findings; inbox/dev/ holds hand-backs and gates
OPS/decisions/<date>.md    verdicts, work orders, approval boxes
OPS/reports/<date>.md      the CEO report
```

## The office

https://claude.ai/artifact/XeDaMgpcp1foLDuX9N8KMD (source: `ops/office.html`).
A live page shows each department at work: who is on shift, what they are
doing, what waits for you. Every scheduled run writes its status to the page's
data store (`workers/<department>`: state, task, last, next; `events/<id>`:
from, to, text; `board/ceo`: waiting). When nobody is on shift it plays a
sample week, labelled as one.

## Rules every agent follows

- Only Dev changes code, only in its own worktree (CLAUDE.md P1).
- Nobody pushes, merges, tags or publishes a release. You merge.
- No reindex, no .exe rebuild, no credentials, no personal DeepSeek key.
- Blind runs pin every provider role to the company OpenAI model.
- A step that needs a person stops and says so; it never guesses.

## The plan after phase 1 (not built yet)

**Phase 2: Audit and Security.**
- Audit (Sonnet 5.5, low, weekly): cost per answer and latency from
  `log_report` and the run states, plus what the agents themselves cost.
  Every cost cut must pass the same gate.
- Security (Opus 5.5, high, fortnightly, plus any auth or widget change):
  `security-review` on the changes since the last run, `pip-audit`, a secrets
  scan, and whether real customer text reaches an outside grader.

**Phase 3: R&D and Marketing/Sales** (monthly, Opus 5.5 high and Sonnet 5.5
medium). Market comparison against the leaders, the look and feel, and new
infrastructure ideas. They send proposals through the product owner like
everyone else.

**Phase 4: the whiteboards and the reward system** (requested 2026-10-08).
- *Central whiteboard (product owner).* One board with three lanes: done,
  in progress, next. Every finding becomes a card that moves from proposal to
  decision to build to gate to merged, with its score change attached. It
  replaces reading the decisions files by hand, and keeps the history of what
  was tried and rejected. Built as a page backed by the same data store as the
  office, written only by the product owner's runs.
- *Department whiteboards.* Each department head gets its own board: the
  work assigned to it, its queue, and its load (open items against capacity,
  e.g. Dev at most 3 approved items). The product owner assigns; heads
  re-order their own queue. Overload shows as a red lane and holds new
  approvals for that department.
- *Reward system: improvement of the month.* Points go to the department
  whose work led to a merged fix, and only from measured results: the gate
  passed, wrong answers removed and the score gain on the following week's
  fresh blind set. Self-reported work earns nothing, which keeps the reward
  from being gamed. Rewards are things that help agents work better, such as a
  higher effort level or first place in the queue for a month, and a
  leaderboard in the office. Customer earns for findings that turn into
  merged fixes; Dev for fixes that pass first time; the product owner for a
  low rate of reverted merges.
