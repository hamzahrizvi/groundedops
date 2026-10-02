#!/usr/bin/env python3
"""Summarise one overnight eval batch (tools/overnight_eval.sh) into a small
summary.json.

The reading session (PENDING.md session 5) opens only this file, never the
raw results or logs, so everything a reader needs to decide what is noise is
here and nothing else is: per suite, which cases flipped and how often; the
blind before/after as per-case stable flips only (never totals as the
headline); the widget-path agreement and dropped keys; and what the labelled
log says about who checked the answers and what it cost.

    python tools/eval_batch_summary.py eval_runs/<stamp> --since <UTC ISO>

Writes <dir>/summary.json and prints it. Offline: no backend, no model.
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
import eval as rag_eval  # noqa: E402  (pure helpers only)

GRADER_FAILURES = ("grader returned nothing", "unparsed grader output",
                   "grader import failed")


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def suite_summary(path):
    """One eval.py --results file -> stable count, the flip table, grader
    health and latency. A missing file is reported, not skipped: a suite
    that never ran must not read as a suite that passed."""
    data = _load(path)
    if data is None:
        return {"file": os.path.basename(path), "missing": True}
    results = data.get("results") or []
    per = rag_eval._per_case(results)
    flips = sorted(f"{sum(v)}/{len(v)}  {k}" for k, v in per.items()
                   if 0 < sum(v) < len(v))
    always_fail = sorted(k for k, v in per.items() if v and not any(v))
    grader_errors = sum(
        1 for r in results
        if any(str((r.get("checks") or {}).get("_grade_reason", "")).startswith(g)
               for g in GRADER_FAILURES))
    return {
        "file": os.path.basename(path),
        "repeats": data.get("repeats"),
        "cases": len(per),
        "stable_pass": sum(1 for v in per.values() if v and all(v)),
        "flipped": flips,
        "always_fail": always_fail,
        "request_errors": sum(1 for r in results if r.get("error")),
        "grader_errors": grader_errors,
        "skipped": sum(1 for r in results if r.get("skipped")),
        "wall_by_role": {role: {"n": n, "p50": p50, "p90": p90}
                         for role, (n, p50, p90)
                         in rag_eval.latency_by_role(results).items()},
    }


def blind_comparison(before_path, after_path):
    a, b = _load(before_path), _load(after_path)
    if a is None or b is None:
        return {"missing": [p for p, d in ((before_path, a), (after_path, b))
                            if d is None]}
    cmp = rag_eval.compare_results(a, b)
    return {
        "before": os.path.basename(before_path),
        "after": os.path.basename(after_path),
        "note": "MEASURE-ONLY. Quote per-case stable flips, never totals.",
        "stable_pass_before_fail_after": [r["case"] for r in cmp["lost"]],
        "stable_fail_before_pass_after": [r["case"] for r in cmp["gained"]],
        "flipped_within_a_side": [f"{r['a']} -> {r['b']}  {r['case']}"
                                  for r in cmp["flaky"]],
        "unchanged": cmp["unchanged"],
        "totals_context_only": {"before": cmp["totals_a"],
                                "after": cmp["totals_b"]},
    }


def live_summary(path):
    data = _load(path)
    if data is None:
        return {"missing": True}
    s = data.get("summary") if isinstance(data, dict) else None
    if not s:
        return {"note": "single-path run; no agreement recorded"}
    agree = s.get("agreement") or {}
    return {
        "query": s.get("query"), "widget": s.get("widget"),
        "agreement": f"{agree.get('agreed')}/{agree.get('turns')}",
        "disagreements": [
            f"{d['scenario']} T{d['turn']}: query={d['query']} "
            f"widget={d['widget']}"
            + (f" (dropped {','.join(d['missing_keys'])})" if d["missing_keys"] else "")
            for d in agree.get("disagreements") or []],
    }


def _pct(values, p):
    return rag_eval._percentile(values, p) if values else None


def log_summary(log_dir, since):
    """The labelled log (M2) over this batch's window: who asked, how turns
    ended, what verified them, and what verifying cost (M7's re-measure)."""
    files = sorted(glob.glob(os.path.join(log_dir, "logs_*.jsonl")))
    files.append(os.path.join(log_dir, "logs.jsonl"))
    rows = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if (row.get("timestamp") or "") >= since:
                        rows.append(row)
        except OSError:
            continue
    tests = [r for r in rows if r.get("origin") in ("eval", "live", "preflight")]
    by_via = {}
    for r in tests:
        t = r.get("timing") or {}
        by_via.setdefault(r.get("verified_by") or "none", []).append(t)
    cost = {}
    for via, timings in sorted(by_via.items()):
        entry = {"n": len(timings)}
        for key in ("verifier_llm_time", "grounding_time", "total_time"):
            vals = [t[key] for t in timings if isinstance(t.get(key), (int, float))]
            if vals:
                entry[key] = {"p50": _pct(vals, 50), "p90": _pct(vals, 90)}
        cost[via] = entry
    served = [r for r in tests if r.get("outcome") == "respond"
              and isinstance(r.get("grounding_score"), (int, float))]
    return {
        "window_since_utc": since,
        "rows": len(rows),
        "rows_unlabelled": sum(1 for r in rows if "origin" not in r
                               or r.get("origin") is None),
        "origin": dict(Counter(r.get("origin") for r in rows)),
        "surface": dict(Counter(r.get("surface") for r in tests)),
        "outcome": dict(Counter(r.get("outcome") for r in tests)),
        "verified_by": dict(Counter(r.get("verified_by") for r in tests)),
        "service_degraded": sum(1 for r in tests if r.get("service_degraded")),
        "cost_by_verified_by": cost,
        "served_nli_under_0.1": (f"{sum(1 for r in served if r['grounding_score'] < 0.1)}"
                                 f"/{len(served)}"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--since", required=True, help="UTC ISO start of the batch")
    ap.add_argument("--log-dir", default=str(REPO / "src"))
    ap.add_argument("--blind-before", default=None)
    a = ap.parse_args()
    out = Path(a.out_dir)
    meta = _load(out / "batch_meta.json") or {}
    summary = {"batch": meta, "suites": {}}
    for name in ("tuned", "blind1", "blind2", "retrieval"):
        summary["suites"][name] = suite_summary(out / f"m4_{name}.json")
    before = a.blind_before or next(iter(sorted(out.glob("m5_blind2_*.json"))), None)
    if before:
        summary["blind2_before_after"] = blind_comparison(before, out / "m4_blind2.json")
    summary["live"] = live_summary(out / "live.json")
    summary["log"] = log_summary(a.log_dir, a.since)
    sweep = _load(out / "m13_sweep.json")
    summary["m13_sweep"] = (
        {"file": "m13_sweep.json",
         "report": "m13_sweep_report.txt (sweep_grounding.py --report-only, "
                   "captured right after the run)"}
        if sweep else "missing")
    (out / "summary.json").write_text(json.dumps(summary, indent=1),
                                      encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
