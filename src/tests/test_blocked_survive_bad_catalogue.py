"""A catalogue file that cannot be read keeps blocking what the last readable
one blocked (D-20261107-08)."""
import contextlib
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _harness  # noqa: E402,F401 -- temp CATALOG_CONFIG, light main
import catalog  # noqa: E402
import main  # noqa: E402
import visible  # noqa: E402


KEY = "oldp_manual"


@contextlib.contextmanager
def _own_catalogue():
    with tempfile.TemporaryDirectory() as tmp:
        old, old_rules = catalog._PATH, catalog._last_block_rules
        catalog._PATH = os.path.join(tmp, "catalog_config.json")
        catalog._last_block_rules = None
        try:
            catalog._save({"categories": [{"key": "c", "name": "C", "products": [
                {"key": KEY, "name": "Old", "sources": []}]}]})
            yield
        finally:
            catalog._PATH, catalog._last_block_rules = old, old_rules
            catalog._invalidate_derived()


def _shown(key):
    rows = [{"source": "old.pdf", "product": key}, {"source": "cur.pdf", "product": "cur_product"}]
    return [r["source"] for r in main._drop_hidden(rows)]


def _write(text):
    with open(catalog._PATH, "w", encoding="utf-8") as fh:
        fh.write(text)


def test_a_damaged_catalogue_keeps_the_last_blocked_set():
    with _own_catalogue():
        key = KEY
        catalog.set_obsolete(key, {"date": "2020-01"})
        with open(catalog._PATH, encoding="utf-8") as fh:
            good = fh.read()
        try:
            assert key in catalog.blocked_products()
            assert _shown(key) == ["cur.pdf"]
            _write("{")
            with patch.object(catalog.logger, "warning", wraps=catalog.logger.warning) as warn:
                assert key in catalog.blocked_products()           # from memory
                assert warn.called
            assert _shown(key) == ["cur.pdf"]
            assert not visible.may_show("old.pdf", key)
            assert visible.may_show("cur.pdf", "cur_product")
            catalog._last_block_rules = None                       # a restart
            assert key in catalog.blocked_products()               # from the .blocked copy
            assert not visible.may_show("old.pdf", key)
            _write(good)
            catalog.set_obsolete(key, None)                        # readable, block removed
            assert key not in catalog.blocked_products()
            assert _shown(key) == ["old.pdf", "cur.pdf"]
            _write("{")
            assert key not in catalog.blocked_products()           # last good set is now empty of it
        finally:
            _write(good)
            catalog.set_obsolete(key, None)


def test_visible_blocked_keeps_its_last_set_on_an_error():
    with patch.object(catalog, "blocked_products", return_value={"oldp"}):
        assert visible.blocked() == {"oldp"}
    with patch.object(catalog, "blocked_products", side_effect=RuntimeError("x")):
        assert visible.blocked() == {"oldp"}
    with patch.object(catalog, "blocked_products", return_value=set()):
        assert visible.blocked() == set()


if __name__ == "__main__":
    test_a_damaged_catalogue_keeps_the_last_blocked_set()
    test_visible_blocked_keeps_its_last_set_on_an_error()
    print("PASS blocked survive bad catalogue")
