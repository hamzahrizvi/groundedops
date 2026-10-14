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
OPS/state/office_off (the CEO's power button, via tools/ceo_inbox.py) holds every department and merges.
The CEO's refresh button (OPS/state/refresh_request, newer than the last run's DONE) puts Customer's run first.
At the CEO's lab cap (OPS/lab_cap.json, tools/ops_costs.py cap_status) Customer's run and gate checks WAIT: both
spend lab money, and tools/blind_eval.py would stop them anyway. New findings still wake the product owner.

Lanes (2026-10-09): three scheduled runners share the work side by side (ops/schedules/dispatch.md).
  python tools/office_next.py --lane N --take      the plan, then TOOK <key> | <line>, or TOOK nothing
  python tools/office_next.py --lane N --release   at the end of the lane's tick
--take claims the first runnable line no other lane holds with an O_EXCL file in OPS/state/claims,
so two lanes can never start the same work. Dev builds up to MAX_DEV work orders at once, each in
its own worktree on its own branch; merges, and every other department, run one at a time. Work
other lanes are running counts against the budget. tools/blind_eval.py makes test runs take turns.

Budget (the CEO's rule, 2026-10-08): the 5-hour window stays at or under 70%,
and the weekly window keeps pace with the week - x% of the way from the last
reset to the next, at most x% used - so it can be used up by the Friday reset.
Since 2026-10-09 it may run up to AHEAD points ahead of that pace (the CEO's
choice): a strict pace allowed one job per 6-7 hours after each reset.
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
from ops_ledger import decisions, git_sets, stage_of  # noqa: E402
from ops_costs import cap_status  # noqa: E402
from office_diary import due as diary_due  # noqa: E402

TASK = "groundedops-office-"
IN_FLIGHT = {"approved, queued", "sent back, rebuilding", "built, waiting for the check", "check passed", "check failed", "ready to merge", "blocked"}
# ponytail: guessed (5-hour %, weekly %) per run; tune from plan-usage-history.json once each has run a few times
COST = {"product-owner": (10, 2), "dev": (20, 4), "customer": (15, 3), "security": (15, 3), "audit": (3, 1),
        "rnd": (15, 3), "marketing": (8, 2), "diary": (2, 0)}
WEEK = datetime.timedelta(days=7)
HISTORY = os.path.join(os.environ.get("APPDATA", ""), "Claude", "plan-usage-history.json")
RESET = (4, 19)   # ponytail: weekly window resets Friday ~19:00 UTC (get_usage, 2026-10-08); pass --resets if the plan moves it
AHEAD = 15            # weekly points the office may run ahead of the week's pace (the CEO, 2026-10-09)
CLAIMS = ("state", "claims")
MAX_APPROVED = 3      # the product owner's cap on approved, not yet merged items (.claude/agents/product-owner.md)
MAX_DEV = 2          # ponytail: Dev builds at once; each has its own worktree, merges are serialized
STALE = 14 * 3600     # a claim older than this belongs to a lane that stopped running (longest run seen ~9 h)


def latest_usage(path=HISTORY, fallback=None):
    """(5-hour %, weekly %, minutes old) from the freshest sample: the app's own, or the one the
    dispatcher writes to OPS/state/usage.json from get_usage when the app stops recording (it
    recorded nothing 12:50-20:54 on 2026-10-08 and the office sat idle all afternoon)."""
    samples = []
    for p in (path, fallback):
        try:
            d = json.load(open(p, encoding="utf-8"))
            samples.append(d["samples"][-1] if "samples" in d else {"t": os.path.getmtime(p) * 1000, **d})   # usage.json: {"u": {...}}, timed by its write
        except (OSError, TypeError, ValueError, KeyError, IndexError):
            pass
    if not samples:
        return 0, 0, float("inf")
    s = max(samples, key=lambda s: s["t"])
    return s["u"].get("fh", 0), s["u"].get("sd", 0), (time.time() - s["t"] / 1000) / 60


def next_reset(now):
    d = now.replace(hour=RESET[1], minute=0, second=0, microsecond=0) + datetime.timedelta(days=(RESET[0] - now.weekday()) % 7)
    return d if d > now else d + WEEK


def _mtime(pattern):
    return max((os.path.getmtime(p) for p in glob.glob(pattern)), default=0)


def claims(ops):
    """{key: {lane, at, line}} for work a lane is doing now. One file per piece of work in
    OPS/state/claims, made with O_EXCL so two lanes can never take the same thing."""
    out = {}
    for p in glob.glob(os.path.join(ops, *CLAIMS, "*.json")):
        try:
            c = json.load(open(p, encoding="utf-8")) if time.time() - os.path.getmtime(p) <= STALE else None
            if not c or (c.get("pid") and not _alive(c["pid"])):   # a stopped lane, or a blind test (tools/blind_eval.py) that died
                os.remove(p)
                continue
            out[os.path.basename(p)[:-5]] = c
        except (OSError, ValueError):
            pass
    return out


def _alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        return True
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return code.value == 259   # STILL_ACTIVE


def release(ops, lane):
    """Drop every claim this lane holds."""
    for p in glob.glob(os.path.join(ops, *CLAIMS, "*.json")):
        try:
            if json.load(open(p, encoding="utf-8")).get("lane") == lane:
                os.remove(p)
        except (OSError, ValueError):
            pass


def take(ops, repo, five, week, resets, now, lane):
    """The plan, then a claim on its first runnable line no other lane holds. Runs of one lane
    never overlap, so this lane's leftover claims are from its last run and are dropped first."""
    release(ops, lane)
    d = os.path.join(ops, *CLAIMS)
    os.makedirs(d, exist_ok=True)
    rows = entries(ops, repo, five, week, resets, now, claims(ops))
    for line, key, ok in rows:
        if not ok:
            continue
        try:
            fd = os.open(os.path.join(d, key + ".json"), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:   # another lane won the race: try the next line
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"lane": lane, "at": now.isoformat(timespec="seconds"), "line": line}, f)
        try:   # a "Run now" press is used up by the start it allowed
            os.remove(os.path.join(ops, "state", "run_now", "dev" if key.startswith("dev-") else key))
        except OSError:
            pass
        return [r[0] for r in rows] + [f"TOOK {key} | {line}"]
    return ([r[0] for r in rows] or ["IDLE"]) + ["TOOK nothing"]


