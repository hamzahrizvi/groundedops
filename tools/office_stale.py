"""Clears office page status rows that a lab job outlived (dispatch.md step 4).

  python tools/office_stale.py [--ops C:/Users/hrizvi/groundedops-ops] <folder of workers/*.json>
  python tools/office_stale.py --selftest

A lane that stops waiting on the lab leaves Customer's and the test lab's rows "blocked", while the job it
started still finishes by itself (lane 3, 2026-11-04: runs/20261104d graded 85.0% at 14:31, rows said blocked
until fixed by hand). A row that is not idle and was last written before the newest lab job finished is stale:
for each, this writes <folder>/../stale/<doc>.json = {state: idle, task: "", last, screen, updated} and prints
"IDLE <doc> <that file>"; the lane updates the row from the file. NONE when nothing is stale.
Finished = runs/*/DONE or FAILED (Customer and the lab), inbox/dev/*.gate*/gate.json or FAILED (the lab).
"""
import argparse
import datetime
import glob
import json
import os
import sys

OPS = "C:/Users/hrizvi/groundedops-ops"


def _t(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


def _first(p):
    try:
        return (open(p, encoding="utf-8", errors="replace").readline().strip() or "no reason given")[:40]
    except OSError:
        return "no reason given"


def finished(ops):
    """[(finish time, plain line, also Customer's)] for every finished lab job."""
    out = []
    for run in glob.glob(os.path.join(ops, "runs", "*", "")):
        name = os.path.basename(os.path.dirname(run))
        if os.path.exists(run + "DONE"):
            try:
                pct = json.load(open(run + "cases_state.json", encoding="utf-8"))["summary"]["sys_normal"]["pct_of_max"]
                out.append((_t(run + "DONE"), f"Test run {name} finished: {pct:.1f}%", True))
            except (OSError, ValueError, KeyError, TypeError):
                out.append((_t(run + "DONE"), f"Test run {name} finished", True))
        elif os.path.exists(run + "FAILED"):
            out.append((_t(run + "FAILED"), f"Test run {name} stopped: {_first(run + 'FAILED')}", True))
    for g in glob.glob(os.path.join(ops, "inbox", "dev", "*.gate*", "")):
        i = os.path.basename(os.path.dirname(g)).split(".gate")[0]
        if os.path.exists(g + "gate.json"):
            try:
                v = json.load(open(g + "gate.json", encoding="utf-8")).get("verdict", "done")
            except (OSError, ValueError):
                v = "done"
            out.append((_t(g + "gate.json"), f"Check of fix {i} finished: {v}", False))
        elif os.path.exists(g + "FAILED"):
            out.append((_t(g + "FAILED"), f"Check of fix {i} stopped: {_first(g + 'FAILED')}", False))
    return out


def stale(folder, ops, now=None):
    jobs = finished(ops)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    lines = []
    for doc, mine in (("customer", lambda j: j[2]), ("lab", lambda j: True)):
        try:
            row = json.load(open(os.path.join(folder, doc + ".json"), encoding="utf-8"))
            updated = datetime.datetime.fromisoformat(row["updated"].replace("Z", "+00:00")).timestamp()
        except (OSError, ValueError, KeyError, AttributeError):
            continue   # no row, or no time on it: nothing to compare
        last = max(filter(mine, jobs), default=None)
        if row.get("state", "idle") == "idle" or not last or last[0] <= updated:
            continue
        at = datetime.datetime.fromtimestamp(last[0])   # local time, as the screen's other lines
        patch = {"state": "idle", "task": "", "last": last[1],
                 "screen": ((row.get("screen") or []) + [f"{at:%H:%M} {last[1]} (cleared by a lane)"])[-8:],
                 "updated": datetime.datetime.fromtimestamp(last[0], datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        out = os.path.join(os.path.dirname(os.path.abspath(folder)), "stale", doc + ".json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        json.dump(patch, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        lines.append(f"IDLE {doc} {out}")
    return lines or ["NONE"]


def selftest():
    import tempfile
    ops = tempfile.mkdtemp()
    w = lambda p, s="": (os.makedirs(os.path.dirname(os.path.join(ops, p)), exist_ok=True),
                          open(os.path.join(ops, p), "w", encoding="utf-8").write(s))
    pull = os.path.join(ops, "pull", "workers")
    row = lambda doc, state, at: w(f"pull/workers/{doc}.json", json.dumps({"state": state, "task": "waiting", "updated": at,
                                                                         "screen": [f"l{n}" for n in range(8)]}))
    row("customer", "blocked", "2020-01-01T00:00:00Z"); row("lab", "blocked", "2020-01-01T00:00:00Z")
    assert stale(pull, ops) == ["NONE"], "no finished job yet"
    w("runs/20261104d/cases_state.json", json.dumps({"summary": {"sys_normal": {"pct_of_max": 85.0}}})); w("runs/20261104d/DONE")
    got = stale(pull, ops)
    assert [l.split()[:2] for l in got] == [["IDLE", "customer"], ["IDLE", "lab"]], got
    p = json.load(open(got[0].split(" ", 2)[2], encoding="utf-8"))
    assert p["state"] == "idle" and p["last"] == "Test run 20261104d finished: 85.0%" and len(p["screen"]) == 8, p
    assert p["screen"][-1].endswith("85.0% (cleared by a lane)") and p["screen"][0] == "l1", p["screen"]
    row("customer", "blocked", "2099-01-01T00:00:00Z")   # written after the job finished: a newer job's row
    row("lab", "idle", "2020-01-01T00:00:00Z")
    assert stale(pull, ops) == ["NONE"], "fresh and idle rows are left alone"
    w("inbox/dev/D-1.gate/gate.json", json.dumps({"verdict": "PASS"})); os.utime(os.path.join(ops, "runs/20261104d/DONE"), (1, 1))
    row("lab", "working", "2020-01-01T00:00:00Z")
    got = stale(pull, ops)
    assert len(got) == 1 and got[0].startswith("IDLE lab"), got
    assert json.load(open(got[0].split(" ", 2)[2], encoding="utf-8"))["last"] == "Check of fix D-1 finished: PASS"
    w("runs/x/FAILED", "lab stopped at the cap\n"); row("customer", "blocked", "2020-01-01T00:00:00Z")
    got = stale(pull, ops)
    assert json.load(open(got[0].split(" ", 2)[2], encoding="utf-8"))["last"] == "Test run x stopped: lab stopped at the cap", got
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default=OPS)
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest or not a.folder:
        return selftest()
    print("\n".join(stale(a.folder, a.ops)))


if __name__ == "__main__":
    sys.exit(main())
