"""Pins for the five findings deferred on 2026-09-25 (PENDING.md).

Each test names the plausible input or outage that used to go wrong.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_deep_effort_keeps_the_safety_nets():
    """The widget's "deep" effort forces a model but is NOT the console's
    rethink: extraction, escalation and the grounding retry stay on."""
    import inspect
    import main
    src = inspect.getsource(main.query)
    assert "and not payload.forced_by_effort" in src
    assert "elif payload.force_provider and payload.force_model:" in src
    assert "req.forced_by_effort = True" in inspect.getsource(main._widget_answer) \
        if hasattr(main, "_widget_answer") else True
    assert main.QueryRequest(q="x").forced_by_effort is False
    assert main.QueryRequest(q="x", top_k=10).top_k == 10


def test_a_reranker_outage_is_reported_as_one():
    """Without rerank scores every passage read as 0.0 and the turn was
    refused as "nothing relevant", recording a documentation gap."""
    import reranker
    real = reranker._get
    reranker._get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no model"))
    try:
        out = reranker.rerank("q", [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}], top_k=2)
        assert out and all(c.get("rerank_failed") for c in out)
        assert not any("rerank_score" in c for c in out)
    finally:
        reranker._get = real
    import inspect
    import main
    assert 'status_code=503' in inspect.getsource(main.query).split("rerank_failed")[1][:800]


def test_a_provider_call_has_an_overall_deadline():
    """requests' timeout bounds each read; a provider that keeps the socket
    alive could hold a call open for hours. The deadline ends the wait."""
    import llm

    def slow_post(url, **kw):
        time.sleep(2)
        raise AssertionError("should not be reached inside the deadline")

    real_post, real_deadline = llm._HTTP.post, llm.PROVIDER_DEADLINE_SECONDS
    llm._HTTP.post = slow_post
    llm.PROVIDER_DEADLINE_SECONDS = 0.3
    llm._provider_down.clear()
    try:
        t = time.time()
        out = llm._call_openai("q", model="m", api_key="k")
        assert out is None and time.time() - t < 1.5
        assert llm.provider_cooling("openai")
    finally:
        llm._HTTP.post, llm.PROVIDER_DEADLINE_SECONDS = real_post, real_deadline
        llm._provider_down.clear()


def test_a_topic_inside_a_document_is_not_a_document_request():
    import doc_request
    names = ["NV9 Spectral", "nv9_spectral", "NV9S", "NV9USB+", "nv9usb", "Note Validators",
             "MyCheckr", "mycheckr", "SMART Coin System", "SCS"]
    assert doc_request.document_request("is there documentation on the MDB pinout?", names) is None
    assert doc_request.document_request("can I have the manual for the RS232 settings", names) is None
    assert doc_request.document_request("do you have a guide on how to install it", names) is None
    for q in ("can I have the NV9 manual", "can I have the manual for the NV9S please",
              "is there a datasheet for the SMART Coin System",
              "do you have the MyCheckr user guide"):
        assert doc_request.document_request(q, names), q
    # Without names the old behaviour stands.
    assert doc_request.document_request("can I have the NV9 manual")


def test_faq_candidates_must_share_a_content_word_with_the_question():
    """Three entries about the same PRODUCT and a different QUESTION used to
    be offered as a menu; one close paraphrase is served, not offered."""
    import faq_store
    pool = {
        "a": "What are typical applications for the NV9USB+?",
        "b": "What is the NV9USB+ Range?",
        "c": "Can the NV9USB+ Range provide note recycling?",
        "d": "Can the NV9USB+ be mounted in different orientations?",
        "e": "What is the operating speed of the SMART Coin System?",
        "f": "What does the SMART Coin System eliminate?",
    }
    entries = [{"id": k, "question": v, "answer": "x", "origin": "curated"} for k, v in pool.items()]
    real = (faq_store.list_for_product, faq_store._semantic_scores, faq_store._product_terms,
            faq_store.record_gap)
    faq_store.list_for_product = lambda scope: entries
    faq_store._product_terms = lambda: {"nv9usb", "nv9", "usb", "smart", "coin", "system", "scs", "range"}
    faq_store.record_gap = lambda *a, **k: None
    try:
        faq_store._semantic_scores = lambda q, items: {"a": 0.93, "b": 0.911, "c": 0.5, "d": 0.5, "e": 0.5, "f": 0.5}
        out = faq_store.suggest_candidates("How does the NV9USB+ communicate with a host?", "nv9usb")
        assert out["mode"] == "none", out
        faq_store._semantic_scores = lambda q, items: {"c": 0.927, "d": 0.834, "a": 0.833, "b": 0.5, "e": 0.5, "f": 0.5}
        out = faq_store.suggest_candidates("Can the NV9USB+ recycle notes?", "nv9usb")
        assert out["mode"] == "answer" and out["entry"]["id"] == "c", out
        faq_store._semantic_scores = lambda q, items: {"e": 0.974, "f": 0.934, "a": 0.5, "b": 0.5, "c": 0.5, "d": 0.5}
        out = faq_store.suggest_candidates("How many coins per second does the SMART Coin System handle?", "sku_scs")
        assert out["mode"] == "answer" and out["entry"]["id"] == "e", out
        # Two real candidates stay a choice.
        faq_store._semantic_scores = lambda q, items: {"c": 0.93, "d": 0.93, "a": 0.5, "b": 0.5, "e": 0.5, "f": 0.5}
        out = faq_store.suggest_candidates("Can the NV9USB+ recycle notes when mounted vertically?", "nv9usb")
        assert out["mode"] == "disambiguate" and {c["id"] for c in out["candidates"]} == {"c", "d"}, out
    finally:
        (faq_store.list_for_product, faq_store._semantic_scores, faq_store._product_terms,
         faq_store.record_gap) = real


def test_a_family_name_does_not_override_the_selected_product():
    import main
    real_names, real_aliases = main._product_names, main._product_aliases
    main._product_names = lambda: {"mycheckr": "MyCheckr", "mycheckr_mini": "MyCheckr mini",
                                   "nv9usb": "NV9USB+", "bv30": "BV30"}
    main._product_aliases = lambda: {"mycheckr_mini": ["MyCheckr Mini"]}
    try:
        assert main._is_family_parent("mycheckr", "mycheckr_mini")
        assert not main._is_family_parent("mycheckr_mini", "mycheckr")
        assert not main._is_family_parent("nv9usb", "mycheckr_mini")
        scope, eff = main._resolve_question_scope("mycheckr_mini", None, ["mycheckr"])
        assert eff == "mycheckr_mini", (scope, eff)
        scope, eff = main._resolve_question_scope("mycheckr_mini", None, ["nv9usb"])
        assert eff == "nv9usb", (scope, eff)
        scope, eff = main._resolve_question_scope(None, None, ["mycheckr"])
        assert eff == "mycheckr", (scope, eff)
    finally:
        main._product_names, main._product_aliases = real_names, real_aliases


def test_a_curated_answer_beats_the_commercial_deflect():
    import inspect
    import main
    src = inspect.getsource(main._sales_answer)
    assert src.index('record=False).get("mode") == "answer"') < src.index('policy.value("sales_mode")')


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
