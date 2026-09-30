"""A failed rebuild restores the collection that was serving beforehand."""
import tempfile
import sys
import types
from pathlib import Path
from unittest.mock import patch

import reindex


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
