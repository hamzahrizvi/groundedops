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
  python eval.py --layer retrieval --repeats 3
                                    # stable retrieval-only gate (all runs pass)
  python eval.py --cases eval_cases_retrieval.json --repeats 3
                                    # run a separate, corpus-specific suite
  python eval.py --cases eval_cases_retrieval.json \
      --baseline eval_baseline_retrieval.json --repeats 3
                                    # gate that suite against its own baseline
  python eval.py --compare-results A.json B.json
                                    # offline before/after diff of two --results files
  python eval.py --baseline-from-results RESULTS.json [--baseline X.json]
                                    # reset the pass mark from a repeated run's
                                    # saved results: only cases that passed on
                                    # every attempt (M4); refuses blind sets

Requires the backend running on :8000. LLM grading uses a model via the
backend's llm.generate(); configure with:
  EVAL_GRADER_PROVIDER (default "local")   EVAL_GRADER_MODEL (default "mistral")
  DEEPSEEK_API_KEY (needed if grader provider is "deepseek", and for any
  case marked requires_deepseek)

Each case belongs to a layer: faq, retrieval, comparison, refusal, or
grounding. ``--repeats`` records every attempt but gates on the conservative
result: a case is stable only when every non-skipped attempt passes.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests

HERE = Path(__file__).parent
URL = os.getenv("EVAL_URL", "http://127.0.0.1:8000/query")
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
VALID_LAYERS = {"faq", "retrieval", "comparison", "refusal", "grounding", "blind"}


def _arg_value(name: str, default: str | None = None) -> str | None:
    """Return ``--name VALUE`` or ``--name=VALUE`` without a dependency."""
    flag = f"--{name}"
    for i, arg in enumerate(sys.argv):
        if arg == flag and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return default


def case_layer(case: dict) -> str:
    return str(case.get("layer") or "faq").strip().lower()


def case_key(case: dict) -> str:
    """Layer-qualified key prevents a FAQ and retrieval case sharing a query."""
    return f"{case_layer(case)}::{case['q']}"


def _keyword_present(low_answer: str, entry) -> bool:
    """A keywords_all entry is either a literal substring, or a list of
    alternatives (any-of) for a fact the model may phrase more than one way
    ("1s" vs "1 second") — exact substring matching either way, no stemming
    or synonym table."""
    if isinstance(entry, list):
        return any(str(alt).lower() in low_answer for alt in entry)
    return str(entry).lower() in low_answer


def classify_outcome(data: dict) -> str:
    role = (data.get("role") or "").lower()
    if data.get("needs_clarification") or role == "clarify":
        return "clarify"
    if role == "rejected":
        return "rejected"
    # The operator's commercial deflect answers nothing from the documents,
    # and a friendly refusal keeps the model's role ("fast") while the
    # pipeline's own classification says the question was unanswerable.
    # Both used to count as "answered", so a refusal case could never pass
    # and an unwanted deflect could never fail.
    if role == "sales":
        return "rejected"
    if (data.get("answerability") or "") in ("unanswerable", "documented_elsewhere",
                                             "not_mentioned"):
        return "rejected"
    # A friendly refusal keeps the answering role (INFERABLE/ADVISORY turns
    # never reset role away from fast/reasoning) but is a rejection wearing
    # the answer's clothes. Ported from run_live.py:64-65's offer_support
    # rule, which is how the stress test scores the same turns; without it
    # eval.py counted these as "answered" purely because role never changed.
    if data.get("offer_support") and (
            not data.get("sources") or (data.get("answerability") or "") == "unanswerable"):
        return "rejected"
    if role in ANSWERED_ROLES or (role not in ("none", "") and data.get("answer")):
        return "answered"
    # Fallback: a "not found" answer with no role is effectively a rejection.
    return "rejected"


# 8.15: the three shapes run_live.observed_outcome can see and classify_outcome
# folds into "rejected". A case asks for one of these by name; every older
# case keeps scoring on classify_outcome, so "sales" still counts as rejected.
SPECIFIC_OUTCOMES = ("handoff", "deflect", "manual")
VALID_OUTCOMES = ("answered", "clarify", "rejected") + SPECIFIC_OUTCOMES