def waiting(ops, repo, now, taken=()):
    """[(department, why, claim key)] for every office task with work waiting, most urgent first.
    Dev builds skip work orders another lane already holds, so a second lane gets the next one."""
    items = decisions(ops)
    merged, reverted = git_sets(repo)
    dev = {i: stage_of(it, ops, merged, reverted) for i, it in items.items() if it.get("assigned", "dev") == "dev"}
    check = [i for i, s in dev.items() if s == "built, waiting for the check"]
    build = sorted((i for i, s in dev.items() if s in ("approved, queued", "sent back, rebuilding")),
                   key=lambda i: items[i].get("from") != "security")   # security fixes first, then id order
    review = lambda i: os.path.join(ops, "inbox", "security", f"review-{i}.md")
    def verdict(i):   # Security's review of the branch: CLEAR, BLOCK or ""
        if not os.path.exists(review(i)):
            return ""
        m = re.search(r"(?mi)^Verdict:\s*(CLEAR|BLOCK)", open(review(i), encoding="utf-8").read())
        return m.group(1).upper() if m else ""
    out = [("merge", i, "merge") for i, s in dev.items() if s == "ready to merge" and verdict(i) == "CLEAR"]   # tools/office_merge.py
    blocked = [os.path.join(ops, "inbox", "dev", i + ".md") for i, s in dev.items() if s == "blocked"]
    blocked += [review(i) for i, s in dev.items() if s in ("check passed", "ready to merge") and verdict(i) == "BLOCK"]
    findings = [p for p in set(glob.glob(os.path.join(ops, "inbox", "*", "*.md")) + blocked)   # a BLOCKED hand-back needs a decision
                # every department report wakes the product owner, notes too (the CEO, 2026-10-10); a Security review is read with its branch
                # a BLOCKED hand-back or BLOCK review counts until a triage newer than it (decisions/*.md) has seen it
                if (p in blocked or (os.path.basename(os.path.dirname(p)) != "dev" and not os.path.basename(p).startswith("review-")))
                and os.path.getmtime(p) > _mtime(os.path.join(ops, "decisions", "*.md"))]
    lab = cap_status(ops)
    capped = lab["stopped"] and (f"lab stopped at the cap (${lab['spent']:.2f} of ${lab['usd']:.2f} this {lab['per']})"
                                 "; raise it on the Costs tab")
    if capped and check:   # a gate check spends lab money: it waits with the lab
        out.append(("product-owner", f"check {', '.join(check)} waits: {capped}", None))
        check = []
    if check or findings:
        out.append(("product-owner", "; ".join(filter(None, [check and "check " + ", ".join(check),
                                                             findings and f"{len(findings)} new findings"])), "product-owner"))
    free = [i for i in build if f"dev-{i}" not in taken]
    if free:
        out.append(("dev", f"build {free[0]}" + (f" (+{len(free) - 1} queued)" if len(free) > 1 else ""), f"dev-{free[0]}"))
    # approved work for another department (2026-10-14: five such orders sat untouched): one at a time each, until it
    # files OPS/inbox/<dept>/<id>-done.md, which wakes the product owner to close it
    for i, it in sorted(items.items()):
        dept = it.get("assigned", "dev")
        if (dept in COST and dept not in ("dev", "product-owner", "diary") and stage_of(it, ops, merged, reverted) == "approved, queued"
                and not os.path.exists(os.path.join(ops, "inbox", dept, f"{i}-done.md"))
                and not any(d == dept and why.startswith("work order") for d, why, _ in out)):
            out.append((dept, f"work order {i}", f"{dept}-{i}"))
    age = lambda t: (now.timestamp() - t) / 86400
    last_run = _mtime(os.path.join(ops, "runs", "*", "DONE"))
    try:
        last_merge = int(subprocess.run(["git", "-C", repo, "log", "-1", "--format=%ct", "--grep=agents/dev-"],
                                        capture_output=True, text=True, timeout=30).stdout.strip() or 0)
    except Exception:
        last_merge = 0
    # parked work comes back by priority (the CEO, 2026-10-14: 34 deferred items sat with slots free): the product owner
    # re-decides the DEFER list when a slot is free and it has not looked since one freed up, or daily
    stages = {i: stage_of(it, ops, merged, reverted) for i, it in items.items() if it.get("assigned", "dev") != "ceo"}
    slots = MAX_APPROVED - sum(s in IN_FLIGHT for s in stages.values())
    parked = sum(s == "deferred" for s in stages.values())
    triaged = _mtime(os.path.join(ops, "decisions", "*.md"))
    if slots > 0 and parked and (last_merge > triaged or age(triaged) >= 1) and not any(d == "product-owner" and k for d, _, k in out):
        out.append(("product-owner", f"{slots} of {MAX_APPROVED} slots free: re-decide the {parked} parked items, most valuable first", "product-owner"))
    asked =_mtime(os.path.join(ops, "state", "refresh_request"))   # the CEO's refresh button, via tools/ceo_inbox.py
    if asked > last_run:   # first in the queue until a run finishes after the press
        out.insert(0, ("customer", "the CEO asked for a fresh score", "customer"))
    elif not last_run or age(last_run) >= 14:
        out.append(("customer", "no test run in 14 days" if last_run else "no test run yet", "customer"))
    elif last_merge > last_run or age(last_run) >= 7:
        # the CEO, 2026-10-12: each fix is proven on its own failure type's hidden siblings by the retrofit gate;
        # a new 40-question bank only once Customer's fixes in flight are all through, to find the next issues
        busy = sorted(i for i, s in dev.items() if items[i].get("from") == "customer" and s in IN_FLIGHT)
        if busy and last_merge > last_run:   # the CEO, 2026-10-13: the score shows each fix, so re-ask the last bank
            out.append(("customer", "re-ask the last 40 questions: a fix merged since the last test run", "customer"))
        else: out.append(("customer", f"waits until {len(busy)} customer fixes in flight are through ({', '.join(busy[:3])}); "
                    "the gate tests each fix on its own questions" if busy else
                    "the customer fixes are all through: a new blind bank to find more issues", "customer" if not busy else None))
    unreviewed = [i for i, s in dev.items() if s in ("check passed", "ready to merge")
                  and not os.path.exists(os.path.join(ops, "inbox", "security", f"review-{i}.md"))]
    if unreviewed or age(_mtime(os.path.join(ops, "state", "security_last_run.txt"))) >= 13:
        out.append(("security", "review " + ", ".join(unreviewed) if unreviewed else "fortnightly review due", "security"))
    if age(_mtime(os.path.join(ops, "inbox", "audit", "*-weekly.md"))) >= 7:
        out.append(("audit", "weekly cost review due", "audit"))
    for dept, why in (("rnd", "monthly research due"), ("marketing", "monthly market survey due")):   # on since 2026-10-10
        if age(max(_mtime(os.path.join(ops, "inbox", dept, "*.md")), _mtime(os.path.join(ops, "state", f"{dept}_last_run.txt")))) >= 30:
            out.append((dept, why, dept))
    days = diary_due(ops, repo, now.astimezone().replace(tzinfo=None))   # the product owner's diary (tools/office_diary.py), on a light model
    if "diary" in run_now(ops):   # Do it now: today's entry too, before the day ends (office_diary.py --today)
        out.append(("diary", "you asked for it now", "diary"))
    elif days:
        out.append(("diary", f"diary for {', '.join(str(d) for d in days[:3])}" + (" and more" if len(days) > 3 else ""), "diary"))
    for dept in sorted(run_now(ops) - {d for d, _, _ in out} - {"dev"}):   # "Do it now" with nothing due (Dev needs a work order)
        out.append((dept, "you asked for it now", dept))
    if capped:   # a blind run spends lab money too
        out = [("customer", capped, None) if x[0] == "customer" else x for x in out]
    return out


