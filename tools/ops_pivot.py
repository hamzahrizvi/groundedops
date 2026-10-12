"""Product status pivot: how the bot does by question category, type and product, run by run.

  python tools/ops_pivot.py [--ops C:/Users/hrizvi/groundedops-ops] [--repo <REPO>] > pivot.json
  python tools/ops_pivot.py --export-real <RUN> --src <REPO>/src [--days 7]
  python tools/ops_pivot.py --prune [--ops <OPS>]
  python tools/ops_pivot.py --selftest

Build: reads every OPS/runs/<stamp>/ that has graded answers (cases.json with
the Customer's `category` per case, cases_state.json grades, and
real_categorised.json for the website questions), and writes per-run
aggregates the office page pivots, plus `trend`: the difficulty-adjusted score per
run with its noise band, the product fixes merged between runs (from REPO's git
merges and their gate.json) and a plain verdict on whether the product got
better. Export: writes RUN/real_questions.json, the
week's website-widget questions and how each ended, for Customer to categorise,
and RUN/widget_feedback.json (the week's widget votes, without session ids).
Prune: deletes widget_feedback.json, real_questions.json and real_categorised.json
from runs older than RETENTION_DAYS (90, from the env or REPO/src/.env), keeping their counts
in real_counts.json, and drops older votes by created_at from the newer runs' widget_feedback.json.
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
RIGHT = ("correct", "correct_refusal", "clarified")   # the page's Right tab; its Wrong tab is verdict "wrong"
REVIEW_RUNS = 8      # the page shows the last 8 runs' columns; each cell opens its questions
KEEP_RUNS = 12       # ponytail: the page shows the last 12 runs; ops/runs keeps every run
NOISE_PTS = 3.0      # the retrofit gate's noise rule: moves under 3 points are the bot's and the marker's own flicker
VISITOR_FILES = ("widget_feedback.json", "real_questions.json", "real_categorised.json")  # D-20261010-22: visitor text in a run
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


def _labels():
    """pipeline_trace.LABELS (src/ beside tools/): stdlib-only, so importing it costs nothing."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
        import pipeline_trace
        return pipeline_trace.LABELS
    except Exception:
        return {}


def trace(row, log, labels):
    """Where the bot's answer went, compact for the page: the full stage list when the run kept it
    (blind_eval.py from 2026-10-12), else how it ended from the run's app_logs.jsonl line."""
    p = (row or {}).get("pipeline")
    if p and p.get("stages"):
        ex = p.get("exit") or {}
        return {"exit": ex.get("label"), "because": (ex.get("note") or "")[:200], "ms": p.get("ms"),
                "stages": [[s.get("at_ms"), s.get("label"), (s.get("note") or "")[:160]] for s in p["stages"]]}
    if log:
        t = log.get("timing") or {}
        return {"exit": labels.get(log.get("outcome"), log.get("outcome")), "ms": round((t.get("total_time") or 0) * 1000),
                "via": log.get("verified_by"), "verifier": log.get("verifier"), "sources": log.get("sources") or [],
                "times": {k[:-5]: v for k, v in t.items() if k.endswith("_time") and k != "total_time" and v}}
    return None


def review(run_dir):
    """The run's questions with the bot's answer and the grader's reason, for the CEO's review pop-up."""
    st = _load(os.path.join(run_dir, "cases_state.json"))
    logs, labels = {}, _labels()
    lp = os.path.join(run_dir, "app_logs.jsonl")
    if os.path.exists(lp):   # the bot's own log of this run; last line per question wins (a turn1 case logs twice)
        for line in open(lp, encoding="utf-8"):
            try:
                e = json.loads(line)
                logs[e.get("query")] = e
            except ValueError:
                pass
    out = []
    for f in run_facts(run_dir):
        g = st.get("grades", {}).get(f["id"], {})
        bot = next((k for k, v in (g.get("labels") or {}).items() if v == "sys_normal"), None)
        why = str(g.get("why") or "")
        if bot:   # the grader compares two unnamed answers; name them
            other = "B" if bot == "A" else "A"   # whole words only: "1.50A" stays
            why = re.sub(rf"\b{other}\b", "the whole-manual AI", re.sub(rf"\b{bot}\b", "the bot", why))
        row = (st.get("sys_normal") or {}).get(f["id"], {})
        out.append(dict(f, answer=str(row.get("answer", ""))[:600], why=why[:400], trace=trace(row, logs.get(f["q"]), labels)))
    return out


def agg(facts):
    n = len(facts)
    base = [f["base"] for f in facts if f["base"] is not None]
    return {"n": n, "bot_pct": round(100 * sum(f["bot"] for f in facts) / (3 * n), 1),
            "base_pct": round(100 * sum(base) / (3 * len(base)), 1) if base else None,
            "right": sum(f.get("verdict") in RIGHT for f in facts), "wrong": sum(f.get("verdict") == "wrong" for f in facts),
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
                       "wrong": sum(f.get("verdict") == "wrong" for f in facts)})
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


