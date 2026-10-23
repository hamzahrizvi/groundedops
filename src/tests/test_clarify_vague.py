"""clarify.py: when to ask "which product?" (2026-09-26).

A fixed vocabulary stands in for the index, so these run without one.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clarify  # noqa: E402

ALL = {"bv30", "nv9usb", "nv200s", "sku_scs", "mycheckr", "mycheckr_mini", "myconnect"}
VOCAB = {
    "machine": set(ALL), "error": set(ALL), "turn": set(ALL), "power": set(ALL),
    "red": {"bv30", "nv9usb", "nv200s", "sku_scs"}, "flashing": {"bv30", "nv9usb", "nv200s", "sku_scs"},
    "age": {"mycheckr", "mycheckr_mini", "myconnect"}, "limit": {"mycheckr", "mycheckr_mini", "myconnect", "sku_scs"},
    "reset": set(ALL), "password": {"mycheckr", "mycheckr_mini"}, "router": {"myconnect"},
    "recommend": {"nv200s"}, "good": set(ALL), "laptop": {"myconnect"}, "life": set(ALL),
}


def ask(q, top=0.0):
    return clarify.unscoped_clarify_products(q, top, vocab=VOCAB)


def test_vague_questions_about_a_device_are_asked():
    assert ask("It won't turn on, help?") == sorted(ALL)
    assert ask("My machine's showing an error, can you tell me what it means", 0.002) == sorted(ALL)
    assert ask("How do I set the age limit?", 0.094) == ["myconnect", "mycheckr", "mycheckr_mini"] \
        or set(ask("How do I set the age limit?", 0.094)) == {"myconnect", "mycheckr", "mycheckr_mini"}
    assert set(ask("it keeps flashing red, whats wrong with it", 0.14)) == {"bv30", "nv9usb", "nv200s", "sku_scs"}


def test_off_topic_questions_are_not():
    assert ask("how do I reset the password on my Cisco router", 0.496) == []   # "cisco" is not manual vocabulary
    assert ask("can you recommend a good laptop") == []                         # shared by one product at most
    assert ask("what is the meaning of life") == []                            # no device, scored nothing
    assert ask("how do I cook pasta") == []
    assert ask("hello") == []


def test_one_shared_document_is_never_ambiguous_with_itself():
    shared = [{"source": "ICU_Network_API.pdf", "product": "mycheckr,mycheckr_mini", "rerank_score": 0.997},
              {"source": "ICU_Network_API.pdf", "product": "mycheckr,mycheckr_mini", "rerank_score": 0.99},
              {"source": "MyConnect.pdf", "product": "myconnect", "rerank_score": 0.40}]
    assert clarify.near_top_products(shared, 8, 0.997, 0.65) == []


def test_only_near_top_products_are_alternatives():
    rs = [{"source": "BV30.pdf", "product": "bv30", "rerank_score": 0.981},
          {"source": "NV9USB.pdf", "product": "nv9usb", "rerank_score": 0.964},
          {"source": "NV200S.pdf", "product": "nv200s", "rerank_score": 0.90},
          {"source": "NV9S.pdf", "product": "nv9_spectral", "rerank_score": 0.805},
          {"source": "SCS.pdf", "product": "sku_scs", "rerank_score": 0.177}]
    assert clarify.near_top_products(rs, 8, 0.981, 0.65) == ["bv30", "nv200s", "nv9usb"]
    # Below the confidence line the ordering is noise: keep the caller's span.
    assert clarify.near_top_products(rs, 8, 0.40, 0.65) is None


def test_many_products_are_offered_as_categories():
    cat = {"categories": [
        {"key": "note_val", "name": "Note Validators", "products": [{"key": k} for k in ("bv30", "nv9usb", "nv200s")]},
        {"key": "coin_hoppers", "name": "Coin Hoppers", "products": [{"key": "sku_scs"}]},
        {"key": "biometrics", "name": "Biometrics", "products": [{"key": k} for k in ("mycheckr", "mycheckr_mini", "myconnect")]}]}
    opts = clarify.options_for(sorted(ALL), cat, {})
    assert [o["key"] for o in opts] == ["note_val", "coin_hoppers", "biometrics"]
    few = clarify.options_for(["mycheckr", "mycheckr_mini"], cat, {"mycheckr": "MyCheckr", "mycheckr_mini": "MyCheckr mini"})
    assert few == [{"key": "mycheckr", "label": "MyCheckr"}, {"key": "mycheckr_mini", "label": "MyCheckr mini"}]


def test_the_pipeline_asks_only_when_unscoped():
    import inspect, re
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
               encoding="utf-8").read()
    i = src.index("_vague_products: list[str] = []")
    assert "not payload.product and not payload.category" in src[i:i + 300]
    assert "has_domain_vocabulary(q)" not in src


def test_the_classifier_reads_one_word_and_fails_open_to_none():
    # llm needs `requests`, which CI's unit job does not install. A missing
    # optional package is a skip everywhere else in this suite; imported
    # inside the test, it was a failure.
    try:
        import llm
    except ModuleNotFoundError as exc:
        raise SkipTest(f"needs {exc.name}")  # noqa: F821 -- injected by the runner
    cat = {"categories": [{"key": "note_val", "name": "Note Validators",
                           "products": [{"key": "bv30", "name": "BV30"}, {"key": "nv_general", "name": "General"}]}]}
    seen = {}
    real = llm.generate_with_fallback
    try:
        def fake(role, prompt, **kw):
            seen["prompt"] = prompt
            return {"text": "PRODUCT", "provider": "deepseek"}
        llm.generate_with_fallback = fake
        assert clarify.classify_unscoped("it keeps beeping", cat) == "product"
        assert "BV30" in seen["prompt"] and "General" not in seen["prompt"]
        llm.generate_with_fallback = lambda role, prompt, **kw: {"text": "Other.", "provider": "deepseek"}
        assert clarify.classify_unscoped("my HP printer keeps jamming", cat) == "other"
        llm.generate_with_fallback = lambda role, prompt, **kw: {"text": "I was unable to generate a response.", "provider": "none"}
        assert clarify.classify_unscoped("anything", cat) is None
    finally:
        llm.generate_with_fallback = real


def test_candidate_products_for_the_buttons():
    assert set(clarify.candidate_products("how do I set the age limit", vocab=VOCAB)) == {"mycheckr", "mycheckr_mini", "myconnect"}
    assert clarify.candidate_products("it keeps beeping", vocab=VOCAB) == sorted(ALL)


def test_the_pipeline_asks_the_model_only_when_unscoped_and_weak():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
               encoding="utf-8").read()
    i = src.index("_unscoped_verdict = None")
    block = src[i:i + 500]
    assert "not payload.product and not payload.category" in block
    assert "top_score < AMBIGUOUS_CEILING" in block


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
