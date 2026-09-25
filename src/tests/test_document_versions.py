"""Document replacement and freshness must be explicit and recoverable."""
import tempfile
from pathlib import Path
from unittest.mock import patch

import docstore
import ingest


class _Vector:
    def __init__(self, value):
        self.value = value

    def tolist(self):
        return self.value


class _Collection:
    def __init__(self):
        self.rows = {}

    def get(self, where=None, include=None):
        rows = list(self.rows.items())
        if where and "source" in where:
            rows = [(key, value) for key, value in rows
                    if value[1].get("source") == where["source"]]
        return {
            "ids": [key for key, _ in rows],
            "documents": [value[0] for _, value in rows],
            "metadatas": [value[1] for _, value in rows],
        }

    def add(self, ids, documents, embeddings, metadatas):
        for key, text, meta in zip(ids, documents, metadatas):
            if key in self.rows:
                raise ValueError("duplicate id")
            self.rows[key] = (text, meta)

    def delete(self, ids):
        for key in ids:
            self.rows.pop(key, None)


def test_same_name_replacement_is_versioned_and_atomic():
    collection = _Collection()
    old = b"old manual"
    new = b"new manual"

    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(docstore, "store_dir", return_value=tmp), \
         patch.object(docstore, "read_dirs", return_value=[tmp]), \
         patch.object(ingest, "get_collection", return_value=collection), \
         patch.object(ingest, "extract_pages",
                      return_value=[(1, "USB support and setup instructions")]), \
         patch.object(ingest, "embed_texts",
                      side_effect=lambda texts: [_Vector([0.1, 0.2]) for _ in texts]), \
         patch.object(ingest, "_dedupe_enabled", return_value=False), \
         patch.object(ingest, "invalidate_retrieval_cache") as invalidate:
        assert ingest.ingest_file(old, "Manual.txt") == 1
        old_id = next(iter(collection.rows))
        assert docstore.sha256(old)[:16] in old_id

        # A normal duplicate upload does not alter the durable source.
        assert ingest.ingest_file(new, "Manual.txt") == 0
        assert Path(tmp, "Manual.txt").read_bytes() == old

        assert ingest.ingest_file(new, "Manual.txt", replace_existing=True) == 1
        assert len(collection.rows) == 1
        new_id = next(iter(collection.rows))
        assert new_id != old_id
        assert docstore.sha256(new)[:16] in new_id
        assert Path(tmp, "Manual.txt").read_bytes() == new

        manifest = docstore.load_manifest()["documents"]["Manual.txt"]
        assert manifest["version"] == docstore.sha256(new)
        assert manifest["history"][-1]["sha256"] == docstore.sha256(old)
        status = docstore.freshness("Manual.txt", docstore.sha256(new))
        assert status["status"] == "current"
        assert invalidate.call_count == 2


def test_freshness_detects_index_and_original_drift():
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(docstore, "store_dir", return_value=tmp), \
         patch.object(docstore, "read_dirs", return_value=[tmp]), \
         patch.object(docstore, "current_settings", return_value={"parser": "test"}):
        docstore.save("Manual.txt", b"recorded")
        docstore.record("Manual.txt", content=b"recorded",
                        settings={"parser": "test"})
        Path(tmp, "Manual.txt").write_bytes(b"changed outside ingestion")

        status = docstore.freshness("Manual.txt", "wrong-index-version")
        assert status["status"] == "stale"
        assert "original_changed" in status["reasons"]
        assert "index_version_mismatch" in status["reasons"]
