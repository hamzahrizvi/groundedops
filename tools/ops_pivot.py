"""Product status pivot: how the bot does by question category, type and product, run by run.

  python tools/ops_pivot.py [--ops C:/Users/hrizvi/groundedops-ops] > pivot.json
  python tools/ops_pivot.py --export-real <RUN> --src <REPO>/src [--days 7]
  python tools/ops_pivot.py --selftest

Build: reads every OPS/runs/<stamp>/ that has graded answers (cases.json with
the Customer's `category` per case, cases_state.json grades, and
real_categorised.json for the website questions), and writes per-run
aggregates the office page pivots. Export: writes RUN/real_questions.json, the
week's website-widget questions and how each ended, for Customer to categorise.
"""
import argparse
import datetime
import glob
import json
import os
import sys
from collections import Counter, defaultdict

DIMS = ("category", "type", "product")
KEEP_RUNS = 12       # ponytail: the page shows the last 12 runs; ops/runs keeps every run


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


def build(ops):
    runs = sorted(d for d in glob.glob(os.path.join(ops, "runs", "*"))
                  if os.path.exists(os.path.join(d, "cases_state.json")) and os.path.exists(os.path.join(d, "cases.json")))
    runs = [d for d in runs if run_facts(d)][-KEEP_RUNS:]
    names = [os.path.basename(d) for d in runs]
    overall, blind, real = {}, {dim: defaultdict(dict) for dim in DIMS}, {}
    latest = []
    for d, name in zip(runs, names):
        facts = run_facts(d)
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
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--export-real", metavar="RUN")
    ap.add_argument("--src")
    ap.add_argument("--days", type=int, default=7)
    o = ap.parse_args()
    if o.export_real:
        export_real(o.export_real, o.src, o.days)
    else:
        json.dump(build(o.ops), sys.stdout, indent=1, ensure_ascii=False)
