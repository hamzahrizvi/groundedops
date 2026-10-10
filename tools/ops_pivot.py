"""Product status pivot: how the bot does by question category, type and product, run by run.

  python tools/ops_pivot.py [--ops C:/Users/hrizvi/groundedops-ops] [--repo <REPO>] > pivot.json
  python tools/ops_pivot.py --export-real <RUN> --src <REPO>/src [--days 7]
  python tools/ops_pivot.py --selftest

Build: reads every OPS/runs/<stamp>/ that has graded answers (cases.json with
the Customer's `category` per case, cases_state.json grades, and
real_categorised.json for the website questions), and writes per-run
aggregates the office page pivots, plus `trend`: the difficulty-adjusted score per
run with its noise band, the product fixes merged between runs (from REPO's git
merges and their gate.json) and a plain verdict on whether the product got
better. Export: writes RUN/real_questions.json, the
week's website-widget questions and how each ended, for Customer to categorise.
"""
import argparse
import datetime
import glob
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

DIMS = ("category", "type", "product")
KEEP_RUNS = 12       # ponytail: the page shows the last 12 runs; ops/runs keeps every run
NOISE_PTS = 3.0      # the retrofit gate's noise rule: moves under 3 points are the bot's and the marker's own flicker
MERGE = re.compile(r"Merge branch 'agents/dev-(D-\d{8}-\d+)' \(D-\d{8}-\d+: (.*)\)$")


def _load(p):
    return json.load(open(p, encoding="utf-8"))


def run_facts(run_dir):
    grades = _load(os.path.join(run_dir, "cases_state.json")).get("grades", {})
    out = []
    for c in _load(os.path.join(run_dir, "cases.json"))["cases"]:
        g = grades.get(c["id"], {})
        if "sys_normal" not in g:
            continue
        out.append({"id": c["id"], "q": c["q"], "product": c.get("product") or "none", "type": c["type"],
                    "category": c.get("category") or "uncategorised", "bot": g["sys_normal"]["score"],
                    "base": (g.get("baseline") or {}).get("score"), "verdict": g["sys_normal"].get("verdict", "")})
    return out


def agg(facts):
    n = len(facts)
    base = [f["base"] for f in facts if f["base"] is not None]
    return {"n": n, "bot_pct": round(100 * sum(f["bot"] for f in facts) / (3 * n), 1),
            "base_pct": round(100 * sum(base) / (3 * len(base)), 1) if base else None,
            "right": sum(f["bot"] == 3 for f in facts), "wrong": sum(f["bot"] == 0 for f in facts),
            "declined": sum(f["verdict"] == "safe_decline" for f in facts)}


def adjusted(by_run):
    """Difficulty-adjusted score per run, 0-100, with its standard error.

    Difficulty = question type: it explains about half the spread in the bot's marks, while the
    whole-manual AI's mark on the same question explains 2% (measured on the 2026-10-08..10 runs), so
    the gap to that AI is not used here. Each type weighs its share across all runs in every run, so a set
    heavy on hard types does not read as a worse bot. The error comes from how many questions of each type
    were asked, using each type's spread pooled over all runs (4 questions per type per run is too few alone).
    """
    cells = defaultdict(list)
    for r, facts in by_run.items():
        for f in facts:
            cells[(r, f["type"])].append(100 * f["bot"] / 3)
    share = Counter(t for (_, t), ys in cells.items() for _ in ys)
    ss, dof = Counter(), Counter()
    for (_, t), ys in cells.items():
        m = sum(ys) / len(ys)
        ss[t] += sum((y - m) ** 2 for y in ys)
        dof[t] += len(ys) - 1
    every = [y for ys in cells.values() for y in ys]
    spread = sum((y - sum(every) / len(every)) ** 2 for y in every) / max(1, len(every) - 1)
    var = {t: ss[t] / dof[t] if dof[t] else spread for t in share}
    out = {}
    for r in by_run:
        # ponytail: a type missing from a run is dropped and the rest reweighted; Customer writes all ten every run
        types = [t for t in share if (r, t) in cells]
        w = sum(share[t] for t in types)
        out[r] = {"adj": sum(share[t] / w * sum(cells[(r, t)]) / len(cells[(r, t)]) for t in types),
                  "se": math.sqrt(sum((share[t] / w) ** 2 * var[t] / len(cells[(r, t)]) for t in types))}
    return out


def change(a, b):
    """b - a in points, the noise it must beat (90% from the questions asked, never under the gate's 3), and the call."""
    d, noise = b["adj"] - a["adj"], max(NOISE_PTS, 1.645 * math.hypot(a["se"], b["se"]))
    return round(d, 1), round(noise, 1), "better" if d > noise else "worse" if d < -noise else "same"