def fine_outcome(data: dict) -> str | None:
    """handoff / deflect / manual, or None. Mirrors run_live.observed_outcome:
    a manual is a document handed over (download_url, no cited page)."""
    role = (data.get("role") or "").lower()
    if role == "sales" or data.get("kind") == "deflected":
        return "deflect"
    if role == "document" or any(
            (s or {}).get("download_url") and not (s or {}).get("pages")
            and not str((s or {}).get("page_label") or "").startswith("page")
            for s in data.get("sources") or []):
        return "manual"
    if role == "handoff":
        return "handoff"
    return None


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
        # No more "does the word 'pass' appear anywhere" fallback: it let a
        # chatty non-JSON response score a pass off one stray word. The
        # latest recorded run had 0 unparsed outputs, so removing this
        # changes no score; a genuinely unparsed reply now fails closed.
        return False, f"unparsed grader output: {text[:120]}"


def run_case(case: dict, session_id: str, do_grade: bool) -> dict:
    q = case["q"]
    body = {"q": q, "session_id": session_id, "deepseek_api_key": DEEPSEEK_KEY}
    if case.get("force_provider"):
        body["force_provider"] = case["force_provider"]
        body["force_model"] = case.get("force_model")
    if case.get("product"):
        body["product"] = case["product"]  # v2.1: product-scoped retrieval
    if case.get("skip_faq"):
        body["skip_faq"] = True

    checks = {}
    skipped = False

    if case.get("requires_deepseek") and not DEEPSEEK_KEY:
        return {"q": q, "layer": case_layer(case), "skipped": True,
                "reason": "no DeepSeek key", "passed": None, "checks": {}}

    # M3: wall seconds for the request alone (grading excluded: it is the
    # instrument's cost, not the visitor's). 8.11 and 10.1 read this field.
    t0 = time.perf_counter()
    try:
        r = requests.post(URL, json=body, timeout=180)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {"q": q, "layer": case_layer(case), "error": str(e),
                "wall": round(time.perf_counter() - t0, 3),
                "passed": False, "checks": {"request": False}}
    wall = round(time.perf_counter() - t0, 3)

    answer = data.get("answer", "") or ""
    outcome = classify_outcome(data)
    fine = fine_outcome(data)

    # 1. outcome
    if case.get("outcome") in SPECIFIC_OUTCOMES:
        checks["outcome"] = (fine == case["outcome"])
    elif "outcome" in case:
        checks["outcome"] = (outcome == case["outcome"])

    # 2. keywords
    low = answer.lower()
    if case.get("keywords_all"):
        checks["keywords_all"] = all(_keyword_present(low, k) for k in case["keywords_all"])
    if case.get("keywords_absent"):
        checks["keywords_absent"] = all(k.lower() not in low for k in case["keywords_absent"])
    if case.get("sources_any"):
        sources = "\n".join(str(s.get("source") or "")
                            for s in data.get("sources") or [])
        checks["sources_any"] = any(s.lower() in sources.lower()
                                    for s in case["sources_any"])
    # M9: a spec-table answer citing the RIGHT document is not enough --
    # sources_any passes even when the cited page is the table of contents.
    # _build_sources (main.py:2011) already emits each source's `pages`
    # list; expected_page is one page or a list of acceptable pages (a
    # table spanning a page break, where the answer may come from either
    # the primary chunk or its table_completion continuation).
    if case.get("expected_page") is not None:
        cited_pages = {p for s in (data.get("sources") or [])
                       for p in (s.get("pages") or []) if isinstance(p, int)}
        want = case["expected_page"]
        wanted_pages = want if isinstance(want, list) else [want]
        checks["expected_page"] = any(p in cited_pages for p in wanted_pages)

    # 3. LLM grade (only meaningful for answered outcomes)
    if do_grade and case.get("grade") and case.get("reference") and outcome == "answered":
        ok, reason = llm_grade(q, case["reference"], answer)
        checks["grade"] = ok
        checks["_grade_reason"] = reason

    passed = all(v for k, v in checks.items() if not k.startswith("_"))
    return {
        "q": q,
        "layer": case_layer(case),
        "outcome": outcome,
        "fine_outcome": fine,
        "grounding": data.get("grounding_score"),
        "retrieval": data.get("retrieval_score"),
        "provider": data.get("provider"),
        "role": data.get("role"),
        "wall": wall,
        "answer": answer,
        "checks": checks,
        "passed": passed,
        "skipped": skipped,
    }


