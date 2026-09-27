"""
Figures: the pictures in a manual, cropped at ingest and shown beside an
answer.

A manual's photos, screenshots and dimension drawings are content the text
pipeline cannot carry: "see the diagram below" retrieves the sentence and
loses the diagram. This module crops every worthwhile raster image out of
a PDF when it is ingested, keeps the crops beside the original (under
<documents>/figures/<source>/), and records which page each came from.
Chunks on that page carry the crop names as `figures` metadata, so an
answer's sources can show the picture that sat next to the cited text.

Nothing here is searchable. The crops are attached to chunks by PAGE, not
by content, so retrieval, grounding and the eval score are unaffected.
Reading the text INSIDE a figure (dimension labels, pin names) is a
separate, measured step -- see figure_text below and ingest.py.

What is NOT a figure, measured on this corpus of fourteen manuals:

  * the page background. NV200 Spectral SSP carries one full-page image
    object on all 182 pages (the red-corner template) -- dropped by both
    the repeat rule and the page-area rule;
  * a logo repeated on most pages;
  * icons and bullet glyphs: anything under MIN_SIDE_PT a side;
  * a blank or flat-colour image (a white spacer, a coloured band).

Crops are rendered from the page region rather than decoded from the image
stream: a drawing is often a raster with its dimension lines drawn as
vectors on top, and the rendered region has both.
"""
import hashlib
import json
import logging
import os
import re
import shutil
from collections import Counter

import docstore

logger = logging.getLogger(__name__)

FIG_DIRNAME = "figures"
SIDECAR = "figures.json"

MIN_SIDE_PT = 50          # smaller than this is an icon or a glyph
MAX_PAGE_FRACTION = 0.85  # larger than this is the page itself (background/scan)
MERGE_GAP_PT = 6          # image tiles closer than this are one figure
MAX_PER_PAGE = 8
RENDER_DPI = 120
CAPTION_BAND_PT = 30      # text this close under a figure is its caption
BLANK_STDDEV = 4.0        # pixel spread below this is a flat colour

_NAME_RE = re.compile(r"^p(\d+)_(\d+)\.png$")
_sidecar_cache: dict[str, tuple[float, dict]] = {}


def figures_dir(source: str) -> str:
    """Where one document's crops live. Never created here."""
    return os.path.join(docstore.store_dir(), FIG_DIRNAME,
                        docstore._safe_basename(source))


def remove_figures(source: str) -> None:
    """Drop a document's crops, on delete or before a replacement is cut."""
    d = figures_dir(source)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    _sidecar_cache.pop(d, None)


def figure_path(source: str, name: str) -> str | None:
    """Absolute path of one crop, or None. `name` comes off a URL, so it must
    be exactly the shape this module writes -- nothing else is resolved."""
    if not _NAME_RE.match(name or ""):
        return None
    d = figures_dir(source)
    if not os.path.isdir(d):
        return None
    for entry in os.listdir(d):
        if entry == name and os.path.isfile(os.path.join(d, entry)):
            return os.path.join(d, entry)
    return None


def figures_for(source: str) -> dict:
    """The sidecar for a document: {name: {page, bbox, caption, ...}}.
    Cached by mtime so _build_sources can call it per answer."""
    d = figures_dir(source)
    p = os.path.join(d, SIDECAR)
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        return {}
    hit = _sidecar_cache.get(d)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        figs = data.get("figures") or {}
    except Exception as exc:
        logger.warning("figures sidecar unreadable for %r: %s", source, exc)
        figs = {}
    _sidecar_cache[d] = (mtime, figs)
    return figs


# ── selection ─────────────────────────────────────────────────────────────

def _stream_key(im: dict) -> str:
    """Identity of an image's bytes, for spotting the one repeated on every
    page. The resource name is per page and not reliable; the bytes are."""
    try:
        raw = im["stream"].get_rawdata() or b""
        return hashlib.md5(raw[:65536]).hexdigest() + f":{len(raw)}"
    except Exception:
        return f"{im.get('name')}:{im.get('srcsize')}"


def _candidate_boxes(page, repeated: set) -> list[tuple[float, float, float, float]]:
    W, H = float(page.width), float(page.height)
    boxes = []
    for im in page.images:
        if _stream_key(im) in repeated:
            continue
        x0, top = max(0.0, float(im["x0"])), max(0.0, float(im["top"]))
        x1, bottom = min(W, float(im["x1"])), min(H, float(im["bottom"]))
        w, h = x1 - x0, bottom - top
        if w < MIN_SIDE_PT or h < MIN_SIDE_PT:
            continue
        if (w * h) / (W * H) > MAX_PAGE_FRACTION:
            continue
        boxes.append((x0, top, x1, bottom))
    return _merge(boxes)


