"""/figure refuses a marked document's crops when asked by its Windows 8.3
short name, which resolves to the same folder (D-20261024-08)."""
import ctypes
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
MARKED, PUBLIC = "Internal Pricing List.pdf", "Public Doc List.pdf"
dirs = {}
for doc in (MARKED, PUBLIC):
    d = os.path.join(docstore.store_dir(), figures.FIG_DIRNAME, doc)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "p1_1.png"), "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
    dirs[doc] = d
real_rdb.set_internal([MARKED], True)


def short_name(folder):
    if os.name != "nt":
        return ""
    buf = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetShortPathNameW(folder, buf, 1024):
        return ""
    base = os.path.basename(buf.value)
    return "" if base.lower() == os.path.basename(folder).lower() else base


short = short_name(dirs[MARKED])
if not short:
    print("skipped: no 8.3 short names on this platform/volume")
    sys.exit(0)

client = TestClient(_harness.app)
get = lambda v: client.get(f"/figure/{quote(v, safe='')}/p1_1.png").status_code  # noqa: E731
assert get(MARKED) == 404, "long name of marked doc"
assert get(short) == 404, ("short name of marked doc", short)
assert get(PUBLIC) == 200, "public doc"
pshort = short_name(dirs[PUBLIC])
if pshort:
    assert get(pshort) == 200, ("short name of public doc", pshort)
print("PASS figure short names")
