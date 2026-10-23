"""10.2: OCR verified end to end, short of the live index.

  1. The route: POST /admin/ocr starts a job on the manifest's pending pages,
     the job runs ingest_ocr_pages, and /upload/status reports it done;
     an install without the engine gets 503, not a button that fails later.
  2. The engine: the real recogniser reads a page that is only pixels.
  3. The label: a chunk stored with ocr=True (test_ocr pins that ingest does
     this) is cited as "(OCR)" in the answer's sources.

ingest is stubbed by _harness, so the route's worker gets a stand-in; the
chunk-writing half is test_ocr.test_ocr_chunks_are_tagged_replaceable_and_recorded.
"""
import os
import sys
import tempfile
import time
from unittest.mock import patch

import _harness
from fastapi.testclient import TestClient

import docstore
import main
import ocr
import routes_documents

client = TestClient(_harness.app)
ADMIN = {"x-admin-password": _harness.SUPPORT_TOKEN}


def _text_pdf(path, lines):
    """A scan: the text exists only as pixels."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (1240, 1754), "white")
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default(size=48)
    d = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        d.text((120, 200 + i * 90), line, fill="black", font=font)
    im.save(path)


def test_route_runs_the_job_on_pending_pages():
    calls = []

    def fake_ocr(source, pages, progress=None):
        calls.append((source, pages))
        return len(pages)

    with patch.object(routes_documents, "_ocr_availability", return_value=(True, None)), \
         patch.object(docstore, "find", return_value="/x/Scan.pdf"), \
         patch.object(docstore, "ocr_state", return_value={"pending": [2, 3], "done": []}), \
         patch.object(sys.modules["ingest"], "ingest_ocr_pages", fake_ocr, create=True):
        r = client.post("/admin/ocr", json={"source": "Scan.pdf"}, headers=ADMIN)
        assert r.status_code == 200, r.text
        assert r.json()["pages"] == [2, 3]
        job = r.json()["job_id"]
        for _ in range(50):
            st = client.get(f"/upload/status/{job}", headers=ADMIN).json()
            if st.get("done"):
                break
            time.sleep(0.05)
    assert st["status"] == "done" and st["chunks_added"] == 2, st
    assert calls == [("Scan.pdf", [2, 3])]


def test_route_refuses_without_an_engine():
    with patch.object(routes_documents, "_ocr_availability",
                      return_value=(False, "No module named rapidocr")):
        r = client.post("/admin/ocr", json={"source": "Scan.pdf"}, headers=ADMIN)
    assert r.status_code == 503 and "rapidocr" in r.json()["detail"]


def test_real_engine_reads_a_scanned_page():
    ok, why = ocr.available()
    if not ok:
        print(f"SKIP real OCR engine: {why}")   # loud, not a silent pass
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Scan.pdf")
        _text_pdf(path, ["Supply voltage 12V DC", "Weight 1.05 Kg"])
        out = ocr.ocr_pdf_pages(path, [1])
    assert [p for p, _, _ in out] == [1]
    text = out[0][1].lower()
    assert "12v" in text.replace(" ", "") and "1.05" in text, out


def test_ocr_chunk_is_cited_as_ocr():
    rows = [{"id": "Scan.pdf:ocr:2", "source": "Scan.pdf", "page": 2, "ocr": True,
             "text": "Weight 1.05 Kg"},
            {"id": "Manual.pdf:4", "source": "Manual.pdf", "page": 4, "text": "x"}]
    by_src = {s["source"]: s for s in main._build_sources(rows)}
    assert by_src["Scan.pdf"]["ocr"] is True
    assert by_src["Scan.pdf"]["page_label"].endswith("(OCR)")
    assert "(OCR)" not in (by_src["Manual.pdf"]["page_label"] or "")