def real_counts(run_dir):
    """Website questions per category and outcome: from real_categorised.json,
    or from real_counts.json once retention has deleted the text. None if neither."""
    rp = os.path.join(run_dir, "real_categorised.json")
    if os.path.exists(rp):
        by = defaultdict(Counter)
        for r in _load(rp):
            by[r.get("category") or "uncategorised"][r.get("outcome") or "other"] += 1
        return {k: dict(v) for k, v in by.items()}
    cp = os.path.join(run_dir, "real_counts.json")
    return _load(cp) if os.path.exists(cp) else None


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
        rc = real_counts(d)
        if rc is not None:
            real[name] = rc
        latest = facts
    cats = os.path.join(ops, "categories.json")
    return {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "runs": names, "categories": _load(cats) if os.path.exists(cats) else {},
            "overall": overall, "blind": {k: dict(v) for k, v in blind.items()}, "real": real,
            "trend": trend(by_run, ends, merged_fixes(repo, ops) if repo else []),
            "cases": {n: review(d) for d, n in list(zip(runs, names))[-REVIEW_RUNS:]},
            "latest": {"run": names[-1] if names else None,
                       "cases": [dict(f, q=f["q"][:160]) for f in latest]}}


def export_real(run, src, days):
    print(copy_feedback(src, run, days), "widget votes from the last", days, "days (session ids dropped)")
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


def _since(data, since):
    """The product's own rule (src/widget_api.py prune_before): keep votes whose
    created_at is at or after `since`; a vote without one goes."""
    key = since.astimezone(datetime.timezone.utc).isoformat()
    return {k: v for k, v in (data.items() if isinstance(data, dict) else [])
            if isinstance(v, dict) and (v.get("created_at") or "") >= key}


def retention_days(repo=None):
    """Same rule as the product (REPO/src/retention.py): RETENTION_DAYS, 0 = off, unset or bad = 90.
    Read like the product reads it: the environment first, else REPO/src/.env (first line wins)."""
    v = os.getenv("RETENTION_DAYS")
    p = os.path.join(repo, "src", ".env") if repo else ""
    if v is None and os.path.isfile(p):
        for line in open(p, encoding="utf-8-sig"):
            k, _, x = line.strip().partition("=")
            if k.strip() == "RETENTION_DAYS":
                v = x.strip().strip('"').strip("'")
                break
    try:
        return max(0, int(v if v is not None else 90))
    except ValueError:
        return 90


