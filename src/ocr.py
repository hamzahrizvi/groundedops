"""
OCR for the pages the text layer could not give us.

Scanned and image-only pages have no text layer, so parsing.extract_pages()
returns nothing for them and they are simply not in the knowledge base. This
module reads those pages from their pixels instead. It is a FALLBACK, run
only on the pages an admin has approved, never on a whole document:

  * A text layer is the characters the author typed. OCR is a guess from
    the picture, and it misreads exactly the content this corpus is asked
    about most -- "1.O5 Kg" for "1.05 Kg", "NV95" for "NV9S". Replacing a
    good text layer with a guess would cost accuracy on every page to gain
    it on none.
  * Everything layout-aware in parsing.py (table detection, heading
    detection by font, prose/table separation) reads pdfplumber's character
    and font data. OCR has neither, so an OCR'd page is prose only. Tables
    on it come out as loose lines.
  * It is slow: 1-3 s a page on CPU against milliseconds for text
    extraction, and the corpus is 800+ pages.

Engine: RapidOCR (Apache-2.0, ONNX models, pure pip install). Tesseract was
rejected because it is a separate binary that would have to be installed on
the machine beside the packaged exe. Pages are rendered by pypdfium2, which
is already bundled as pdfplumber's renderer. Both imports are guarded the
way pdfplumber's is in parsing.py: a missing engine makes OCR unavailable
and says so; it never breaks ingestion.

The ENGLISH recogniser, deliberately. RapidOCR's default is the Chinese
PP-OCR model, which reads Latin text but drops the spaces between words:
measured on a manual page, "Thedeviceoffersavarietyofconfigurationoptions"
-- one token to BM25, matching nothing. The English PP-OCRv4 model gave
"The device offers a variety of configuration options" on the same page.
rapidocr 3.x downloads a model on first use into its own package directory
(rapidocr/models/); packaging/groundedops.spec collects that directory, so
the build machine must have run OCR once (or `python -m ocr --warm`) for
the exe to carry the model and never need the network.

Chunks produced from OCR text are stored with metadata ocr=True. The
citation label carries it ("page 12 (OCR)") so a reader knows the
provenance of an answer drawn from a guessed page.
"""
import logging
import threading

logger = logging.getLogger(__name__)

# Render resolution. 200 DPI is the usual floor for OCR on printed text;
# scanned manuals are typically 150-300 DPI originals, and rendering above
# the source resolution buys nothing but time.
RENDER_DPI = 200

# Longest rendered side, in pixels, whatever the page box says. A PDF made
# by saving a scan through Pillow declares one point per pixel, so an A4
# scan becomes a 17x24 inch "page"; at 200 DPI that is 4800 px a side and
# 20 s of recognition for what is a normal page of text. 2400 px is A4 at
# ~200 DPI, and recognition cost scales with pixels.
MAX_RENDER_SIDE = 2400

# Detections below this confidence are dropped rather than indexed. RapidOCR's
# recogniser scores a line 0..1; garbage lines from a diagram or a smudge sit
# well under 0.5 and would only feed BM25 noise.
MIN_LINE_CONFIDENCE = 0.5

_engine = None
_engine_lock = threading.Lock()
_unavailable_reason: str | None = None


def _load_engine():
    """The RapidOCR engine, created once. Returns None when it cannot be."""
    global _engine, _unavailable_reason
    with _engine_lock:
        if _engine is not None or _unavailable_reason is not None:
            return _engine
        try:
            try:
                # 3.x: the English recogniser is selectable. See the module
                # docstring for why it is not optional.
                from rapidocr import RapidOCR, LangRec, ModelType, OCRVersion
                _engine = RapidOCR(params={
                    "Rec.lang_type": LangRec.EN,
                    "Rec.ocr_version": OCRVersion.PPOCRV4,
                    "Rec.model_type": ModelType.MOBILE,
                    # The v4 mobile detector, not the v6 default: same 25
                    # lines found on the test page, 6.6s -> ~4s.
                    "Det.ocr_version": OCRVersion.PPOCRV4,
                    "Det.model_type": ModelType.MOBILE,
                    # A scan is upright or it is not; the per-line 180°
                    # classifier costs a pass per line to answer that.
                    "Global.use_cls": False,
                    # rapidocr's config.yaml turns the ONNX memory arena
                    # OFF, so every inference reallocates. Measured on one
                    # page: 15.2s off, 9.8s on. Same text either way.
                    "EngineConfig.onnxruntime.enable_cpu_mem_arena": True,
                })
            except ImportError:
                # 1.x (rapidocr_onnxruntime) ships only the Chinese model.
                # Readable, but word spacing suffers; better than nothing.
                from rapidocr_onnxruntime import RapidOCR
                _engine = RapidOCR()
                logger.warning("OCR: rapidocr_onnxruntime 1.x in use; the "
                               "English recogniser needs the rapidocr package")
        except Exception as exc:
            _unavailable_reason = f"OCR engine not installed ({exc})"
            logger.warning("%s; scanned pages cannot be read", _unavailable_reason)
        return _engine


