"""S29 performance mode: the product's documents as context (retrieval_db.product_bundle)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import retrieval_db as r  # noqa: E402


def _c(n, section, text, product="nv9usb", source="NV9USB+.pdf"):
    return {"id": f"{source}:x:{n}", "source": source, "section": section,
            "text": text, "product": product}


CHUNKS = [
    _c(2, "Protocols › Introduction", "SSP and eSSP ccTalk MDB Pulse"),
    _c(1, "Protocols › Introduction", "The NV9USB+ supports standard industry protocols."),
    _c(3, "Cleaning", "Use a mild detergent " * 20),
    _c(4, "", "Loose page footer"),
    _c(1, "Other", "Spectral text", product="nv9_spectral", source="NV9S.pdf"),
]


def _with_index(fn):
    old = r._get_bm25_index, r.get_collection
    r._get_bm25_index, r.get_collection = (lambda col: (None, CHUNKS)), (lambda: None)
    try:
        return fn()
    finally:
        r._get_bm25_index, r.get_collection = old


def test_whole_scope_in_document_order_when_it_fits():
    b = _with_index(lambda: r.product_bundle("nv9usb", [], budget=10**6))
    assert [c["id"].rsplit(":", 1)[1] for c in b] == ["1", "2", "3", "4"]


def test_over_budget_takes_whole_sections_in_rank_order():
    ranked = [CHUNKS[1]]       # only the intro ranked; its sibling list did not
    b = _with_index(lambda: r.product_bundle("nv9usb", ranked, budget=200))
    assert {c["id"] for c in b} == {CHUNKS[0]["id"], CHUNKS[1]["id"]}


def test_excluded_sources_stay_out():
    b = _with_index(lambda: r.product_bundle("nv9usb", [], excluded=("nv9usb+",)))
    assert b == []


def test_evidence_is_what_the_answer_drew_on():
    ev = r.bundle_evidence(CHUNKS[:4], "Yes, it supports SSP, eSSP, ccTalk, MDB and pulse.",
                           have=[CHUNKS[1]])
    assert ev and ev[0]["id"] == CHUNKS[0]["id"]