def merged_fixes(repo, ops):
    """Dev fixes merged into REPO's checked-out branch, oldest first, with their gate result when kept."""
    try:
        log = subprocess.run(["git", "-C", repo, "log", "--merges", "--format=%ct %s", "HEAD"],
                             capture_output=True, text=True, encoding="utf-8", check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    fixes = []
    for line in log.splitlines():
        when, _, subject = line.partition(" ")
        m = MERGE.match(subject)
        if not m:
            continue
        fix = {"id": m[1], "title": m[2], "when": int(when)}
        gp = os.path.join(ops, "inbox", "dev", m[1] + ".gate", "gate.json")
        if os.path.exists(gp):
            g = _load(gp)
            fix["gate"] = {"verdict": g.get("verdict"), "before": g.get("before_pct"), "after": g.get("after_pct")}
        fixes.append(fix)
    return sorted(fixes, key=lambda f: f["when"])


def _day(stamp):
    return f"{int(stamp[6:8])} {datetime.date(2000, int(stamp[4:6]), 1):%b}"


def trend(by_run, ends, fixes):
    """Adjusted score per run, the fixes merged between runs and what each step did, and the overall verdict."""
    names = list(by_run)
    adj = adjusted(by_run)
    points = []
    for r in names:
        facts, a = by_run[r], adj[r]
        points.append({"run": r, "when": ends[r], "n": len(facts), "adj": round(a["adj"], 1), "se": round(a["se"], 2),
                       "lo": round(max(0, a["adj"] - 1.645 * a["se"]), 1), "hi": round(min(100, a["adj"] + 1.645 * a["se"]), 1),
                       "raw": round(100 * sum(f["bot"] for f in facts) / (3 * len(facts)), 1),
                       "wrong": sum(f["bot"] == 0 for f in facts)})
    steps = []
    for a, b in zip(names, names[1:]):
        # ponytail: a run is placed at its DONE time; a merge landing mid-run counts for the next run
        between = [f for f in fixes if ends[a] < f["when"] <= ends[b]]
        if between:
            d, noise, call = change(adj[a], adj[b])
            steps.append({"from": a, "to": b, "fixes": between, "delta": d, "noise": noise, "call": call})
    waiting = [f for f in fixes if names and f["when"] > ends[names[-1]]]
    if len(names) < 2:
        verdict = "Not enough test runs yet: the trend needs two."
    else:
        d, noise, call = change(adj[names[0]], adj[names[-1]])
        span = f"from {_day(names[0])} to {_day(names[-1])}"
        verdict = {"better": f"Better: the adjusted score rose {d} points {span}, more than the {noise}-point noise.",
                   "worse": f"Worse: the adjusted score fell {-d} points {span}, more than the {noise}-point noise.",
                   "same": f"No change we can tell yet: the adjusted score moved {d:+} points {span}, "
                           f"inside the {noise}-point noise."}[call]
        n_fix = sum(len(s["fixes"]) for s in steps)
        verdict += f" {n_fix} fix{'es' if n_fix != 1 else ''} merged in that time."
    return {"points": points, "steps": steps, "waiting": waiting, "verdict": verdict, "noise_floor": NOISE_PTS}


def build(ops, repo=None):
    runs = sorted(d for d in glob.glob(os.path.join(ops, "runs", "*"))
                  if os.path.exists(os.path.join(d, "cases_state.json")) and os.path.exists(os.path.join(d, "cases.json")))
    runs = [d for d in runs if run_facts(d)][-KEEP_RUNS:]
    names = [os.path.basename(d) for d in runs]
    overall, blind, real = {}, {dim: defaultdict(dict) for dim in DIMS}, {}
    latest, by_run, ends = [], {}, {}
    for d, name in zip(runs, names):
        facts = run_facts(d)
        by_run[name] = facts
        done = os.path.join(d, "DONE")
        ends[name] = int(os.path.getmtime(done if os.path.exists(done) else os.path.join(d, "cases_state.json")))
        overall[name] = agg(facts)
        for dim in DIMS:
            groups = defaultdict(list)
            for f in facts:
                groups[f[dim]].append(f)
            for g, fs in groups.items():
                blind[dim][g][name] = agg(fs)
        rp = os.path.join(d, "real_categorised.json")
        if os.path.exists(rp):
            by = defaultdict(Counter)
            for r in _load(rp):
                by[r.get("category") or "uncategorised"][r.get("outcome") or "other"] += 1
            real[name] = {k: dict(v) for k, v in by.items()}
        latest = facts
    cats = os.path.join(ops, "categories.json")
    return {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "runs": names, "categories": _load(cats) if os.path.exists(cats) else {},
            "overall": overall, "blind": {k: dict(v) for k, v in blind.items()}, "real": real,
            "trend": trend(by_run, ends, merged_fixes(repo, ops) if repo else []),
            "latest": {"run": names[-1] if names else None,
                       "cases": [dict(f, q=f["q"][:160]) for f in latest]}}


def export_real(run, src, days):
    sys.path.insert(0, src)
    import log_report as L
    since = datetime.datetime.now() - datetime.timedelta(days=days)
    out = []
    for r in L.load_rows(os.path.abspath(src)):
        ts = L._parse_ts(r.get("timestamp"))
        if r.get("origin") != "widget" or not ts or ts.replace(tzinfo=None) < since:
            continue
        kind = L.turn_kind(r)
        if kind != "other":
            out.append({"q": (r.get("query") or "")[:300], "outcome": kind, "when": r.get("timestamp")})
    json.dump(out, open(os.path.join(run, "real_questions.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(len(out), "website questions")


def _selftest():
    import tempfile
    ops = tempfile.mkdtemp()
    for name, scores in (("20261005", [3, 0, 1]), ("20261012", [3, 3, 2])):
        d = os.path.join(ops, "runs", name)
        os.makedirs(d)
        cases = [{"id": f"C-{i}", "q": f"q{i}", "product": p, "type": t, "category": c} for i, (p, t, c) in
                 enumerate([("nv200s", "lookup", "simple"), ("nv200s", "comparison", "technical"), (None, "vague", "conversational")])]
        verdict = {3: "correct", 2: "partial", 1: "safe_decline", 0: "wrong"}
        grades = {f"C-{i}": {"sys_normal": {"score": s, "verdict": verdict[s]}, "baseline": {"score": 3}} for i, s in enumerate(scores)}
        json.dump({"cases": cases}, open(os.path.join(d, "cases.json"), "w"))
        json.dump({"grades": grades}, open(os.path.join(d, "cases_state.json"), "w"))
    json.dump([{"q": "price?", "category": "commercial", "type": "lookup", "outcome": "refused"},
               {"q": "jam", "category": "technical", "type": "procedure_or_troubleshoot", "outcome": "answered"}],
              open(os.path.join(ops, "runs", "20261012", "real_categorised.json"), "w"))
    r = build(ops)
    assert r["runs"] == ["20261005", "20261012"], r["runs"]
    assert r["overall"]["20261005"] == {"n": 3, "bot_pct": 44.4, "base_pct": 100.0, "right": 1, "wrong": 1, "declined": 1}, r["overall"]
    assert r["blind"]["category"]["technical"]["20261005"]["wrong"] == 1
    assert r["blind"]["category"]["technical"]["20261012"]["bot_pct"] == 100.0
    assert r["blind"]["product"]["none"]["20261012"]["bot_pct"] == 66.7
    assert r["real"]["20261012"] == {"commercial": {"refused": 1}, "technical": {"answered": 1}}
    assert r["latest"]["run"] == "20261012" and len(r["latest"]["cases"]) == 3
    t = r["trend"]
    assert [p["raw"] for p in t["points"]] == [p["adj"] for p in t["points"]] == [44.4, 88.9], t   # same mix: nothing to adjust
    assert t["steps"] == [] and t["verdict"].startswith("No change we can tell yet"), t   # 3 questions: +44 is noise
    # a harder mix must not read as a worse bot: run B asks the hard type three times, the easy one once
    easy, hard = {"type": "lookup", "bot": 3}, {"type": "comparison", "bot": 0}
    a = adjusted({"A": [easy, easy, hard, hard], "B": [easy, hard, hard, hard]})
    assert round(a["A"]["adj"], 1) == round(a["B"]["adj"], 1) == 37.5, a   # raw: 50 against 25
    assert change({"adj": 70, "se": 0}, {"adj": 72.9, "se": 0}) == (2.9, 3.0, "same")
    assert change({"adj": 70, "se": 4}, {"adj": 80, "se": 4})[2] == "better"
    assert change({"adj": 70, "se": 6}, {"adj": 80, "se": 6})[2] == "same"     # 10 points inside a 14-point band
    fix = {"id": "D-20261006-01", "title": "x", "when": 2}
    t = trend({"20261005": [easy, hard], "20261012": [easy, easy]}, {"20261005": 1, "20261012": 3}, [fix, dict(fix, when=4)])
    assert len(t["steps"]) == 1 and t["steps"][0]["fixes"] == [fix] and len(t["waiting"]) == 1, t
    assert MERGE.match("Merge branch 'agents/dev-D-20261008-16' (D-20261008-16: Adding manuals needs (no) sign-in)")[2]         == "Adding manuals needs (no) sign-in"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--repo", default="C:/Users/hrizvi/Downloads/Git/groundedops", help="product checkout, for merged fixes")
    ap.add_argument("--export-real", metavar="RUN")
    ap.add_argument("--src")
    ap.add_argument("--days", type=int, default=7)
    o = ap.parse_args()
    if o.export_real:
        export_real(o.export_real, o.src, o.days)
    else:
        json.dump(build(o.ops, o.repo), sys.stdout, indent=1, ensure_ascii=False)
