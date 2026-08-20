"""
Tests for parsing.py — document extraction quality.

The bugs being guarded against, all observed on real documents:

  1. python-docx keeps table cells in doc.tables, NOT doc.paragraphs, so
     reading only paragraphs dropped every table in every .docx silently.
     For product documentation that is most of the specifications.

  2. pypdf's extract_text() flattens a table's cells into one space-joined
     run, so "URL | BASE_URL/..." became "URL BASE_URL/..." and the
     label/value relationship was lost. A question whose answer sat in a spec
     table retrieved that table at 0.99 similarity and was still refused.

  3. Running headers/footers were indexed once per page — 88 copies of
     "Document Revision - v.15 ICU_Network_API - 10" in one manual.
"""

import os
import tempfile

from docx import Document

import parsing
from parsing import _render_table, _strip_repeated_lines, extract_pages


# ── _render_table ──────────────────────────────────────────────────────────

def test_render_table_two_columns_becomes_label_value():
    rows = [["URL", "BASE_URL/credentials"], ["Method", "POST"]]
    out = _render_table(rows)
    assert "URL: BASE_URL/credentials" in out
    assert "Method: POST" in out


def test_render_table_wide_keeps_cells_separated():
    rows = [["Status", "Body", "Notes"], ["200", "{}", "ok"]]
    out = _render_table(rows)
    # Pipe-separated, so a model can still see which cell is which column.
    assert "Status | Body | Notes" in out
    assert "200 | {} | ok" in out


def test_render_table_newlines_inside_cells_do_not_break_rows():
    rows = [["Headers", "Content-Type: application/json\nAuthorization: Bearer"]]
    out = _render_table(rows)
    assert out.count("\n") == 0, "a cell's newline must not split the row"
    assert "Authorization: Bearer" in out


def test_render_table_ignores_entirely_blank_rows():
    assert _render_table([["", ""], ["A", "B"]]) == "A: B"
    assert _render_table([]) == ""
    assert _render_table(None) == ""


def test_render_table_ragged_rows_do_not_raise():
    rows = [["a", "b", "c"], ["d"]]
    out = _render_table(rows)
    assert "a | b | c" in out


# ── .docx tables ───────────────────────────────────────────────────────────

def _docx_with_table(path):
    doc = Document()
    doc.add_paragraph("Specifications follow.")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Operating voltage"
    table.cell(0, 1).text = "12VDC"
    table.cell(1, 0).text = "Protocols"
    table.cell(1, 1).text = "SSP, ccTalk, SI2"
    doc.add_paragraph("End of section.")
    doc.save(path)


def test_docx_table_content_is_extracted():
    tmp = os.path.join(tempfile.mkdtemp(), "spec.docx")
    _docx_with_table(tmp)

    pages = extract_pages(tmp)
    assert len(pages) == 1
    text = pages[0][1]

    # The whole point: this used to be absent entirely.
    assert "Operating voltage: 12VDC" in text
    assert "Protocols: SSP, ccTalk, SI2" in text
    # And the surrounding prose is still there.
    assert "Specifications follow." in text
    assert "End of section." in text


def test_docx_keeps_document_order():
    """A table must stay between the paragraphs that introduce and follow it —
    appending all tables at the end separates a spec from its context."""
    tmp = os.path.join(tempfile.mkdtemp(), "order.docx")
    _docx_with_table(tmp)

    text = extract_pages(tmp)[0][1]
    intro = text.index("Specifications follow.")
    spec = text.index("Operating voltage: 12VDC")
    end = text.index("End of section.")
    assert intro < spec < end


# ── running headers/footers ────────────────────────────────────────────────

def test_strip_repeated_lines_drops_furniture_keeps_content():
    footer = "Document Revision - v.15 ICU_Network_API - 10"
    pages = [(i, f"{footer}\nUnique content for page {i}.") for i in range(1, 9)]

    out = _strip_repeated_lines(pages)

    assert len(out) == 8
    for num, text in out:
        assert footer not in text
        assert f"Unique content for page {num}." in text


def test_strip_repeated_lines_leaves_short_documents_alone():
    """On a 3-page file a repeated line is as likely to be real content."""
    pages = [(i, "Shared line\nBody %d" % i) for i in range(1, 4)]
    assert _strip_repeated_lines(pages) == pages


def test_strip_repeated_lines_keeps_long_repeated_passages():
    """A long verbatim repeat is more likely a repeated warning than
    furniture, so the filter is length-bounded."""
    warning = ("W" * 200)
    pages = [(i, f"{warning}\nBody {i}") for i in range(1, 9)]
    out = _strip_repeated_lines(pages)
    assert all(warning in text for _, text in out)


# ── pages with no extractable text ─────────────────────────────────────────

def test_unsupported_extension_returns_empty_not_raise():
    tmp = os.path.join(tempfile.mkdtemp(), "thing.rtf")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("hello")
    assert extract_pages(tmp) == []


def test_missing_file_returns_empty_not_raise():
    assert extract_pages(os.path.join(tempfile.mkdtemp(), "nope.pdf")) == []


def test_txt_is_one_page():
    tmp = os.path.join(tempfile.mkdtemp(), "notes.txt")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("plain text body")
    assert extract_pages(tmp) == [(1, "plain text body")]


def test_pdfplumber_is_the_active_reader():
    """If pdfplumber is missing the code still works, but tables get flattened
    by pypdf -- which is the failure mode this module exists to fix. Assert the
    install is present so a dropped dependency is loud, not silent."""
    assert parsing.pdfplumber is not None, "pdfplumber not installed"