def over_budget(dept, five, week, resets, now):
    """Why starting this department now would break the budget, or None."""
    c5, cw = COST[dept]
    pace = 100 * (now - (resets - WEEK)) / WEEK
    if five + c5 > 70:
        return f"5-hour window {five}% + ~{c5}% would pass 70%"
    if week + cw > pace + AHEAD:
        return f"weekly {week}% + ~{cw}% is more than {AHEAD} points ahead of the week's pace ({pace:.0f}%)"
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


def run_now(ops):
    """Departments the CEO pressed "Run now" for (OPS/state/run_now/<dept>, via tools/ceo_inbox.py): their next
    start skips the budget pace and a hold. A press lapses after a day, and taking the work uses it up."""
    d = os.path.join(ops, "state", "run_now")
    return {n for n in (os.listdir(d) if os.path.isdir(d) else [])
            if time.time() - os.path.getmtime(os.path.join(d, n)) < 86400}


def is_off(ops):
    return os.path.exists(os.path.join(ops, "state", "office_off"))


def entries(ops, repo, five, week, resets, now, taken):
    """[(line, claim key, runnable)]. Work other lanes are running counts against the budget,
    since two lanes starting together both see the same usage figures."""
    held, forced = holds(ops, now), run_now(ops)
    if is_off(ops):
        held |= {"merge"} | {d for d, _, _ in waiting(ops, repo, now, taken)}
    cost = lambda k: (0, 0) if taken[k].get("lane") == "hand" else COST.get("dev" if k.startswith("dev-") else k, (0, 0))   # a blind test spends no Claude usage
    five, week = five + sum(cost(k)[0] for k in taken), week + sum(cost(k)[1] for k in taken)
    building = sum(k.startswith("dev-") for k in taken)
    out = []
    for dept, why, key in waiting(ops, repo, now, taken):
        if key is None:   # waits on other work, not on the budget
            out.append((f"WAIT {TASK}{dept}  {why}", None, False))
            continue
        lane = f" (lane {taken[key].get('lane')})" if key in taken else ""
        if dept == "merge":
            stop = "held (OPS/state/hold)" if "merge" in held else f"another merge is running{lane}" if lane else None
            out.append((f"WAIT merge {why}  {stop}" if stop else f"MERGE {why}  gate PASS, READY TO MERGE, Security CLEAR", key, not stop))
            continue
        pressed = dept in forced and not is_off(ops)   # "Run now": skips a hold and the budget, not the 5-hour limit itself
        stop = ("held (OPS/state/hold)" if dept in held and not pressed else f"already running{lane}" if lane else
                f"{MAX_DEV} builds already running" if dept == "dev" and building >= MAX_DEV else
                f"5-hour window at {five}%" if pressed and five >= 90 else
                None if pressed else over_budget(dept, five, week, resets, now))
        out.append((f"WAIT {TASK}{dept}  {why}; {stop}" if stop else f"RUN  {TASK}{dept}  {why}", key, not stop))
    return out


