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
other lanes are running counts against the 5-hour budget. tools/blind_eval.py makes test runs take turns.

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
from ops_ledger import answered, decisions, git_sets, stage_of  # noqa: E402
from ops_costs import cap_status  # noqa: E402
from office_diary import due as diary_due  # noqa: E402
from office_merge import OFFICE, git as _git, in_the_way, names_tip  # noqa: E402
from main_port import approved as port_approved, due as port_due  # noqa: E402

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
# Work modes (the CEO, 2026-10-14): (approved not merged, Dev builds at once, work orders per department at once).
# More in flight means more lanes run side by side: two Dev builds, or two copies of one department on different orders.
MODES = {"easy": (3, 1, 1), "high": (3, 2, 1), "ultra": (6, 3, 2), "sweep": (8, 4, 3), "batch": (3, 4, 3)}   # batch: approvals outlive it
# The cleaner's sweep (the CEO, 2026-10-30; the page's Cleaner): in the last SWEEP_HOURS before the weekly reset, with at
# least SWEEP_LEFT % of the week unspent, the allowance would expire unused, so every lane works, nothing waits on the
# budget pace, and the "sweep" caps above apply. Holds, the office switch, the lab cap and SWEEP_FIVE still stop it;
# OPS/state/no_sweep switches it off.
SWEEP_HOURS, SWEEP_LEFT, SWEEP_FIVE = 3, 10, 85
# Mo's batches (the CEO, 2026-10-30): from Thursday 00:00 UTC (BATCH_FROM hours before the reset), while the week is
# BATCH_PACE times ahead of its pace (about 60% left on Thursday, less as Friday nears), he sweeps the last BATCH_MIN
# minutes before each 5-hour window resets: what is left of that window would expire anyway, and a fresh one follows.
# The 5-hour reset time is OPS/state/usage.json "r5" (written from get_usage by dispatch.md step 1).
BATCH_FROM, BATCH_PACE, BATCH_MIN, BATCH_FIVE = 43, 2.3, 30, 95
# The page's "Call Mo" sweeps for MO_CALL hours from the press; "Send Mo home" stops any sweep for MO_HOME hours.
MO_CALL, MO_HOME = 2, 6
MAX_DEV = 2         # ponytail: Dev builds at once; each has its own worktree, merges are serialized
RUN_ALL = 4 * 3600    # backstop only: "Run all now" ends with the tick that picks it up (take stamps it, end_tick removes it)
IDLE_EVERY = 3        # ponytail: days between discovery runs (security, audit, rnd, marketing) while nothing else waits
PARKED = ("state", "parked_review")   # the parked ids last handed to the product owner (take() writes it)
LANE_ONE_LATE = 90 * 60   # lanes 2 and 3 also work when lane 1 has not looked for work for this long (it stopped); 1.5 x its hourly schedule (the CEO, 2026-10-26)
REFRESH = 3600   # an idle lane 1 refreshes the office page and the cost charts this often
STALE = 14 * 3600     # a claim older than this belongs to a lane that stopped running (longest run seen ~9 h)
# Minutes a claim may be held before its lane counts as stuck, likely on a permission prompt nobody sees (2026-10-23: the
# auto-mode check gave no verdict on a Security Bash call, which then waited from 12:32 until someone stopped it at 14:26).
# The longest normal piece in the lanes' transcripts 10-09..10-23 (a --take to the lane's next --take or --release):
# security 9, merge 2, diary 3, marketing 2, audit 5, rnd 20, customer 39, product-owner 160, dev 313. Shown and told
# once, never dropped: other lanes take other work meanwhile, and dropping a live claim caused double work (D-20261020-05).
STUCK = {"security": 45, "merge": 30, "diary": 30, "marketing": 30, "audit": 30, "rnd": 60, "customer": 120,
         "product-owner": 240, "dev": 420, "rnd-trial": 480}
# an R&D work order (claim rnd-D-...) is a lab trial, not desk research: D-20261109-03 ran 373 minutes with every
# arm writing as it went, and the 60-minute R&D limit called it stuck twice (the CEO, 2026-11-10)


def limit(key):
    """Minutes the claim `key` may be held before its lane counts as stuck."""
    return STUCK["rnd-trial"] if key.startswith("rnd-D-") else STUCK.get(key.split("-D-")[0], 120)
TOLD = ("state", "stuck_told")   # the stuck claims already told to the CEO, {key: its "at"}


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


def stuck(held, now):
    """{key: since} for the claims held past their department's STUCK limit. A blind test started by hand has its pid
    checked instead (claims())."""
    out = {}
    for k, c in held.items():
        try:
            at = datetime.datetime.fromisoformat(c["at"])
        except (KeyError, TypeError, ValueError):
            continue
        if c.get("lane") != "hand" and now - at > datetime.timedelta(minutes=limit(k)):
            out[k] = at
    return out


def stuck_why(since):
    return f"stuck since {since.astimezone():%H:%M}, likely waiting on a prompt"


def tell_stuck(ops, now):
    """STUCK lines for claims newly past their limit, each printed once (OPS/state/stuck_told) for the lane to push to the CEO."""
    held = claims(ops)
    found = {k: at.isoformat() for k, at in stuck(held, now).items()}
    p = os.path.join(ops, *TOLD)
    try:
        told = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        told = {}
    if found != told:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(found, f)
    what = lambda k: " ".join(held[k].get("line", k).replace(TASK, "").split()[1:])
    return [f"STUCK lane {held[k].get('lane')} on {what(k)}: {stuck_why(datetime.datetime.fromisoformat(at))}."
            " Open its run in the Claude app and answer or stop it." for k, at in found.items() if told.get(k) != at]


def release(ops, lane):
    """Drop every claim this lane holds."""
    for p in glob.glob(os.path.join(ops, *CLAIMS, "*.json")):
        try:
            if json.load(open(p, encoding="utf-8")).get("lane") == lane:
                os.remove(p)
        except (OSError, ValueError):
            pass


def stamp_run_all(ops, lane):
    """The first lane to look for work after a "Run all now" press owns it for the rest of its tick."""
    p = os.path.join(ops, "state", "run_now", "all")
    try:
        if os.path.exists(p) and not open(p, encoding="utf-8").read().startswith("lane "):
            open(p, "w", encoding="utf-8").write(f"lane {lane}" + chr(10))
    except OSError:
        pass


def end_tick(ops, lane):
    """--release at the end of a lane's tick: its claims go, and a "Run all now" press it owns is used up."""
    release(ops, lane)
    p = os.path.join(ops, "state", "run_now", "all")
    try:
        if open(p, encoding="utf-8").read().split() == ["lane", str(lane)]:
            os.remove(p)
    except OSError:
        pass


# In the sweep, finishing beats starting (judge panel, 2026-10-30): in-flight work goes first, and nothing starts that
# cannot end before the reset. ponytail: a piece's usual length is half its STUCK limit; tune from the transcripts.
TYPICAL = {k: v // 2 for k, v in STUCK.items()}
SWEEP_ORDER = ("merge", "security", "product-owner")   # a merge, Security on a built branch, a gate check; then the rest


# Work that pays off next week (the CEO, 2026-10-30): in the end-of-week sweep a Dev build that cannot finish runs its
# PLAN phase only (ops/schedules/dev-approved.md "plan <id>"; next week's build skips PLAN), and an empty question bank
# is refilled (customer-weekly.md "write the bank"). Both outputs keep after the reset. ponytail: guessed lengths.
AHEAD_JOBS = {"dev-plan": 45, "customer-bank": 40}
SWEEP_LOG = ("state", "sweep_log.jsonl")   # one line per weekly reset: what the week left unspent, and why (sweep_log())


SKILLS = os.path.join(os.path.expanduser("~"), ".claude", "scheduled-tasks")   # the tasks' own prompts (SKILL.md)


def prompt_knows(task, marker):
    """True when the scheduled task's prompt has `marker`: a lane follows that prompt, so a line it cannot read (e.g. a
    Dev "plan <id>" under a prompt that only knows "build") is never offered."""
    try:
        return marker in open(os.path.join(SKILLS, TASK + task, "SKILL.md"), encoding="utf-8").read()
    except OSError:
        return False


def planned_ahead(ops, i):
    """A sweep's PLAN-only run for work order `i` that no later BUILD or hand-back has used yet."""
    d = os.path.join(ops, "inbox", "dev", i)
    return os.path.exists(d + ".planned-ahead") and _mtime(d + ".planned-ahead") > max(_mtime(d + ".build.md"), _mtime(d + ".md"))


def sweep_fits(dept, resets, now):
    return (resets - now).total_seconds() / 60 >= TYPICAL.get(dept, 60)


def sweep_rank(line):
    dept = "merge" if line.startswith(("MERGE", "WAIT merge")) else line.split()[1].removeprefix(TASK)
    return SWEEP_ORDER.index(dept) if dept in SWEEP_ORDER else len(SWEEP_ORDER) + (dept == "dev")


def five_reset(ops):
    """When the current 5-hour window resets (usage.json "r5"), or None if unknown or already past."""
    try:
        v = json.load(open(os.path.join(ops, "state", "usage.json"), encoding="utf-8"))["r5"]
        r = (datetime.datetime.fromtimestamp(v / 1000 if v > 1e11 else v, datetime.timezone.utc) if isinstance(v, (int, float))
             else datetime.datetime.fromisoformat(v.replace("Z", "+00:00")))
        r = r if r.tzinfo else r.replace(tzinfo=datetime.timezone.utc)   # a time written without a zone is UTC, never a crash
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return None
    return r if r > datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1) else None