MEASURE_ONLY_BANNER = (
    "MEASURE-ONLY: this run includes blind cases. Read and compare them;\n"
    "never tune against them or lock them in as a baseline.")


def has_blind(cases_or_results) -> bool:
    return any(case_layer(c) == "blind" for c in cases_or_results)


def _per_case(results: list) -> dict:
    """{layer::q: [passed, ...]} over non-skipped attempts."""
    out = {}
    for r in results:
        if r.get("skipped"):
            continue
        out.setdefault(case_key(r), []).append(bool(r.get("passed")))
    return out


def compare_results(a: dict, b: dict) -> dict:
    """Pure before/after diff of two results files (eval.py --results).

    The headline is the per-case STABLE flips: a case that passed on every
    attempt on one side and failed on every attempt on the other. A case
    that flipped within a side is noise until more repeats say otherwise,
    so it is listed but never counted as a change. Totals are context only:
    the suite carries about +/-3 cases of run-to-run noise."""
    pa = _per_case(a.get("results") or [])
    pb = _per_case(b.get("results") or [])

    def state(runs):
        if not runs:
            return "absent"
        if all(runs):
            return "stable_pass"
        if not any(runs):
            return "stable_fail"
        return "flaky"

    lost, gained, flaky, only_a, only_b = [], [], [], [], []
    unchanged = 0
    for key in sorted(set(pa) | set(pb)):
        ra, rb = pa.get(key) or [], pb.get(key) or []
        sa, sb = state(ra), state(rb)
        row = {"case": key, "a": f"{sum(ra)}/{len(ra)}", "b": f"{sum(rb)}/{len(rb)}"}
        if sa == "absent":
            only_b.append(row)
        elif sb == "absent":
            only_a.append(row)
        elif sa == "stable_pass" and sb == "stable_fail":
            lost.append(row)
        elif sa == "stable_fail" and sb == "stable_pass":
            gained.append(row)
        elif "flaky" in (sa, sb):
            flaky.append(row)
        else:
            unchanged += 1

    def totals(per):
        return {"cases": len(per),
                "stable_pass": sum(1 for v in per.values() if all(v))}

    both = (a.get("results") or []) + (b.get("results") or [])
    return {"lost": lost, "gained": gained, "flaky": flaky,
            "only_a": only_a, "only_b": only_b, "unchanged": unchanged,
            "totals_a": totals(pa), "totals_b": totals(pb),
            "blind": has_blind(both)}


def baseline_from_results(data: dict, source: str = "") -> dict:
    """The pass mark, reset from a saved ``--results`` file (M4).

    Same shape --update-baseline writes, built offline from a repeated run:
    a case is in the mark only when it passed on every attempt, so the gate
    is "the cases that pass every time on unchanged code", not one lucky
    run. ``source`` records where the numbers came from. Refuses a blind
    set: those are measured, never locked in."""
    results = data.get("results") or []
    if has_blind(results):
        raise ValueError("blind cases are measured, never a baseline")
    per = _per_case(results)
    cases = {key: all(runs) for key, runs in per.items()}
    rate = sum(cases.values()) / len(cases) if cases else 0.0
    repeats = data.get("repeats") or max((len(v) for v in per.values()),
                                         default=0)
    out = {"pass_rate": rate, "repeats": repeats, "cases": cases}
    if source:
        out["source"] = source
    return out


