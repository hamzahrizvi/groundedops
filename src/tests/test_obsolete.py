"""Obsolete products (CEO 2026-11-05): ticked in the console, their answers
blocked from the date, the reply gives the dates and points to support.

Imports main, so the suite runs it in its own process; no pytest fixtures.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_dir = tempfile.mkdtemp()
os.environ["CATALOG_CONFIG"] = os.path.join(_dir, "catalog.json")
os.environ["FAMILIES_CONFIG"] = os.path.join(_dir, "families.json")
with open(os.environ["CATALOG_CONFIG"], "w") as fh:
    json.dump({"categories": [{"key": "coin", "name": "Coin", "products": [
        {"key": "smart_hopper_3", "name": "Smart Hopper 3", "sources": []},
        {"key": "sku_scs", "name": "SMART Coin System", "sources": []},
        {"key": "bv20", "name": "BV20", "sources": []}]}]}, fh)

import catalog  # noqa: E402
import main  # noqa: E402


def test_a_tick_blocks_from_its_date_and_is_checked():
    catalog.set_obsolete("smart_hopper_3", {"date": "2025-12", "service_end": "2099-12",
                                            "replacement": "SMART Hopper 4"})
    catalog.set_obsolete("bv20", {"date": "2099-01"})          # not yet
    assert catalog.blocked_products() == {"smart_hopper_3"}
    assert catalog.obsolete_of("bv20") is None
    assert catalog.obsolete_of("smart_hopper_3")["name"] == "Smart Hopper 3"
    for bad in ({"date": ""}, {"date": "Dec 2025"}, {"date": "2025-13"}):
        try:
            catalog.set_obsolete("bv20", bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    catalog.set_obsolete("bv20", None)
    assert "obsolete" not in catalog._product_by_key(catalog._load(), "bv20")


def test_its_documents_answer_nobody_unless_shared():
    catalog.set_obsolete("smart_hopper_3", {"date": "2025-12"})
    kept = main._drop_hidden([{"source": "a.pdf", "product": "smart_hopper_3"},
                              {"source": "b.pdf", "product": "smart_hopper_3,sku_scs"},
                              {"source": "c.pdf", "product": "sku_scs"},
                              {"source": "d.pdf", "product": ""}])
    assert [r["source"] for r in kept] == ["b.pdf", "c.pdf", "d.pdf"]
    # Answers left on: its documents answer as before.
    catalog.set_obsolete("smart_hopper_3", {"date": "2025-12", "block_answers": False})
    assert len(main._drop_hidden([{"source": "a.pdf", "product": "smart_hopper_3"}])) == 1


def test_the_reply_gives_the_dates_and_support():
    catalog.set_obsolete("smart_hopper_3", {"date": "2025-12", "service_end": "2099-12",
                                            "replacement": "SMART Hopper 4"})
    r = main._obsolete_reply("how do I reset it", ["smart_hopper_3"], "rid", 0.0, "s1")
    assert r["reason"] == "obsolete_product" and r["offer_support"], r
    assert r["answer"].startswith("The Smart Hopper 3 was made obsolete in December 2025; "
                                  "service and repairs continue until December 2099."), r["answer"]
    assert "replacement is the SMART Hopper 4" in r["answer"] and "support team" in r["answer"]
    # A comparison with a current product is answered.
    assert main._obsolete_reply("q", ["smart_hopper_3", "sku_scs"], "rid", 0.0, "s1") is None
    assert main._obsolete_reply("q", [], "rid", 0.0, "s1") is None


def test_confluence_import_follows_the_setting():
    import confluence_sync
    catalog.set_obsolete("smart_hopper_3", {"date": "2025-12", "confluence_import": False})
    assert confluence_sync.import_blocked("smart_hopper_3")
    assert not confluence_sync.import_blocked("smart_hopper_3,sku_scs")
    assert not confluence_sync.import_blocked("")


def test_the_family_card_says_it_is_obsolete():
    import families
    catalog.set_obsolete("smart_hopper_3", {"date": "2025-12", "replacement": "SMART Hopper 4"})
    fam = next(f for f in families.families() if f["key"] == "smart_hopper")
    cards = {c["label"]: c for c in families.options(fam, [0, 1], "q")}
    assert cards["SMART Hopper 3"]["note"] == \
        "Obsolete since December 2025; replaced by the SMART Hopper 4.", cards
    assert cards["SMART Hopper 4"]["note"] == ""


def test_the_support_hub_list_is_read_as_suggestions():
    import obsolete_list
    page = ("x.txt", "SMART Hopper\n<<<GO_TABLE>>>\n"
            "Product Name | Label Identifier | Dataset Code | Manufactured Date | "
            "Software Obsolescence Date | Service And Repairs Obsolescence Date\n"
            "SMART Hopper | BR29 & Below | XXX010 | Before 2017 | June 2021\n"
            "SMART Hopper 3 | Firmware SH0003 | XXX01M | Before 2023 | Dec 2025 | Dec 2027\n"
            "<<</GO_TABLE>>>\nBV20\n<<<GO_TABLE>>>\n"
            "Product Name | Label Identifier | Dataset Code | Manufactured Date | Obsolescence Date\n"
            "BV20 | All | All | All | Sept 2019\n<<</GO_TABLE>>>")
    got = {s["product_key"]: s["obsolete"] for s in obsolete_list.suggestions(catalog.catalog(), page)}
    assert set(got) == {"smart_hopper_3", "bv20"}, got   # "SMART Hopper" is no product's name
    assert got["smart_hopper_3"]["date"] == "2025-12" and got["smart_hopper_3"]["service_end"] == "2027-12"
    assert not got["smart_hopper_3"]["block_answers"]      # only some units
    assert got["bv20"] == dict(got["bv20"], date="2019-09", versions="", block_answers=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
