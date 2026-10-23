"""Every range has a "<range>_general" entry for documents shared by all its
products, even a catalogue saved without one (Coin Hoppers, 2026-10-23), and
it cannot be deleted."""
# run-in-own-process: sets CATALOG_CONFIG at import
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TMP = tempfile.mkdtemp()
os.environ["CATALOG_CONFIG"] = os.path.join(_TMP, "catalog.json")
with open(os.environ["CATALOG_CONFIG"], "w", encoding="utf-8") as f:
    json.dump({"categories": [{"key": "coin_hoppers", "name": "Coin Hoppers", "products": [
        {"key": "smart_hopper_3", "name": "Smart Hopper 3", "sources": []}]}]}, f)

import catalog  # noqa: E402


def test_a_range_without_one_gets_its_shared_entry():
    keys = [p["key"] for p in catalog.catalog()["categories"][0]["products"]]
    assert keys == ["coin_hoppers_general", "smart_hopper_3"], keys


def test_the_shared_entry_cannot_be_deleted():
    try:
        catalog.delete_product("coin_hoppers", "coin_hoppers_general", delete_content=True)
    except ValueError as e:
        assert "cannot be deleted" in str(e)
    else:
        raise AssertionError("deleted the range's shared entry")