def prune_runs(ops, days, now=None):
    """D-20261010-22: delete visitor text (VISITOR_FILES) from OPS/runs folders older
    than `days` (by folder name YYYYMMDD[b..]), after saving the website-question counts
    to real_counts.json. D-20261010-29: in every other folder, votes older than `days`
    by their own created_at are removed from widget_feedback.json. Returns the deleted or trimmed paths."""
    if days <= 0:
        return []
    cutoff = (now or datetime.datetime.now()) - datetime.timedelta(days=days)
    gone = []
    for d in sorted(glob.glob(os.path.join(ops, "runs", "*"))):
        try:
            old = datetime.datetime.strptime(os.path.basename(d)[:8], "%Y%m%d") < cutoff
        except ValueError:
            old = False                                   # undated folder: no whole-file delete, rows still trimmed
        if not old:
            p = os.path.join(d, "widget_feedback.json")   # D-20261010-29: each vote by its own created_at
            if os.path.exists(p):
                data = _load(p)
                kept = _since(data, cutoff)
                if kept != data:
                    json.dump(kept, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
                    gone.append(p)
            continue
        counts = os.path.join(d, "real_counts.json")
        rc = real_counts(d)
        if rc is not None and not os.path.exists(counts):
            json.dump(rc, open(counts, "w", encoding="utf-8"), indent=1)
        for f in VISITOR_FILES:
            p = os.path.join(d, f)
            if os.path.exists(p):
                os.remove(p)
                gone.append(p)
    return gone


def copy_feedback(src, run, days):
    """Copy the widget votes of the last `days` days (by created_at) into RUN without
    session ids, so each vote is copied once. Returns how many."""
    p = os.path.join(src, "widget_feedback.json")
    if not os.path.exists(p):
        return 0
    data = _load(p)
    out = {k: {f: x for f, x in v.items() if f != "session_id"}
           for k, v in (data.items() if isinstance(data, dict) else []) if isinstance(v, dict)}
    out = _since(out, datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days))
    json.dump(out, open(os.path.join(run, "widget_feedback.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return len(out)


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
    # D-20261010-22: retention on a throwaway OPS
    ops2 = tempfile.mkdtemp()
    one = {"cases": [{"id": "C-0", "q": "q0", "product": "nv200s", "type": "lookup", "category": "simple"}]}
    for name in ("20260601", "20261001", "notadate"):
        d = os.path.join(ops2, "runs", name)
        os.makedirs(d)
        if name != "notadate":                                     # an undated folder is no graded run (build would choke on its name)
            json.dump(one, open(os.path.join(d, "cases.json"), "w"))
            json.dump({"grades": {"C-0": {"sys_normal": {"score": 3, "verdict": "correct"}}}}, open(os.path.join(d, "cases_state.json"), "w"))
        json.dump({"old": {"question": "q", "vote": "down", "created_at": "2026-07-04T00:00:00+00:00"}, "new": {"question": "q", "vote": "up", "created_at": "2026-10-01T00:00:00+00:00"}}, open(os.path.join(d, "widget_feedback.json"), "w"))
        json.dump([{"q": "jam", "outcome": "answered", "when": "x"}], open(os.path.join(d, "real_questions.json"), "w"))
        json.dump([{"q": "jam", "category": "technical", "outcome": "answered"}], open(os.path.join(d, "real_categorised.json"), "w"))
    before = build(ops2)["real"]
    now = datetime.datetime(2026, 10, 12)
    assert prune_runs(ops2, 0, now) == []                          # RETENTION_DAYS=0: off
    gone = prune_runs(ops2, 90, now)
    old = os.path.join(ops2, "runs", "20260601")
    fb = lambda name: os.path.join(ops2, "runs", name, "widget_feedback.json")
    assert sorted(gone) == sorted([os.path.join(old, f) for f in VISITOR_FILES] + [fb("20261001"), fb("notadate")]), gone
    for name in ("20261001", "notadate"):                       # D-20261010-29: the 100-day-old vote goes, the fresh one stays
        assert list(_load(fb(name))) == ["new"], name
    for name in ("20261001", "notadate"):                         # recent, and undatable, keep everything
        assert all(os.path.exists(os.path.join(ops2, "runs", name, f)) for f in VISITOR_FILES), name
    assert _load(os.path.join(old, "real_counts.json")) == {"technical": {"answered": 1}}
    assert build(ops2)["real"] == before, (build(ops2)["real"], before)   # the pivot keeps the old run's counts
    assert prune_runs(ops2, 90, now) == []                         # second run: nothing left to delete
    src = tempfile.mkdtemp()
    ago = lambda n: (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=n)).isoformat()
    json.dump({"a": {"question": "q", "vote": "down", "session_id": "s", "created_at": ago(100)},
               "b": {"question": "q", "vote": "down", "session_id": "s", "created_at": ago(1)}},
              open(os.path.join(src, "widget_feedback.json"), "w"))
    run = tempfile.mkdtemp()
    assert copy_feedback(src, run, 7) == 1
    assert list(_load(os.path.join(run, "widget_feedback.json"))) == ["b"]
    assert "session_id" not in _load(os.path.join(run, "widget_feedback.json"))["b"]
    assert copy_feedback(run + "/missing", run, 7) == 0
    saved = os.environ.pop("RETENTION_DAYS", None)
    try:
        repo = tempfile.mkdtemp(); os.makedirs(os.path.join(repo, "src"))
        assert retention_days(repo) == 90                       # no .env
        open(os.path.join(repo, "src", ".env"), "w").write("# RETENTION_DAYS=5\nRETENTION_DAYS=30\nRETENTION_DAYS=40\n")
        assert retention_days(repo) == 30                       # the product's .env, first line wins
        os.environ["RETENTION_DAYS"] = "7"
        assert retention_days(repo) == 7                        # the environment wins, as in the product
    finally:
        os.environ.pop("RETENTION_DAYS", None)
        if saved is not None:
            os.environ["RETENTION_DAYS"] = saved
    # trace: the kept stage list wins; else the app log's ending; else nothing
    full = {"pipeline": {"ms": 9, "exit": {"label": "Stopped", "note": "score 0.2"},
                         "stages": [{"at_ms": 1, "label": "Took the question", "note": None}]}}
    t = trace(full, {"outcome": "respond"}, {})
    assert t["exit"] == "Stopped" and t["stages"] == [[1, "Took the question", ""]], t
    t = trace({}, {"outcome": "respond", "timing": {"total_time": 1.5, "llm_time": 0.5, "rerank_time": 0}}, {"respond": "Answered"})
    assert t["exit"] == "Answered" and t["ms"] == 1500 and t["times"] == {"llm": 0.5}, t
    assert trace({}, None, {}) is None
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
    ap.add_argument("--prune", action="store_true", help="delete visitor text from runs older than RETENTION_DAYS")
    o = ap.parse_args()
    if o.prune:
        days = retention_days(o.repo)
        gone = prune_runs(o.ops, days)
        print(len(gone), "visitor-text files deleted or trimmed (older than", days, "days)")
        sys.exit(0)
    if o.export_real:
        export_real(o.export_real, o.src, o.days)
    else:
        json.dump(build(o.ops, o.repo), sys.stdout, indent=1)   # ASCII escapes: the dispatcher redirects stdout on a cp1252 console