def _merge(boxes: list) -> list:
    """Union of boxes that overlap or nearly touch. A figure exported as
    tiles arrives as several adjacent image objects; one crop, not four."""
    boxes = list(boxes)
    changed = True
    while changed:
        changed = False
        out: list = []
        for b in boxes:
            for i, o in enumerate(out):
                if (b[0] <= o[2] + MERGE_GAP_PT and b[2] >= o[0] - MERGE_GAP_PT
                        and b[1] <= o[3] + MERGE_GAP_PT and b[3] >= o[1] - MERGE_GAP_PT):
                    out[i] = (min(b[0], o[0]), min(b[1], o[1]),
                              max(b[2], o[2]), max(b[3], o[3]))
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


_CAPTION_LEAD = re.compile(r"^(fig(ure|\.)?|image|diagram|photo|drawing)\b", re.I)


def _caption(page, box) -> str:
    """The first text line directly under a figure, if there is one.

    Read across the page's full width, not the figure's: a caption is
    usually wider than a small photo, and cropping to the photo's edges
    returned "cting the MyCheckr to the WiFi please follow the MyCheckr
    manu". A line that names itself a figure wins over the first line, so
    "Figure 3: bezel options" beats a heading that happens to follow.
    Kept short: a caption is a line, a paragraph is the next section.
    """
    x0, top, x1, bottom = box
    H, W = float(page.height), float(page.width)
    if bottom >= H - 2:
        return ""
    try:
        band = page.crop((0, bottom, W, min(H, bottom + CAPTION_BAND_PT)))
        text = (band.extract_text() or "").strip()
    except Exception:
        return ""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return ""
    for ln in lines:
        if _CAPTION_LEAD.match(ln):
            return ln[:120]
    return lines[0] if len(lines[0]) <= 120 else ""


def _is_blank(pil_image) -> bool:
    try:
        from PIL import ImageStat
        return ImageStat.Stat(pil_image.convert("L")).stddev[0] < BLANK_STDDEV
    except Exception:
        return False


# ── extraction ────────────────────────────────────────────────────────────

def extract_figures(pdf_path: str, source: str, progress=None) -> dict[int, list[str]]:
    """Cut the figures out of a PDF. Returns {page: [crop names]}.

    Replaces whatever crops the document had: this runs when a document is
    ingested, and a new version's figures are the new version's. Never
    raises for one bad page or image; a figure that cannot be cut is a
    figure that is not shown.
    """
    try:
        import pdfplumber
    except Exception:
        return {}
    remove_figures(source)
    out_dir = figures_dir(source)
    by_page: dict[int, list[str]] = {}
    sidecar: dict[str, dict] = {}

    with pdfplumber.open(pdf_path) as pdf:
        n_pages = len(pdf.pages)
        # A stream on many pages is furniture (logo, background), whatever
        # its size. Same rule of thumb as parsing._strip_repeated_lines.
        seen: Counter = Counter()
        for page in pdf.pages:
            try:
                for key in {_stream_key(im) for im in page.images}:
                    seen[key] += 1
            except Exception:
                continue
        cutoff = max(3, int(n_pages * 0.3))
        repeated = {k for k, c in seen.items() if c >= cutoff}

        for pno, page in enumerate(pdf.pages, start=1):
            if progress and (pno % 10 == 0 or pno == n_pages):
                try:
                    progress(pno, n_pages)
                except Exception:
                    pass
            try:
                boxes = _candidate_boxes(page, repeated)
            except Exception as exc:
                logger.debug("figures: page %d of %r skipped: %s", pno, source, exc)
                continue
            if not boxes:
                continue
            boxes.sort(key=lambda b: -((b[2] - b[0]) * (b[3] - b[1])))
            boxes = sorted(boxes[:MAX_PER_PAGE], key=lambda b: (b[1], b[0]))
            k = 0
            for box in boxes:
                try:
                    pil = page.crop(box).to_image(resolution=RENDER_DPI).original
                    if _is_blank(pil):
                        continue
                    k += 1
                    name = f"p{pno}_{k}.png"
                    os.makedirs(out_dir, exist_ok=True)
                    pil.convert("RGB").save(os.path.join(out_dir, name),
                                            optimize=True)
                    by_page.setdefault(pno, []).append(name)
                    sidecar[name] = {
                        "page": pno,
                        "bbox": [round(v, 1) for v in box],
                        "width": pil.size[0], "height": pil.size[1],
                        "caption": _caption(page, box),
                    }
                except Exception as exc:
                    logger.debug("figures: crop on page %d of %r failed: %s",
                                 pno, source, exc)
                    continue

    if sidecar:
        try:
            with open(os.path.join(out_dir, SIDECAR), "w", encoding="utf-8") as fh:
                json.dump({"source": source, "figures": sidecar}, fh, indent=1)
        except Exception as exc:
            logger.warning("figures sidecar not written for %r: %s", source, exc)
    logger.info("figures: %d cut from %d page(s) of '%s'",
                len(sidecar), len(by_page), source)
    return by_page


