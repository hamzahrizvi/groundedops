"""Figures cut from a manual at ingest, attached to chunks by page.

Guarded here:

  1. A real picture on a page is cut out and named for its page; an icon,
     a full-page background and a logo repeated on most pages are not.
  2. Chunks from that page carry the crop names; other pages' chunks do not.
  3. /figure resolves only names of the shape figures.py writes.
  4. Replacing a document replaces its crops; deleting removes them.
"""
import os
import tempfile
from unittest.mock import patch

import pypdfium2 as pdfium

import docstore
import figures


def _bitmap(w, h, rgb):
    """A flat-colour bitmap with a dark square in it, so the crop is not
    'blank' by figures._is_blank."""
    bmp = pdfium.PdfBitmap.new_native(int(w), int(h), format=pdfium.raw.FPDFBitmap_BGR)
    bmp.fill_rect(rgb, 0, 0, int(w), int(h))
    bmp.fill_rect((20, 20, 20, 255), int(w * 0.3), int(h * 0.3), int(w * 0.4), int(h * 0.4))
    return bmp


def _pdf(path, pages):
    """pages: list of [(x, y_from_bottom, w, h, rgb), ...] per page, on A4."""
    pdf = pdfium.PdfDocument.new()
    for spec in pages:
        page = pdf.new_page(595, 842)
        for (x, y, w, h, rgb) in spec:
            img = pdfium.PdfImage.new(pdf)
            img.set_bitmap(_bitmap(w, h, rgb))
            img.set_matrix(pdfium.PdfMatrix().scale(w, h).translate(x, y))
            page.insert_obj(img)
        page.gen_content()
    pdf.save(path)


RED = (200, 40, 40, 255)
BLUE = (40, 40, 200, 255)


def test_real_pictures_are_cut_and_furniture_is_not():
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(docstore, "store_dir", return_value=tmp):
        pdf = os.path.join(tmp, "m.pdf")
        logo = (500, 800, 60, 30, BLUE)             # top-right, every page
        _pdf(pdf, [
            [(100, 500, 200, 150, RED), (50, 50, 12, 12, RED), logo],   # photo + icon
            [(0, 0, 595, 842, RED), logo],                                # background
            [logo],
            [(80, 300, 250, 120, BLUE), logo],
        ])
        by_page = figures.extract_figures(pdf, "Manual.pdf")

    assert by_page == {1: ["p1_1.png"], 4: ["p4_1.png"]}, by_page


def test_sidecar_records_page_and_size():
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(docstore, "store_dir", return_value=tmp):
        pdf = os.path.join(tmp, "m.pdf")
        _pdf(pdf, [[(100, 500, 200, 150, RED)]])
        figures.extract_figures(pdf, "Manual.pdf")
        meta = figures.figures_for("Manual.pdf")
        assert set(meta) == {"p1_1.png"}
        assert meta["p1_1.png"]["page"] == 1
        assert meta["p1_1.png"]["width"] > 100
        assert figures.figures_by_page("Manual.pdf") == {1: ["p1_1.png"]}
        assert figures.describe("Manual.pdf", ["p1_1.png", "p9_9.png"], "/figure/Manual.pdf") == [
            {"name": "p1_1.png", "page": 1, "caption": "",
             "width": meta["p1_1.png"]["width"], "height": meta["p1_1.png"]["height"],
             "url": "/figure/Manual.pdf/p1_1.png"}]


def test_tiles_of_one_figure_become_one_crop():
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(docstore, "store_dir", return_value=tmp):
        pdf = os.path.join(tmp, "m.pdf")
        # Two image objects side by side with a 2pt seam.
        _pdf(pdf, [[(100, 500, 100, 150, RED), (202, 500, 100, 150, BLUE)]])
        by_page = figures.extract_figures(pdf, "Manual.pdf")
        assert by_page == {1: ["p1_1.png"]}
        assert figures.figures_for("Manual.pdf")["p1_1.png"]["bbox"][2] >= 300


def test_figure_path_resolves_only_our_names():
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(docstore, "store_dir", return_value=tmp):
        pdf = os.path.join(tmp, "m.pdf")
        _pdf(pdf, [[(100, 500, 200, 150, RED)]])
        figures.extract_figures(pdf, "Manual.pdf")
        assert figures.figure_path("Manual.pdf", "p1_1.png")
        assert figures.figure_path("Manual.pdf", "figures.json") is None
        assert figures.figure_path("Manual.pdf", "../m.pdf") is None
        assert figures.figure_path("Manual.pdf", "p2_1.png") is None
        assert figures.figure_path("Other.pdf", "p1_1.png") is None


def test_replacement_and_removal():
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(docstore, "store_dir", return_value=tmp):
        pdf = os.path.join(tmp, "m.pdf")
        _pdf(pdf, [[(100, 500, 200, 150, RED)], [(100, 500, 200, 150, RED)]])
        assert set(figures.extract_figures(pdf, "Manual.pdf")) == {1, 2}
        _pdf(pdf, [[(100, 500, 200, 150, RED)]])
        assert set(figures.extract_figures(pdf, "Manual.pdf")) == {1}
        assert figures.figure_path("Manual.pdf", "p2_1.png") is None
        figures.remove_figures("Manual.pdf")
        assert not os.path.isdir(figures.figures_dir("Manual.pdf"))
        assert figures.figures_for("Manual.pdf") == {}
