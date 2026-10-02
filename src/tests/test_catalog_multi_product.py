"""A document filed under several products keeps all of them through the
catalogue and a rebuild (9.7 decision 1)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import catalog  # noqa: E402
import reindex  # noqa: E402

TREE = {"categories": [
    {"key": "bio", "name": "Biometrics", "products": [
        {"key": "bio_general", "name": "General", "sources": ["api.pdf"]},
        {"key": "mc", "name": "MyCheckr", "sources": ["manual.pdf"]},
        {"key": "mini", "name": "Mini", "sources": []},
    ]},
]}


def _with_temp_catalogue(fn):
    with tempfile.TemporaryDirectory() as tmp:
        old = catalog._PATH
        catalog._PATH = os.path.join(tmp, "catalog_config.json")
        try:
            catalog._save(dict(TREE, categories=[dict(c, products=[dict(p, sources=list(p["sources"]))
                                                                  for p in c["products"]])
                                                 for c in TREE["categories"]]))
            fn()
        finally:
            catalog._PATH = old
            catalog._invalidate_derived()


def test_refile_lists_the_source_under_every_product_and_nowhere_else():
    def body():
        catalog.refile_source("manual.pdf", ["mc", "mini", "bio_general"])
        assign = reindex.catalog_assignment()
        assert assign["manual.pdf"] == ("bio", "bio_general,mc,mini")
        assert assign["api.pdf"] == ("bio", "bio_general")
        catalog.refile_source("manual.pdf", ["mini"])
        assert reindex.catalog_assignment()["manual.pdf"] == ("bio", "mini")
        catalog.refile_source("manual.pdf", [])
        assert "manual.pdf" not in reindex.catalog_assignment()
    _with_temp_catalogue(body)


def test_attach_accepts_comma_joined_keys_like_ingest_does():
    def body():
        catalog.attach_source("bio", "mc,mini", "guide.pdf")
        assert reindex.catalog_assignment()["guide.pdf"] == ("bio", "mc,mini")
        try:
            catalog.attach_source("bio", "mc,nope", "x.pdf")
            assert False, "an unknown product must be refused"
        except ValueError:
            pass
    _with_temp_catalogue(body)
