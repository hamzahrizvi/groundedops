"""Pure tests for eval.py's layered, repeatable gate helpers."""
import sys
import types
from unittest.mock import patch

import eval as rag_eval


def test_case_key_is_layer_qualified():
    assert rag_eval.case_key({"q": "What is it?"}) == "faq::What is it?"
    assert (rag_eval.case_key({"q": "What is it?", "layer": "retrieval"})
            == "retrieval::What is it?")


def test_run_case_forwards_skip_faq_and_checks_sources():
    sent = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "answer": "The answer is in the manual.",
                "role": "accurate",
                "provider": "local",
                "sources": [{"source": "Manual.pdf"}],
            }

    def fake_post(url, json, timeout):
        sent.update(json)
        return Response()

    case = {
        "q": "Where is the answer?",
        "layer": "retrieval",
        "skip_faq": True,
        "outcome": "answered",
        "sources_any": ["manual.pdf"],
    }
    with patch.object(rag_eval.requests, "post", side_effect=fake_post):
        result = rag_eval.run_case(case, "session", do_grade=False)

    assert sent["skip_faq"] is True
    assert result["layer"] == "retrieval"
    assert result["checks"] == {"outcome": True, "sources_any": True}
    assert result["passed"] is True


def test_a_deflect_and_a_stated_non_answer_are_rejections():
    assert rag_eval.classify_outcome({"role": "sales", "answer": "I can only answer technical questions"}) == "rejected"
    assert rag_eval.classify_outcome({"role": "fast", "answer": "I don't have that", "answerability": "unanswerable"}) == "rejected"
    # 8.3: "the documentation doesn't mention X" is a rejection, whatever role
    # the turn kept.
    assert rag_eval.classify_outcome({"role": "fast", "answer": "The BV30 documentation doesn't mention Bluetooth", "answerability": "not_mentioned"}) == "rejected"
    assert rag_eval.classify_outcome({"role": "fast", "answer": "It weighs 1.05 kg", "answerability": "stated"}) == "answered"
    assert rag_eval.classify_outcome({"role": "clarify", "needs_clarification": True}) == "clarify"


def test_a_friendly_refusal_is_rejected_even_though_role_stayed_answering():
    # INFERABLE/ADVISORY refusals never reset role away from fast/reasoning
    # (main.py's offer_support branches), so without the ported run_live.py
    # rule these scored "answered" purely because role was untouched.
    assert rag_eval.classify_outcome({
        "role": "fast", "answer": "I don't hold anything on that.",
        "answerability": "inferable", "offer_support": True, "sources": [],
    }) == "rejected"
    assert rag_eval.classify_outcome({
        "role": "reasoning", "answer": "I don't hold anything on that.",
        "answerability": "unanswerable", "offer_support": True,
        "sources": [{"source": "Manual.pdf"}],
    }) == "rejected"
    # offer_support with real sources and a non-unanswerable kind (the
    # capability/inference branches that DID answer) stays "answered".
    assert rag_eval.classify_outcome({
        "role": "fast", "answer": "Yes, that is documented.",
        "answerability": "inferable", "offer_support": False,
        "sources": [{"source": "Manual.pdf"}],
    }) == "answered"


def test_keywords_all_supports_any_of_lists_for_alternate_phrasing():
    case = {"q": "duration?", "keywords_all": [["1s", "1 second"], "relay"]}
    assert rag_eval._keyword_present("default relay duration is ~1s", case["keywords_all"][0])
    assert rag_eval._keyword_present("default relay duration is 1 second", case["keywords_all"][0])
    assert not rag_eval._keyword_present("default duration is 2 seconds", case["keywords_all"][0])


def test_llm_grade_no_longer_passes_on_a_bare_unparsed_word():
    fake_llm = types.SimpleNamespace(
        generate=lambda *a, **k: {"text": "I think this should pass overall."})
    with patch.dict(sys.modules, {"llm": fake_llm}):
        ok, reason = rag_eval.llm_grade("Q", "ref", "ans")
    assert ok is False
    assert reason.startswith("unparsed grader output")


def test_preflight_grader_aborts_on_empty_verdict():
    fake_llm = types.SimpleNamespace(generate=lambda *a, **k: {"text": ""})
    with patch.dict(sys.modules, {"llm": fake_llm}):
        assert rag_eval.preflight_grader() is False


