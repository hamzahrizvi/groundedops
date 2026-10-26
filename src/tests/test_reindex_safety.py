"""A failed rebuild restores the collection that was serving beforehand."""
import os
import tempfile
import sys
import types
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("INTERNAL_SOURCES_PATH",
                      os.path.join(tempfile.mkdtemp(), "internal_sources.json"))
import retrieval_db  # noqa: E402,F401  loaded with the real db before the tests stub it
import reindex  # noqa: E402


def test_snapshot_accepts_numpy_embeddings():
    import numpy as np

    class Col:
        def get(self, include):
            return {"ids": ["a", "b"], "documents": ["x", "y"],
                    "metadatas": [{}, {}],
                    "embeddings": np.array([[0.1, 0.2], [0.3, 0.4]])}

    fake_db = types.ModuleType("db")
    fake_db.get_collection = lambda: Col()
    with patch.dict(sys.modules, {"db": fake_db}):
        snap = reindex.snapshot_collection()
    assert snap["embeddings"] == [[0.1, 0.2], [0.3, 0.4]]
    assert all(isinstance(v, float) for row in snap["embeddings"] for v in row)


def test_failed_rebuild_rolls_back_previous_collection():
    snapshot = {
        "ids": ["old:1"], "documents": ["known good"],
        "metadatas": [{"source": "old.txt"}], "embeddings": [[0.1]],
    }
    restored = []

    with tempfile.TemporaryDirectory() as tmp:
        first = Path(tmp, "first.txt")
        second = Path(tmp, "second.txt")
        first.write_text("one", encoding="utf-8")
        second.write_text("two", encoding="utf-8")
        targets = [
            {"source": "first.txt", "path": str(first), "chunks": 1},
            {"source": "second.txt", "path": str(second), "chunks": 1},
        ]

        fake_db = types.ModuleType("db")
        fake_db.reset_collection = lambda: None
        fake_ingest = types.ModuleType("ingest")
        fake_ingest.ingest_file = lambda *args, **kwargs: 0

        with patch.dict(sys.modules, {"db": fake_db, "ingest": fake_ingest}), \
             patch.object(reindex, "snapshot_collection", return_value=snapshot), \
             patch.object(reindex, "restore_collection",
                          side_effect=lambda value: restored.append(value) or 1), \
             patch.object(fake_ingest, "ingest_file", side_effect=[1, 0]):
            try:
                reindex.rebuild(targets, dry_run=False)
                assert False, "a zero-chunk document must abort a rebuild"
            except RuntimeError as exc:
                assert "produced no chunks" in str(exc)

    assert restored == [snapshot]


def test_from_store_with_only_refuses_before_rebuilding():
    # An empty pattern is still --only: it must not fall through to a full rebuild.
    for pattern in ("NV9*", ""):
        for extra in ([], ["--dry-run"]):
            argv = ["reindex.py", "--from-store", "--only", pattern] + extra
            with patch.object(sys, "argv", argv), \
                 patch.object(reindex, "from_store") as from_store, \
                 patch.object(reindex, "rebuild") as rebuild:
                assert reindex.main() == 2
            rebuild.assert_not_called()
            from_store.assert_not_called()


def test_from_store_without_only_still_rebuilds():
    target = {"source": "a.pdf", "path": "a.pdf", "chunks": 1,
              "category": "c", "product": "p", "filed": True}
    with patch.object(sys, "argv", ["reindex.py", "--from-store"]), \
         patch.object(reindex, "from_store", return_value=[target]), \
         patch.object(reindex, "rebuild") as rebuild:
        assert reindex.main() == 0
    rebuild.assert_called_once_with([target], False)


def test_an_internal_document_does_not_abort_a_rebuild():
    # A marked document is stored, never indexed (D-20261024-06): 0 chunks
    # with report["internal"] is expected, not a broken parse.
    def fake_ingest_file(content, source, report=None, **kw):
        if source == "internal.txt":
            report["internal"] = True
            return 0
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        targets = []
        for name in ("internal.txt", "public.txt"):
            Path(tmp, name).write_text(name, encoding="utf-8")
            targets.append({"source": name, "path": str(Path(tmp, name)), "chunks": 1})
        fake_db = types.ModuleType("db")
        fake_db.reset_collection = lambda: None
        fake_ingest = types.ModuleType("ingest")
        fake_ingest.ingest_file = fake_ingest_file
        with patch.dict(sys.modules, {"db": fake_db, "ingest": fake_ingest}), \
             patch.object(reindex, "snapshot_collection", return_value={"ids": []}), \
             patch.object(reindex, "restore_collection") as restore, \
             patch.object(reindex.docstore, "ocr_state", return_value={"done": []}):
            assert reindex.rebuild(targets, dry_run=False) == 1
        restore.assert_not_called()