# ── text inside figures ───────────────────────────────────────────────────
# Step 3 of the images plan. Pin names, connector numbers and dimension
# labels live in the picture, not the text layer; OCR on the crop reads
# them. Whether that text is INDEXED is ingest.py's decision (measured
# against the eval, flag-controlled); here it is only read and kept in the
# sidecar beside the crop it came from.

FIGURE_TEXT_DPI = 220
MAX_FIGURE_SIDE_PX = 2400
MIN_FIGURE_TEXT_CHARS = 6


def figure_text(pdf_path: str, source: str, names: list[str] | None = None,
                progress=None) -> dict[str, tuple[str, float]]:
    """OCR the text in a document's figures. Returns {name: (text, conf)}
    for the figures that had legible text, and writes it into the sidecar.

    Re-rendered from the PDF at FIGURE_TEXT_DPI rather than read from the
    stored crop: the crops are cut at 120 DPI for display, and a pin
    number on a connector photo is a few pixels tall at that size.
    """
    import ocr as _ocr
    engine = _ocr._load_engine()
    if engine is None:
        raise RuntimeError(_ocr._unavailable_reason or "OCR engine unavailable")
    import numpy as np
    import pdfplumber

    meta = dict(figures_for(source))
    wanted = [n for n in (names or sorted(meta, key=_name_key)) if n in meta]
    out: dict[str, tuple[str, float]] = {}
    if not wanted:
        return out

    with pdfplumber.open(pdf_path) as pdf:
        total = len(wanted)
        for done, name in enumerate(wanted, start=1):
            m = meta[name]
            try:
                page = pdf.pages[int(m["page"]) - 1]
                box = tuple(m["bbox"])
                w_pt = max(box[2] - box[0], box[3] - box[1], 1)
                dpi = min(FIGURE_TEXT_DPI, MAX_FIGURE_SIDE_PX * 72 / w_pt)
                pil = page.crop(box).to_image(resolution=dpi).original
                result = engine(np.asarray(pil.convert("RGB")))
                if isinstance(result, tuple):
                    result = result[0]
                kept = [(t, s) for t, s in _ocr._lines_in_reading_order(result)
                        if t and s >= _ocr.MIN_LINE_CONFIDENCE]
            except Exception as exc:
                logger.debug("figure text: %s of %r failed: %s", name, source, exc)
                kept = []
            text = "\n".join(t for t, _ in kept).strip()
            if len(text) >= MIN_FIGURE_TEXT_CHARS:
                conf = round(sum(s for _, s in kept) / len(kept), 3)
                out[name] = (text, conf)
                meta[name] = {**m, "text": text, "text_conf": conf}
            else:
                meta[name] = {k: v for k, v in m.items() if k not in ("text", "text_conf")}
            if progress:
                try:
                    progress(done, total)
                except Exception:
                    pass

    d = figures_dir(source)
    try:
        with open(os.path.join(d, SIDECAR), "w", encoding="utf-8") as fh:
            json.dump({"source": source, "figures": meta}, fh, indent=1)
        _sidecar_cache.pop(d, None)
    except Exception as exc:
        logger.warning("figures sidecar not updated for %r: %s", source, exc)
    return out


def _name_key(name: str) -> tuple[int, int]:
    m = _NAME_RE.match(name)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def figures_by_page(source: str) -> dict[int, list[str]]:
    """{page: [names]} from the sidecar, for chunks added after ingest
    (the OCR step) that need the same page attachment."""
    out: dict[int, list[str]] = {}
    for name, meta in figures_for(source).items():
        try:
            out.setdefault(int(meta.get("page")), []).append(name)
        except (TypeError, ValueError):
            continue
    for names in out.values():
        names.sort(key=lambda n: int(_NAME_RE.match(n).group(2)) if _NAME_RE.match(n) else 0)
    return out


def describe(source: str, names: list[str], url_prefix: str) -> list[dict]:
    """The sources-payload form of some crops: url, page, caption. Names
    not on disk are dropped rather than served as broken images."""
    meta = figures_for(source)
    out = []
    for name in names:
        m = meta.get(name)
        if not m or not figure_path(source, name):
            continue
        out.append({"name": name, "page": m.get("page"),
                    "caption": m.get("caption") or "",
                    "width": m.get("width"), "height": m.get("height"),
                    "url": f"{url_prefix}/{name}"})
    return out
