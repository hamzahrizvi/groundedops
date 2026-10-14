"""What the office spends, per model and per department, by day, week and month.

  python tools/ops_costs.py [--ops C:/Users/hrizvi/groundedops-ops] [--claude ~/.claude/projects] > costs.json
  python tools/ops_costs.py --cap [--ops ...]    the lab cap as JSON; exit 3 when the lab is stopped at it
  python tools/ops_costs.py --selftest

API: the test lab's provider calls (tools/blind_eval.py): each run's cases_state.json and each
gate's (after/, before/, gate_rerun.py's re-asks, the hidden siblings), per answer and per grade.
"lab" lists them per run and gate, with the CEO's cap (OPS/lab_cap.json, cap_status) and what it
has used. The live product does not log tokens, so its traffic is not here; the provider console has it.
Claude: every assistant message in the groundedops session transcripts, tokens x list price. The
office runs on a Claude plan, so this is what the same work would cost on the API, not a bill.
Each subagent's department comes from its .meta.json (agentType); the rest is "lanes and sessions".
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
import tempfile
from collections import defaultdict

# $ per 1M tokens: input, output, cache read; a cache write is billed at 1.25x input (5-minute cache).
# From the claude-api skill's model table, 2026-10-12. ponytail: Haiku's cache read assumed at 10% of input
CLAUDE_PRICE = {"claude-fable-5-1": (10.0, 50.0, 0.25), "claude-opus-5-5": (4.0, 20.0, 0.20),
                "claude-sonnet-5-5": (2.0, 10.0, 0.20), "claude-haiku-5-5": (0.10, 0.50, 0.01)}
DAYS, WEEKS, MONTHS = 14, 8, 6


def claude_usd(model, u):
    p = CLAUDE_PRICE.get(model)
    if not p:
        return 0.0
    i, o, r = p
    return (u.get("input_tokens", 0) * i + u.get("output_tokens", 0) * o + u.get("cache_read_input_tokens", 0) * r
            + u.get("cache_creation_input_tokens", 0) * i * 1.25) / 1e6


def claude_rows(root):
    """[(date, model, department, usd)], one per assistant message (a message streamed over several
    lines is counted once)."""
    out, seen = [], set()
    for p in glob.glob(os.path.join(root, "*groundedops*", "**", "*.jsonl"), recursive=True):
        meta = p[:-6] + ".meta.json"
        try:
            dept = json.load(open(meta, encoding="utf-8")).get("agentType") if os.path.exists(meta) else None
        except ValueError:
            dept = None
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                m = j.get("message") or {}
                if m.get("role") != "assistant" or not m.get("usage") or m.get("id") in seen:
                    continue
                seen.add(m.get("id"))
                usd = claude_usd(m.get("model"), m["usage"])
                if usd:
                    out.append((j.get("timestamp", "")[:10], m["model"], dept or "lanes and sessions", usd))
    return out


GATE_ID = re.compile(r"D-\d{8}-\d+")
NOT_MODEL = {None, "none", "structured", "faq-curated", "stub"}   # answers the bot gave without a model call
# Where blind_eval.py leaves its answers: (kind, glob under OPS). A gate's after/ and before/ and
# gate_rerun.py's copies carry the run's baseline answers along, and siblings ask no baseline, so a
# baseline is counted under runs/ only. sys_before is the run's own answers copied in, never counted.
LAB_FILES = (("run", ("runs", "*", "cases_state.json")),
            ("run", ("heldout", "market", "*", "cases_state.json")),
             ("gate", ("inbox", "dev", "*.gate*", "after", "cases_state.json")),
             ("gate", ("inbox", "dev", "*.gate*", "before", "cases_state.json")),
             ("gate", ("inbox", "dev", "*.gate*", "rerun", "*", "cases_state.run*.json")),
             ("gate", ("heldout", "gates", "*", "sib_*", "cases_state.json")))


def lab_rows(ops):
    """[(date, model, what, usd, name, kind)] for every paid test-lab call: what = bot, baseline or
    grader; kind = run or gate; name = the run folder, or the gate's work-order id (never a hidden
    folder name). A row is dated by its own "at" (blind_eval.py, 2026-10-12), else by the runs/<YYYYMMDD>
    folder, else by the file's time: a gate re-marks its run and re-saves that file, which would move
    the whole run's spend to the day of the gate."""
    agg = defaultdict(float)
    for kind, parts in LAB_FILES:
        for p in glob.glob(os.path.join(ops, *parts)):
            try:
                s = json.load(open(p, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rel = os.path.relpath(p, ops).replace(os.sep, "/")
            if kind == "run":
                name = rel.split("/")[-2]
                m = re.match(r"(\d{4})(\d{2})(\d{2})", name)
                day = f"{m[1]}-{m[2]}-{m[3]}" if m else None
            else:
                m = GATE_ID.search(rel)
                name, day = (m.group(0) if m else "gate"), None
            day = day or datetime.date.fromtimestamp(os.path.getmtime(p)).isoformat()
            grades = s.get("grades") or {}
            file_model = ((s.get("summary") or {}).get("grader") or {}).get("model") or next(
                (g["usage"]["model"] for g in grades.values() if isinstance(g, dict) and (g.get("usage") or {}).get("model")),
                "lab model (not logged)")
            systems = [k for k in s if k.startswith("sys_") and k != "sys_before"] + (["baseline"] if kind == "run" else [])
            for system in systems:
                what = "baseline" if system == "baseline" else "bot"
                for r in (s.get(system) or {}).values():
                    if isinstance(r, dict) and r.get("usd"):
                        model = r.get("model") if r.get("model") not in NOT_MODEL else file_model
                        agg[(r.get("at") or day, model, what, name, kind)] += r["usd"]
            logged = [g["usage"] for g in grades.values() if isinstance(g, dict) and (g.get("usage") or {}).get("usd")]
            for u in logged:
                agg[(u.get("at") or day, u.get("model") or file_model, "grader", name, kind)] += u["usd"]
            g = (s.get("summary") or {}).get("grader") or {}
            if not logged and g.get("usd"):   # grades from before 2026-10-10 logged only the run's total
                agg[(day, file_model, "grader", name, kind)] += g["usd"]
    return [(d, m, w, usd, n, k) for (d, m, w, n, k), usd in agg.items()]


def cap_status(ops, today=None):
    """The CEO's lab spending cap, OPS/lab_cap.json {"usd", "per": day|week|month} (tools/ceo_inbox.py
    writes it from the Costs tab). {"usd": None, "stopped": False} with no file: no cap. An unreadable
    file stops the lab (fail closed). spent = the lab's spend since the start of this day/week/month."""
    p = os.path.join(ops, "lab_cap.json")
    if not os.path.exists(p):
        return {"usd": None, "per": None, "since": None, "spent": None, "stopped": False}
    today = today or datetime.date.today()
    try:
        c = json.load(open(p, encoding="utf-8"))
        cap, per = float(c["usd"]), c.get("per", "month")
        since = {"day": today, "week": today - datetime.timedelta(days=today.weekday()), "month": today.replace(day=1)}[per]
        if cap < 0:
            raise ValueError("negative cap")
    except (OSError, ValueError, TypeError, KeyError) as e:
        return {"usd": 0.0, "per": "month", "since": today.isoformat(), "spent": 0.0, "stopped": True,
                "error": f"lab_cap.json unreadable ({e.__class__.__name__}): the lab stays stopped until the cap is set again"}
    spent = sum(r[3] for r in lab_rows(ops) if r[0] >= since.isoformat())
    return {"usd": cap, "per": per, "since": since.isoformat(), "spent": round(spent, 4), "stopped": spent >= cap,
            "set_at": c.get("set_at")}


def lab_table(rows):
    """One row per run or gate per day, newest first: {date, name, kind, bot, baseline, grader, total}."""
    t = defaultdict(lambda: {"bot": 0.0, "baseline": 0.0, "grader": 0.0})
    for day, _m, what, usd, name, kind in rows:   # ponytail: a gate's attempts on one day merge into one row
        t[(day, name, kind)][what] += usd
    out = [{"date": d, "name": n, "kind": k, **{w: round(v, 4) for w, v in x.items()}, "total": round(sum(x.values()), 4)}
           for (d, n, k), x in t.items()]
    return sorted(out, key=lambda r: (r["date"], r["name"]), reverse=True)


def periods(rows, today):
    """{"day"|"week"|"month": [{"period", "by": {key: usd}}]} oldest first; key is the row's second field
    (by=1) and third field (by=2) both, as "model" and "group"."""
    keys = {"day": lambda d: d.isoformat(), "week": lambda d: f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}",
            "month": lambda d: d.strftime("%Y-%m")}
    want = {"day": [keys["day"](today - datetime.timedelta(days=n)) for n in range(DAYS - 1, -1, -1)],
            "week": sorted({keys["week"](today - datetime.timedelta(weeks=n)) for n in range(WEEKS)}),
            "month": [f"{(today.year * 12 + today.month - 1 - n) // 12}-{(today.month - 1 - n) % 12 + 1:02d}" for n in range(MONTHS - 1, -1, -1)]}
    out = {}
    for unit, key in keys.items():
        model, group = defaultdict(lambda: defaultdict(float)), defaultdict(lambda: defaultdict(float))
        for day, m, g, usd in rows:
            try:
                k = key(datetime.date.fromisoformat(day))
            except ValueError:
                continue
            model[k][m] += usd
            group[k][g] += usd
        out[unit] = [{"period": k, "model": {m: round(v, 4) for m, v in model[k].items()},
                      "group": {g: round(v, 4) for g, v in group[k].items()}} for k in want[unit]]
    return out


def build(ops, claude_root, today=None):
    today = today or datetime.date.today()
    rows = lab_rows(ops)
    table = lab_table(rows)
    return {"updated": datetime.datetime.now().isoformat(timespec="seconds"),
            "api": periods([r[:4] for r in rows], today), "claude": periods(claude_rows(claude_root), today),
            "lab": {"rows": table, "since_start": round(sum(r["total"] for r in table), 4),
                    "first": min((r["date"] for r in table), default=None), "cap": cap_status(ops, today)}}


def selftest():
    with tempfile.TemporaryDirectory() as t:
        ops, cl = os.path.join(t, "ops"), os.path.join(t, "claude", "C--groundedops", "s1", "subagents")
        os.makedirs(os.path.join(ops, "runs", "r1"))
        os.makedirs(cl)
        json.dump({"sys_normal": {"c1": {"usd": 0.5}, "c2": {"usd": 0.25}}, "baseline": {"c1": {"usd": 0.1}},
                   "summary": {"grader": {"model": "deepseek-v4-flash", "usd": 0.2}}},
                  open(os.path.join(ops, "runs", "r1", "cases_state.json"), "w"))
        msg = {"timestamp": datetime.date.today().isoformat() + "T10:00:00Z",
               "message": {"id": "m1", "role": "assistant", "model": "claude-opus-5-5",
                           "usage": {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_input_tokens": 0}}}
        with open(os.path.join(cl, "agent-a.jsonl"), "w") as f:
            f.write(json.dumps(msg) + "\n" + json.dumps(msg) + "\n")   # one message, streamed on two lines
        json.dump({"agentType": "dev"}, open(os.path.join(cl, "agent-a.meta.json"), "w"))
        c = build(ops, os.path.join(t, "claude"))
        today = c["claude"]["day"][-1]
        assert today["model"] == {"claude-opus-5-5": 6.0} and today["group"] == {"dev": 6.0}, today   # 4 + 2, once
        api = c["api"]["day"][-1]
        assert api["model"] == {"deepseek-v4-flash": 1.05} and api["group"]["bot"] == 0.75, api
        assert len(c["api"]["day"]) == DAYS and len(c["api"]["week"]) == WEEKS and len(c["api"]["month"]) == MONTHS
        assert [r["period"] for r in periods([], datetime.date(2026, 2, 15))["month"]] == \
            ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02"]
    lab_selftest()
    print("selftest ok")


def lab_selftest():
    with tempfile.TemporaryDirectory() as ops:
        def put(rel, d, days_old=0):
            p = os.path.join(ops, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            json.dump(d, open(p, "w", encoding="utf-8"))
            t = (datetime.datetime(2026, 10, 12, 12) - datetime.timedelta(days=days_old)).timestamp()
            os.utime(p, (t, t))
        g = lambda usd, at=None: {"usage": {"model": "deepseek-v4-flash", "usd": usd, **({"at": at} if at else {})}}
        # an old run re-marked today (file time today): its answers keep the run's date, the new grades are today's
        put("runs/20260920/cases_state.json", {"sys_normal": {"c1": {"usd": 1.0, "model": "stub"}}, "baseline": {"c1": {"usd": 2.0}},
                                               "grades": {"c1": g(0.5, "2026-10-12")}})
        put("inbox/dev/D-20261012-05.gate/after/cases_state.json",   # baseline copied from the run: not counted again
            {"sys_normal": {"c1": {"usd": 0.25, "at": "2026-10-12"}}, "baseline": {"c1": {"usd": 2.0}},
             "sys_before": {"c1": {"usd": 1.0}}, "grades": {"c1": g(0.125, "2026-10-12")}})
        put("inbox/dev/D-20261012-05.gate/rerun/head/cases_state.run1.json", {"sys_normal": {"c1": {"usd": 0.25}}, "baseline": {"c1": {"usd": 2.0}}})
        put("heldout/gates/D-20261012-05.gate-20261012-1000/sib_x/cases_state.json", {"sys_normal": {"c9": {"usd": 0.5}}})
        rows = lab_rows(ops)
        assert sum(r[3] for r in rows if r[2] == "baseline") == 2.0, "a gate's copied baseline is not counted twice"
        assert {r[4] for r in rows} == {"20260920", "D-20261012-05"}, "a gate is named by its work order, not its folders"
        old = {(r[0], r[2]): r[3] for r in rows if r[4] == "20260920"}
        assert old == {("2026-09-20", "bot"): 1.0, ("2026-09-20", "baseline"): 2.0, ("2026-10-12", "grader"): 0.5}, old
        assert {r[1] for r in rows if r[4] == "20260920"} == {"deepseek-v4-flash"}, "a stub answer takes the run's model"
        t = lab_table(rows)
        assert t[0] == {"date": "2026-10-12", "name": "D-20261012-05", "kind": "gate", "bot": 1.0, "baseline": 0.0,
                        "grader": 0.125, "total": 1.125}, t[0]   # after + re-ask + sibling, one row
        today = datetime.date(2026, 10, 12)
        assert cap_status(ops, today) == {"usd": None, "per": None, "since": None, "spent": None, "stopped": False}, "no file: no cap"
        cap = os.path.join(ops, "lab_cap.json")
        json.dump({"usd": 5, "per": "month"}, open(cap, "w"))
        c = cap_status(ops, today)
        assert (c["since"], c["spent"], c["stopped"]) == ("2026-10-01", 1.625, False), c   # September's run is not this month's
        json.dump({"usd": 1.6, "per": "day"}, open(cap, "w"))
        assert cap_status(ops, today)["stopped"], "cap reached"
        assert cap_status(ops, datetime.date(2026, 10, 13))["spent"] == 0, "a new day starts at 0"
        open(cap, "w").write("{not json")
        c = cap_status(ops, today)
        assert c["stopped"] and c["usd"] == 0 and "error" in c, "an unreadable cap stops the lab"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--claude", default=os.path.join(os.path.expanduser("~"), ".claude", "projects"))
    ap.add_argument("--cap", action="store_true", help="print the lab cap; exit 3 when the lab is stopped at it")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.cap:
        c = cap_status(a.ops)
        print(json.dumps(c))
        sys.exit(3 if c["stopped"] else 0)
    print(json.dumps(build(a.ops, a.claude), indent=1))


if __name__ == "__main__":
    main()