def test_expected_page_checks_the_cited_pages_not_just_the_source():
    sent = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "answer": "The BV30 PSU voltage too low fault is 4 red, 1 blue.",
                "role": "accurate", "provider": "local",
                "sources": [{"source": "BV30 User Manual-v1.pdf", "pages": [1, 31]}],
            }

    def fake_post(url, json, timeout):
        sent.update(json)
        return Response()

    with patch.object(rag_eval.requests, "post", side_effect=fake_post):
        wrong_page = rag_eval.run_case(
            {"q": "x", "expected_page": 32}, "s", do_grade=False)
        right_page = rag_eval.run_case(
            {"q": "x", "expected_page": [31, 32]}, "s", do_grade=False)

    assert wrong_page["checks"]["expected_page"] is False
    assert right_page["checks"]["expected_page"] is True


def test_preflight_grader_passes_on_a_real_verdict_even_if_it_is_fail():
    fake_llm = types.SimpleNamespace(
        generate=lambda *a, **k: {"text": '{"verdict":"fail","reason":"wrong"}'})
    with patch.dict(sys.modules, {"llm": fake_llm}):
        assert rag_eval.preflight_grader() is True


def _res(q, passed, layer="faq", repeat=1, **extra):
    return dict({"q": q, "layer": layer, "passed": passed, "repeat": repeat}, **extra)


def test_compare_results_headlines_only_stable_flips():
    a = {"results": [
        _res("lost", True), _res("lost", True, repeat=2),
        _res("gained", False), _res("gained", False, repeat=2),
        _res("noisy", True), _res("noisy", False, repeat=2),
        _res("same", True), _res("same", True, repeat=2),
        _res("gone", True),
        _res("skipped", None, skipped=True),
    ]}
    b = {"results": [
        _res("lost", False), _res("lost", False, repeat=2),
        _res("gained", True), _res("gained", True, repeat=2),
        _res("noisy", True), _res("noisy", True, repeat=2),
        _res("same", True), _res("same", True, repeat=2),
        _res("new", False),
    ]}
    cmp = rag_eval.compare_results(a, b)
    assert [r["case"] for r in cmp["lost"]] == ["faq::lost"]
    assert cmp["lost"][0]["a"] == "2/2" and cmp["lost"][0]["b"] == "0/2"
    assert [r["case"] for r in cmp["gained"]] == ["faq::gained"]
    assert [r["case"] for r in cmp["flaky"]] == ["faq::noisy"]
    assert [r["case"] for r in cmp["only_a"]] == ["faq::gone"]
    assert [r["case"] for r in cmp["only_b"]] == ["faq::new"]
    assert cmp["unchanged"] == 1
    assert cmp["blind"] is False
    # a case is keyed by layer too: the same question in two layers is two cases
    assert rag_eval.compare_results(
        {"results": [_res("q", True, layer="blind")]},
        {"results": [_res("q", False, layer="retrieval")]})["blind"] is True


def test_run_case_records_wall_seconds_and_role():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"answer": "ok", "role": "fast", "sources": []}

    with patch.object(rag_eval.requests, "post", return_value=Response()):
        res = rag_eval.run_case({"q": "x", "outcome": "answered"}, "s", do_grade=False)
    assert res["role"] == "fast"
    assert isinstance(res["wall"], float) and res["wall"] >= 0


def test_latency_by_role_reports_nearest_rank_p50_p90():
    results = [_res(str(i), True, role="fast", wall=float(i)) for i in range(1, 11)]
    results.append(_res("s", None, skipped=True, role="fast", wall=99.0))
    results.append(_res("r", True, role="reasoning", wall=4.0))
    rows = rag_eval.latency_by_role(results)
    assert rows["fast"] == (10, 5.0, 9.0)
    assert rows["reasoning"] == (1, 4.0, 4.0)


def test_update_baseline_is_refused_on_a_blind_set_and_writes_nothing():
    import json as _json
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        cases = os.path.join(d, "cases.json")
        baseline = os.path.join(d, "baseline.json")
        with open(cases, "w") as f:
            _json.dump({"cases": [{"q": "x", "outcome": "answered", "layer": "blind"}]}, f)
        argv = ["eval.py", "--cases", cases, "--baseline", baseline,
                "--update-baseline", "--no-grade"]
        with patch.object(sys, "argv", argv), \
                patch.object(rag_eval.requests, "post",
                             side_effect=AssertionError("must not run")):
            assert rag_eval.main() == 2
        assert not os.path.exists(baseline)


