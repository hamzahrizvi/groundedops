"""Self-check for tools/embed_parity.py's pure helpers (D-20261023-06).

  python tools/lab_lock.py python tools/test_embed_parity.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from embed_parity import overlap, verdict  # noqa: E402


def row(**kw):
    r = {"query_cos": 0.999, "top10": 10, "n": 10, "top10_scoped": None, "n_scoped": None}
    r.update(kw)
    return r


def main():
    assert overlap(["a", "b"], ["b", "c"]) == 1
    assert verdict(0.995, [row(), row()]) == "CLEARED"
    assert verdict(0.98, [row()]) == "FINDING"
    assert verdict(0.995, [row(query_cos=0.985)]) == "FINDING"
    assert verdict(0.995, [row(top10=8)]) == "FINDING"
    assert verdict(0.995, [row(top10_scoped=3, n_scoped=3)]) == "CLEARED"
    assert verdict(0.995, [row(top10_scoped=2, n_scoped=3)]) == "FINDING"
    print("ok")


if __name__ == "__main__":
    main()
