#!/usr/bin/env python3
"""sweep_grounding.py — measure the false-refusal rate of the grounding gate.

WHY THIS EXISTS

GROUNDING_THRESHOLD (0.55) decides whether a generated answer is shown or
discarded as unsupported by the retrieved context. It has never been
measured against real cases, so the rate at which it throws away CORRECT
answers has been unknown — `PENDING.md` has carried that as the single
biggest unmeasured risk in the pipeline.

THE CHEAP WAY TO SWEEP IT

The obvious approach — run the eval suite once per candidate threshold — is
not needed. `grounding.check_grounding` computes `min_score` entirely
independently of the threshold it is passed; the threshold is only the final
`min_score >= threshold` comparison. So:

    run every case ONCE, record its score, then evaluate any threshold
    against those recorded scores arithmetically.

One generation pass answers the question for every threshold at once.

WHAT IT REPORTS

Only cases the suite marks `outcome: "answered"` can produce a FALSE
refusal, so those are what the rate is computed over. Cases that never
reached the grounding check (refused earlier by the retrieval gate, or
served from the curated FAQ) are counted separately rather than being
silently folded in — the gate cannot be blamed for a refusal it never made.

USAGE

    python sweep_grounding.py                  # run the suite, then report
    python sweep_grounding.py --dry-run        # show what it would run
    python sweep_grounding.py --report-only    # re-report from the last run
    python sweep_grounding.py --cases eval_cases_extensive.json

Results are written to sweep_grounding.json so the report can be
regenerated, and so a later run can be diffed against this one.

This makes real provider calls and costs real tokens (roughly one answered
query per case). It is a measurement, not something to run in a loop.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "sweep_grounding.json")

# The range worth looking at. 0.0 = the gate is off; 0.95 = almost nothing
# survives it. The current production value sits in the middle of this.
CANDIDATES = [0.0, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
CURRENT = 0.55


def load_cases(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    cases = data if isinstance(data, list) else data.get("cases", data)
    if not isinstance(cases, list):
        raise SystemExit(f"{path}: expected a list of cases, or a 'cases' key")
    return cases


def run_suite(cases: list[dict]) -> list[dict]:
    """Run every 'answered' case once and record its grounding score."""
    # Imported here, not at module scope: this pulls in chromadb and three
    # transformer models, which takes a while and should not happen for
    # --report-only or --dry-run.
    import main

    # main.query() refuses with 503 until APP_STATE["ready"] is set, and that
    # normally happens in FastAPI's startup handler. Importing the module
    # does not run it, so a headless caller has to warm the stack itself --
    # otherwise every case fails identically with "System is still loading".
    if not main.APP_STATE.get("ready"):
        print("Loading models (embeddings, reranker, NLI)...")
        main._warmup_stack()
        if not main.APP_STATE.get("ready"):
            raise SystemExit(
                f"Warmup failed: {main.APP_STATE.get('error')}\n"
                f"The index or a model is unavailable; nothing can be measured.")
        print("Ready.\n")

    answerable = [c for c in cases if c.get("outcome") == "answered"]
    print(f"{len(answerable)} answerable case(s) of {len(cases)} total\n")

    out = []
    for i, case in enumerate(answerable, 1):
        q = case["q"]
        started = time.time()
        try:
            req = main.QueryRequest(q=q, product=case.get("product"),
                                    skip_faq=True)
            result = main.query(req, None)
        except Exception as e:
            print(f"  [{i}/{len(answerable)}] ERROR  {q[:58]}  ({e})")
            out.append({"q": q, "product": case.get("product"),
                        "error": str(e), "grounding_score": None})
            continue

        score = result.get("grounding_score")
        flagged = bool(result.get("flagged"))
        secs = time.time() - started

        # A score of None means the grounding check never ran for this case
        # (served from the FAQ, or refused before generation). Those cannot
        # be false refusals BY THE GATE, so they are recorded and excluded
        # from the rate rather than counted as passes.
        mark = "--" if score is None else f"{score:.4f}"
        print(f"  [{i}/{len(answerable)}] {mark}  {'FLAGGED ' if flagged else ''}"
              f"{q[:52]}  ({secs:.0f}s)")

        out.append({
            "q": q,
            "product": case.get("product"),
            "grounding_score": score,
            "flagged": flagged,
            "reached_grounding": score is not None,
            "answer_chars": len(result.get("answer") or ""),
            "seconds": round(secs, 1),
        })
    return out


def report(rows: list[dict]) -> None:
    scored = [r for r in rows if r.get("grounding_score") is not None]
    skipped = [r for r in rows if r.get("grounding_score") is None
               and not r.get("error")]
    errors = [r for r in rows if r.get("error")]

    print("\n" + "=" * 66)
    print("  GROUNDING THRESHOLD SWEEP")
    print("=" * 66)
    print(f"  cases that reached the grounding check : {len(scored)}")
    print(f"  cases that never reached it            : {len(skipped)}")
    if errors:
        print(f"  cases that errored                     : {len(errors)}")
    if not scored:
        print("\n  Nothing reached the grounding check — nothing to sweep.")
        return

    vals = sorted(r["grounding_score"] for r in scored)
    print(f"\n  score  min {vals[0]:.4f}   median {vals[len(vals)//2]:.4f}"
          f"   max {vals[-1]:.4f}")

    print("\n  Every one of these cases SHOULD be answered, so any refusal")
    print("  here is a false refusal caused by the gate.\n")
    print("  threshold   kept   false refusals            ")
    print("  ---------   ----   --------------------------")
    for t in CANDIDATES:
        refused = [r for r in scored if r["grounding_score"] < t]
        kept = len(scored) - len(refused)
        pct = 100.0 * len(refused) / len(scored)
        bar = "#" * int(round(pct / 4))
        flag = "  <-- current" if abs(t - CURRENT) < 1e-9 else ""
        print(f"     {t:.2f}      {kept:>3}   {len(refused):>3} "
              f"({pct:5.1f}%) {bar}{flag}")

    at_current = [r for r in scored if r["grounding_score"] < CURRENT]
    print()
    if at_current:
        print(f"  At the current {CURRENT}, these correct answers are discarded:")
        for r in sorted(at_current, key=lambda r: r["grounding_score"]):
            print(f"    {r['grounding_score']:.4f}  {r['q'][:58]}")
    else:
        print(f"  At the current {CURRENT}, NO correct answer is discarded.")

    # The largest gap in the sorted scores below the current value is where
    # a threshold can move most without changing behaviour - useful when
    # deciding whether the number is sitting on a cliff or on a plateau.
    if len(vals) > 1:
        gaps = [(vals[i + 1] - vals[i], vals[i], vals[i + 1])
                for i in range(len(vals) - 1)]
        gap, lo, hi = max(gaps)
        if gap > 0.01:
            print(f"\n  Widest gap between adjacent scores: {lo:.4f} -> {hi:.4f} "
                  f"({gap:.4f}).")
            print("  A threshold inside that gap is the least sensitive to noise.")

    if skipped:
        print(f"\n  Never reached the gate ({len(skipped)}) — refused earlier or")
        print("  served from the FAQ, so the gate is not responsible for these:")
        for r in skipped[:8]:
            print(f"    {r['q'][:60]}")
        if len(skipped) > 8:
            print(f"    ... and {len(skipped) - 8} more")


def main_cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cases", default=os.path.join(HERE, "eval_cases.json"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report-only", action="store_true",
                    help="re-report from sweep_grounding.json without running")
    a = ap.parse_args()

    if a.report_only:
        if not os.path.isfile(RESULTS):
            raise SystemExit(f"No previous run at {RESULTS}")
        with open(RESULTS, encoding="utf-8") as fh:
            saved = json.load(fh)
        report(saved["rows"])
        print(f"\n  (from {saved.get('finished_at', 'an earlier run')})")
        return 0

    cases = load_cases(a.cases)
    answerable = [c for c in cases if c.get("outcome") == "answered"]

    if a.dry_run:
        print(f"Would run {len(answerable)} answerable case(s) "
              f"of {len(cases)} in {os.path.basename(a.cases)}:")
        for c in answerable:
            print(f"  [{c.get('product') or 'no product'}] {c['q']}")
        print("\nEach is one real provider call. Nothing was run.")
        return 0

    started = time.time()
    rows = run_suite(cases)
    with open(RESULTS, "w", encoding="utf-8") as fh:
        json.dump({
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "cases_file": os.path.basename(a.cases),
            "current_threshold": CURRENT,
            "elapsed_seconds": round(time.time() - started, 1),
            "rows": rows,
        }, fh, indent=2)
    report(rows)
    print(f"\n  Saved to {os.path.basename(RESULTS)} "
          f"({time.time() - started:.0f}s total).")
    print("  Re-report any time with --report-only; no provider calls.")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
