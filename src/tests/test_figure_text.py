"""Text read out of figures: kept in the sidecar, indexed only by choice.

  1. A figure-text chunk names the figure (page, caption) above the labels
     read from it, so the model knows it is looking at a pinout, not prose.
  2. index_figure_text() adds those chunks to a document already held,
     tagged ocr=True / figure_text=True with the figure's name, and
     replaces its own earlier chunks rather than duplicating them.
  3. ingest_file() indexes none of it unless FIGURE_TEXT_INDEX is on.
"""
import os
import tempfile
from unittest.mock import patch

import docstore
import figures
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


def _sidecar(tmp, source, figs):
    d = figures.figures_dir(source)
    os.makedirs(d, exist_ok=True)
    import json
    with open(os.path.join(d, figures.SIDECAR), "w", encoding="utf-8") as fh:
        json.dump({"source": source, "figures": figs}, fh)
    for name in figs:
        with open(os.path.join(d, name), "wb") as fh:
            fh.write(b"\x89PNG")


def test_figure_chunk_names_the_figure_above_its_labels():
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(docstore, "store_dir", return_value=tmp):
        _sidecar(tmp, "M.pdf", {"p12_1.png": {"page": 12, "caption": "Figure 4: SSP connector"},
                                "p13_1.png": {"page": 13, "caption": ""}})
        out = ingest._figure_chunks("M.pdf", {"p12_1.png": ("1 +V\n2 GND\n15 TX", 0.9),
                                              "p13_1.png": ("65.84\n36.92", 0.8),
                                              "p99_1.png": ("orphan", 0.9)})
    assert [(p, n) for p, n, *_ in out] == [(12, "p12_1.png"), (13, "p13_1.png")]
    body = out[0][2]
    assert "Figure on page 12: Figure 4: SSP connector" in body
    assert "Text in the figure:\n1 +V\n2 GND\n15 TX" in body
    assert body.startswith("[M — Figure: Figure 4: SSP connector]")
    assert out[1][2].startswith("[M — Figure]")


def test_figure_chunk_carries_its_page_heading():
    """9.14: a figure chunk held only labels and a caption, so search
    rarely found it. It now carries its page's section heading, and a page
    with none takes the nearest earlier page's."""
    headings = ingest._page_headings([(10, "Connectors"), (10, ""), (10, "Connectors"),
                                      (10, "SSP pinout"), (14, "Mounting")])
    assert headings == {10: "Connectors / SSP pinout", 14: "Mounting"}
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(docstore, "store_dir", return_value=tmp):
        _sidecar(tmp, "M.pdf", {"p12_1.png": {"page": 12, "caption": "Figure 4"},
                                "p9_1.png": {"page": 9, "caption": ""}})
        out = ingest._figure_chunks("M.pdf", {"p12_1.png": ("1 +V", 0.9),
                                              "p9_1.png": ("65.84", 0.8)}, headings)
    by_name = {n: (body, heading) for _, n, body, _, heading in out}
    body, heading = by_name["p12_1.png"]
    assert heading == "Connectors / SSP pinout"
    assert body.startswith("[M — Connectors / SSP pinout — Figure: Figure 4]")
    assert "Figure on page 12, Connectors / SSP pinout: Figure 4" in body
    assert by_name["p9_1.png"][1] == ""     # nothing at or before page 9


def test_index_figure_text_tags_and_replaces():
    collection = _Collection()
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(docstore, "store_dir", return_value=tmp), \
         patch.object(docstore, "read_dirs", return_value=[tmp]), \
         patch.object(ingest, "get_collection", return_value=collection), \
         patch.object(ingest, "embed_texts",
                      side_effect=lambda texts: [_Vector([0.1]) for _ in texts]), \
         patch.object(ingest, "invalidate_retrieval_cache"), \
         patch.object(ingest, "catalog_scope_for",
                      return_value={"category": "validators", "product": "nv9"}), \
         patch.object(figures, "figure_text",
                      return_value={"p12_1.png": ("1 +V\n2 GND", 0.91)}) as run:
        with open(os.path.join(tmp, "M.pdf"), "wb") as fh:
            fh.write(b"%PDF-1.4 stub")
        _sidecar(tmp, "M.pdf", {"p12_1.png": {"page": 12, "caption": "Pinout"}})
        docstore.record("M.pdf", content=b"%PDF-1.4 stub", chunks=3, pages=20)
        # A prose chunk already held must survive untouched.
        collection.rows["M.pdf:abc:0"] = ("prose", {"source": "M.pdf", "page": 12,
                                                     "section": "Connectors"})

        assert ingest.index_figure_text("M.pdf") == 1
        assert run.call_count == 1
        figs = {k: v for k, v in collection.rows.items() if ":figtext:" in k}
        assert len(figs) == 1
        _, meta = next(iter(figs.values()))
        assert meta["ocr"] is True and meta["figure_text"] is True
        assert meta["figures"] == "p12_1.png" and meta["page"] == 12
        assert meta["product"] == "nv9" and meta["prod_nv9"] is True
        assert meta["section"] == "Connectors"      # 9.14: from the held prose

        # Second run replaces, never duplicates; the prose chunk stays.
        assert ingest.index_figure_text("M.pdf") == 1
        assert len(collection.rows) == 2
        assert "M.pdf:abc:0" in collection.rows


def test_ingest_file_indexes_no_figure_text_by_default():
    collection = _Collection()
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(docstore, "store_dir", return_value=tmp), \
         patch.object(docstore, "read_dirs", return_value=[tmp]), \
         patch.object(ingest, "get_collection", return_value=collection), \
         patch.object(ingest, "extract_pages_report",
                      return_value=([(1, "Mounting uses four M4 screws.")],
                                    {"pages": 1, "empty": [], "garbled": []})), \
         patch.object(ingest, "embed_texts",
                      side_effect=lambda texts: [_Vector([0.1]) for _ in texts]), \
         patch.object(ingest, "_dedupe_enabled", return_value=False), \
         patch.object(ingest, "invalidate_retrieval_cache"), \
         patch.object(figures, "extract_figures", return_value={1: ["p1_1.png"]}), \
         patch.object(figures, "figure_text") as text_run:
        assert ingest.FIGURE_TEXT_INDEX is False
        assert ingest.ingest_file(b"%PDF-1.4 x", "M.pdf") == 1
        assert text_run.call_count == 0
        _, meta = next(iter(collection.rows.values()))
        assert meta["figures"] == "p1_1.png"
        assert "figure_text" not in meta
