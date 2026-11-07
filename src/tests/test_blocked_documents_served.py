"""/figure and /source_file refuse a document filed only under blocked
obsolete products, and still serve one shared with a current product
(D-20261106-07)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["INTERNAL_SOURCES_PATH"] = os.path.join(tempfile.mkdtemp(), "internal_sources.json")
import retrieval_db as real_rdb  # noqa: E402

import _harness  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import catalog  # noqa: E402
import docstore  # noqa: E402
import figures  # noqa: E402

sys.modules["retrieval_db"].is_excluded = real_rdb.is_excluded
_harness.seed_doc("Old.pdf", ["x"], product="oldp")
_harness.seed_doc("Shared.pdf", ["x"], product="oldp,cur")
for doc in ("Old.pdf", "Shared.pdf"):
    d = os.path.join(docstore.store_dir(), figures.FIG_DIRNAME, doc)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "p1_1.png"), "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
    with open(os.path.join(docstore.store_dir(), doc), "wb") as fh:
        fh.write(b"original")

client = TestClient(_harness.app)
orig = catalog.blocked_products
try:
    assert client.get("/figure/Old.pdf/p1_1.png").status_code == 200
    assert client.get("/source_file/Old.pdf").status_code == 200
    catalog.blocked_products = lambda: {"oldp"}
    for url in ("/figure/Old.pdf/p1_1.png", "/figure/OLD.PDF/p1_1.png",
                "/figure/Old.pdf./p1_1.png", "/source_file/Old.pdf"):
        assert client.get(url).status_code == 404, url
    assert client.get("/figure/Shared.pdf/p1_1.png").status_code == 200
    assert client.get("/source_file/Shared.pdf").status_code == 200
finally:
    catalog.blocked_products = orig
assert client.get("/figure/Old.pdf/p1_1.png").status_code == 200
print("PASS blocked documents served")
