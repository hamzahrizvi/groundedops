"""Pure tests for eval_retrieval.py's cases-file and keyword-matching helpers."""
import json
import tempfile
from pathlib import Path

import eval_retrieval


def test_arg_value_reads_flag_and_equals_form():
    assert eval_retrieval._arg_value(["--cases", "x.json"], "cases") == "x.json"
    assert eval_retrieval._arg_value(["--cases=x.json"], "cases") == "x.json"
    assert eval_retrieval._arg_value(["--other"], "cases") is None


def test_load_cases_honours_an_explicit_path():
    # No tmp_path fixture: this suite is also run by run_tests.py's own
    # no-fixtures runner, not only pytest.
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "custom_cases.json"
        p.write_text(json.dumps({"cases": [{"q": "hi"}]}), encoding="utf-8")
        assert eval_retrieval.load_cases(str(p)) == [{"q": "hi"}]


def test_first_hit_rank_matches_any_of_keyword_lists():
    results = [{"text": "default relay duration is ~1s"}]
    assert eval_retrieval.first_hit_rank(results, set(), [["1s", "1 second"]]) == 1
    assert eval_retrieval.first_hit_rank(results, set(), [["2s", "2 seconds"]]) is None