def test_baseline_from_results_keeps_only_cases_that_pass_every_time():
    data = {"repeats": 3, "results": [
        _res("always", True), _res("always", True, repeat=2), _res("always", True, repeat=3),
        _res("flaky", True), _res("flaky", False, repeat=2), _res("flaky", True, repeat=3),
        _res("never", False), _res("never", False, repeat=2), _res("never", False, repeat=3),
        _res("skipped", None, skipped=True),
        _res("r", True, layer="retrieval"),
    ]}
    base = rag_eval.baseline_from_results(data, source="eval_runs/x/m4.json")
    # M4: a flip on unchanged code is a fail in the mark, not a pass
    assert base["cases"] == {"faq::always": True, "faq::flaky": False,
                             "faq::never": False, "retrieval::r": True}
    assert base["pass_rate"] == 0.5 and base["repeats"] == 3
    assert base["source"] == "eval_runs/x/m4.json"
    # same shape --update-baseline writes, so the gate's diff reads it as is
    assert set(base) == {"pass_rate", "repeats", "cases", "source"}
    # repeats is recovered from the attempts when the results file has none
    assert rag_eval.baseline_from_results({"results": data["results"][:3]})["repeats"] == 3


def test_baseline_from_results_refuses_a_blind_set_and_writes_nothing():
    import json as _json
    import os
    import tempfile
    import pytest
    with pytest.raises(ValueError):
        rag_eval.baseline_from_results({"results": [_res("q", True, layer="blind")]})
    with tempfile.TemporaryDirectory() as d:
        results = os.path.join(d, "results.json")
        baseline = os.path.join(d, "baseline.json")
        with open(results, "w") as f:
            _json.dump({"repeats": 3, "results": [_res("q", True, layer="blind")]}, f)
        argv = ["eval.py", "--baseline-from-results", results, "--baseline", baseline]
        with patch.object(sys, "argv", argv), \
                patch.object(rag_eval.requests, "post",
                             side_effect=AssertionError("must not run")):
            assert rag_eval.main() == 2
        assert not os.path.exists(baseline)
        # and the non-blind path writes the mark without touching the backend
        with open(results, "w") as f:
            _json.dump({"repeats": 2, "results": [
                _res("a", True), _res("a", True, repeat=2),
                _res("b", True), _res("b", False, repeat=2)]}, f)
        with patch.object(sys, "argv", argv), \
                patch.object(rag_eval.requests, "post",
                             side_effect=AssertionError("must not run")):
            assert rag_eval.main() == 0
        with open(baseline) as f:
            written = _json.load(f)
        assert written["cases"] == {"faq::a": True, "faq::b": False}
        assert written["pass_rate"] == 0.5 and written["repeats"] == 2


def test_fine_outcomes_mirror_run_live_and_leave_old_cases_alone():
    """8.15: handoff / deflect / manual are scored by name; a case asking for
    "rejected" still passes on a sales deflect, as it did before."""
    fo = rag_eval.fine_outcome
    assert fo({"role": "handoff", "answer": "I'll hand this over"}) == "handoff"
    assert fo({"role": "sales", "answer": "Contact sales"}) == "deflect"
    assert fo({"role": "document", "sources": [{"download_url": "/source_file/x"}]}) == "manual"
    assert fo({"role": "fast", "sources": [{"download_url": "/source_file/x"}]}) == "manual"
    assert fo({"role": "fast", "sources": [{"download_url": "/x", "pages": [3]}]}) is None, \
        "a cited page is an answer, not a document handed over"
    assert fo({"role": "fast", "answer": "It weighs 1 kg"}) is None
    assert set(rag_eval.VALID_OUTCOMES) >= {"handoff", "deflect", "manual", "rejected"}

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self.data

    sales = {"answer": "Please contact our sales team.", "role": "sales"}
    for want, ok in (("deflect", True), ("rejected", True), ("handoff", False)):
        with patch.object(rag_eval.requests, "post", return_value=Response(sales)):
            res = rag_eval.run_case({"q": "price?", "outcome": want}, "s", do_grade=False)
        assert res["checks"]["outcome"] is ok, want
        assert res["fine_outcome"] == "deflect"


def test_breakdown_counts_layers_and_the_faq_share():
    results = [_res("a", True, layer="faq", provider="faq"),
               _res("b", False, layer="faq", provider="deepseek"),
               _res("c", True, layer="refusal", provider="none"),
               _res("d", None, skipped=True, layer="refusal", provider="faq")]
    b = rag_eval.breakdown(results)
    assert b["layers"] == {"faq": [1, 2], "refusal": [1, 1]}
    assert b["providers"] == {"faq": 1, "deepseek": 1, "none": 1}
