"""The verifiers read the rest of each cited page, every passage labelled (D-20261015-02)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import retrieval_db as r  # noqa: E402


def _c(src, page, n, text="some text here"):
    return {"id": f"{src}:x:{n}", "source": src, "page": page, "text": text}


INDEX = [_c("A.pdf", 3, 3), _c("A.pdf", 3, 1, "first chunk"), _c("A.pdf", 3, 2),
         _c("A.pdf", 4, 4), _c("B.pdf", 3, 1), _c("A.pdf", None, 9)]


def _with_index(fn):
    old = r._get_bm25_index, r.get_collection
    r._get_bm25_index, r.get_collection = (lambda col: (None, INDEX)), (lambda: None)
    try:
        return fn()
    finally:
        r._get_bm25_index, r.get_collection = old


def test_cited_pages_adds_the_rest_of_each_cited_page_in_order_within_budget():
    cited = [_c("A.pdf", 3, 2)]
    out = _with_index(lambda: r.cited_pages(cited, budget=10**6))
    assert [c["id"] for c in out] == ["A.pdf:x:1", "A.pdf:x:3"]
    assert all(c["fetched_by"] == "cited_page" for c in out)
    assert _with_index(lambda: r.cited_pages(cited, budget=3)) == []
    assert _with_index(lambda: r.cited_pages([_c("A.pdf", None, 9)], budget=10**6)) == []
    # A .txt is all page 1: its "page" is the whole document, so none is added.
    global INDEX
    old, INDEX = INDEX, INDEX + [_c("T.txt", 1, 1), _c("T.txt", 1, 2)]
    try:
        assert _with_index(lambda: r.cited_pages([_c("T.txt", 1, 1)], budget=10**6)) == []
    finally:
        INDEX = old


def test_cited_pages_never_raises():
    old = r._get_bm25_index
    def boom(col):
        raise RuntimeError("no index")
    r._get_bm25_index = boom
    try:
        assert r.cited_pages([_c("A.pdf", 3, 2)], budget=100) == []
    finally:
        r._get_bm25_index = old


def _run_verifier(chunks):
    seen = []
    old = main.generate_with_fallback, main.llm_verify_enabled
    main.generate_with_fallback = lambda *a, **k: (
        seen.append(a[1]) or {"text": "SUPPORT: YES\nRELEVANCE: YES\nok"})
    main.llm_verify_enabled = lambda: True
    try:
        return main._llm_verified("The supply is +24 VDC.", chunks, None, "supply?"), seen
    finally:
        main.generate_with_fallback, main.llm_verify_enabled = old


def test_verifier_reads_each_passage_under_its_document_page_label():
    ok, seen = _run_verifier([
        {"text": "+24 VDC nominal", "source": "NV4000 Range User Manual-v2.pdf",
         "page": 20, "product": "nv4000"},
        {"text": "bare"}])
    prompt = seen[0]
    assert ok is True
    assert "[NV4000 Range User Manual-v2.pdf, page 20" in prompt
    assert "\n\nbare" in prompt
    assert "the product and document its passage's label names" in prompt


def test_verifier_fails_closed_on_labels_without_text():
    ok, seen = _run_verifier([{"text": "", "source": "X.pdf", "page": 1}])
    assert ok is False and seen == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
