"""Compare blind-run arms on the same cases, one block per arm (D-20261018-01).

  python tools/arm_compare.py <label>=<run_dir>[@<system>] ...

Each <run_dir> holds cases.json and a graded cases_state.json (tools/blind_eval.py), and with
--repeats its cases_state.r2.json ...: marks are then each question's mean over the answers ("answers").
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ops_pivot import run_facts           # noqa: E402  the office page's reading of a run (D-20261019-06)
from retrofit_gate import repeat_paths    # noqa: E402

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
    """A run answered more than once (cases_state.r2.json ...) is read as the office page reads it
    (tools/ops_pivot.py run_facts, D-20261019-06): marks are each question's mean over the answers, a
    verdict is "wrong" when most answers are; times, cost and tokens count every answer of every repeat."""
    cases = {c["id"]: c for c in json.load(open(os.path.join(run, "cases.json"), encoding="utf-8"))["cases"]}
    states = [json.load(open(f, encoding="utf-8")) for f in repeat_paths(os.path.join(run, "cases_state.json"))]
    rows = states[0][system]
    facts = {f["id"]: f for f in run_facts(run, system)}
    ok = [c for c in cases if c in rows and c in facts]
    g = {c: facts[c] for c in ok}   # "bot": the mean mark, "verdict": the majority reading
    r = {c: rows[c] for c in ok}
    every = [(c, x) for i, s in enumerate(states) if not (i and system in s.get("copied_from_r1", []))
             for c in ok if isinstance(x := (s.get(system) or {}).get(c), dict) and "wall" in x]
    walls = [x["wall"] for _, x in every]
    usd = [x["usd"] for _, x in every]
    miss = [x.get("prompt_tokens", 0) - x.get("cached", 0) for _, x in every]   # input tokens that missed the cache
    hit = [x["usd"] - m * (IN - HIT) / 1e6 for (_, x), m in zip(every, miss)]
    comp = [round((x["usd"] - m * IN / 1e6 - x.get("cached", 0) * HIT / 1e6) / (OUT / 1e6)) for (_, x), m in zip(every, miss)]
    if not all("prompt_tokens" in x for _, x in every):   # baseline rows keep usd only: token split unknown
        hit = comp = None
    ground = [ms / 1000 for _, x in every if (ms := stage_ms(x.get("pipeline"), "ground")) is not None]
    sr = {c: ((x.get("pipeline") or {}).get("meta") or {}).get("self_route") for c, x in r.items()}
    turned = [c for c in ok if sr[c] == "answered"]
    types = sorted({cases[c]["type"] for c in ok})
    return {
        "run": run, "system": system, "n": len(ok),
        "answers": "one answer" if len(states) == 1 else f"mean of {len(states)} answers",
        "pct_of_max": round(100 * sum(x["bot"] for x in g.values()) / (3 * len(ok)), 1),
        "by_type": {t: f"{round(sum(g[c]['bot'] for c in ok if cases[c]['type'] == t), 1):g}/{3 * sum(cases[c]['type'] == t for c in ok)}"
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
