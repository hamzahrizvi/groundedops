"""Compare blind-run arms on the same cases, one block per arm (D-20261018-01).

  python tools/arm_compare.py <label>=<run_dir>[@<system>] ...

Each <run_dir> holds cases.json and a graded cases_state.json (tools/blind_eval.py).
<system> is the state key the arm's answers are under: sys_normal (default), sys_perf
for a "system perf" arm, baseline for the whole-manual baseline.

Prints JSON per arm: marks overall and per type, verdicts, median and slowest-tenth
time, mean cost as measured and as if every input token were a cache hit, mean and max
completion tokens, the verifier's time (the "ground" stage), the whole-manual re-read
(self_route) outcomes with the verdict of every answer it turned out of a refusal, and
for every decline the step that declined. Offline: reads files only.
"""
import json, os, sys, statistics as st
from collections import Counter

# deepseek-v4-flash $ per 1M tokens: input, cached input, output (tools/blind_eval.py PRICE)
IN, HIT, OUT = 0.30, 0.006, 1.20
DECLINES = ("safe_decline", "correct_refusal")


def p90(xs):
    return sorted(xs)[int(.9 * len(xs))] if xs else None   # same index as blind_eval's summary


def stage_ms(pipe, sid):
    """ms spent reaching stage `sid`: its at_ms minus the stage before it."""
    s = (pipe or {}).get("stages") or []
    for i, x in enumerate(s):
        if x["id"] == sid and i:
            return x["at_ms"] - s[i - 1]["at_ms"]


def declined_at(pipe):
    ids = [x["id"] for x in (pipe or {}).get("stages") or []]
    if "suppress" in ids:
        return "checker suppressed it"
    if any(i.startswith("none.") for i in ids):
        return "none band"
    return "writer refusal"


def arm(run, system="sys_normal"):
    cases = {c["id"]: c for c in json.load(open(os.path.join(run, "cases.json"), encoding="utf-8"))["cases"]}
    state = json.load(open(os.path.join(run, "cases_state.json"), encoding="utf-8"))
    rows, grades = state[system], state.get("grades", {})
    ok = [c for c in cases if c in rows and isinstance(grades.get(c, {}).get(system), dict)]
    g = {c: grades[c][system] for c in ok}
    r = {c: rows[c] for c in ok}
    walls = [x["wall"] for x in r.values()]
    usd = [x["usd"] for x in r.values()]
    miss = {c: x.get("prompt_tokens", 0) - x.get("cached", 0) for c, x in r.items()}   # input tokens that missed the cache
    hit = [x["usd"] - miss[c] * (IN - HIT) / 1e6 for c, x in r.items()]
    comp = [round((x["usd"] - miss[c] * IN / 1e6 - x.get("cached", 0) * HIT / 1e6) / (OUT / 1e6)) for c, x in r.items()]
    if not all("prompt_tokens" in x for x in r.values()):   # baseline rows keep usd only: token split unknown
        hit = comp = None
    ground = [ms / 1000 for x in r.values() if (ms := stage_ms(x.get("pipeline"), "ground")) is not None]
    sr = {c: ((x.get("pipeline") or {}).get("meta") or {}).get("self_route") for c, x in r.items()}
    turned = [c for c in ok if sr[c] == "answered"]
    types = sorted({cases[c]["type"] for c in ok})
    return {
        "run": run, "system": system, "n": len(ok),
        "pct_of_max": round(100 * sum(x["score"] for x in g.values()) / (3 * len(ok)), 1),
        "by_type": {t: f"{sum(g[c]['score'] for c in ok if cases[c]['type'] == t)}/{3 * sum(cases[c]['type'] == t for c in ok)}"
                    for t in types},
        "verdicts": dict(sorted(Counter(x["verdict"] for x in g.values()).items())),
        "median_s": st.median(walls), "p90_s": p90(walls), "max_s": max(walls),
        "usd_mean": round(st.mean(usd), 6), "usd_mean_all_cached": hit and round(st.mean(hit), 6), "usd_max": max(usd),
        "completion_tokens_mean": comp and round(st.mean(comp)), "completion_tokens_max": comp and max(comp),
        "verifier_s": {"n": len(ground), "median": st.median(ground) if ground else None, "p90": p90(ground)},
        "self_route": dict(Counter(v for v in sr.values() if v)),
        # the re-read's effect on marks, case by case: refusals it turned into answers and how each was graded
        "reread_answered": {c: g[c]["verdict"] for c in turned},
        "reread_right": sum(g[c]["verdict"] == "correct" for c in turned),
        "reread_broke_correct_refusal": [c for c in turned if cases[c]["expected"] == "rejected" and g[c]["verdict"] != "correct_refusal"],
        "declined_at": dict(Counter(declined_at(r[c].get("pipeline")) for c in ok if g[c]["verdict"] in DECLINES)),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2 or any("=" not in a for a in sys.argv[1:]):
        sys.exit(__doc__)
    out = {}
    for a in sys.argv[1:]:
        label, spec = a.split("=", 1)
        run, _, system = spec.partition("@")
        out[label] = arm(run, system or "sys_normal")
    print(json.dumps(out, indent=1))
