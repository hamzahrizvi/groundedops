"""How far apart are N answers of the same code to the same questions? (D-20261018-02)

  python tools/repeat_spread.py <run_dir> [sys_normal,baseline]

Reads <run_dir>/cases.json and the run's repeats (cases_state.json, cases_state.r2.json, ... from
blind_eval.py --repeats N), files only. Per arm: each repeat's overall score (pct_of_max) and marks per
question type, the spread (max - min) of each; the per-question SD of the mark over the repeats; and
Miller's smallest change a paired before/after on the same questions can detect:

  MDE_K = (z_conf + z_power) * sqrt(2 * mean_i(var_i) / (K * n))   marks, x100/3 in points

with var_i the variance of question i's mark over the repeats, n questions, K answers per question,
z_conf = 1.645 (90% confidence two-sided, the office's band) and z_power = 1.2816 (90% power).
An arm copied into a repeat from repeat 1 (blind_eval.py "copied_from_r1") is one answer, not N: skipped.
Verdict ADOPT when sys_normal's overall spread is 5 points or more, or any type's spread 4 marks or more
(the work order's rule). Prints a markdown table; writes <run_dir>/repeat_spread.json.
"""
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from retrofit_gate import repeat_paths  # noqa: E402

Z_CONF, Z_POWER = 1.645, 1.2816


def spread(run_dir, systems=("sys_normal", "baseline")):
    cases = json.load(open(os.path.join(run_dir, "cases.json"), encoding="utf-8"))["cases"]
    reps = [json.load(open(f, encoding="utf-8")) for f in repeat_paths(os.path.join(run_dir, "cases_state.json"))]
    out = {"run": os.path.basename(os.path.abspath(run_dir)), "repeats": len(reps), "z_conf": Z_CONF, "z_power": Z_POWER, "arms": {}}
    for s in systems:
        if any(s in r.get("copied_from_r1", []) for r in reps):
            out["arms"][s] = {"skipped": "copied from repeat 1 in a later repeat: one answer, not N"}
            continue
        ids = [c["id"] for c in cases if all(s in r.get("grades", {}).get(c["id"], {}) for r in reps)]
        if not ids or len(reps) < 2:
            out["arms"][s] = {"skipped": "needs 2+ repeats with grades"}
            continue
        marks = {cid: [r["grades"][cid][s]["score"] for r in reps] for cid in ids}
        typ = {c["id"]: c["type"] for c in cases}
        n = len(ids)
        overall = [round(100 * sum(marks[c][k] for c in ids) / (3 * n), 1) for k in range(len(reps))]
        by_type = {}
        for t in sorted({typ[c] for c in ids}):
            tc = [c for c in ids if typ[c] == t]
            got = [sum(marks[c][k] for c in tc) for k in range(len(reps))]
            by_type[t] = {"marks": got, "of": 3 * len(tc), "spread": max(got) - min(got)}
        var = [st.variance(marks[c]) for c in ids]
        sd = [v ** 0.5 for v in var]
        mde = {f"K{k}": round(100 / 3 * (Z_CONF + Z_POWER) * (2 * st.mean(var) / (k * n)) ** 0.5, 1) for k in (1, 3)}
        out["arms"][s] = {"n": n, "overall": overall, "overall_spread": round(max(overall) - min(overall), 1),
                          "by_type": by_type, "sd_mean": round(st.mean(sd), 3), "sd_nonzero": sum(x > 0 for x in sd),
                          "var_mean": round(st.mean(var), 4), "mde_pts": mde}
    a = out["arms"].get("sys_normal", {})
    big = [t for t, v in a.get("by_type", {}).items() if v["spread"] >= 4]
    out["adopt"] = bool(a.get("overall")) and (a["overall_spread"] >= 5 or bool(big))
    out["why"] = (f"sys_normal overall spread {a.get('overall_spread')} pts (rule: 5+); types spanning 4+ marks: {big or 'none'}"
                  if a.get("overall") else "no sys_normal repeats to read")
    return out


def table(r):
    lines = [f"Run {r['run']}, {r['repeats']} repeats. Verdict: {'ADOPT' if r['adopt'] else 'do not adopt'} ({r['why']}).", ""]
    for s, a in r["arms"].items():
        if "skipped" in a:
            lines += [f"{s}: skipped, {a['skipped']}.", ""]
            continue
        k = len(a["overall"])
        lines += [f"### {s} ({a['n']} questions)", "",
                  "| | " + " | ".join(f"r{i + 1}" for i in range(k)) + " | spread |", "|---" * (k + 2) + "|",
                  "| overall pct_of_max | " + " | ".join(map(str, a["overall"])) + f" | {a['overall_spread']} pts |"]
        lines += [f"| {t} (of {v['of']}) | " + " | ".join(map(str, v["marks"])) + f" | {v['spread']} |" for t, v in a["by_type"].items()]
        lines += ["", f"Per-question SD: mean {a['sd_mean']} marks; {a['sd_nonzero']} of {a['n']} questions moved between repeats.",
                  f"Smallest detectable paired change (90% confidence, 90% power): one answer {a['mde_pts']['K1']} pts, "
                  f"three answers {a['mde_pts']['K3']} pts. MDE_K = ({r['z_conf']} + {r['z_power']}) * sqrt(2 * {a['var_mean']} / (K * {a['n']})) marks x 100/3.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    res = spread(sys.argv[1], tuple(sys.argv[2].split(",")) if len(sys.argv) > 2 else ("sys_normal", "baseline"))
    json.dump(res, open(os.path.join(sys.argv[1], "repeat_spread.json"), "w", encoding="utf-8"), indent=1)
    print(table(res))
