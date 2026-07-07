#!/usr/bin/env python3
"""
eval.py — objective RAG regression gate.

Runs the cases in eval_cases.json against the live backend and scores each on
up to three checks:
  1. outcome   — answered vs clarify vs rejected matches expectation
  2. keywords  — required substrings present / forbidden substrings absent
  3. grade     — LLM-graded correctness against a reference (optional per case)

It then DIFFS the run against a committed baseline (eval_baseline.json) and
FAILS (exit 1) if any case that used to pass now fails (a regression), or if
the overall pass-rate drops below the baseline. This turns "did the app get
better or worse?" into an objective, repeatable check.

Usage:
  python eval.py                     # run + compare against baseline
  python eval.py --update-baseline   # accept current results as the baseline
  python eval.py --no-grade          # skip LLM grading (faster, outcome+keywords only)

Requires the backend running on :8000. LLM grading uses a model via the
backend's llm.generate(); configure with:
  EVAL_GRADER_PROVIDER (default "local")   EVAL_GRADER_MODEL (default "mistral")
  DEEPSEEK_API_KEY (needed if grader provider is "deepseek", and for any
  case marked requires_deepseek)
"""

import json
import os
import sys
import uuid
from pathlib import Path

import requests

HERE = Path(__file__).parent
URL = "http://127.0.0.1:8000/query"
CASES_FILE = HERE / "eval_cases.json"
BASELINE_FILE = HERE / "eval_baseline.json"
RESULTS_FILE = HERE / "eval_results.json"

GRADER_PROVIDER = os.getenv("EVAL_GRADER_PROVIDER", "local")
GRADER_MODEL = os.getenv("EVAL_GRADER_MODEL", "mistral")


def _deepseek_key():
    k = os.getenv("DEEPSEEK_API_KEY")
    if k:
        return k
    try:
        import keyvault
        return keyvault.load_key()
    except Exception:
        return None


DEEPSEEK_KEY = _deepseek_key()

# Roles the backend uses for a real answer (anything not clarify/rejected/none).
ANSWERED_ROLES = {"fast", "reasoning", "accurate", "extract", "rethink"}


def classify_outcome(data: dict) -> str:
    role = (data.get("role") or "").lower()
    if data.get("needs_clarification") or role == "clarify":
        return "clarify"
    if role == "rejected":
        return "rejected"
    if role in ANSWERED_ROLES or (role not in ("none", "") and data.get("answer")):
        return "answered"
    # Fallback: a "not found" answer with no role is effectively a rejection.
    return "rejected"


def llm_grade(question: str, reference: str, answer: str) -> tuple[bool, str]:
    """Ask a model whether `answer` is correct given `reference`. Returns
    (passed, reason). Fails closed (returns False) if the grader is
    unavailable, so a broken grader never silently 'passes' everything."""
    try:
        import llm
    except Exception as e:
        return False, f"grader import failed: {e}"

    prompt = (
        "You are grading whether an ANSWER is factually correct given the "
        "reference facts. Ignore wording/style; judge only correctness.\n\n"
        f"QUESTION: {question}\n"
        f"REFERENCE FACTS: {reference}\n"
        f"ANSWER: {answer}\n\n"
        'Reply with ONLY a JSON object: {"verdict":"pass"|"fail","reason":"<short>"}'
    )
    out = llm.generate(GRADER_PROVIDER, prompt, GRADER_MODEL, deepseek_api_key=DEEPSEEK_KEY)
    text = (out or {}).get("text", "") if isinstance(out, dict) else ""
    if not text:
        return False, "grader returned nothing"
    # Be forgiving about extra prose around the JSON.
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        verdict = json.loads(text[start:end])
        return verdict.get("verdict", "").lower() == "pass", verdict.get("reason", "")[:160]
    except Exception:
        # Last resort: look for the word pass/fail.
        low = text.lower()
        if "pass" in low and "fail" not in low:
            return True, "grader said pass (unparsed)"
        return False, f"unparsed grader output: {text[:120]}"


