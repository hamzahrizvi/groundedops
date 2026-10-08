"""Which office task should run next, within the usage budget?

  python tools/office_next.py [--five 11 --week 38] [--resets 2026-10-09T18:59:59Z] [--ops ...] [--repo ...]
  python tools/office_next.py --selftest

--five / --week are the plan's "5-hour limit" and "Weekly - all models"
percentUsed (get_usage); by default they come from the desktop app's own
15-minute samples in %APPDATA%/Claude/plan-usage-history.json, so the
dispatcher's command never changes and one "always allow" covers it. --resets
is the weekly window's resetsAt; by default the next Friday 19:00 UTC. Prints one line per office task with work waiting, most
urgent first:
  RUN  <task-id>  <why>    fits the budget: start it
  WAIT <task-id>  <why>    has work, but would break the budget
or IDLE when nothing waits. The hourly runner (groundedops-office-dispatch) does
the first RUN line itself by following that task's prompt: scheduled runs
cannot start other scheduled tasks.

A department named on a line of OPS/state/hold is held: WAIT, whatever it has.

Budget (the CEO's rule, 2026-10-08): the 5-hour window stays at or under 70%,
and the weekly window keeps pace with the week - x% of the way from the last
reset to the next, at most x% used - so it can be used up by the Friday reset.
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ops_ledger import NOTES, decisions, git_sets, stage_of  # noqa: E402

TASK = "groundedops-office-"
# ponytail: guessed (5-hour %, weekly %) per run; tune from plan-usage-history.json once each has run a few times
COST = {"product-owner": (10, 2), "dev": (20, 4), "customer": (15, 3), "security": (15, 3), "audit": (3, 1)}
WEEK = datetime.timedelta(days=7)
HISTORY = os.path.join(os.environ.get("APPDATA", ""), "Claude", "plan-usage-history.json")
RESET = (4, 19)   # ponytail: weekly window resets Friday ~19:00 UTC (get_usage, 2026-10-08); pass --resets if the plan moves it


def latest_usage(path=HISTORY):
    """(5-hour %, weekly %, minutes old) from the app's newest usage sample."""
    s = json.load(open(path, encoding="utf-8"))["samples"][-1]
    return s["u"].get("fh", 0), s["u"].get("sd", 0), (time.time() - s["t"] / 1000) / 60


def next_reset(now):
    d = now.replace(hour=RESET[1], minute=0, second=0, microsecond=0) + datetime.timedelta(days=(RESET[0] - now.weekday()) % 7)
    return d if d > now else d + WEEK


def _mtime(pattern):
    return max((os.path.getmtime(p) for p in glob.glob(pattern)), default=0)


def waiting(ops, repo, now):
    """[(department, why)] for every office task with work waiting, most urgent first."""
    items = decisions(ops)
    merged, reverted = git_sets(repo)
    dev = {i: stage_of(it, ops, merged, reverted) for i, it in items.items() if it.get("assigned", "dev") == "dev"}
    check = [i for i, s in dev.items() if s == "built, waiting for the check"]
    build = [i for i, s in dev.items() if s in ("approved, queued", "sent back, rebuilding")]
    review = lambda i: os.path.join(ops, "inbox", "security", f"review-{i}.md")
    def verdict(i):   # Security's review of the branch: CLEAR, BLOCK or ""
        if not os.path.exists(review(i)):
            return ""
        m = re.search(r"(?mi)^Verdict:\s*(CLEAR|BLOCK)", open(review(i), encoding="utf-8").read())
        return m.group(1).upper() if m else ""
    out = [("merge", i) for i, s in dev.items() if s == "ready to merge" and verdict(i) == "CLEAR"]   # tools/office_merge.py
    blocked = [os.path.join(ops, "inbox", "dev", i + ".md") for i, s in dev.items() if s == "blocked"]
    blocked += [review(i) for i, s in dev.items() if s in ("check passed", "ready to merge") and verdict(i) == "BLOCK"]
    findings = [p for p in set(glob.glob(os.path.join(ops, "inbox", "*", "*.md")) + blocked)   # a BLOCKED hand-back needs a decision
                if p in blocked or (os.path.basename(os.path.dirname(p)) != "dev" and not NOTES.search(os.path.basename(p)))
                and os.path.getmtime(p) > _mtime(os.path.join(ops, "decisions", "*.md"))]
    if check or findings:
        out.append(("product-owner", "; ".join(filter(None, [check and "check " + ", ".join(check),
                                                             findings and f"{len(findings)} new findings"]))))
    if build:
        out.append(("dev", f"build {build[0]}" + (f" (+{len(build) - 1} queued)" if len(build) > 1 else "")))
    age = lambda t: (now.timestamp() - t) / 86400
    last_run = _mtime(os.path.join(ops, "runs", "*", "DONE"))
    try:
        last_merge = int(subprocess.run(["git", "-C", repo, "log", "-1", "--format=%ct", "--grep=agents/dev-"],
                                        capture_output=True, text=True, timeout=30).stdout.strip() or 0)
    except Exception:
        last_merge = 0
    if not last_run or age(last_run) >= 7 or last_merge > last_run:
        out.append(("customer", "a fix merged since the last test" if last_run and last_merge > last_run else "weekly test due"))
    unreviewed = [i for i, s in dev.items() if s in ("check passed", "ready to merge")
                  and not os.path.exists(os.path.join(ops, "inbox", "security", f"review-{i}.md"))]
    if unreviewed or age(_mtime(os.path.join(ops, "state", "security_last_run.txt"))) >= 13:
        out.append(("security", "review " + ", ".join(unreviewed) if unreviewed else "fortnightly review due"))
    if age(_mtime(os.path.join(ops, "inbox", "audit", "*-weekly.md"))) >= 7:
        out.append(("audit", "weekly cost review due"))
    return out


