"""OCR of unreadable pages: offered, never run unasked; tagged when run.

The behaviours guarded here:

  1. The parser REPORTS the pages it could not read instead of only logging
     them (extract_pages_report). Before this, a scanned page was known to
     the log file and to nobody else.
  2. A page with a text layer that is garbage -- (cid:N) runs, symbols -- is
     a candidate too. It used to be indexed as prose and never matched.
  3. OCR chunks are stored with ocr=True and the page number, replace any
     earlier OCR chunks for the same pages, and are recorded in the manifest
     so a rebuild re-reads the same pages.
  4. A missing engine makes OCR unavailable with a reason; it never breaks
     ingestion, which does not import the engine at all.
"""
import os
import tempfile
from unittest.mock import patch

import docstore
import ingest
import ocr
import parsing
from parsing import _looks_garbled, extract_pages_report


# ── candidates ─────────────────────────────────────────────────────────────

def test_cid_runs_are_garbled():
    text = "(cid:42)(cid:17)(cid:88) " * 20
    assert _looks_garbled(text)


def test_symbol_soup_is_garbled():
    assert _looks_garbled("▒▒▒░░░▓▓▓ ■■■ ▒▒▒░░░▓▓▓ ■■■ ▒▒▒░░░▓▓▓ ■■■ ▒▒▒░░░ ▓▓▓")


def test_prose_and_spec_lines_are_not_garbled():
    assert not _looks_garbled(
        "The NV9 Spectral validator accepts up to 16 denominations and "
        "operates at 12V DC ±10%. Weight: 1.05 Kg. Interface: SSP, ccTalk.")
    # A short page that is one part number is legitimately mostly symbols.
    assert not _looks_garbled("PA-0114-3 / v2.1")


def _image_only_pdf(path, pages=2):
    """A PDF whose pages are pictures with no text layer -- a scan."""
    from PIL import Image, ImageDraw
    imgs = []
    for i in range(pages):
        im = Image.new("RGB", (600, 800), "white")
        ImageDraw.Draw(im).rectangle((50, 50, 550, 750), outline="black")
        imgs.append(im)
    imgs[0].save(path, save_all=True, append_images=imgs[1:])


def test_report_lists_image_only_pages():
    tmp = os.path.join(tempfile.mkdtemp(), "scan.pdf")
    _image_only_pdf(tmp, pages=3)
    pages, report = extract_pages_report(tmp)
    assert pages == []
    assert report["pages"] == 3
    assert report["empty"] == [1, 2, 3]
    assert report["garbled"] == []


def test_report_is_empty_for_a_text_file():
    tmp = os.path.join(tempfile.mkdtemp(), "notes.txt")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("plain text body")
    pages, report = extract_pages_report(tmp)
    assert pages == [(1, "plain text body")]
    assert report == {"pages": 1, "empty": [], "garbled": []}


def test_garbled_text_layer_is_reported_but_still_indexed():
    tmp = os.path.join(tempfile.mkdtemp(), "g.pdf")
    with patch.object(parsing, "_extract_pdf",
                      return_value=([(1, "(cid:3)(cid:9)(cid:12)" * 30),
                                     (2, "Real prose about the validator.")],
                                    [3])):
        pages, report = extract_pages_report(tmp)
    assert [p for p, _ in pages] == [1, 2]
    assert report == {"pages": 3, "empty": [3], "garbled": [1]}


# ── engine availability ────────────────────────────────────────────────────

def test_unavailable_engine_gives_a_reason_not_an_exception():
    with patch.object(ocr, "_load_engine", return_value=None), \
         patch.object(ocr, "_unavailable_reason", "OCR engine not installed (x)"):
        ok, why = ocr.available()
    assert ok is False
    assert "not installed" in why


def test_lines_come_out_in_reading_order():
    # Two columns: the detector found the right column first.
    box = lambda x, y: [[x, y], [x + 100, y], [x + 100, y + 20], [x, y + 20]]
    result = [
        (box(300, 10), "right-1", 0.9),
        (box(10, 10), "left-1", 0.9),
        (box(10, 60), "left-2", 0.9),
        (box(300, 60), "right-2", 0.9),
    ]
    lines = ocr._lines_in_reading_order(result)
    assert [t for t, _ in lines] == ["left-1 right-1", "left-2 right-2"]


