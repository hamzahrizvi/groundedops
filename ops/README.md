# The agent office

Departments of Claude agents that test GroundedOps every week, propose fixes,
build the approved ones and report to the CEO. The core loop is
**test -> decide -> build -> check -> report**; the other departments feed it
proposals through the product owner.

## Who does what

| Department | Agent | Model, effort | When | Status |
|---|---|---|---|---|
| Customer | `.claude/agents/customer.md` | Sonnet 5.5, medium | Mon 01:00 | on |
| Test lab | `tools/weekly_blind.sh`, `tools/gate_run.sh` | no model (shell) | after Customer; before each PO run | on |
| Product owner | `.claude/agents/product-owner.md` | Fable 5.1, high | Tue and Fri 08:00 | on |
| Dev | `.claude/agents/dev.md` | Opus 5.5, high | weekdays 20:00, only when something is approved | on |
| Audit | `.claude/agents/audit.md` | Sonnet 5.5, low | Wed 09:00 | phase 2, built, switched off |
| Security | `.claude/agents/security.md` | Opus 5.5, high | Wed 14:00, acts every 2nd week or when a branch waits | phase 2, built, switched off |
| R&D | `.claude/agents/rnd.md` | Opus 5.5, high | 1st of the month | phase 3, built, switched off |
| Marketing | `.claude/agents/marketing.md` | Sonnet 5.5, medium | 15th of the month | phase 3, built, switched off |
| CEO (you) | - | - | when the report lands | ticks approvals, merges branches |

Scheduled tasks are named `groundedops-office-<department>`. Their prompts
live in `ops/schedules/` (`customer-weekly.md`, `product-owner.md`,
`dev-approved.md`, and `department.md` for the other four), each with
`office-status.md` appended. Edit them there and copy the change into the task.

**Switching a phase on:** enable its tasks in the Scheduled sidebar, or tell
Claude "switch on phase 2". Suggested: phase 2 once phase 1 has had two clean
weeks, phase 3 once Dev has merged a gated fix.

## The week

1. **Mon 01:00, Customer.** Exports each product's manual text from the index
   (`tools/export_manuals.py`), writes 40 new blind questions from that text
   alone, the test lab asks the bot and grades the answers against a
   whole-manual baseline, then Customer re-checks every miss and files one
   finding per *type* of failure, plus 4 hidden sibling questions per type.
2. **Tue 08:00, Product owner.** Scores every department's findings, rejects
   patches for one question, checks the PENDING.md "Rejected" list, approves
   at most 3 at once, writes the CEO report, then updates the boards.
3. **Your turn.** Read the report. Approve by ticking `- [x] CEO approved` in
   the decisions file, or tell any Claude session "approve D-...".
4. **Weekdays 20:00, Dev.** Takes one approved work order (this month's reward
   leader's first), builds it in its own worktree on `agents/dev-<id>`, runs
   the tests, hands back. Never merges.
5. **Fri 08:00, Product owner.** The test lab runs the gate on each hand-back;
   Security's branch review (phase 2) can block it; the product owner marks it
   READY TO MERGE or sends it back. You merge.

## The retrofit gate

A fix passes only if (`tools/retrofit_gate.py`):
1. every **hidden sibling question** (same weakness, other products and
   wording, written by Customer, never shown to Dev) scores at least 2 of 3;
2. no question that was right before is now **wrong**;
3. the overall score does not drop by more than the noise (3 points).

The siblings are the point. Dev sees the failing questions, so passing them
proves little; passing questions it never saw proves the fix generalises.

## Boards and rewards

`tools/ops_ledger.py` builds three things from the files below and from git,
after every product owner run, and the office shows them:
- **Plan board:** every finding and decision as a card in Coming up /
  In progress / Done, with its stage (proposed, waiting for CEO, approved,
  built, check passed or failed, ready to merge, merged, rejected, reverted).
- **Department boards:** each department's doing and queued work against its
  capacity (Dev 3, product owner 8 to triage, others 2). Full boards turn red.
- **Improvement of the month:** points from measured results only. Finder:
  +2 approved, +5 merged after a gate PASS, +1 per wrong answer fixed.
  Builder: +5 first-try PASS merged (+3 after a send-back), +1 per wrong answer
  fixed. Product owner: +1 per merge, -3 per revert (builder -3 too). The
  leader's findings go first in Dev's queue.

The ledger reads the decisions format in `.claude/agents/product-owner.md`
and the hand-back header in `.claude/agents/dev.md`; keep them in step.

## Where things live

`OPS = C:/Users/hrizvi/groundedops-ops` (outside the repo, so agents never
touch the shared working tree, and the hidden questions are not in git):

```
OPS/runs/<YYYYMMDD>/       manuals/, cases.json, cases_state.json, run.log, DONE
OPS/heldout/<YYYYMMDD>/    siblings_<type>.json   (Dev must never read)
OPS/inbox/<department>/    findings and proposals; inbox/dev/ holds hand-backs and gates;
                           inbox/security/review-<id>.md holds branch reviews
OPS/decisions/<date>.md    verdicts, work orders, approval boxes
OPS/reports/<date>.md      the CEO report
OPS/ledger.json            boards and rewards (also uploaded to the office)
OPS/state/                 Security's last reviewed commit and run date
```

## The office

https://claude.ai/artifact/XeDaMgpcp1foLDuX9N8KMD (source: `ops/office.html`).
An open-plan floor: people sit at their desks while working and take breaks
around the building otherwise; hand-offs are walked over as papers; the
report is a meeting with you. Its data store holds `workers/<department>`
(state, task, last, next), `events/<id>` (from, to, text), `board/ceo`
(waiting) and `ledger/current` (the boards). A department appears in the
office once its `workers` row exists; `off` means hired but switched off.
When nobody is on shift it plays a sample week, labelled as one.

## Rules every agent follows

- Only Dev changes code, only in its own worktree (CLAUDE.md P1).
- Nobody pushes, merges, tags or publishes a release. You merge.
- No reindex, no .exe rebuild, no credentials, no personal DeepSeek key.
- Blind runs pin every provider role to the company OpenAI model.
- A step that needs a person stops and says so; it never guesses.