def run_case(case: dict, session_id: str, do_grade: bool) -> dict:
    q = case["q"]
    body = {"q": q, "session_id": session_id, "deepseek_api_key": DEEPSEEK_KEY}
    if case.get("force_provider"):
        body["force_provider"] = case["force_provider"]
        body["force_model"] = case.get("force_model")

    checks = {}
    skipped = False

    if case.get("requires_deepseek") and not DEEPSEEK_KEY:
        return {"q": q, "skipped": True, "reason": "no DeepSeek key", "passed": None, "checks": {}}

    try:
        r = requests.post(URL, json=body, timeout=180)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {"q": q, "error": str(e), "passed": False, "checks": {"request": False}}

    answer = data.get("answer", "") or ""
    outcome = classify_outcome(data)

    # 1. outcome
    if "outcome" in case:
        checks["outcome"] = (outcome == case["outcome"])

    # 2. keywords
    low = answer.lower()
    if case.get("keywords_all"):
        checks["keywords_all"] = all(k.lower() in low for k in case["keywords_all"])
    if case.get("keywords_absent"):
        checks["keywords_absent"] = all(k.lower() not in low for k in case["keywords_absent"])

    # 3. LLM grade (only meaningful for answered outcomes)
    if do_grade and case.get("grade") and case.get("reference") and outcome == "answered":
        ok, reason = llm_grade(q, case["reference"], answer)
        checks["grade"] = ok
        checks["_grade_reason"] = reason

    passed = all(v for k, v in checks.items() if not k.startswith("_"))
    return {
        "q": q,
        "outcome": outcome,
        "grounding": data.get("grounding_score"),
        "retrieval": data.get("retrieval_score"),
        "provider": data.get("provider"),
        "answer": answer[:200],
        "checks": checks,
        "passed": passed,
        "skipped": skipped,
    }


def main():
    do_grade = "--no-grade" not in sys.argv
    update_baseline = "--update-baseline" in sys.argv

    spec = json.loads(CASES_FILE.read_text())
    cases = spec["cases"]

    session_id = str(uuid.uuid4())
    results = []
    print("=" * 70)
    print(f"RAG EVAL  ({len(cases)} cases, grading={'on' if do_grade else 'off'})")
    print("=" * 70)
    for i, case in enumerate(cases, 1):
        if case.get("new_session"):
            session_id = str(uuid.uuid4())
        res = run_case(case, session_id, do_grade)
        results.append(res)
        if res.get("skipped"):
            mark = "SKIP"
        elif res.get("passed"):
            mark = "PASS"
        else:
            mark = "FAIL"
        cks = " ".join(f"{k}={v}" for k, v in res.get("checks", {}).items() if not k.startswith("_"))
        print(f"[{i:02d}] {mark}  outcome={res.get('outcome','?'):8} {cks}")
        print(f"      Q: {res['q']}")
        if mark == "FAIL":
            if res.get("checks", {}).get("_grade_reason"):
                print(f"      grade reason: {res['checks']['_grade_reason']}")
            print(f"      answer: {res.get('answer','')[:160]}")

    scored = [r for r in results if not r.get("skipped")]
    npass = sum(1 for r in scored if r.get("passed"))
    rate = npass / len(scored) if scored else 0.0
    print("-" * 70)
    print(f"  {npass}/{len(scored)} passed  ({rate:.0%})   skipped={sum(1 for r in results if r.get('skipped'))}")

    # Persist this run.
    by_q = {r["q"] + ("::" + r.get("provider", "") if r.get("provider") else ""): r for r in results}
    RESULTS_FILE.write_text(json.dumps({"pass_rate": rate, "results": results}, indent=2))

    if update_baseline:
        BASELINE_FILE.write_text(json.dumps({"pass_rate": rate, "cases": {r["q"]: bool(r.get("passed")) for r in scored}}, indent=2))
        print(f"\nBaseline updated → {BASELINE_FILE.name}  (pass_rate {rate:.0%})")
        return 0

    # Diff against baseline.
    if not BASELINE_FILE.exists():
        print("\nNo baseline yet. Review the results above, then lock them in with:")
        print("  python eval.py --update-baseline")
        return 0

    baseline = json.loads(BASELINE_FILE.read_text())
    base_cases = baseline.get("cases", {})
    regressions, fixes = [], []
    for r in scored:
        was = base_cases.get(r["q"])
        now = bool(r.get("passed"))
        if was is True and not now:
            regressions.append(r["q"])
        elif was is False and now:
            fixes.append(r["q"])

    print("\nvs baseline:")
    print(f"  pass_rate {baseline.get('pass_rate', 0):.0%} → {rate:.0%}")
    for q in fixes:
        print(f"  ✔ FIXED: {q}")
    for q in regressions:
        print(f"  x REGRESSED: {q}")

    if regressions or rate < baseline.get("pass_rate", 0):
        print("\nGATE: FAIL — regressions or lower pass-rate than baseline.")
        return 1
    print("\nGATE: PASS — no regressions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