def _percentile(values: list, pct: float) -> float:
    """Nearest-rank percentile; no numpy for an advisory print."""
    vals = sorted(values)
    if not vals:
        return 0.0
    rank = -(-pct * len(vals) // 100)  # ceil
    return vals[max(0, min(len(vals), int(rank)) - 1)]


def latency_by_role(results: list) -> dict:
    """{role: (n, p50, p90)} from the per-case wall field. Advisory only:
    two backends on one box skew each other's timings (PENDING.md: the
    same question reranked in 1.2s on one process and 0.9s on the other)."""
    by = {}
    for r in results:
        if r.get("skipped") or r.get("wall") is None:
            continue
        by.setdefault(r.get("role") or "?", []).append(r["wall"])
    return {role: (len(w), _percentile(w, 50), _percentile(w, 90))
            for role, w in sorted(by.items())}


def print_latency(results: list) -> None:
    rows = latency_by_role(results)
    if not rows:
        return
    print("  wall seconds per role (advisory; p50 / p90):")
    for role, (n, p50, p90) in rows.items():
        print(f"    {role:10} n={n:3}  p50={p50:6.2f}s  p90={p90:6.2f}s")


def breakdown(results: list) -> dict:
    """8.15: runs passed per layer, and which provider answered. provider
    'faq' is the curated-answer share, read from a normal run -- no
    --skip-faq second pass, which would record a gap on every question."""
    layers, providers = {}, {}
    for r in results:
        if r.get("skipped"):
            continue
        n = layers.setdefault(r.get("layer") or "faq", [0, 0])
        n[0] += bool(r.get("passed"))
        n[1] += 1
        prov = r.get("provider") or "none"
        providers[prov] = providers.get(prov, 0) + 1
    return {"layers": layers, "providers": providers}


def print_breakdown(results: list) -> None:
    b = breakdown(results)
    if not b["layers"]:
        return
    print("  per layer (runs passed): " + ", ".join(
        f"{k} {p}/{n}" for k, (p, n) in sorted(b["layers"].items())))
    print("  answered by: " + ", ".join(
        f"{k} {n}" for k, n in sorted(b["providers"].items(), key=lambda kv: -kv[1])))


def print_comparison(cmp: dict, name_a: str, name_b: str) -> None:
    if cmp["blind"]:
        print(MEASURE_ONLY_BANNER)
    print("=" * 70)
    print(f"A = {name_a}")
    print(f"B = {name_b}")
    print("=" * 70)
    print(f"Stable pass on A, stable fail on B ({len(cmp['lost'])}):")
    for r in cmp["lost"]:
        print(f"  - {r['case']}   A {r['a']}  B {r['b']}")
    print(f"Stable fail on A, stable pass on B ({len(cmp['gained'])}):")
    for r in cmp["gained"]:
        print(f"  + {r['case']}   A {r['a']}  B {r['b']}")
    if cmp["flaky"]:
        print(f"Flipped within a side, not counted ({len(cmp['flaky'])}):")
        for r in cmp["flaky"]:
            print(f"  ~ {r['case']}   A {r['a']}  B {r['b']}")
    for label, rows in (("Only in A", cmp["only_a"]), ("Only in B", cmp["only_b"])):
        if rows:
            print(f"{label} ({len(rows)}): " + "; ".join(r["case"] for r in rows))
    ta, tb = cmp["totals_a"], cmp["totals_b"]
    print("-" * 70)
    print(f"Totals: A {ta['stable_pass']}/{ta['cases']} stable, "
          f"B {tb['stable_pass']}/{tb['cases']} stable, "
          f"{cmp['unchanged']} unchanged. Within +/-3 noise unless a case is")
    print("stable on both sides: quote the per-case lines above, not these.")


def preflight_grader() -> bool:
    """One real grader call before case 1. EVAL_GRADER_PROVIDER/EVAL_GRADER_
    MODEL default to a local Ollama ("local"/"mistral") that most shells
    never start, so a run that exports only DEEPSEEK_API_KEY still grades
    against the unreachable default and every case silently reports "grader
    returned nothing" — a poisoned run discovered only after it finishes.
    A real fail verdict from a reachable grader is NOT an abort; only the
    two infra-failure shapes are."""
    ok, reason = llm_grade(
        "What colour is a clear sky?", "The sky is blue.", "The sky is blue.")
    if not ok and (reason == "grader returned nothing"
                   or (reason or "").startswith("grader import failed")):
        print(f"preflight: FAIL — grader ({GRADER_PROVIDER}/{GRADER_MODEL}) "
              f"did not answer: {reason}")
        print("  Set EVAL_GRADER_PROVIDER / EVAL_GRADER_MODEL to a reachable")
        print("  provider (e.g. EVAL_GRADER_PROVIDER=deepseek with")
        print("  DEEPSEEK_API_KEY exported), or pass --no-grade.")
        return False
    return True


def preflight(skip: bool, do_grade: bool = False) -> bool:
    """
    Health-gate the run. A 40+ case eval against a degraded backend
    produces a poisoned log that costs more time to un-learn than the
    check costs to run: local timeouts cascade into DeepSeek hammering,
    'grader returned nothing', and false REGRESSED lines against the
    baseline. Verify the pipeline end-to-end BEFORE case 1:

      1. Backend answers on /query at all.
      2. A real query completes with provider != 'none' (proves the
         local model is loaded and generating within its timeout —
         this call also warms mistral so case 1 doesn't pay cold-load).

    Abort loudly on failure. --skip-preflight bypasses (e.g. when
    intentionally testing degraded behaviour).
    """
    if skip:
        print("preflight: SKIPPED (--skip-preflight)")
        return True
    print("preflight: checking backend + local model ...")
    try:
        r = requests.post(
            URL,
            json={"q": "what is MyCheckr", "session_id": f"preflight-{uuid.uuid4()}"},
            timeout=300,  # generous: this doubles as the model warm-up call
        )
        r.raise_for_status()
        body = r.json()
    except Exception as e:
        print("preflight: FAIL — backend not reachable/healthy:", e)
        print("  Start the API (uvicorn main:app --port 8000) and ensure")
        print("  Ollama is running (`ollama list`), then re-run.")
        return False

    provider = body.get("provider") or body.get("model_provider")
    answer = (body.get("answer") or "")[:60]
    if provider in (None, "none"):
        print("preflight: FAIL — backend up but generation failed "
              f"(provider={provider!r}). Local model likely timing out or "
              "not loaded. Warm it (`ollama run mistral \"hi\"`), check ")
        print("  Ollama logs, then re-run.")
        return False

    print(f"preflight: OK (provider={provider}, answer starts: {answer!r})")

    if do_grade:
        print(f"preflight: checking grader ({GRADER_PROVIDER}/{GRADER_MODEL}) ...")
        if not preflight_grader():
            return False
        print("preflight: grader OK")

    return True


def main():
    do_grade = "--no-grade" not in sys.argv
    update_baseline = "--update-baseline" in sys.argv
    report = "--report" in sys.argv  # survey mode: show answers, no judging/gate
    skip_preflight = "--skip-preflight" in sys.argv

    if "--compare-results" in sys.argv:
        i = sys.argv.index("--compare-results")
        if len(sys.argv) < i + 3:
            print("usage: eval.py --compare-results A.json B.json")
            return 2
        path_a, path_b = Path(sys.argv[i + 1]), Path(sys.argv[i + 2])
        cmp = compare_results(json.loads(path_a.read_text()),
                              json.loads(path_b.read_text()))
        print_comparison(cmp, str(path_a), str(path_b))
        return 0

    # M4: reset the pass mark from a repeated run's saved results, offline.
    if "--baseline-from-results" in sys.argv:
        src = _arg_value("baseline-from-results")
        if not src:
            print("usage: eval.py --baseline-from-results RESULTS.json "
                  "[--baseline eval_baseline.json]")
            return 2
        src_path = Path(src)
        baseline_path = Path(_arg_value("baseline", str(BASELINE_FILE)))
        data = json.loads(src_path.read_text())
        if has_blind(data.get("results") or []):
            print(MEASURE_ONLY_BANNER)
            print("Refusing to build a baseline from blind results; "
                  "nothing written.")
            return 2
        try:
            rel = src_path.resolve().relative_to(HERE.parent.resolve())
        except ValueError:
            rel = src_path.name
        base = baseline_from_results(data, source=str(rel).replace("\\", "/"))
        baseline_path.write_text(json.dumps(base, indent=2))
        print(f"Baseline written -> {baseline_path.name} from {rel}: "
              f"{sum(base['cases'].values())}/{len(base['cases'])} cases "
              f"stable across {base['repeats']} run(s) "
              f"({base['pass_rate']:.0%})")
        return 0

    # v10.4: --selfcheck validates the suite schema + baseline parse WITHOUT
    # a running backend or corpus. Used by CI (where the ITL corpus isn't
    # present) to catch broken cases/baseline before merge. Exit 0 on OK.
    if "--selfcheck" in sys.argv or os.getenv("EVAL_SELFCHECK") == "1":
        try:
            cases_path = Path(_arg_value("cases", str(CASES_FILE)))
            spec = json.loads(cases_path.read_text())
            cases = spec["cases"]
            assert isinstance(cases, list) and cases, "no cases"
            for i, c in enumerate(cases):
                assert "q" in c, f"case {i} missing 'q'"
                assert c.get("outcome") in VALID_OUTCOMES, \
                    f"case {i} bad outcome"
                assert case_layer(c) in VALID_LAYERS, \
                    f"case {i} has invalid layer {case_layer(c)!r}"
            baseline_path = Path(_arg_value("baseline", str(BASELINE_FILE)))
            if baseline_path.exists():
                json.loads(baseline_path.read_text())  # must parse
            print(f"selfcheck OK — {len(cases)} cases, baseline "
                  f"{'present' if baseline_path.exists() else 'absent'}")
            return 0
        except Exception as e:
            print(f"selfcheck FAILED: {e}")
            return 1
    # In report mode, optionally force every case to actually answer (via a
    # forced provider) so you can see what the model WOULD say even where the
    # system currently clarifies or rejects. Needs a DeepSeek key by default.
    force_answers = "--force-answers" in sys.argv
    force_prov = os.getenv("EVAL_FORCE_PROVIDER", "deepseek")
    # Seventh copy of the retired "deepseek-chat" alias lived here. Default to
    # the same env var every other deepseek call site reads.
    force_mdl = os.getenv("EVAL_FORCE_MODEL",
                          os.getenv("ONLINE_DEEPSEEK_MODEL", "deepseek-v4-flash"))
    if report:
        do_grade = False

    cases_path = Path(_arg_value("cases", str(CASES_FILE)))
    layer_filter = (_arg_value("layer") or "").strip().lower() or None
    repeats_raw = _arg_value("repeats", "1")
    try:
        repeats = max(1, int(repeats_raw or "1"))
    except ValueError:
        print(f"Invalid --repeats value: {repeats_raw!r}")
        return 2
    if layer_filter and layer_filter not in VALID_LAYERS:
        print(f"Invalid --layer {layer_filter!r}; choose from {sorted(VALID_LAYERS)}")
        return 2

    spec = json.loads(cases_path.read_text())
    cases = spec["cases"]
    if layer_filter:
        cases = [c for c in cases if case_layer(c) == layer_filter]
    if not cases:
        print("No cases selected.")
        return 2

    baseline_path = Path(_arg_value("baseline", str(BASELINE_FILE)))
    results_path = Path(_arg_value("results", str(RESULTS_FILE)))

    # M3: a blind set is measured, never tuned against. Locking it in as a
    # baseline would make it a target, so refuse before anything runs or
    # is written.
    blind = has_blind(cases)
    if blind:
        print(MEASURE_ONLY_BANNER)
        if update_baseline:
            print("Refusing --update-baseline on blind cases; nothing written.")
            return 2

    if not preflight(skip_preflight, do_grade):
        return 2  # distinct exit code: environment failure, not eval failure

    results = []
    print("=" * 70)
    print(f"RAG EVAL  ({len(cases)} cases × {repeats} run(s), "
          f"grading={'on' if do_grade else 'off'}, "
          f"layer={layer_filter or 'all'})")
    print("=" * 70)
    for repeat in range(1, repeats + 1):
        session_id = f"eval-{uuid.uuid4()}"  # M2: origin=eval server-side
        for i, case in enumerate(cases, 1):
            if case.get("new_session"):
                session_id = f"eval-{uuid.uuid4()}"
            run_case_input = dict(case)
            if report and force_answers and not run_case_input.get("force_provider"):
                run_case_input["force_provider"] = force_prov
                run_case_input["force_model"] = force_mdl
                run_case_input.pop("requires_deepseek", None)
            res = run_case(run_case_input, session_id, do_grade)
            res["repeat"] = repeat
            results.append(res)
            if res.get("skipped"):
                mark = "SKIP"
            elif report:
                mark = "SEEN"
            elif res.get("passed"):
                mark = "PASS"
            else:
                mark = "FAIL"
            cks = " ".join(f"{k}={v}" for k, v in res.get("checks", {}).items() if not k.startswith("_"))
            print(f"[{repeat}.{i:02d}] {mark} [{res['layer']}] outcome={res.get('outcome','?'):8} {cks}")
            print(f"      Q: {res['q']}")
            if res.get("grounding") is not None or res.get("retrieval") is not None:
                print(f"      scores: retrieval={res.get('retrieval')}  grounding={res.get('grounding')}  provider={res.get('provider')}")
            if res.get("checks", {}).get("_grade_reason"):
                print(f"      grade reason: {res['checks']['_grade_reason']}")
            if res.get("error"):
                print(f"      error: {res['error']}")
            if not res.get("skipped"):
                print(f"      A: {res.get('answer','')}")

    scored = [r for r in results if not r.get("skipped")]
    npass = sum(1 for r in scored if r.get("passed"))
    rate = npass / len(scored) if scored else 0.0
    grouped = {}
    for result in scored:
        grouped.setdefault(f"{result.get('layer', 'faq')}::{result['q']}", []).append(bool(result.get("passed")))
    stable_cases = {key: all(outcomes) for key, outcomes in grouped.items()}
    stable_rate = (sum(stable_cases.values()) / len(stable_cases)
                   if stable_cases else 0.0)

    if report:
        print("-" * 70)
        print(f"  survey of {len(scored)} cases — no pass/fail applied.")
        print("  Review the answers above, fill in expected outcome/keywords/")
        print("  reference in eval_cases.json, then: python eval.py --update-baseline")
        results_path.write_text(json.dumps({"results": results}, indent=2))
        return 0

    print("-" * 70)
    print(f"  {npass}/{len(scored)} individual runs passed ({rate:.0%}); "
          f"{sum(stable_cases.values())}/{len(stable_cases)} cases stable "
          f"across {repeats} run(s) ({stable_rate:.0%}); "
          f"skipped={sum(1 for r in results if r.get('skipped'))}")
    print_latency(results)
    print_breakdown(results)
    if blind:
        print(MEASURE_ONLY_BANNER)

    # Persist this run.
    results_path.write_text(json.dumps({"pass_rate": rate,
                                        "stable_pass_rate": stable_rate,
                                        "repeats": repeats,
                                        "results": results}, indent=2))

    if update_baseline:
        baseline_path.write_text(json.dumps({"pass_rate": stable_rate,
                                             "repeats": repeats,
                                             "cases": stable_cases}, indent=2))
        print(f"\nBaseline updated -> {baseline_path.name}  (stable pass_rate {stable_rate:.0%})")
        return 0

    # Diff against baseline.
    if not baseline_path.exists():
        print("\nNo baseline yet. Review the results above, then lock them in with:")
        print("  python eval.py --update-baseline --baseline " + baseline_path.name)
        return 0

    baseline = json.loads(baseline_path.read_text())
    base_cases = baseline.get("cases", {})
    regressions, fixes = [], []
    for key, now in stable_cases.items():
        # Pre-layer baselines used the bare question as their key; accepting
        # them makes the format migration non-destructive.
        bare_q = key.split("::", 1)[-1]
        was = base_cases.get(key, base_cases.get(bare_q))
        if was is True and not now:
            regressions.append(key)
        elif was is False and now:
            fixes.append(key)

    print("\nvs baseline:")
    print(f"  stable pass_rate {baseline.get('pass_rate', 0):.0%} -> {stable_rate:.0%}")
    for q in fixes:
        print(f"  ✔ FIXED: {q}")
    for q in regressions:
        print(f"  x REGRESSED: {q}")

    if regressions or stable_rate < baseline.get("pass_rate", 0):
        print("\nGATE: FAIL — regressions or lower pass-rate than baseline.")
        return 1
    print("\nGATE: PASS — no regressions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