def _since(ops, name):
    """Hours since OPS/state/<name> was written, or None."""
    p = os.path.join(ops, "state", name)
    return (time.time() - os.path.getmtime(p)) / 3600 if os.path.exists(p) else None


def sweep(ops, week, resets, now, r5=None):
    """(on, why): Mo's sweep. On when you called him (MO_CALL hours), in the last SWEEP_HOURS before the weekly reset with
    SWEEP_LEFT % unspent, or in a batch: the last BATCH_MIN minutes of a 5-hour window, from BATCH_FROM hours before the
    reset, while the week is BATCH_PACE times ahead of its pace. Off while the office is off, OPS/state/no_sweep exists,
    or you sent him home in the last MO_HOME hours. `r5` (the 5-hour reset) defaults to usage.json's."""
    left, to_go = 100 - week, (resets - now).total_seconds()
    home, called = _since(ops, "mo_home"), _since(ops, "mo_called")
    if is_off(ops) or os.path.exists(os.path.join(ops, "state", "no_sweep")) or (home is not None and home < MO_HOME) or left < SWEEP_LEFT:
        return False, ""
    if called is not None and called < MO_CALL:
        return True, f"you called Mo: {left:.0f}% of the week left, until {MO_CALL - called:.1f} h from now"
    if 0 < to_go <= SWEEP_HOURS * 3600:
        return True, f"the cleaner's sweep: {left:.0f}% of the week left, {to_go / 3600:.1f} h to the reset"
    r5 = r5 or five_reset(ops)
    pace = 100 * to_go / WEEK.total_seconds()
    if r5 and 0 < to_go <= BATCH_FROM * 3600 and left >= BATCH_PACE * pace and 0 < (r5 - now).total_seconds() <= BATCH_MIN * 60:
        return True, f"Mo's batch: {left:.0f}% of the week left with {pace:.0f}% of the week to go; the 5-hour window resets at {r5:%H:%M} UTC"
    return False, ""


def work_mode(ops, week, resets, now):
    """(mode, why): the weekly allowance left against the time left before the reset. 1.5x or more is ultra,
    1.1x or more high, less easy. OPS/state/mode holding easy, high or ultra overrides it (the CEO's choice),
    and the cleaner's sweep overrides both: what is left of the week would otherwise expire."""
    on, why = sweep(ops, week, resets, now)
    if on:
        return ("batch" if why.startswith("Mo's batch") else "sweep"), why
    try:
        m = open(os.path.join(ops, "state", "mode"), encoding="utf-8").read().strip().lower()
        if m in MODES:
            return m, "set by you"
    except OSError:
        pass
    left, time_left = 100 - week, max(100 * (resets - now) / WEEK, 1)
    r = left / time_left
    return ("ultra" if r >= 1.5 else "high" if r >= 1.1 else "easy"), f"{left:.0f}% of the week's allowance left, {time_left:.0f}% of the week"


EVERY = (0, 15, 30, 60, 120)   # "Next run" on the office page: minutes between a lane's runs; 0 = back to back


def every(ops):
    """Minutes between a lane's runs, from OPS/state/every (the CEO's "Next run" setting); 60 when unset."""
    try:
        m = int(open(os.path.join(ops, "state", "every"), encoding="utf-8").read().strip())
        return m if m in EVERY else 60
    except (OSError, ValueError):
        return 60


def lane_cron(lane, minutes):
    """The lane's cron: every N minutes, the three lanes a third of N apart. Back to back is every 10 minutes:
    runs of one task never overlap, so the next starts within 10 minutes of the last one ending.
    Lane 1 follows "Next run" too, 10 minutes at the fastest (the CEO, 2026-10-26: hourly routines; it was always every
    10 minutes so page decisions never waited, and at hourly a decision now waits up to an hour).
    Lanes 2 and 3 check at most every 30 minutes: they only add a second job beside a long one, and at every 10
    minutes their wake-ups were a large share of the office's cost (2026-10-21)."""
    step = max(minutes, 10) if lane == 1 else max(minutes, 30)
    off = (lane - 1) * step // 3
    if step < 60:
        return ",".join(str(m) for m in range(off, 60, step)) + " * * * *"
    return f"{off % 60} " + ("*" if step == 60 else f"{off // 60}-23/{step // 60}") + " * * *"


def needed(ops, lane, free=()):
    """Lane 1 runs a full tick when it holds a leftover claim or `free` (startable() lines) is not empty; otherwise
    it stops after reading the page's decisions, and once an hour also refreshes the page and the cost charts
    ("refresh due"). 84% of its October wake-ups found nothing and still cost a full tick (2026-10-21).
    Lanes 2 and 3 exist for when a long job (a build, a check) holds a lane, so the
    rest of the work is not stuck behind it: they run only while another lane holds work, or when lane 1
    has not looked for work in LANE_ONE_LATE (the CEO, 2026-10-19: most of their wake-ups found nothing)."""
    if is_off(ops):   # red for every lane; lane 1's step 0 still reads the page first, so its power button turns it back on
        return False, "the office is off"
    held = claims(ops).values()
    if lane == 1:
        os.makedirs(os.path.join(ops, "state"), exist_ok=True)
        open(os.path.join(ops, "state", "lane-1-seen"), "w").close()   # lane 1 is alive: lanes 2 and 3 stay asleep
        if any(c.get("lane") == 1 for c in held):
            return True, "lane 1 holds a leftover claim"
        if free:
            return True, f"work is free: {free[0]}"
        stamp = os.path.join(ops, "state", "lane-1-refreshed")
        if time.time() - _mtime(stamp) < REFRESH:
            return False, "nothing to do"
        open(stamp, "w").close()
        return False, "nothing to do; refresh due"
    if any(c.get("lane") == lane for c in held):   # a leftover claim of this lane's last run: run, so take() drops it (else it blocks every lane until STALE)
        return True, f"lane {lane} holds a leftover claim"
    _, week, old = latest_usage(HISTORY, fallback=os.path.join(ops, "state", "usage.json"))   # HISTORY read now: the selftest swaps it
    now = datetime.datetime.now(datetime.timezone.utc)
    if free and old <= 60 and sweep(ops, week, next_reset(now), now)[0]:
        return True, f"the cleaner's sweep: every lane works until the weekly reset; {free[0]}"
    # stale figures while the clock alone says Mo could be sweeping: wake, so the full tick refreshes them from get_usage
    # (2026-10-30: lanes 1 and 3 sat in long runs, nobody refreshed usage, and the 16:00 sweep never started)
    if 60 < old < float("inf") and sweep(ops, 0, next_reset(now), now)[0]:   # inf: no figures ever (nothing to refresh from)
        return True, "usage figures are stale during Mo's sweep window: refresh them and look for work"
    busy = sorted({c.get("lane") for c in held if c.get("lane") not in (lane, None, "hand")})
    if busy:
        return True, (f"lane {busy[0]} is busy" if len(busy) == 1 else "lanes " + " and ".join(map(str, busy)) + " are busy")
    seen = _mtime(os.path.join(ops, "state", "lane-1-seen"))
    if time.time() - seen > LANE_ONE_LATE:
        return True, "lane 1 has not looked for work in 90 minutes"
    return False, "no other lane is busy"


