"""D-20261019-17: two named products are answered, not asked "which part?"."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _harness  # noqa: F401,E402
import main  # noqa: E402
import clarify  # noqa: E402
from test_pipeline_trace import _ask, CHUNK  # noqa: E402


def test_two_named_products_are_answered_not_asked_which_part():
    chunks = [dict(CHUNK, id=f"c{i}", source=s, product=p, rerank_score=sc)
              for i, (s, p, sc) in enumerate([("SCS.pdf", "sku_scs", 0.55),
                                              ("NV4000.pdf", "nv4000", 0.53),
                                              ("CashGenic.pdf", "cashgenic", 0.52),
                                              ("SCS.pdf", "sku_scs", 0.40)])]
    with patch.object(main, "_product_names",
                      return_value={"sku_scs": "SMART Coin System", "nv4000": "NV4000",
                                    "cashgenic": "CashGenic"}),          patch.object(main, "_product_aliases", return_value={}),          patch.object(clarify, "classify_unscoped", return_value="product"):
        out = _ask(q="a smart coin system and an nv4000 on one 24V rail, which one pulls more max current?",
                   rerank=chunks, retrieve_from_db=chunks)
    assert out["role"] != "clarify", out.get("role")
    assert "which part" not in out["answer"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
