"""The product owner's diary: what the office did each day, the problems and how they were sorted.

  python tools/office_diary.py --facts 2026-10-13 [--ops ...] [--repo ...]   the day's facts, one per line
  python tools/office_diary.py --due                                          days that need an entry
  python tools/office_diary.py --selftest

The product owner's DIARY job (.claude/agents/product-owner.md) turns the facts into a few
plain lines in OPS/diary/<YYYY-MM-DD>.md. A day is due once it is over (or after DAY_ENDS
that evening), has facts, and has no entry yet; only the last LOOKBACK days are written.
office_brief.py puts the latest entries on the office page.
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

DAY_ENDS = 22     # ponytail: tonight's entry is written after 22:00; later work that night is not in it
LOOKBACK = 7
OFFICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MERGE = re.compile(r"Merge branch 'agents/dev-(D-\d{8}-\d+)' \((?:D-\d{8}-\d+: )?(.*)\)$")


def _on(path, day):
    try:
        return datetime.date.fromtimestamp(os.path.getmtime(path)) == day
    except OSError:
        return False


def _log(repo, day, *flags):
    try:
        return subprocess.run(["git", "-C", repo, "log", *flags, "--format=%s", f"--since={day} 00:00",
                               f"--until={day} 23:59:59", "HEAD"], capture_output=True, text=True,
                              encoding="utf-8", timeout=30).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []


def facts(ops, repo, day, office=None):
    """Plain fact lines for one day, from the files the office already keeps."""
    office = office or OFFICE
    d, stamp = day, day.strftime("%Y%m%d")
    out = []
    for where, path in (("product", repo), ("office", office)):
        for m in filter(None, map(MERGE.match, _log(path, d, "--merges"))):
            out.append(f"Merged into the {where}: {m[2]} ({m[1]})")
        for subject in _log(path, d, "--first-parent", "--no-merges"):   # made straight on the branch, not by Dev
            out.append(f"Changed in the {where} by hand: {subject}")
    for run in sorted(glob.glob(os.path.join(ops, "runs", stamp + "*"))):
        name = os.path.basename(run)
        if os.path.exists(os.path.join(run, "FAILED")):
            out.append(f"Test run {name} failed at: " + open(os.path.join(run, "FAILED"), encoding="utf-8").read().strip())
        if os.path.exists(os.path.join(run, "DONE")):
            try:
                s = json.load(open(os.path.join(run, "cases_state.json"), encoding="utf-8"))["summary"]
                out.append(f"Test run {name} finished: bot {s['sys_normal']['pct_of_max']}%, "
                           f"whole-manual AI {s['baseline']['pct_of_max']}%, {s['sys_normal']['verdicts'].get('wrong', 0)} wrong")
            except (OSError, ValueError, KeyError):
                out.append(f"Test run {name} finished")
    for g in sorted(glob.glob(os.path.join(ops, "inbox", "dev", "*.gate", "gate.json"))):
        if _on(g, d):
            try:
                j = json.load(open(g, encoding="utf-8"))
            except ValueError:
                continue
            out.append(f"Gate check {os.path.basename(os.path.dirname(g))[:-5]}: {j.get('verdict')}"
                       + (f" ({j.get('before_pct')}% to {j.get('after_pct')}%)" if j.get("before_pct") is not None else ""))
    for h in sorted(glob.glob(os.path.join(ops, "inbox", "dev", "D-*.md"))):
        if _on(h, d) and "." not in os.path.basename(h)[:-3]:
            st = re.search(r"(?mi)^\W*Status:\s*(.+)$", open(h, encoding="utf-8").read())
            out.append(f"Dev handed back {os.path.basename(h)[:-3]}: {st[1].strip() if st else 'no status line'}")
    for dept in sorted(os.listdir(os.path.join(ops, "inbox")) if os.path.isdir(os.path.join(ops, "inbox")) else []):
        if dept == "dev":
            continue
        n = [f for f in glob.glob(os.path.join(ops, "inbox", dept, "*.md")) if _on(f, d)]
        if n:
            out.append(f"{dept.capitalize() if dept != 'ceo' else 'The CEO'} filed {len(n)} note{'s' if len(n) > 1 else ''}: "
                       + ", ".join(os.path.basename(f)[:-3] for f in n[:4]))
    for p in sorted(glob.glob(os.path.join(ops, "decisions", stamp + "*.md"))):
        text = open(p, encoding="utf-8").read()
        for verdict in ("APPROVE", "AUTO-APPROVED", "NEEDS CEO", "DEFER", "REJECT", "READY TO MERGE", "SEND BACK", "DONE"):
            n = len(re.findall(rf"(?m)^- Verdict: {verdict}\b", text))
            if n:
                out.append(f"Product owner decisions: {n} {verdict.lower()}")
    if glob.glob(os.path.join(ops, "reports", stamp + "*.md")):
        out.append("Product owner wrote the CEO's report")
    picks = os.path.join(ops, "state", "model_picks.log")
    if os.path.exists(picks):
        day_picks = [line.split("\t") for line in open(picks, encoding="utf-8") if line.startswith(str(d))]
        if day_picks:
            by = {}
            for p in day_picks:
                by[p[3]] = by.get(p[3], 0) + 1
            out.append("Models picked: " + ", ".join(f"{m} {n}" for m, n in sorted(by.items())))
    return out


def due(ops, repo, now=None, office=None):
    now = now or datetime.datetime.now()
    days = [now.date() - datetime.timedelta(days=k) for k in range(LOOKBACK, -1, -1)]
    return [d for d in days if (d < now.date() or now.hour >= DAY_ENDS)
            and not os.path.exists(os.path.join(ops, "diary", f"{d}.md")) and facts(ops, repo, d, office)]


def latest(ops, n=14):
    """The newest n entries, newest first, for the office page."""
    return [{"date": os.path.basename(p)[:-3], "text": open(p, encoding="utf-8").read()[:2000]}
            for p in sorted(glob.glob(os.path.join(ops, "diary", "*.md")), reverse=True)[:n]]


def selftest():
    with tempfile.TemporaryDirectory() as ops:
        day = datetime.date(2026, 10, 13)
        w = lambda p, s="": (os.makedirs(os.path.dirname(os.path.join(ops, p)), exist_ok=True),
                             open(os.path.join(ops, p), "w", encoding="utf-8").write(s))
        w("runs/20261013/DONE")
        w("runs/20261013/cases_state.json", json.dumps({"summary": {"sys_normal": {"pct_of_max": 70.8, "verdicts": {"wrong": 5}},
                                                                     "baseline": {"pct_of_max": 95.0}}}))
        w("runs/20261013b/FAILED", "grade\n")
        w("decisions/20261013.md", "## D-1 | x\n- Verdict: DEFER\n\n## D-2 | y\n- Verdict: DEFER\n")
        stamp = datetime.datetime(2026, 10, 13, 12).timestamp()
        for p in glob.glob(os.path.join(ops, "**", "*"), recursive=True):
            os.utime(p, (stamp, stamp))
        f = facts(ops, ops, day, office=ops)
        assert "Test run 20261013 finished: bot 70.8%, whole-manual AI 95.0%, 5 wrong" in f, f
        assert "Test run 20261013b failed at: grade" in f and "Product owner decisions: 2 defer" in f, f
        assert due(ops, ops, datetime.datetime(2026, 10, 13, 15), office=ops) == [], "not over yet"
        assert due(ops, ops, datetime.datetime(2026, 10, 13, 22, 5), office=ops) == [day], "due tonight"
        w("diary/2026-10-13.md", "Done: a test run.\n")
        assert due(ops, ops, datetime.datetime(2026, 10, 14, 9), office=ops) == [], "written once"
        assert latest(ops)[0]["date"] == "2026-10-13"
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--repo", default=os.path.normpath(os.path.join(OFFICE, "..", "..", "..")))
    ap.add_argument("--facts")
    ap.add_argument("--due", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.facts:
        sys.stdout.reconfigure(encoding="utf-8")
        print("\n".join(facts(a.ops, a.repo, datetime.date.fromisoformat(a.facts))) or "Nothing recorded.")
    elif a.due:
        print("\n".join(str(d) for d in due(a.ops, a.repo)) or "NONE")
    else:
        selftest()


if __name__ == "__main__":
    main()