def available() -> tuple[bool, str | None]:
    """Whether OCR can run here, and if not, why -- for the console to show
    instead of a button that would fail."""
    try:
        import pypdfium2  # noqa: F401
    except Exception as exc:
        return False, f"page renderer not installed ({exc})"
    if _load_engine() is None:
        return False, _unavailable_reason
    return True, None


def _render_page(pdf, page_index: int):
    """One page as a PIL image at RENDER_DPI, capped at MAX_RENDER_SIDE."""
    page = pdf[page_index]
    try:
        w, h = page.get_size()          # points
        scale = min(RENDER_DPI / 72, MAX_RENDER_SIDE / max(w, h, 1))
        return page.render(scale=scale).to_pil()
    finally:
        page.close()


def _triples(result):
    """Normalise an engine result to [(box, text, score), ...].

    rapidocr 3.x returns an object with .boxes/.txts/.scores; 1.x returns
    the list of triples directly (or None for an empty page).
    """
    if result is None:
        return []
    if hasattr(result, "txts"):
        if not result.txts:
            return []
        return list(zip(result.boxes, result.txts, result.scores))
    return list(result)


def _lines_in_reading_order(result) -> list[tuple[str, float]]:
    """RapidOCR's [box, text, score] triples as (text, score), top-to-bottom
    then left-to-right.

    The detector returns boxes in the order it found them, which is roughly
    reading order but not reliably: two columns interleave. Grouping boxes
    whose vertical centres fall within half a line height of each other, and
    sorting within the group by x, reads a column layout the way a person
    does.
    """
    rows = []
    for box, text, score in _triples(result):
        try:
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            top, bottom = min(ys), max(ys)
            rows.append(((top + bottom) / 2, bottom - top, min(xs),
                         (text or "").strip(), float(score or 0)))
        except Exception:
            continue
    rows.sort(key=lambda r: (r[0], r[2]))

    out: list[tuple[str, float]] = []
    line: list = []
    line_y = None
    for cy, h, x, text, score in rows:
        if line and line_y is not None and abs(cy - line_y) > max(h, 1) * 0.5:
            line.sort(key=lambda r: r[2])
            out.append((" ".join(r[3] for r in line if r[3]),
                        min(r[4] for r in line)))
            line = []
        if not line:
            line_y = cy
        line.append((cy, h, x, text, score))
    if line:
        line.sort(key=lambda r: r[2])
        out.append((" ".join(r[3] for r in line if r[3]),
                    min(r[4] for r in line)))
    return out


def ocr_pdf_pages(path: str, pages: list[int],
                  progress=None) -> list[tuple[int, str, float]]:
    """Read the given 1-indexed pages of a PDF from their pixels.

    Returns [(page_number, text, mean_confidence), ...] for every page that
    produced text. A page that produced nothing is left out, the same way
    extract_pages() leaves out a page with no text layer. `progress(done,
    total)` is called after each page so the console can show movement;
    OCR is the one ingest step slower than embedding.
    """
    engine = _load_engine()
    if engine is None:
        raise RuntimeError(_unavailable_reason or "OCR engine unavailable")
    import numpy as np
    import pypdfium2 as pdfium

    wanted = sorted({int(p) for p in pages if int(p) >= 1})
    out: list[tuple[int, str, float]] = []
    pdf = pdfium.PdfDocument(path)
    try:
        total = len(wanted)
        for done, pno in enumerate(wanted, start=1):
            if pno > len(pdf):
                logger.warning("OCR: %s has no page %d (of %d)", path, pno, len(pdf))
                continue
            try:
                image = _render_page(pdf, pno - 1)
                result = engine(np.asarray(image.convert("RGB")))
                if isinstance(result, tuple):      # 1.x: (triples, timings)
                    result = result[0]
            except Exception as exc:
                logger.warning("OCR failed on %s page %d: %s", path, pno, exc)
                continue
            kept = [(t, s) for t, s in _lines_in_reading_order(result)
                    if t and s >= MIN_LINE_CONFIDENCE]
            if kept:
                text = "\n".join(t for t, _ in kept)
                conf = sum(s for _, s in kept) / len(kept)
                out.append((pno, text, round(conf, 3)))
            if progress:
                try:
                    progress(done, total)
                except Exception:
                    pass
    finally:
        pdf.close()
    return out


if __name__ == "__main__":          # pragma: no cover
    # `python -m ocr --warm` on the build machine: constructs the engine so
    # the English model is downloaded into the package before packaging.
    import sys
    if "--warm" in sys.argv:
        ok, why = available()
        print("OCR ready" if ok else f"OCR unavailable: {why}")
        sys.exit(0 if ok else 1)
