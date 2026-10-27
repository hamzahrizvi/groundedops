"""Self-check for tools/arm_compare.py: python tools/test_arm_compare.py (no model, no lab)."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arm_compare import arm


def stages(*ids):
    return [{"id": s, "at_ms": 1000 * i} for i, s in enumerate(ids)]


with tempfile.TemporaryDirectory() as d:
    cases = [{"id": "x1", "type": "lookup", "expected": "answered"},
             {"id": "x2", "type": "phantom", "expected": "rejected"},
             {"id": "x3", "type": "phantom", "expected": "rejected"}]
    row = lambda sr, *ids: {"answer": "a", "wall": 2.0, "usd": 0.0003, "prompt_tokens": 1000, "cached": 0,
                            "pipeline": {"stages": stages(*ids), "meta": {"self_route": sr} if sr else {}}}
    state = {"sys_normal": {"x1": row("answered", "entry", "generate", "widen", "ground", "respond"),
                            "x2": row("answered", "entry", "generate", "widen", "ground", "respond"),
                            "x3": row(None, "entry", "band", "none.reject")},
             "grades": {"x1": {"sys_normal": {"score": 3, "verdict": "correct"}},
                        "x2": {"sys_normal": {"score": 0, "verdict": "wrong"}},
                        "x3": {"sys_normal": {"score": 3, "verdict": "correct_refusal"}}}}
    json.dump({"cases": cases}, open(os.path.join(d, "cases.json"), "w"))
    json.dump(state, open(os.path.join(d, "cases_state.json"), "w"))
    a = arm(d)
    assert a["n"] == 3 and a["pct_of_max"] == 66.7, a
    assert a["self_route"] == {"answered": 2} and a["reread_right"] == 1, a
    assert a["reread_answered"] == {"x1": "correct", "x2": "wrong"}, a
    assert a["reread_broke_correct_refusal"] == ["x2"], a          # a correct refusal turned into a wrong answer
    assert a["declined_at"] == {"none band": 1}, a
    assert a["verifier_s"]["median"] == 1.0, a                       # ground stage 1000 ms after the one before it
    assert a["usd_mean_all_cached"] == round(0.0003 - 1000 * (0.30 - 0.006) / 1e6, 6), a
    assert a["completion_tokens_mean"] == 0, a                       # 1000 uncached input tokens = $0.0003, nothing left over
    for x in state["sys_normal"].values():
        del x["prompt_tokens"], x["cached"]                            # baseline rows keep usd only
    json.dump(state, open(os.path.join(d, "cases_state.json"), "w"))
    b = arm(d)
    assert b["usd_mean_all_cached"] is None and b["completion_tokens_mean"] is None, b
    assert a["answers"] == "one answer" and a["by_type"] == {"lookup": "3/3", "phantom": "3/6"}, a
    # D-20261019-06: three answers per question are read as the office page reads them
    for k, (marks, wall) in enumerate([((2, 3, 3), 4.0), ((1, 3, 3), 6.0)], 2):
        v = {"x1": "correct", "x2": "correct", "x3": "correct_refusal"}
        json.dump({"sys_normal": {c: dict(x, wall=wall) for c, x in state["sys_normal"].items()},
                   "grades": {c: {"sys_normal": {"score": m, "verdict": v[c]}} for c, m in zip(("x1", "x2", "x3"), marks)}},
                  open(os.path.join(d, f"cases_state.r{k}.json"), "w"))
    c = arm(d)
    assert c["answers"] == "mean of 3 answers" and c["pct_of_max"] == 77.8, c        # means 2, 2, 3
    assert c["verdicts"] == {"correct": 2, "correct_refusal": 1}, c                    # x2 wrong in 1 of 3: not wrong
    assert c["by_type"] == {"lookup": "2/3", "phantom": "5/6"}, c
    assert (c["median_s"], c["max_s"]) == (4.0, 6.0), c                                # every answer of every repeat
print("ok")