def over_budget(dept, five, week, resets, now):
    """Why starting this department now would break the budget, or None."""
    c5, cw = COST[dept]
    pace = 100 * (now - (resets - WEEK)) / WEEK
    if five + c5 > 70:
        return f"5-hour window {five}% + ~{c5}% would pass 70%"
    if week + cw > pace:
        return f"weekly {week}% + ~{cw}% is ahead of the week's pace ({pace:.0f}%)"
    return None


def holds(ops, now):
    """Departments held now. OPS/state/hold has '<dept> [<ISO end time>]' per line; a line
    without an end time lapses 4 hours after the file was written, so a forgotten hold
    cannot stall the office (it did for 80 minutes on 2026-10-08)."""
    p = os.path.join(ops, "state", "hold")
    try:
        lines, written = open(p, encoding="utf-8").read().splitlines(), os.path.getmtime(p)
    except OSError:
        return set()
    held = set()
    for dept, *until in (line.split() for line in lines if line.strip()):
        end = (datetime.datetime.fromisoformat(until[0].replace("Z", "+00:00")) if until
               else datetime.datetime.fromtimestamp(written + 4 * 3600, datetime.timezone.utc))
        if now < end:
            held.add(dept)
    return held


def plan(ops, repo, five, week, resets, now):
    lines = []
    held = holds(ops, now)
    for dept, why in waiting(ops, repo, now):
        if dept == "merge":
            lines.append(f"WAIT merge {why}  held (OPS/state/hold)" if "merge" in held
                         else f"MERGE {why}  gate PASS, READY TO MERGE, Security CLEAR")
            continue
        stop = "held (OPS/state/hold)" if dept in held else over_budget(dept, five, week, resets, now)
        lines.append(f"WAIT {TASK}{dept}  {why}; {stop}" if stop else f"RUN  {TASK}{dept}  {why}")
    return lines or ["IDLE"]