def plan(ops, repo, five, week, resets, now, taken=None):
    return [r[0] for r in entries(ops, repo, five, week, resets, now, claims(ops) if taken is None else taken)] or ["IDLE"]


def selftest():
    import office_diary
    now = datetime.datetime(2026, 10, 8, 7, 0, tzinfo=datetime.timezone.utc)
    resets = datetime.datetime(2026, 10, 9, 19, 0, tzinfo=datetime.timezone.utc)   # pace ~79%, so up to ~94% with AHEAD
    with tempfile.TemporaryDirectory() as ops:
        office_diary.OFFICE = ops   # no real commits in the test's diary
        for d in ("decisions", "inbox/dev", "inbox/audit", "runs/20261008", "state"):
            os.makedirs(os.path.join(ops, d))
        w = lambda p, s="": open(os.path.join(ops, p), "w", encoding="utf-8").write(s)
        w("decisions/20261008.md", "## D-20261008-01 | Two products\n- Assigned: dev\n- Verdict: NEEDS CEO\n- [x] CEO approved\n\n"
                                   "## D-20261008-02 | Asks\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n\n"
                                   "## D-20261008-07 | Manuals\n- Assigned: ceo\n- Verdict: NEEDS CEO\n- [x] CEO approved\n")
        w("inbox/dev/D-20261008-01.md", "Status: READY\n")
        for p in ("runs/20261008/DONE", "state/security_last_run.txt", "inbox/audit/20261008-weekly.md",
                  "state/rnd_last_run.txt", "state/marketing_last_run.txt"):
            w(p)
        os.utime(os.path.join(ops, "inbox/audit/20261008-weekly.md"), (time.time() - 60, time.time() - 60))   # already triaged
        got = plan(ops, ops, 11, 38, resets, now)
        assert got[0].startswith("RUN  groundedops-office-product-owner  check D-20261008-01"), got
        assert got[1].startswith("RUN  groundedops-office-dev  build D-20261008-02"), got
        assert len(got) == 2, got                                       # D-07 is the CEO's; tests, reviews not due
        os.remove(os.path.join(ops, "state/rnd_last_run.txt"))
        assert any("office-rnd  monthly research due" in x for x in plan(ops, ops, 11, 38, resets, now)), "R&D due monthly"
        w("state/rnd_last_run.txt")
        w("decisions/20261008b.md", "## D-20261008-08 | Key leak\n- From: security\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n")
        assert "build D-20261008-08 (+1 queued)" in plan(ops, ops, 11, 38, resets, now)[1], "security first"
        os.remove(os.path.join(ops, "decisions/20261008b.md"))
        w("decisions/20261008c.md", "## D-20261008-04 | Four\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n\n"
                                    "## D-20261008-05 | Five\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n")
        took = [take(ops, ops, 11, 38, resets, now, n)[-1].split(" |")[0] for n in (1, 2, 3, 4)]
        assert took == ["TOOK product-owner", "TOOK dev-D-20261008-02", "TOOK dev-D-20261008-04", "TOOK nothing"], took
        p = plan(ops, ops, 11, 38, resets, now)
        assert "already running (lane 1)" in p[0] and "2 builds already running" in p[1], p
        release(ops, 3)
        assert "would pass 70%" in plan(ops, ops, 25, 38, resets, now)[1], "other lanes' work counts against the budget"
        assert take(ops, ops, 11, 38, resets, now, 1)[-1].startswith("TOOK product-owner"), "a lane's own leftover claim never blocks it"
        for n in (1, 2):
            release(ops, n)
        assert claims(ops) == {}, claims(ops)
        hand = lambda pid: w("state/claims/customer.json", json.dumps({"lane": "hand", "pid": pid, "line": "RUN  groundedops-office-lab  blind test"}))
        hand(os.getpid())
        assert "customer" in claims(ops) and not take(ops, ops, 11, 38, resets, now, 1)[-1].startswith("TOOK customer"), "a blind test started by hand holds Customer's run"
        assert not any(l.startswith("RUN  groundedops-office-customer") for l in plan(ops, ops, 11, 38, resets, now)), "no second blind run"
        release(ops, 1)
        hand(2 ** 31 - 2)
        assert claims(ops) == {}, "a dead blind test's claim is dropped"
        os.remove(os.path.join(ops, "decisions/20261008c.md"))
        assert plan(ops, ops, 55, 38, resets, now)[1].startswith("WAIT groundedops-office-dev"), "5-hour cap"
        assert "pace" in plan(ops, ops, 11, 107, resets, now)[0], "weekly pace"
        assert plan(ops, ops, 11, 90, resets, now)[0].startswith("RUN"), "up to AHEAD points ahead of pace is fine"
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
        w("u.json", json.dumps({"u": {"fh": 13, "sd": 47}}))
        os.utime(os.path.join(ops, "u.json"), (time.time() + 5, time.time() + 5))
        assert latest_usage(os.path.join(ops, "h.json"), os.path.join(ops, "u.json"))[:2] == (13, 47), "fresher fallback wins"
        assert latest_usage(os.path.join(ops, "nope.json"))[2] == float("inf"), "no figures at all is stale"
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
        w("state/refresh_request", "2026-10-08 07:00 x\n")
        os.utime(os.path.join(ops, "state/refresh_request"), (time.time() + 5, time.time() + 5))
        p = plan(ops, ops, 11, 38, resets, now)
        assert p[0].startswith("RUN  groundedops-office-customer  the CEO asked for a fresh score"), p
        assert sum("customer" in x for x in p) == 1, p
        assert "would pass 70%" in plan(ops, ops, 60, 38, resets, now)[0], "a press still waits for the budget"
        os.makedirs(os.path.join(ops, "state", "run_now")); w("state/run_now/customer")
        assert plan(ops, ops, 60, 38, resets, now)[0].startswith("RUN  groundedops-office-customer"), "Run now skips the budget"
        assert "5-hour window at 92%" in plan(ops, ops, 92, 38, resets, now)[0], "but not the 5-hour limit itself"
        assert take(ops, ops, 60, 38, resets, now, 7)[-1].startswith("TOOK customer") and not run_now(ops), "used up"
        release(ops, 7)
        w("decisions/20261008w.md", "## D-20261008-20 | Fresh hidden questions\n- From: customer\n- Assigned: customer\n- Verdict: AUTO-APPROVED\n")
        assert any(x.startswith("RUN  groundedops-office-customer  work order D-20261008-20") for x in plan(ops, ops, 11, 38, resets, now)), "a customer work order runs"
        os.makedirs(os.path.join(ops, "inbox", "customer"), exist_ok=True); w("inbox/customer/D-20261008-20-done.md", "Done.\n")
        assert not any("work order" in x for x in plan(ops, ops, 11, 38, resets, now)), "filed: the product owner closes it"
        os.remove(os.path.join(ops, "decisions", "20261008w.md")); os.remove(os.path.join(ops, "inbox", "customer", "D-20261008-20-done.md"))
        w("state/run_now/audit")
        assert any(x.startswith("RUN  groundedops-office-audit  you asked for it now") for x in plan(ops, ops, 11, 38, resets, now)), "Do it now, nothing due"
        os.remove(os.path.join(ops, "state", "run_now", "audit"))
        os.utime(os.path.join(ops, "runs/20261008/DONE"), (time.time() + 10, time.time() + 10))
        assert not any("customer" in x for x in plan(ops, ops, 11, 38, resets, now)), "a run finished after the press answers it"
        os.remove(os.path.join(ops, "state/refresh_request"))
        w("state/office_off", "x\n")
        assert all(l.startswith("WAIT") or l == "IDLE" for l in plan(ops, ops, 11, 38, resets, now)), "switched off"
        os.remove(os.path.join(ops, "state/office_off"))
        os.remove(os.path.join(ops, "runs/20261008/DONE"))
        assert any("customer" in x for x in plan(ops, ops, 11, 38, resets, now)), "no test run yet"
        w("runs/20261008/DONE")
        old = now.timestamp() - 8 * 86400
        os.utime(os.path.join(ops, "runs/20261008/DONE"), (old, old))
        w("decisions/20261008d.md", "## D-20261008-09 | Vague asks\n- From: customer\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n")
        cust = lambda: next(x for x in plan(ops, ops, 11, 38, resets, now) if "customer" in x)
        assert cust().startswith("WAIT") and "D-20261008-09" in cust(), "no new bank while a customer fix is in flight"
        git = os.path.join(ops, "git")
        subprocess.run(f'git init -q "{git}" && git -C "{git}" -c user.name=t -c user.email=t commit -q --allow-empty '
                       "-m \"Merge branch 'agents/dev-D-20261008-02'\"", shell=True, check=True)
        p = plan(ops, git, 11, 38, resets, now)
        assert any(x.startswith("RUN  groundedops-office-customer  re-ask the last 40") for x in p), p
        os.remove(os.path.join(ops, "decisions/20261008d.md"))
        assert cust().startswith("RUN") and "all through" in cust(), "a new bank once they are through"
        old = now.timestamp() - 15 * 86400
        os.utime(os.path.join(ops, "runs/20261008/DONE"), (old, old))
        w("decisions/20261008d.md", "## D-20261008-09 | Vague asks\n- From: customer\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n")
        assert cust().startswith("RUN") and "14 days" in cust(), "never more than 14 days without a bank"
        os.remove(os.path.join(ops, "decisions/20261008d.md"))
        os.remove(os.path.join(ops, "runs/20261008/DONE"))
        os.remove(os.path.join(ops, "inbox/security/review-D-20261008-01.md"))
        w("decisions/20261011.md", "# Triage\n")
        os.utime(os.path.join(ops, "decisions/20261011.md"), (time.time() + 20, time.time() + 20))
        po = lambda: next(x for x in plan(ops, ops, 11, 38, resets, now) if "product-owner" in x)
        assert not any("new findings" in x for x in plan(ops, ops, 11, 38, resets, now)), "a BLOCKED hand-back older than the last triage was already decided"
        os.makedirs(os.path.join(ops, "inbox/marketing"))
        for f in ("inbox/marketing/20261011-market.md", "inbox/security/review-D-20261008-01.md"):
            w(f, "Verdict: CLEAR\n")
            os.utime(os.path.join(ops, f), (time.time() + 30, time.time() + 30))
        assert "1 new findings" in po(), "a department's note wakes the PO; a Security review does not"
        json.dump({"usd": 0, "per": "month"}, open(os.path.join(ops, "lab_cap.json"), "w"))   # a cap of $0: stopped
        p = plan(ops, ops, 11, 38, resets, now)
        cust = next(x for x in p if "customer" in x)
        assert cust.startswith("WAIT groundedops-office-customer  lab stopped at the cap ($0.00 of $0.00 this month)"), p
        w("inbox/dev/D-20261008-04.md", "Status: READY\n")
        w("decisions/20261008e.md", "## D-20261008-04 | Four\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n")
        p = plan(ops, ops, 11, 38, resets, now)
        assert any(x.startswith("WAIT groundedops-office-product-owner  check D-20261008-04 waits: lab stopped") for x in p), p
        assert any(x.startswith("RUN  groundedops-office-product-owner  1 new findings") for x in p), "findings still wake the PO"
    print("selftest ok")


