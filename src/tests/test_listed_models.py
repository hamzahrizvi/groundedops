"""A product named for several models answers to each model's name.

General test 2026-11-11 (M1/M4): the manual filed as "Surface Pro and Surface
Pro 2" matched no product when a question said "Surface Pro 2", so a Surface
Pro 2 vs Surface Pro 3 comparison searched only the Pro 3 and refused.

Imports main, so the suite runs it in its own process; no pytest fixtures.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["CATALOG_CONFIG"] = os.path.join(tempfile.mkdtemp(), "catalog.json")
with open(os.environ["CATALOG_CONFIG"], "w") as fh:
    json.dump({"categories": [{"key": "tablets", "name": "Tablets", "products": [
        {"key": "surface_pro", "name": "Surface Pro and Surface Pro 2", "sources": []},
        {"key": "surface_pro_3", "name": "Surface Pro 3", "sources": []},
        {"key": "grinder", "name": "Salt and Pepper Grinder", "sources": []}]}]}, fh)

import main  # noqa: E402


def test_each_listed_model_is_a_name():
    assert main._listed_models("Surface Pro and Surface Pro 2") == ["Surface Pro", "Surface Pro 2"]
    assert main._listed_models("WATCH GT 5 and GT 5 Pro") == ["WATCH GT 5", "GT 5 Pro"]
    assert main._listed_models("X100 / X200") == ["X100", "X200"]


def test_a_plain_name_is_not_split():
    assert main._listed_models("Salt and Pepper Grinder") == []
    assert main._listed_models("Model 3") == []


def test_the_catalogue_aliases_carry_them():
    al = main._product_aliases()
    assert "Surface Pro 2" in al["surface_pro"], al
    assert al["grinder"] == [], al


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