# ── ingest_ocr_pages ───────────────────────────────────────────────────────

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
            rows = [(k, v) for k, v in rows if v[1].get("source") == where["source"]]
        return {"ids": [k for k, _ in rows],
                "documents": [v[0] for _, v in rows],
                "metadatas": [v[1] for _, v in rows]}

    def add(self, ids, documents, embeddings, metadatas):
        for key, text, meta in zip(ids, documents, metadatas):
            if key in self.rows:
                raise ValueError("duplicate id")
            self.rows[key] = (text, meta)

    def delete(self, ids):
        for key in ids:
            self.rows.pop(key, None)


def test_ocr_chunks_are_tagged_replaceable_and_recorded():
    collection = _Collection()
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(docstore, "store_dir", return_value=tmp), \
         patch.object(docstore, "read_dirs", return_value=[tmp]), \
         patch.object(ingest, "get_collection", return_value=collection), \
         patch.object(ingest, "embed_texts",
                      side_effect=lambda texts: [_Vector([0.1, 0.2]) for _ in texts]), \
         patch.object(ingest, "invalidate_retrieval_cache"), \
         patch.object(ingest, "catalog_scope_for",
                      return_value={"category": "validators", "product": "nv9"}), \
         patch.object(ocr, "ocr_pdf_pages",
                      side_effect=lambda path, pages, progress=None: [
                          r for r in [(2, "Weight 1.05 Kg. Supply 12V DC.", 0.91),
                                      (3, "Interface SSP and ccTalk.", 0.88)]
                          if r[0] in pages]) as run:
        # The original must be retained: OCR reads it from disk.
        _image_only_pdf(os.path.join(tmp, "Scan.pdf"), pages=3)
        docstore.record("Scan.pdf", content=b"x", chunks=0, pages=3,
                        ocr={"candidates": [1, 2, 3], "done": []})

        n = ingest.ingest_ocr_pages("Scan.pdf", [2, 3])
        assert n == 2
        assert run.call_args[0][1] == [2, 3]
        metas = [m for _, m in collection.rows.values()]
        assert all(m["ocr"] is True for m in metas)
        assert sorted(m["page"] for m in metas) == [2, 3]
        assert all(m["product"] == "nv9" and m["prod_nv9"] is True for m in metas)
        assert all(":ocr:" in cid for cid in collection.rows)

        state = docstore.ocr_state("Scan.pdf")
        assert state["done"] == [2, 3]
        assert state["pending"] == [1]

        # Approving the same pages again replaces, never duplicates.
        assert ingest.ingest_ocr_pages("Scan.pdf", [2]) == 1
        assert sorted(m["page"] for _, m in collection.rows.values()) == [2, 3]


def test_ocr_pages_rejects_a_missing_original():
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(docstore, "store_dir", return_value=tmp), \
         patch.object(docstore, "read_dirs", return_value=[tmp]):
        try:
            ingest.ingest_ocr_pages("Nope.pdf", [1])
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("expected FileNotFoundError")


def test_ingest_file_reports_candidates_and_keeps_the_original():
    """A fully scanned upload indexes nothing but is NOT thrown away: the
    OCR step needs the file, and the row needs the offer."""
    collection = _Collection()
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(docstore, "store_dir", return_value=tmp), \
         patch.object(docstore, "read_dirs", return_value=[tmp]), \
         patch.object(ingest, "get_collection", return_value=collection):
        pdf = os.path.join(tmp, "src.pdf")
        _image_only_pdf(pdf, pages=2)
        with open(pdf, "rb") as fh:
            content = fh.read()
        report = {}
        assert ingest.ingest_file(content, "Scan.pdf", report=report) == 0
        assert report == {"pages": 2, "ocr_candidates": [1, 2]}
        assert os.path.isfile(os.path.join(tmp, "Scan.pdf"))
        assert docstore.ocr_state("Scan.pdf")["pending"] == [1, 2]