def selftest():
    now = datetime.datetime(2026, 10, 8, 7, 0, tzinfo=datetime.timezone.utc)
    resets = datetime.datetime(2026, 10, 9, 19, 0, tzinfo=datetime.timezone.utc)   # pace ~93%
    with tempfile.TemporaryDirectory() as ops:
        for d in ("decisions", "inbox/dev", "inbox/audit", "runs/20261008", "state"):
            os.makedirs(os.path.join(ops, d))
        w = lambda p, s="": open(os.path.join(ops, p), "w", encoding="utf-8").write(s)
        w("decisions/20261008.md", "## D-20261008-01 | Two products\n- Assigned: dev\n- Verdict: NEEDS CEO\n- [x] CEO approved\n\n"
                                   "## D-20261008-02 | Asks\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n\n"
                                   "## D-20261008-07 | Manuals\n- Assigned: ceo\n- Verdict: NEEDS CEO\n- [x] CEO approved\n")
        w("inbox/dev/D-20261008-01.md", "Status: READY\n")
        for p in ("runs/20261008/DONE", "state/security_last_run.txt", "inbox/audit/20261008-weekly.md"):
            w(p)
        got = plan(ops, ops, 11, 38, resets, now)
        assert got[0].startswith("RUN  groundedops-office-product-owner  check D-20261008-01"), got
        assert got[1].startswith("RUN  groundedops-office-dev  build D-20261008-02"), got
        assert len(got) == 2, got                                       # D-07 is the CEO's; tests, reviews not due
        assert plan(ops, ops, 55, 38, resets, now)[1].startswith("WAIT groundedops-office-dev"), "5-hour cap"
        assert "pace" in plan(ops, ops, 11, 92, resets, now)[0], "weekly pace"
        w("state/hold", "product-owner\n")
        assert plan(ops, ops, 11, 38, resets, now)[0].endswith("held (OPS/state/hold)"), "hold"
        w("state/hold", "product-owner 2026-10-08T06:00Z\n")
        assert plan(ops, ops, 11, 38, resets, now)[0].startswith("RUN"), "an expired hold lapses"
        os.remove(os.path.join(ops, "state/hold"))
        w("inbox/audit/20261009-cheaper-reranker.md")                   # a proposal, unlike the weekly note
        assert "1 new findings" in plan(ops, ops, 11, 38, resets, now)[0], "new finding wakes the product owner"
        w("inbox/dev/D-20261008-02.md", "Status: BLOCKED\nReason: regression\n")
        assert "2 new findings" in plan(ops, ops, 11, 38, resets, now)[0], "a BLOCKED hand-back wakes the product owner"
        assert next_reset(now) == resets and next_reset(resets + datetime.timedelta(hours=1)) == resets + WEEK
        w("h.json", json.dumps({"samples": [{"t": time.time() * 1000, "u": {"fh": 12, "sd": 40}}]}))
        assert latest_usage(os.path.join(ops, "h.json"))[:2] == (12, 40)
        os.makedirs(os.path.join(ops, "inbox/dev/D-20261008-01.gate"))
        w("inbox/dev/D-20261008-01.gate/gate.json", '{"verdict": "PASS"}')
        w("decisions/20261009.md", "## D-20261008-01 | Two products\n- Verdict: READY TO MERGE\n")
        assert any("security" in x and "review D-20261008-01" in x for x in plan(ops, ops, 11, 38, resets, now)), "review first"
        os.makedirs(os.path.join(ops, "inbox/security"))
        w("inbox/security/review-D-20261008-01.md", "# Review\nVerdict: CLEAR\n")
        assert plan(ops, ops, 69, 99, resets, now)[0].startswith("MERGE D-20261008-01"), "merge first, whatever the budget"
        w("inbox/security/review-D-20261008-01.md", "Verdict: BLOCK\n")
        p = plan(ops, ops, 11, 38, resets, now)
        assert not any(x.startswith("MERGE") for x in p) and "new findings" in p[0], p   # a BLOCK wakes the product owner
        os.remove(os.path.join(ops, "runs/20261008/DONE"))
        assert any("customer" in x for x in plan(ops, ops, 11, 38, resets, now)), "no test run yet"
    print("selftest ok")


def main():
    office = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--repo", default=os.path.normpath(os.path.join(office, "..", "..", "..")))
    ap.add_argument("--five", type=float)
    ap.add_argument("--week", type=float)
    ap.add_argument("--resets", help="ISO time the weekly window resets")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    now = datetime.datetime.now(datetime.timezone.utc)
    if a.five is None or a.week is None:
        a.five, a.week, old = latest_usage()
        if old > 60:
            return print(f"STALE usage figures are {old:.0f} minutes old; start nothing")
    resets = datetime.datetime.fromisoformat(a.resets.replace("Z", "+00:00")) if a.resets else next_reset(now)
    print(f"USAGE 5-hour {a.five}%, weekly {a.week}%, weekly resets {resets:%a %d %b %H:%M} UTC")
    print("\n".join(plan(a.ops, a.repo, a.five, a.week, resets, now)))


if __name__ == "__main__":
    main()