def main():
    office = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--repo", default=os.path.normpath(os.path.join(office, "..", "..", "..")))
    ap.add_argument("--five", type=float)
    ap.add_argument("--week", type=float)
    ap.add_argument("--resets", help="ISO time the weekly window resets")
    ap.add_argument("--lane", type=int, help="this runner's lane number, for --take and --release")
    ap.add_argument("--take", action="store_true", help="claim the first runnable line for --lane; last line TOOK <key> | <line> or TOOK nothing")
    ap.add_argument("--release", action="store_true", help="drop every claim --lane holds")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if (a.take or a.release) and not a.lane:
        ap.error("--take and --release need --lane")
    if a.release:
        return release(a.ops, a.lane) or print(f"RELEASED lane {a.lane}")
    now = datetime.datetime.now(datetime.timezone.utc)
    if a.five is None or a.week is None:
        a.five, a.week, old = latest_usage(fallback=os.path.join(a.ops, "state", "usage.json"))
        if old > 60:
            return print(f"STALE usage figures are {old:.0f} minutes old; start nothing"
                         ' (write {"u": {"fh": <5-hour %>, "sd": <weekly %>}} from get_usage to OPS/state/usage.json and run again)')
    resets = datetime.datetime.fromisoformat(a.resets.replace("Z", "+00:00")) if a.resets else next_reset(now)
    print(f"USAGE 5-hour {a.five}%, weekly {a.week}%, weekly resets {resets:%a %d %b %H:%M} UTC")
    print("\n".join(take(a.ops, a.repo, a.five, a.week, resets, now, a.lane) if a.take else plan(a.ops, a.repo, a.five, a.week, resets, now)))


if __name__ == "__main__":
    main()
