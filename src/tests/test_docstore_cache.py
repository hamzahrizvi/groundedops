"""docstore reuses its folder listing and parsed manifest between calls (the
console's document list asks once per document). A new file or a manifest
write must still show on the very next call."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import docstore  # noqa: E402


def test_new_files_and_manifest_writes_show_at_once():
    with tempfile.TemporaryDirectory() as d, \
            patch.dict(os.environ, {"SOURCE_FILE_DIR": d}), \
            patch.object(docstore, "LEGACY_DIR", os.path.join(d, "none")):
        assert docstore.find("a.pdf") is None
        assert docstore.ocr_state("a.pdf")["pending"] == []
        docstore.save("a.pdf", b"one")
        assert docstore.find("a.pdf") == os.path.join(d, "a.pdf")
        docstore.record("a.pdf", content=b"one", ocr={"candidates": [3], "done": []})
        assert docstore.ocr_state("a.pdf")["pending"] == [3]
        assert docstore.freshness("a.pdf")["version"] == docstore.sha256(b"one")
        docstore.record("a.pdf", ocr={"candidates": [3], "done": [3]})
        assert docstore.ocr_state("a.pdf")["pending"] == []
        docstore.delete("a.pdf")
        assert docstore.find("a.pdf") is None
        assert docstore.freshness("a.pdf")["reasons"][:2] == ["original_missing", "manifest_missing"]


if __name__ == "__main__":
    test_new_files_and_manifest_writes_show_at_once()
    print("ok")
