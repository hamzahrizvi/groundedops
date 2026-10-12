"""What the office spends, per model and per department, by day, week and month.

  python tools/ops_costs.py [--ops C:/Users/hrizvi/groundedops-ops] [--claude ~/.claude/projects] > costs.json
  python tools/ops_costs.py --selftest

API: the test lab's provider calls, from each run's cases_state.json (the bot's and the baseline's
per-question usd, and the grader's total). The live product does not log tokens, so its traffic is
not here; the provider console has it.
Claude: every assistant message in the groundedops session transcripts, tokens x list price. The
office runs on a Claude plan, so this is what the same work would cost on the API, not a bill.
Each subagent's department comes from its .meta.json (agentType); the rest is "lanes and sessions".
"""
import argparse
import datetime
import glob
import json
import os
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


def api_rows(ops):
    """[(date, model, what, usd)] from the lab's runs."""
    out = []
    for p in glob.glob(os.path.join(ops, "runs", "*", "cases_state.json")):
        try:
            s = json.load(open(p, encoding="utf-8"))
        except ValueError:
            continue
        day = datetime.date.fromtimestamp(os.path.getmtime(p)).isoformat()
        g = (s.get("summary") or {}).get("grader") or {}
        model = g.get("model") or "lab model (not logged)"
        for system, what in (("sys_normal", "bot"), ("baseline", "baseline")):
            usd = sum(r.get("usd", 0) for r in (s.get(system) or {}).values() if isinstance(r, dict))
            if usd:
                out.append((day, model, what, usd))
        if g.get("usd"):
            out.append((day, model, "grader", g["usd"]))
    return out


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
    return {"updated": datetime.datetime.now().isoformat(timespec="seconds"),
            "api": periods(api_rows(ops), today), "claude": periods(claude_rows(claude_root), today)}


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
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--claude", default=os.path.join(os.path.expanduser("~"), ".claude", "projects"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    print(json.dumps(build(a.ops, a.claude), indent=1))


if __name__ == "__main__":
    main()