def _spaced(ops, lane):
    """"Next run": lane 1 starts new work only every N minutes."""
    gap = every(ops)
    # 3/4 of the gap: lane 1 ticks on that same period, so a full gap skipped every other tick (2026-10-29)
    return lane == 1 and gap > 10 and time.time() - _mtime(os.path.join(ops, "state", "lane-1-worked")) < gap * 45


def startable(ops, rows, lane):
    """[(line, key)] of the entries() rows this lane may start now: runnable, and not held back by "Next run"
    spacing (merges and Run now presses go at once)."""
    spaced, pressed = _spaced(ops, lane), run_now(ops)
    out = []
    for line, key, ok in rows:
        dept = line.split()[1].removeprefix(TASK) if line.startswith("RUN") else "merge"
        if ok and not (spaced and dept != "merge" and "all" not in pressed and dept not in pressed):
            out.append((line, key))
    return out


def take(ops, repo, five, week, resets, now, lane):
    """The plan, then a claim on its first runnable line no other lane holds. Runs of one lane
    never overlap, so this lane's leftover claims are from its last run and are dropped first."""
    release(ops, lane)
    os.makedirs(os.path.join(ops, "state"), exist_ok=True)
    open(os.path.join(ops, "state", f"lane-{lane}-seen"), "w").close()   # needed(): lane 1 is alive
    stamp_run_all(ops, lane)
    d = os.path.join(ops, *CLAIMS)
    os.makedirs(d, exist_ok=True)
    mode, why = work_mode(ops, week, resets, now)   # for the product owner's cap (.claude/agents/product-owner.md)
    with open(os.path.join(ops, "state", "mode.json"), "w", encoding="utf-8") as f:
        json.dump({"mode": mode, "why": why, "approved": MODES[mode][0], "builds": MODES[mode][1], "per_department": MODES[mode][2]}, f)
    rows = entries(ops, repo, five, week, resets, now, claims(ops))
    worked, gap = os.path.join(ops, "state", "lane-1-worked"), every(ops)
    for line, key in startable(ops, rows, lane):
        try:
            fd = os.open(os.path.join(d, key + ".json"), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:   # another lane won the race: try the next line
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"lane": lane, "at": now.isoformat(timespec="seconds"), "line": line}, f)
        if "parked to check: " in line:   # handed over: the same set does not wake the product owner again
            with open(os.path.join(ops, *PARKED), "w", encoding="utf-8") as f:
                f.write(" ".join(line.split("parked to check: ")[1].split(";")[0].replace(",", " ").split()))
        if lane == 1 and key != "merge":
            open(worked, "w").close()
        try:   # a "Run now" press is used up by the start it allowed
            os.remove(os.path.join(ops, "state", "run_now", "dev" if key.startswith("dev-") else key))
        except OSError:
            pass
        return [r[0] for r in rows] + [f"TOOK {key} | {line}"]
    if _spaced(ops, lane) and any(ok and not r.startswith("MERGE") for r, _, ok in rows):
        due = datetime.datetime.fromtimestamp(_mtime(worked) + gap * 45).strftime("%H:%M")
        rows = rows + [(f"SPACED lane 1 starts new work at {due} (Next run: every {gap} min); your decisions were read now", None, False)]
    return ([r[0] for r in rows] or ["IDLE"]) + ["TOOK nothing"]


BRANCHES = ("agents/dev-{}", "agents/office-{}", "agents/dev-{}-office")   # what tools/office_merge.py merge() merges, in order
COMPANIONS = BRANCHES[1:]


def open_branches(i, repo, heads, names=BRANCHES):
    """{branch: tip} for work order i's branches (of names) that exist and are merged into neither the checkout's HEAD
    nor the office line, read the way ops_ledger.git_sets reads merged; heads = {branch: tip} of refs/heads/agents/."""
    return {b: heads[b] for b in (n.format(i) for n in names) if b in heads
            and all(_git(repo, "merge-base", "--is-ancestor", b, into)[0] for into in ("HEAD", "experimental/agent-org-phase1"))}


def blocking(i, repo):
    """(checkout, [files]) for a ready Dev branch: the uncommitted edits in the checkout it merges into that the
    merge would go over (tools/office_merge.py in_the_way; a stray copy of the branch's own file is not one)."""
    target = next((c for c in dict.fromkeys((repo, OFFICE)) if _git(c, "rev-parse", "--verify", "-q", f"agents/dev-{i}")[0] == 0
                   and _git(c, "merge-base", "--is-ancestor", "HEAD", f"agents/dev-{i}")[0] in (0, 1)), repo)
    return target, in_the_way(f"agents/dev-{i}", target)


