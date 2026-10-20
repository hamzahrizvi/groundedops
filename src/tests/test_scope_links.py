"""catalog.scope_links: a product whose manual keeps naming another product is
offered that product's manuals; a passing mention, or manuals already filed
under it or shared to its category, is not."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import catalog  # noqa: E402

TREE = {"categories": [{"key": "nv", "name": "Note validators", "products": [
    {"key": "nv_general", "name": "General", "sources": []},
    {"key": "kit", "name": "CashKit", "sources": []},
    {"key": "head", "name": "NoteHead", "sources": []},
    {"key": "coins", "name": "CoinBox", "sources": []},
]}]}
NAMES = {"kit": "CashKit", "head": "NoteHead", "coins": "CoinBox", "nv_general": "General"}


def named_in(text, candidates):   # stands in for main._products_named_in
    return [k for k in candidates if NAMES[k].lower() in text.lower()]


def test_scope_links_suggests_a_component_and_skips_passing_mentions():
    with tempfile.TemporaryDirectory() as tmp:
        old = catalog._PATH
        catalog._PATH = os.path.join(tmp, "catalog_config.json")
        try:
            catalog._save(TREE)
            chunks = ([("kit.pdf", ["kit"], "nv", "the NoteHead inside the CashKit")] * 6
                      + [("kit.pdf", ["kit"], "nv", "the CashKit lid")] * 4
                      + [("head.pdf", ["head"], "nv", "NoteHead bezel")] * 50
                      + [("head.pdf", ["head"], "nv", "works beside a CoinBox")] * 2
                      # shared to the category: every product's chats search it already
                      + [("head_api.pdf", ["head", "nv_general"], "nv", "NoteHead protocol")] * 3
                      + [("coins.pdf", ["coins"], "nv", "CoinBox hopper")] * 3)
            got = catalog.scope_links(chunks, named_in)
            assert [(s["product"], s["other"], s["hits"], s["chunks"]) for s in got] == [("kit", "head", 6, 10)]
            # the action keeps the source's existing filing and adds the product
            assert got[0]["sources"] == [{"source": "head.pdf", "category": "nv",
                                          "keys": ["head", "kit"]}]
            assert got[0]["product_name"] == "CashKit" and got[0]["other_name"] == "NoteHead"
            # once filed under the kit, there is nothing left to suggest
            filed = [(s, k + ["kit"] if s == "head.pdf" else k, c, t) for s, k, c, t in chunks]
            assert catalog.scope_links(filed, named_in) == []
        finally:
            catalog._PATH = old
            catalog._invalidate_derived()
