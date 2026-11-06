"""D-20261019-17: two named products are answered, not asked "which part?"."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from text_utils import is_refusal, PARTIAL_REFUSAL  # noqa: E402 -- real module, before _harness stubs it
import _harness  # noqa: F401,E402
import main  # noqa: E402
import clarify  # noqa: E402
from test_pipeline_trace import _ask, CHUNK  # noqa: E402


def test_two_named_products_are_answered_not_asked_which_part():
    # The harness pins the band to "confident"; the real band calls a
    # middling score over 3+ sources "ambiguous" (C20261021e-37, 0.528).
    chunks = [dict(CHUNK, id=f"c{i}", source=s, product=p, rerank_score=sc)
              for i, (s, p, sc) in enumerate([("SCS.pdf", "sku_scs", 0.55),
                                              ("NV4000.pdf", "nv4000", 0.53),
                                              ("CashGenic.pdf", "cashgenic", 0.52),
                                              ("SCS.pdf", "sku_scs", 0.40)])]
    names = {"sku_scs": "SMART Coin System", "nv4000": "NV4000",
             "cashgenic": "CashGenic"}
    with patch.object(main, "_product_names", return_value=names), \
         patch.object(main, "_product_aliases", return_value={}), \
         patch.object(clarify, "classify_unscoped", return_value="product"), \
         patch.object(main, "retrieval_confidence_band", return_value="ambiguous"):
        out = _ask(q="a smart coin system and an nv4000 on one 24V rail, "
                     "which one pulls more max current?",
                   rerank=chunks, retrieve_from_db=chunks)
    assert out["role"] != "clarify", out.get("role")
    assert "which part" not in out["answer"]


def test_each_product_arm_rescues_only_what_the_joint_search_lacks():
    # C20261026-24: the ICU's arm's top three were pages the joint list held,
    # so its own page at #8 was never rescued. Every arm must offer new ones.
    joint = [dict(CHUNK, id=f"j{i}", source="MyCheckr.pdf", product="mycheckr",
                  rerank_score=0.9) for i in range(3)]
    calls = []

    def spy(sets, *a, **k):
        calls.append(sets)
        return [r for s in sets for r in s]

    names = {"mycheckr": "MyCheckr", "icu": "ICU Pro"}
    with patch.object(main, "_product_names", return_value=names), \
         patch.object(main, "_product_aliases", return_value={}), \
         patch.object(main, "retrieve_fused", return_value=joint), \
         patch.object(main, "fuse_ranked_results", side_effect=spy):
        _ask(q="what's the difference between the MyCheckr and the ICU Pro "
               "for network connection?",
             rerank=joint, retrieve_from_db=joint)
    assert calls, "a two-product turn fuses the per-product arms"
    have = {r["id"] for r in calls[0][0]}
    assert all(r["id"] not in have for arm in calls[0][1:] for r in arm), calls[0]


def _mid_band(q, product, names):
    srcs = [("NV200 Range User Manual.txt", 0.59), ("NV200 Spectral Manual v2.4.pdf", 0.55),
            ("NV200S Range User Manual-v1.pdf", 0.53), ("Calibration Media Type.txt", 0.40)]
    chunks = [dict(CHUNK, id=f"c{i}", source=s, product=product, rerank_score=sc)
              for i, (s, sc) in enumerate(srcs)]
    with patch.object(main, "_product_names", return_value=names),          patch.object(main, "_product_aliases", return_value={}),          patch.object(clarify, "classify_unscoped", return_value="product"),          patch.object(main, "retrieval_confidence_band", return_value="ambiguous"):
        return _ask(q=q, rerank=chunks, retrieve_from_db=chunks)


def test_a_comparison_within_one_product_is_answered_not_asked_which_part():
    out = _mid_band("whats the difference in calibration media between the NV200 "
                    "and the NV200S?", "nv200s", {"nv200s": "NV200S"})
    assert out["role"] != "clarify", out.get("role")
    assert "few different sections" not in out["answer"]


def test_two_asks_in_the_middling_band_are_answered():
    out = _mid_band("where do I get the dataset and how do I load it on the DA3 "
                    "so it changes the interface?", "da3", {"da3": "DA3"})
    assert out["role"] != "clarify", out.get("role")
    assert "few different sections" not in out["answer"]


def test_one_ask_in_the_middling_band_still_gets_the_section_list():
    out = _mid_band("where is the calibration media kept?", "nv200s",
                    {"nv200s": "NV200S"})
    assert out["role"] == "clarify", out.get("role")
    assert "few different sections" in out["answer"]


def test_a_two_product_answer_that_finds_one_half_keeps_it():
    chunks = [dict(CHUNK, id=f"c{i}", source=f"{p}.pdf", product=p, rerank_score=0.9)
              for i, p in enumerate(["nv200s", "nv9_spectral"])]
    names = {"nv200s": "NV200 Spectral", "nv9_spectral": "NV9 Spectral"}
    text = ("The NV9 Spectral has a minimum operating temperature of +5 C (37.4 F) "
            "and a maximum of +50 C. I could not find that in the knowledge base.")
    with patch.object(main, "_product_names", return_value=names),          patch.object(main, "_product_aliases", return_value={}),          patch.object(main, "is_refusal", is_refusal),          patch.object(sys.modules["text_utils"], "is_refusal", is_refusal, create=True):
        out = _ask(q="which can go lower in temp, the nv200 spectral or the nv9 spectral?",
                   rerank=chunks, retrieve_from_db=chunks,
                   generate_with_fallback={"text": text, "model": "mistral",
                                           "provider": "local"})
    assert "+5" in out["answer"], out["answer"]
    assert PARTIAL_REFUSAL in out["answer"]
    assert not is_refusal(out["answer"])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