def waiting(ops, repo, now, taken=(), mode=MODES["high"]):
    """[(department, why, claim key)] for every office task with work waiting, most urgent first.
    Dev builds skip work orders another lane already holds, so a second lane gets the next one."""
    items = decisions(ops)
    merged, reverted = git_sets(repo)
    dev = {i: stage_of(it, ops, merged, reverted) for i, it in items.items() if it.get("assigned", "dev") == "dev"}
    check = [i for i, s in dev.items() if s == "built, waiting for the check"]
    # a gate that stopped without a verdict since its hand-back and since the last commit stops the same way again: it
    # waits for a change (2026-10-29: D-20261025-11's false leak alarm took lane 1 every hour and nothing else ran)
    head = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%ct"], capture_output=True, text=True).stdout.strip()
    gate = lambda i, f: os.path.join(ops, "inbox", "dev", i + ".gate", f)
    stuck = [i for i in check if os.path.exists(gate(i, "FAILED")) and not os.path.exists(gate(i, "gate.json"))
             and _mtime(gate(i, "FAILED")) > max(_mtime(os.path.join(ops, "inbox", "dev", i + ".md")), int(head or 0))]
    check = [i for i in check if i not in stuck]
    build = sorted((i for i, s in dev.items() if s in ("approved, queued", "sent back, rebuilding")),
                   key=lambda i: (items[i].get("from") != "security", items[i].get("priority", 99), i))   # security, then the PO's rank
    # the CEO, 2026-10-15: nothing reaches Dev until the product owner has checked, after its latest approval (a CEO tick
    # too), that an earlier fix has not already fixed it ("- Still broken:"); an already fixed one it rejects
    recheck = [i for i in build if items[i].get("checked", -1) < items[i].get("approved", 0)]
    build = [i for i in build if i not in recheck]
    review = lambda i: os.path.join(ops, "inbox", "security", f"review-{i}.md")
    # a review older than Dev's latest hand-back judged an earlier round: it counts as none, so Security reviews again
    # (2026-10-23: round 1's BLOCK outlived round 2's fix, and D-20261022-03 plus three builds behind it sat for a day)
    # D-20261112-11: and only when it names the tip of every branch of the item still to merge (tools/office_merge.py
    # merges nothing else); a work order merged without its office companion still waits, for Security and the merge
    heads = dict(l.split(" ", 1) for l in _git(repo, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads/agents/")[1].splitlines())
    ready = {i: s for i, s in dev.items() if s in ("check passed", "ready to merge")}
    ready.update({i: "ready to merge" if items[i]["history"][-1:] == ["READY TO MERGE"] else "check passed" for i, s in dev.items()
                  if s == "merged" and (items[i].get("gate") or {}).get("verdict") == "PASS" and open_branches(i, repo, heads, COMPANIONS)})
    text = lambda i: open(review(i), encoding="utf-8").read() if os.path.exists(review(i)) else ""
    reviewed = lambda i: (os.path.exists(review(i)) and _mtime(review(i)) >= _mtime(os.path.join(ops, "inbox", "dev", i + ".md"))
                          and all(names_tip(text(i), b, t) for b, t in open_branches(i, repo, heads, COMPANIONS if dev[i] == "merged" else BRANCHES).items()))
    def verdict(i):   # Security's review of the branch: CLEAR, BLOCK or ""
        if not reviewed(i):
            return ""
        m = re.search(r"(?mi)^Verdict:\s*(CLEAR|BLOCK)", open(review(i), encoding="utf-8").read())
        return m.group(1).upper() if m else ""
    out = [("product-owner", f"check {i} waits: its gate stopped without a verdict; it runs again after the next commit", None) for i in stuck]
    for i in sorted(i for i, s in ready.items() if s == "ready to merge" and verdict(i) == "CLEAR"):   # tools/office_merge.py
        # someone's uncommitted edit in the way waits, and the lanes take the next work (2026-10-16: one such merge
        # came first in every lane's plan, so nothing else ran for hours)
        target, busy = blocking(i, repo)
        out.append(("merge", f"{i} waits: uncommitted changes in {target} touch {', '.join(busy)}; commit or undo them", None)
                   if busy else ("merge", i, "merge"))
    blocked = [os.path.join(ops, "inbox", "dev", i + ".md") for i, s in dev.items() if s == "blocked"]
    blocked += [review(i) for i in ready if verdict(i) == "BLOCK"]
    findings = [p for p in set(glob.glob(os.path.join(ops, "inbox", "*", "*.md")) + blocked)   # a BLOCKED hand-back needs a decision
                # every department report wakes the product owner, notes too (the CEO, 2026-10-10); a Security review is read with its branch
                # a BLOCKED hand-back or BLOCK review counts until a triage newer than it (decisions/*.md) has seen it
                if (p in blocked or (os.path.basename(os.path.dirname(p)) != "dev" and not os.path.basename(p).startswith("review-")))
                and os.path.getmtime(p) > _mtime(os.path.join(ops, "decisions", "*.md"))]
    lab = cap_status(ops)
    capped = lab["stopped"] and (f"lab stopped at the cap (${lab['spent']:.2f} of ${lab['usd']:.2f} this {lab['per']})"
                                 "; raise it with the DeepSeek slider at the top right")
    if capped and check:   # a gate check spends lab money: it waits with the lab
        out.append(("product-owner", f"check {', '.join(check)} waits: {capped}", None))
        check = []
    if check or findings or recheck:
        out.append(("product-owner", "; ".join(filter(None, [check and "check " + ", ".join(check),
                                                             recheck and "still broken? " + ", ".join(recheck),
                                                             findings and f"{len(findings)} new findings"])), "product-owner"))
    # work orders that change the same files never run side by side (the CEO, 2026-10-14): one waits until the other merges
    stages = {i: stage_of(it, ops, merged, reverted) for i, it in items.items()}
    busy = {i for i, s in stages.items() if s in IN_FLIGHT - {"approved, queued"}} | \
           {i for i in items if any(k.endswith("-" + i) for k in taken)}
    def clash(i):   # the first busy item that touches a file or folder this one touches, with the path
        for j in sorted(busy - {i}):
            for a in items[i].get("touches", []):
                for b in items[j].get("touches", []):
                    # ponytail: the tests folder alone is no clash (2026-10-24: a quarter of all orders list it, so
                    # every two that add tests queued); the same test file still clashes, and office_merge.py sends a
                    # real merge conflict back to Dev
                    if (a == b or a.startswith(b + "/") or b.startswith(a + "/")) and min(a, b, key=len) != "src/tests":
                        return j, min(a, b, key=len)
        return None
    free = [i for i in build if f"dev-{i}" not in taken and not clash(i)]
    if free:
        out.append(("dev", f"build {free[0]}" + (f" (+{len(free) - 1} queued)" if len(free) > 1 else ""), f"dev-{free[0]}"))
    out += [("dev", "build {} waits for {}: both change {}".format(i, *clash(i)), None)
            for i in build if f"dev-{i}" not in taken and clash(i)][:1 if free else 3]
    # approved work for another department (2026-10-14: five such orders sat untouched): one at a time each, until it
    # files OPS/inbox/<dept>/<id>-done.md, which wakes the product owner to close it
    for i, it in sorted(items.items()):
        dept = it.get("assigned", "dev")
        if (dept in COST and dept not in ("dev", "product-owner", "diary") and stages[i] == "approved, queued"
                and (not os.path.exists(os.path.join(ops, "inbox", dept, f"{i}-done.md"))   # done, unless a triage resent it
                     or answered(it, ops, os.path.join(ops, "inbox", dept, f"{i}-done.md")))
                and sum(d == dept and why.startswith("work order") and bool(k) for d, why, k in out) < mode[2]):
            c = f"{dept}-{i}" not in taken and clash(i)
            out.append((dept, "work order {} waits for {}: both change {}".format(i, *c), None) if c else (dept, f"work order {i}", f"{dept}-{i}"))
    age = lambda t: (now.timestamp() - t) / 86400
    last_run = _mtime(os.path.join(ops, "runs", "*", "DONE"))
    try:
        last_merge = int(subprocess.run(["git", "-C", repo, "log", "-1", "--format=%ct", "--grep=agents/dev-"],
                                        capture_output=True, text=True, timeout=30).stdout.strip() or 0)
    except Exception:
        last_merge = 0
    # parked work comes back by priority (the CEO, 2026-10-14: 34 deferred items sat with slots free): the product owner
    # re-decides the DEFER list when a slot is free and it has not looked since one freed up, or daily
    # the CEO, 2026-10-17: with slots free, the top parked items (by the product owner's priority, at most 3) go to it to
    # check - clash with work in flight, already fixed, still relevant - and approve; once per set, so an idle tick
    # does not re-wake it for the ones it just left parked, unless a merge freed something or a day passed
    stages = {i: stage_of(it, ops, merged, reverted) for i, it in items.items() if it.get("assigned", "dev") != "ceo"}
    slots = mode[0] - sum(s in IN_FLIGHT for s in stages.values())
    top = sorted((i for i, s in stages.items() if s == "deferred"), key=lambda i: (items[i].get("priority", 99), i))[:min(slots, 3)]
    triaged = _mtime(os.path.join(ops, "decisions", "*.md"))
    try:
        seen = open(os.path.join(ops, *PARKED), encoding="utf-8").read().split()
    except OSError:
        seen = []
    if top and (top != seen or last_merge > triaged or age(triaged) >= 1):
        why = f"parked to check: {', '.join(top)}"
        po = next((n for n, (d, _, k) in enumerate(out) if d == "product-owner" and k), None)
        if po is None:
            out.append(("product-owner", f"{slots} of {mode[0]} slots free; {why}", "product-owner"))
        else:   # one run does both
            out[po] = (out[po][0], f"{out[po][1]}; {why}", out[po][2])
    # the CEO, 2026-11-12: once a week the product owner offers the product line's fixes to main, and its next run
    # ports the ones the CEO approved (tools/main_port.py, from ops/schedules/product-owner.md)
    ported = sorted(port_approved(ops))
    port = f"port to main: {', '.join(ported)}" if ported else "weekly port to main due" if port_due(ops, now.timestamp()) else ""
    if port:
        po = next((n for n, (d, _, k) in enumerate(out) if d == "product-owner" and k), None)
        if po is None:
            out.append(("product-owner", port, "product-owner"))
        else:
            out[po] = (out[po][0], f"{out[po][1]}; {port}", out[po][2])
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
    unreviewed = [i for i in ready if not reviewed(i)]
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
    # the CEO, 2026-10-17: nothing to build, check or test -> the departments that find work look for more, every IDLE_EVERY days
    if not any(k for d, _, k in out if d not in ("diary", "merge")):
        last = {"security": _mtime(os.path.join(ops, "state", "security_last_run.txt")),
                "audit": _mtime(os.path.join(ops, "inbox", "audit", "*-weekly.md")),
                "rnd": _mtime(os.path.join(ops, "state", "rnd_last_run.txt")),
                "marketing": _mtime(os.path.join(ops, "state", "marketing_last_run.txt"))}
        for dept, t in last.items():
            if dept not in {d for d, _, _ in out} and age(t) >= IDLE_EVERY:
                out.append((dept, "office idle: look for new work to improve the product", dept))
    for dept in sorted(run_now(ops) - {d for d, _, _ in out} - {"dev", "all"}):   # "Do it now" with nothing due (Dev needs a work order)
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
    start skips the budget pace and a hold. A press lapses after a day, and taking the work uses it up.
    "all" (the page's "Run all now") lifts both for every department with work waiting, for one tick: the first lane
    to take work after the press stamps it, and that lane's end of tick removes it."""
    d = os.path.join(ops, "state", "run_now")
    return {n for n in (os.listdir(d) if os.path.isdir(d) else [])
            if time.time() - os.path.getmtime(os.path.join(d, n)) < (RUN_ALL if n == "all" else 86400)}


def is_off(ops):
    return os.path.exists(os.path.join(ops, "state", "office_off"))


def entries(ops, repo, five, week, resets, now, taken):
    """[(line, claim key, runnable)]. Work other lanes are running counts against the budget,
    since two lanes starting together both see the same usage figures."""
    held, forced = holds(ops, now), run_now(ops)
    mode = MODES[work_mode(ops, week, resets, now)[0]]
    swept, swept_why = sweep(ops, week, resets, now)   # a batch may run the 5-hour window to BATCH_FIVE: it resets within the half hour
    if is_off(ops):
        held |= {"merge"} | {d for d, _, _ in waiting(ops, repo, now, taken, mode)}
    cost = lambda k: (0, 0) if taken[k].get("lane") == "hand" else COST.get("dev" if k.startswith("dev-") else k, (0, 0))   # a blind test spends no Claude usage
    # 5-hour only: the weekly figure already holds what running work has spent, and adding its estimate again held
    # every department a day after the reset (the CEO, 2026-10-24); a few points over pace is caught next tick
    five = five + sum(cost(k)[0] for k in taken)
    building = sum(k.startswith("dev-") for k in taken)
    week_end, left_min = swept and swept_why.startswith("the cleaner's sweep"), (resets - now).total_seconds() / 60
    out = []
    for dept, why, key in waiting(ops, repo, now, taken, mode):
        if key is None:   # waits on other work, not on the budget
            out.append((f"WAIT {TASK}{dept}  {why}", None, False))
            continue
        lane = f" (lane {taken[key].get('lane')})" if key in taken else ""
        if dept == "merge":
            stop = "held (OPS/state/hold)" if "merge" in held else f"another merge is running{lane}" if lane else None
            out.append((f"WAIT merge {why}  {stop}" if stop else f"MERGE {why}  gate PASS, READY TO MERGE, Security CLEAR", key, not stop))
            continue
        pressed = (dept in forced or "all" in forced) and not is_off(ops)   # "Run now": skips a hold and the budget, not the 5-hour limit itself
        stop = ("held (OPS/state/hold)" if dept in held and not pressed else f"already running{lane}" if lane else
                f"{mode[1]} builds already running" if dept == "dev" and building >= mode[1] else
                f"5-hour window at {five}%" if pressed and five >= 90 else
                None if pressed else
                (f"5-hour window at {five}%" if five + (COST.get(dept, (0, 0))[0] if swept_why.startswith("Mo's batch") else 0)
                 >= (BATCH_FIVE if swept_why.startswith("Mo's batch") else SWEEP_FIVE) else
                 f"cannot finish before the reset (usually {TYPICAL.get(dept, 60)} min)" if not sweep_fits(dept, resets, now) else
                 None) if swept else   # the sweep skips the pace, not a hold
                over_budget(dept, five, week, resets, now))
        # end of the week: a build that cannot finish plans now and builds after the reset (AHEAD_JOBS)
        if (week_end and dept == "dev" and stop and stop.startswith("cannot finish") and key.startswith("dev-")
                and not planned_ahead(ops, key[4:]) and left_min >= AHEAD_JOBS["dev-plan"] and prompt_knows("dev", "PLAN AHEAD:")):
            why, stop = f"plan {key[4:]}: the plan now, the build after the reset", None
        out.append((f"WAIT {TASK}{dept}  {why}; {stop}" if stop else f"RUN  {TASK}{dept}  {why}", key, not stop))
    # ... and an empty question bank is refilled, so next week's test starts asking at once
    if (week_end and "customer" not in taken and left_min >= AHEAD_JOBS["customer-bank"] and five < SWEEP_FIVE
            and prompt_knows("customer", "BANK ONLY:")):
        import question_bank
        at = next((n for n, r in enumerate(out) if r[0].split()[1:2] == [TASK + "customer"]), None)
        if (at is None or not out[at][2]) and question_bank.need(ops) == "EMPTY":
            row = (f"RUN  {TASK}customer  write the bank: next week's 40 questions in advance", "customer", True)
            out[at if at is not None else len(out):(at + 1) if at is not None else len(out)] = [row]
    if swept:   # finish what is in flight first; sort is stable, so the usual order holds within each rank
        out.sort(key=lambda r: sweep_rank(r[0]))
    return out


def sweep_log(ops, repo, five, week, now):
    """Once per weekly reset, in its last 20 minutes: one line in OPS/state/sweep_log.jsonl with the share of the week
    left unspent and the work that waited on the budget or on time, so README's revisit rule can be checked. Call it
    only with fresh usage figures. Returns the record written, or None."""
    resets = next_reset(now)
    if (resets - now).total_seconds() > 20 * 60:
        return None
    p, key = os.path.join(ops, *SWEEP_LOG), resets.isoformat(timespec="minutes")
    try:
        if any(json.loads(l).get("reset") == key for l in open(p, encoding="utf-8") if l.strip()):
            return None
    except (OSError, ValueError):
        pass
    rows = [r[0] for r in entries(ops, repo, five, week, resets, now, claims(ops))]
    budget = [l for l in rows if re.search(r"would pass|ahead of the week's pace|5-hour window at", l)]
    late = [l for l in rows if "cannot finish before the reset" in l]
    rec = {"reset": key, "at": now.isoformat(timespec="seconds"), "unspent": round(100 - week, 1), "five": five,
           "waiting_budget": len(budget), "waiting_time": len(late), "startable": sum(l.startswith(("RUN", "MERGE")) for l in rows),
           "lines": budget + late}
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def revisit(ops):
    """README's rule for reconsidering a bigger Mo: the last two weekly resets each left more than 5% of the week
    unspent while work waited on the budget. The reason, or ""."""
    try:
        recs = [json.loads(l) for l in open(os.path.join(ops, *SWEEP_LOG), encoding="utf-8") if l.strip()][-2:]
    except (OSError, ValueError):
        return ""
    if len(recs) == 2 and all(r.get("unspent", 0) > 5 and r.get("waiting_budget", 0) > 0 for r in recs):
        return " and ".join(f"{r['unspent']:.0f}% unspent at {r['reset'][:10]} with {r['waiting_budget']} waiting on budget" for r in recs)
    return ""


def plan(ops, repo, five, week, resets, now, taken=None):
    return [r[0] for r in entries(ops, repo, five, week, resets, now, claims(ops) if taken is None else taken)] or ["IDLE"]


def selftest():
    import office_diary
    now = datetime.datetime(2026, 10, 8, 7, 0, tzinfo=datetime.timezone.utc)
    resets = datetime.datetime(2026, 10, 9, 19, 0, tzinfo=datetime.timezone.utc)   # pace ~79%, so up to ~94% with AHEAD
    global HISTORY
    HISTORY = os.path.join(tempfile.gettempdir(), "no-such-usage-history.json")   # the app's real usage must not steer the test
    with tempfile.TemporaryDirectory() as ops:
        office_diary.OFFICE = ops   # no real commits in the test's diary
        for d in ("decisions", "inbox/dev", "inbox/audit", "runs/20261008", "state"):
            os.makedirs(os.path.join(ops, d))
        w = lambda p, s="": open(os.path.join(ops, p), "w", encoding="utf-8").write(s)
        # work mode from the allowance left vs the week left (21% of the week left here), then pinned to high for the rest
        assert [work_mode(ops, wk, resets, now)[0] for wk in (40, 75, 80)] == ["ultra", "high", "easy"]
        w("state/mode", "high\n")
        w("state/every", "0")   # back to back: "Next run" spacing is tested on its own below
        assert work_mode(ops, 40, resets, now) == ("high", "set by you")
        # the cleaner's sweep: only in the last SWEEP_HOURS, only with SWEEP_LEFT % unspent, and it outranks a set mode
        late = resets - datetime.timedelta(hours=2)
        assert work_mode(ops, 50, resets, late)[0] == "sweep" and sweep(ops, 50, resets, late)[0]
        assert not sweep(ops, 50, resets, resets - datetime.timedelta(hours=4))[0], "too early"
        assert not sweep(ops, 95, resets, late)[0], "too little left to sweep"
        w("state/no_sweep")
        assert work_mode(ops, 50, resets, late) == ("high", "set by you"), "the CEO's switch-off"
        os.remove(os.path.join(ops, "state", "no_sweep"))
        # Mo's batches: Thursday on, well ahead of pace, only in the last BATCH_MIN minutes of a 5-hour window
        thu = resets - datetime.timedelta(hours=40)   # 40 h to go = 24% of the week; 2.3x that is 55% left needed
        soon, later = thu + datetime.timedelta(minutes=20), thu + datetime.timedelta(hours=2)
        assert sweep(ops, 40, resets, thu, r5=soon)[1].startswith("Mo's batch"), "60% left on Thursday, window resets in 20 min"
        assert not sweep(ops, 40, resets, thu, r5=later)[0], "not with the window two hours from resetting"
        assert not sweep(ops, 50, resets, thu, r5=soon)[0], "50% left is not ahead enough on Thursday"
        assert not sweep(ops, 20, resets, resets - datetime.timedelta(hours=60), r5=resets - datetime.timedelta(hours=59, minutes=50))[0], "not before Thursday"
        # "Call Mo" sweeps now; "Send Mo home" stops even the end-of-week sweep
        w("state/mo_called")
        assert sweep(ops, 70, resets, thu)[1].startswith("you called Mo")
        os.remove(os.path.join(ops, "state", "mo_called")); w("state/mo_home")
        assert not sweep(ops, 50, resets, late)[0], "sent home"
        os.remove(os.path.join(ops, "state", "mo_home"))
        # finishing beats starting: a Dev build (usually 3.5 h) does not start 1 h before the reset, a Security review does;
        # merges go first, then Security, then the product owner, Dev last
        hour = resets - datetime.timedelta(hours=1)
        assert not sweep_fits("dev", resets, hour) and sweep_fits("security", resets, hour)
        lines = [f"RUN  {TASK}dev  build x", f"RUN  {TASK}customer  re-ask", f"RUN  {TASK}product-owner  check y",
                 "MERGE D-1  gate PASS", f"RUN  {TASK}security  review z"]
        assert [l.split()[1] for l in sorted(lines, key=sweep_rank)] == [
            "D-1", f"{TASK}security", f"{TASK}product-owner", f"{TASK}customer", f"{TASK}dev"]
        assert lane_cron(1, 10) == "0,10,20,30,40,50 * * * *" and lane_cron(1, 60) == "0 * * * *", "lane 1 follows Next run"
        assert lane_cron(2, 60) == "20 * * * *" and lane_cron(3, 0) == "20,50 * * * *" and lane_cron(3, 120) == "20 1-23/2 * * *", "lanes 2 and 3: 30 minutes at most"
        w("decisions/20261008.md", "## D-20261008-01 | Two products\n- Assigned: dev\n- Verdict: NEEDS CEO\n- [x] CEO approved\n\n"
                                   "## D-20261008-02 | Asks\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n- Still broken: yes\n\n"
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
        # end of the week: the build that cannot finish plans now, an empty bank is refilled; both keep after the reset
        end = resets - datetime.timedelta(hours=1)
        global SKILLS
        real_skills, SKILLS = SKILLS, os.path.join(ops, "skills")
        assert not any(" plan D-" in l or "write the bank" in l for l in plan(ops, ops, 11, 38, resets, end)), "not before the prompts know them"
        for t, m in (("dev", "PLAN AHEAD:"), ("customer", "BANK ONLY:")):
            os.makedirs(os.path.join(SKILLS, TASK + t)); w(os.path.join("skills", TASK + t, "SKILL.md"), m)
        got = plan(ops, ops, 11, 38, resets, end)
        assert any(l.startswith("RUN  groundedops-office-dev  plan D-20261008-02: the plan now") for l in got), got
        assert any(l.startswith("RUN  groundedops-office-customer  write the bank") for l in got), got
        w("inbox/dev/D-20261008-02.planned-ahead")
        got = plan(ops, ops, 11, 38, resets, end)
        assert any(l.startswith("WAIT groundedops-office-dev  build D-20261008-02") and "cannot finish" in l for l in got), "planned once"
        os.remove(os.path.join(ops, "inbox/dev/D-20261008-02.planned-ahead"))
        SKILLS = real_skills
        # the week's record, once, in the reset's last 20 minutes; two such Fridays with work on budget trip the revisit rule
        assert sweep_log(ops, ops, 11, 38, end) is None, "too early"
        rec = sweep_log(ops, ops, 11, 38, resets - datetime.timedelta(minutes=10))
        assert rec and rec["unspent"] == 62 and sweep_log(ops, ops, 11, 38, resets - datetime.timedelta(minutes=5)) is None, rec
        assert not revisit(ops), "one Friday is not a pattern"
        open(os.path.join(ops, *SWEEP_LOG), "a").write(json.dumps({"reset": "2026-10-16T19:00+00:00", "unspent": 12, "waiting_budget": 2}) + "\n")
        open(os.path.join(ops, *SWEEP_LOG), "a").write(json.dumps({"reset": "2026-10-23T19:00+00:00", "unspent": 9, "waiting_budget": 1}) + "\n")
        assert "12% unspent" in revisit(ops) and "9% unspent" in revisit(ops)
        os.remove(os.path.join(ops, *SWEEP_LOG))
        os.makedirs(os.path.join(ops, "inbox/dev/D-20261008-01.gate"))
        w("inbox/dev/D-20261008-01.gate/FAILED", "hidden questions in the live log\n")   # stopped since the hand-back
        os.utime(os.path.join(ops, "inbox/dev/D-20261008-01.gate/FAILED"), (time.time() + 5, time.time() + 5))
        got = plan(ops, ops, 11, 38, resets, now)
        assert got[0].startswith("WAIT groundedops-office-product-owner  check D-20261008-01 waits: its gate stopped"), got
        assert got[1].startswith("RUN  groundedops-office-dev  build D-20261008-02"), got
        os.remove(os.path.join(ops, "inbox/dev/D-20261008-01.gate/FAILED")); os.rmdir(os.path.join(ops, "inbox/dev/D-20261008-01.gate"))
        os.remove(os.path.join(ops, "state/rnd_last_run.txt"))
        assert any("office-rnd  monthly research due" in x for x in plan(ops, ops, 11, 38, resets, now)), "R&D due monthly"
        w("state/rnd_last_run.txt")
        w("decisions/20261008b.md", "## D-20261008-08 | Key leak\n- From: security\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n- Still broken: yes\n")
        assert "build D-20261008-08 (+1 queued)" in plan(ops, ops, 11, 38, resets, now)[1], "security first"
        os.remove(os.path.join(ops, "decisions/20261008b.md"))
        # nothing reaches Dev unchecked, a CEO tick included; the product owner's rank orders the rest
        w("decisions/20261008r.md", "## D-20261008-03 | Three\n- Assigned: dev\n- Verdict: NEEDS CEO\n- Still broken: yes\n- [x] CEO approved\n\n"
                                    "## D-20261008-06 | Six\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n- Priority: 1\n- Still broken: yes\n")
        p = plan(ops, ops, 11, 38, resets, now)
        assert "still broken? D-20261008-03" in p[0] and "build D-20261008-06 (+1 queued)" in p[1], p
        w("decisions/20261008s.md", "## D-20261008-03 | Three\n- Still broken: yes, src/x.py still has it\n")
        assert "build D-20261008-06 (+2 queued)" in plan(ops, ops, 11, 38, resets, now)[1], "checked after the tick: it queues"
        for f in ("r", "s"):
            os.remove(os.path.join(ops, f"decisions/20261008{f}.md"))
        w("decisions/20261008c.md", "## D-20261008-04 | Four\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n- Still broken: yes\n\n"
                                    "## D-20261008-05 | Five\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n- Still broken: yes\n")
        took = [take(ops, ops, 11, 38, resets, now, n)[-1].split(" |")[0] for n in (1, 2, 3, 4)]
        assert took == ["TOOK product-owner", "TOOK dev-D-20261008-02", "TOOK dev-D-20261008-04", "TOOK nothing"], took
        p = plan(ops, ops, 11, 38, resets, now)
        assert "already running (lane 1)" in p[0] and "2 builds already running" in p[1], p
        release(ops, 3)
        assert "would pass 70%" in plan(ops, ops, 25, 38, resets, now)[1], "other lanes' work counts against the budget"
        edge = int(100 * (now - (resets - WEEK)) / WEEK) + AHEAD - max(c[1] for c in COST.values())   # the most a start may find
        assert not any("pace" in x for x in plan(ops, ops, 11, edge, resets, now)), "running work is not counted twice in the weekly figure"
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
        # overlapping work orders never run side by side: -05 changes src/, -04 (being built) src/policy.py
        w("decisions/20261008d.md", "## D-20261008-04 | Four\n- Touches: src/policy.py\n\n## D-20261008-05 | Five\n- Touches: .\\SRC\\, tools/x.py\n")
        w("state/claims/dev-D-20261008-04.json", json.dumps({"lane": 1, "line": "RUN  groundedops-office-dev  build D-20261008-04"}))
        p = plan(ops, ops, 11, 38, resets, now)
        assert "WAIT groundedops-office-dev  build D-20261008-05 waits for D-20261008-04: both change src" in p, p
        assert any(l.startswith("RUN  groundedops-office-dev  build D-20261008-02") for l in p), "a build that touches nothing in common still runs"
        w("decisions/20261008d.md", "## D-20261008-04 | Four\n- Touches: src/main.py (the band label, and more), src/tests/\n\n"
                                    "## D-20261008-05 | Five\n- Touches: src/figures.py, src/tests/test_x.py\n")
        assert not any("build D-20261008-05 waits" in l for l in plan(ops, ops, 11, 38, resets, now)), "the tests folder alone is no clash"
        w("decisions/20261008d.md", "## D-20261008-04 | Four\n- Touches: src/main.py (the band label, and more), src/tests/\n\n"
                                    "## D-20261008-05 | Five\n- Touches: src/main.py (another note)\n")
        p = plan(ops, ops, 11, 38, resets, now)
        assert "WAIT groundedops-office-dev  build D-20261008-05 waits for D-20261008-04: both change src/main.py" in p, p
        release(ops, 1)
        os.remove(os.path.join(ops, "decisions/20261008d.md"))
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
        hb = os.path.join(ops, "inbox/dev/D-20261008-01.md")
        gj = os.path.join(ops, "inbox/dev/D-20261008-01.gate/gate.json")
        os.utime(hb, (time.time() + 10, time.time() + 10))   # Dev hands back a round that answers the BLOCK, and it passes the gate
        os.utime(gj, (time.time() + 11, time.time() + 11))
        p = plan(ops, ops, 11, 38, resets, now)
        assert any("security" in x and "review D-20261008-01" in x for x in p) and not any(x.startswith("MERGE") for x in p), p
        os.utime(hb, (time.time() - 100, time.time() - 100))
        os.utime(gj, (time.time() - 100, time.time() - 100))
        # D-20261112-11: a review counts only when it names the tip of every branch of the item still to merge
        g = os.path.join(ops, "git2")
        gr = lambda *a: subprocess.run(["git", "-C", g, "-c", "user.name=t", "-c", "user.email=t", *a], capture_output=True, text=True, check=True).stdout.strip()
        def commit_on(b, f):   # a new branch off main with one commit, then back on main
            gr("checkout", "-qb", b)
            open(os.path.join(g, f), "w").write(b)
            gr("add", "."), gr("commit", "-qm", b), gr("checkout", "-q", "main")
        os.makedirs(g); gr("init", "-q", "-b", "main"); gr("commit", "-q", "--allow-empty", "-m", "base")
        commit_on("agents/dev-D-20261008-01", "f.py")
        sm = lambda: [(d, x) for d, x, _ in waiting(ops, g, now) if d == "merge" or x.startswith("review ")]
        named = lambda b: f"Branch: {b}, commit {gr('rev-parse', b)[:7]}\n"
        w("inbox/security/review-D-20261008-01.md", "Verdict: CLEAR\n")
        p = sm()
        assert any(d == "security" and "D-20261008-01" in x for d, x in p) and not any(d == "merge" for d, _ in p), p   # no line: Security, not MERGE
        w("inbox/security/review-D-20261008-01.md", "Verdict: CLEAR\n" + named("agents/dev-D-20261008-01"))
        p = sm()
        assert ("merge", "D-20261008-01") in p and not any(d == "security" and "D-20261008-01" in x for d, x in p), p
        gr("merge", "-q", "--no-ff", "agents/dev-D-20261008-01", "-m", "merge")       # the product branch merged ...
        commit_on("agents/dev-D-20261008-01-office", "g.py")                       # ... its office companion not
        p = sm()
        assert any(d == "security" and "D-20261008-01" in x for d, x in p) and not any(d == "merge" for d, _ in p), p   # merged id, unreviewed companion
        open(os.path.join(ops, "inbox/security/review-D-20261008-01.md"), "a", encoding="utf-8").write(named("agents/dev-D-20261008-01-office"))
        p = sm()
        assert ("merge", "D-20261008-01") in p and not any(d == "security" and "D-20261008-01" in x for d, x in p), p   # named and CLEAR: MERGE
        w("inbox/security/review-D-20261008-01.md", "Verdict: BLOCK\n")           # as the rest of the test expects
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
        w("decisions/20261008w.md", "## D-20261008-20 | Fresh hidden questions\n- From: customer\n- Assigned: customer\n- Verdict: AUTO-APPROVED\n- Still broken: yes\n")
        assert any(x.startswith("RUN  groundedops-office-customer  work order D-20261008-20") for x in plan(ops, ops, 11, 38, resets, now)), "a customer work order runs"
        os.makedirs(os.path.join(ops, "inbox", "customer"), exist_ok=True); w("inbox/customer/D-20261008-20-done.md", "Done.\n")
        assert not any("work order" in x for x in plan(ops, ops, 11, 38, resets, now)), "filed: the product owner closes it"
        os.remove(os.path.join(ops, "decisions", "20261008w.md")); os.remove(os.path.join(ops, "inbox", "customer", "D-20261008-20-done.md"))
        w("state/run_now/audit")
        assert any(x.startswith("RUN  groundedops-office-audit  you asked for it now") for x in plan(ops, ops, 11, 38, resets, now)), "Do it now, nothing due"
        os.remove(os.path.join(ops, "state", "run_now", "audit"))
        w("decisions/20261008p.md", "## D-20261008-31 | Parked one\n- Assigned: dev\n- Verdict: DEFER\n- Priority: 2\n\n"
                                    "## D-20261008-32 | Parked two\n- Assigned: dev\n- Verdict: DEFER\n- Priority: 1\n")
        p = plan(ops, ops, 11, 38, resets, now)
        assert any("1 of 3 slots free; parked to check: D-20261008-32" in x for x in p), "highest priority first, one per free slot"
        w("state/parked_review", "D-20261008-32")
        assert not any("parked to check" in x for x in plan(ops, ops, 11, 38, resets, now)), "same set: not again"
        os.remove(os.path.join(ops, "decisions", "20261008p.md")); os.remove(os.path.join(ops, "state", "parked_review"))
        w("state/lane-1-seen"); t = time.time() - 3600; os.utime(os.path.join(ops, "state", "lane-1-seen"), (t, t))
        assert needed(ops, 2) == (False, "no other lane is busy"), "an hourly lane 1 an hour ago is on time"
        t = time.time() - 7200; os.utime(os.path.join(ops, "state", "lane-1-seen"), (t, t))
        assert needed(ops, 2) == (True, "lane 1 has not looked for work in 90 minutes"), "lane 1 silent"
        assert needed(ops, 1, ["RUN  x"]) == (True, "work is free: RUN  x")
        assert needed(ops, 1) == (False, "nothing to do; refresh due") and needed(ops, 1) == (False, "nothing to do"), "idle: page refresh hourly"
        assert needed(ops, 2) == (False, "no other lane is busy"), "a gated lane 1 still counts as alive"
        os.makedirs(os.path.join(ops, *CLAIMS), exist_ok=True)
        w("state/claims/dev-D-1.json", json.dumps({"lane": 1, "at": "x", "line": "RUN dev"}))
        assert needed(ops, 2) == (True, "lane 1 is busy") and needed(ops, 3)[0]
        assert needed(ops, 1) == (True, "lane 1 holds a leftover claim"), "lane 1 must run to drop its leftover claim"
        w("state/office_off", "x\n")
        assert needed(ops, 3) == needed(ops, 1, ["RUN  x"]) == (False, "the office is off"), "off: every lane stops at the check"
        os.remove(os.path.join(ops, "state", "office_off"))
        w("state/claims/dev-D-2.json", json.dumps({"lane": 2, "at": "x", "line": "RUN dev"}))
        assert needed(ops, 2) == (True, "lane 2 holds a leftover claim"), "own leftover claim: lane must run to drop it"
        os.remove(os.path.join(ops, "state", "claims", "dev-D-2.json"))
        os.remove(os.path.join(ops, "state", "claims", "dev-D-1.json"))
        ago = lambda m: (now - datetime.timedelta(minutes=m)).isoformat()   # 2026-10-23: a Security run waited on a prompt for 2 hours
        w("state/claims/security.json", json.dumps({"lane": 1, "at": ago(118), "line": "RUN  groundedops-office-security  review D-1"}))
        w("state/claims/dev-D-3.json", json.dumps({"lane": 2, "at": ago(118), "line": "RUN  groundedops-office-dev  build D-3"}))
        assert limit("rnd-D-4") == 480 and limit("rnd") == 60, "an R&D lab trial is not desk research"
        assert (tell_stuck(ops, now)[0].startswith("STUCK lane 1 on security review D-1: stuck since") and len(tell_stuck(ops, now) + tell_stuck(ops, now)) == 0
                and set(claims(ops)) == {"security", "dev-D-3"}), "a claim past its limit is told once and never dropped; a long build is not stuck"
        release(ops, 1); release(ops, 2)
        w("state/every", "120"); w("state/lane-1-worked")
        got = take(ops, ops, 11, 38, resets, now, 1)
        assert got[-1] == "TOOK nothing" and any(x.startswith("SPACED lane 1") for x in got), got
        os.makedirs(os.path.join(ops, "state", "run_now"), exist_ok=True); w("state/run_now/customer")
        assert take(ops, ops, 11, 38, resets, now, 1)[-1].startswith("TOOK customer"), "a Run now press goes at once"
        release(ops, 1); w("state/every", "0"); os.remove(os.path.join(ops, "state", "lane-1-worked"))
        w("state/run_now/all")
        p = plan(ops, ops, 60, 38, resets, now)
        assert p[0].startswith("RUN  groundedops-office-customer") and not any("you asked for it now" in x for x in p), "Run all lifts the budget, adds no work"
        take(ops, ops, 60, 38, resets, now, 5); take(ops, ops, 60, 38, resets, now, 6); end_tick(ops, 6)
        assert "all" in run_now(ops), "another lane's tick end leaves it"
        take(ops, ops, 60, 38, resets, now, 5)
        assert "all" in run_now(ops), "lasts the whole tick, past its own next take"
        end_tick(ops, 5)
        assert "all" not in run_now(ops), "used up at the end of the tick that took it"
        release(ops, 6)
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
        w("decisions/20261008d.md", "## D-20261008-09 | Vague asks\n- From: customer\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n- Still broken: yes\n")
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
        w("decisions/20261008d.md", "## D-20261008-09 | Vague asks\n- From: customer\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n- Still broken: yes\n")
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
        w("decisions/20261008e.md", "## D-20261008-04 | Four\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n- Still broken: yes\n")
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
    ap.add_argument("--cron-set", action="store_true", help="record that --lane's scheduled task now has the CRON --release printed")
    ap.add_argument("--needed", action="store_true", help="NEEDED or NOT NEEDED: lanes 2 and 3 run only while another lane is busy")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if (a.take or a.release or a.cron_set or a.needed) and not a.lane:
        ap.error("--take, --release, --cron-set and --needed need --lane")
    if a.needed:
        free = ()
        now = datetime.datetime.now(datetime.timezone.utc)
        five, week, old = latest_usage(fallback=os.path.join(a.ops, "state", "usage.json"))
        if old <= 60:
            sweep_log(a.ops, a.repo, five, week, now)
        # lane 1 always; lanes 2 and 3 only in the cleaner's sweep, when free work, not a busy lane, wakes them
        if a.lane == 1 or (old <= 60 and sweep(a.ops, week, next_reset(now), now)[0]):
            five, week = (0, 0) if old > 60 else (five, week)   # stale figures count as room: the full tick refreshes them and checks the budget itself
            free = [line for line, _ in startable(a.ops, entries(a.ops, a.repo, five, week, next_reset(now), now, claims(a.ops)), a.lane)]
        ok, why = needed(a.ops, a.lane, free)
        # a lane past its department's limit is told here: every lane's every wake-up runs this check first
        return print("\n".join([("NEEDED " if ok else "NOT NEEDED ") + why] + tell_stuck(a.ops, datetime.datetime.now(datetime.timezone.utc))))
    if a.release:   # and the lane's schedule, when the CEO changed "Next run" on the page
        end_tick(a.ops, a.lane)
        want, path = lane_cron(a.lane, every(a.ops)), os.path.join(a.ops, "state", f"cron-{a.lane}")
        have = open(path, encoding="utf-8").read().strip() if os.path.exists(path) else ""
        return print(f"RELEASED lane {a.lane}" + (f"\nCRON {want}" if want != have else ""))
    if a.cron_set:   # the lane set its scheduled task to the CRON line --release printed
        return open(os.path.join(a.ops, "state", f"cron-{a.lane}"), "w", encoding="utf-8").write(lane_cron(a.lane, every(a.ops)))
    now = datetime.datetime.now(datetime.timezone.utc)
    if a.five is None or a.week is None:
        a.five, a.week, old = latest_usage(fallback=os.path.join(a.ops, "state", "usage.json"))
        u = os.path.join(a.ops, "state", "usage.json")
        no_r5 = (five_reset(a.ops) is None and 0 < (next_reset(now) - now).total_seconds() <= BATCH_FROM * 3600
                 and time.time() - (os.path.getmtime(u) if os.path.exists(u) else 0) > 15 * 60)   # Mo's batches need r5
        if old > 60 or no_r5:
            return print(f"STALE usage figures are {old:.0f} minutes old" + (" and the 5-hour reset time is unknown" if no_r5 else "") + "; start nothing"
                         ' (write {"u": {"fh": <5-hour %>, "sd": <weekly %>}, "r5": "<5-hour resetsAt>"} from get_usage to OPS/state/usage.json and run again)')
        if not a.resets:   # fresh figures, the real reset: the week's record (only in the reset's last 20 minutes)
            sweep_log(a.ops, a.repo, a.five, a.week, now)
    resets = datetime.datetime.fromisoformat(a.resets.replace("Z", "+00:00")) if a.resets else next_reset(now)
    print(f"USAGE 5-hour {a.five}%, weekly {a.week}%, weekly resets {resets:%a %d %b %H:%M} UTC;"
          " work mode {} ({})".format(*work_mode(a.ops, a.week, resets, now)))
    print("\n".join(take(a.ops, a.repo, a.five, a.week, resets, now, a.lane) if a.take else plan(a.ops, a.repo, a.five, a.week, resets, now)))


if __name__ == "__main__":
    main()
