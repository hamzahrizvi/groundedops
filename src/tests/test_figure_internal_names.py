"""/figure refuses a marked document's crops in every spelling Windows
resolves to its folder (D-20261024-01), and still serves a public one."""
import os
import sys
import tempfile
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["INTERNAL_SOURCES_PATH"] = os.path.join(tempfile.mkdtemp(), "internal_sources.json")
import retrieval_db as real_rdb  # noqa: E402  the real rule, before _harness stubs the module

import _harness  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import docstore  # noqa: E402
import figures  # noqa: E402

sys.modules["retrieval_db"].is_excluded = real_rdb.is_excluded   # routes import it at call time
for doc in ("Pricing.pdf", "Public.pdf"):
    d = os.path.join(docstore.store_dir(), figures.FIG_DIRNAME, doc)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "p1_1.png"), "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
real_rdb.set_internal(["Pricing.pdf"], True)

client = TestClient(_harness.app)
assert client.get("/figure/Public.pdf/p1_1.png").status_code == 200   # the test can see a crop at all
for v in ("Pricing.pdf", "PRICING.PDF", "Pricing.pdf.", "Pricing.pdf ",
          "Pricing.pdf::$INDEX_ALLOCATION"):
    r = client.get(f"/figure/{quote(v, safe='')}/p1_1.png")
    assert r.status_code == 404, (v, r.status_code)
real_rdb.set_internal(["Pricing.pdf"], False)
assert client.get("/figure/Pricing.pdf/p1_1.png").status_code == 200   # unmarking serves it again
print("PASS figure internal names")
