"""Verbatim tables and checklists, pulled from the source PDF on demand.

WHY NOT FROM THE INDEX: chunks are prose-normalised and the table fences
used during chunking are stripped before storage, so the index cannot tell
you a chunk WAS a table, let alone give you its columns back. Re-reading the
page from the PDF is the only way to answer "show me the table" with the
actual table rather than a paraphrase of it.

WHY THAT IS AFFORDABLE: chunks already carry `source` and `page`, so this
only ever opens the one file the answer already cited and reads the pages it
already pointed at. pdfplumber opens even the 28MB manual in ~0.02s, and
results are cached per (file, mtime, page).

Nothing here is generated. Every cell is what the PDF says, so a table
returned this way needs no grounding check -- it IS the source.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

try:
    import pdfplumber
except Exception as _exc:                                  # pragma: no cover
    pdfplumber = None
    logging.getLogger(__name__).warning(
        f"pdfplumber unavailable ({_exc}); verbatim tables are disabled")

# (path, mtime, page) -> list of blocks
_CACHE: dict[tuple, list] = {}
_CACHE_MAX = 256

CHECKLIST_RE = re.compile(r"check\s?list", re.I)
# A heading looks like "5C. Final outro checklist - before you leave" or
# "Pre-Requisites Checklist". Kept loose: the point is to find the START of a
# checklist, and the extractor below stops at the next heading-ish line.
_HEADING_RE = re.compile(r"^\s*(\d+[A-Za-z]?[.)]\s+|[A-Z][A-Za-z ]{0,40}$)")


def _render_markdown(rows: list[list]) -> str:
    """A table as markdown, with the source's own cells and nothing added."""
    clean = []
    for row in rows or []:
        cells = [(c or "").replace("\n", " ").strip() for c in row]
        if any(cells):
            clean.append(cells)
    if not clean:
        return ""
    width = max(len(r) for r in clean)
    clean = [r + [""] * (width - len(r)) for r in clean]

    # A blank first cell in the header row is normal in spec tables (the
    # row-label column has no title); keep it rather than inventing one.
    head, body = clean[0], clean[1:]
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join(["---"] * width) + "|"]
    for r in body:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def _caption_above(page, bbox) -> str:
    """The nearest non-empty text line above a table, used as its title.

    Spec manuals put the table's name immediately above it ("Operation",
    "Storage"), and that caption is the only thing distinguishing two
    otherwise identical tables on the same page -- which is exactly the case
    a visitor needs to choose between.
    """
    for window in (60, 150):
        try:
            top = bbox[1]
            if top <= 2:
                return ""          # table starts at the page top: no caption
            above = page.crop((0, max(0, top - window), page.width,
                               max(1, top - 1)))
            lines = [l.strip() for l in (above.extract_text() or "").split("\n")
                     if l.strip()]
            # Prefer the last line that reads like a heading rather than the
            # last line outright -- a table often sits under a sentence of
            # lead-in prose ("Installers are expected to tick all items"),
            # and that sentence is not its name.
            for line in reversed(lines):
                if len(line) <= 70 and not line.endswith("."):
                    return line[:120]
            if window == 150 and lines:
                return lines[-1][:120]
        except Exception:
            return ""
    return ""


def tables_on_page(path: str, page_no: int) -> list[dict]:
    """Every table on one page, verbatim. [] if the page has none."""
    if pdfplumber is None or not os.path.exists(path):
        return []
    try:
        key = (path, os.path.getmtime(path), page_no, "t")
    except OSError:
        return []
    if key in _CACHE:
        return _CACHE[key]

    out: list[dict] = []
    try:
        with pdfplumber.open(path) as pdf:
            if not (1 <= page_no <= len(pdf.pages)):
                return []
            page = pdf.pages[page_no - 1]
            for t in page.find_tables():
                md = _render_markdown(t.extract())
                if not md:
                    continue
                out.append({"kind": "table", "page": page_no,
                            "title": _caption_above(page, t.bbox),
                            "markdown": md})
    except Exception as exc:
        logger.warning(f"table extraction failed for {path} p{page_no}: {exc}")
        return []

    if len(_CACHE) > _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = out
    return out


# A checklist in these documents is not prose with bullets -- it is a TABLE
# with a tick column: "Category | Verification Step | Check" with a U+2610
# BALLOT BOX per row. Detecting the box is far more reliable than guessing
# where a heading's list starts and stops; an earlier line-scanning version
# of this attached the Introduction to a table-of-contents entry.
_TICKBOX = re.compile(r"[☐☑☒□❑]")


def _is_checklist_table(markdown: str) -> bool:
    if _TICKBOX.search(markdown):
        return True
    head = markdown.split("\n", 1)[0].lower()
    return "check" in head and "|" in head


def checklists_on_page(path: str, page_no: int) -> list[dict]:
    """Checklist tables on one page, verbatim.

    Built on tables_on_page rather than re-parsing: a checklist here IS a
    table, so it inherits the same caption logic -- which is what supplies
    "5A. Basic - Must verify before leaving site" versus "5B. Advanced" and
    lets a visitor be asked WHICH checklist they meant.
    """
    out = []
    for b in tables_on_page(path, page_no):
        if _is_checklist_table(b.get("markdown", "")):
            out.append({**b, "kind": "checklist"})
    return out


# ── what the visitor asked for ────────────────────────────────────────────

_TABLE_WORDS = re.compile(
    r"\b(table|tables|full\s+spec\w*|specification\s+table|"
    r"complete\s+(?:spec\w*|list)|as[- ]is|verbatim|whole\s+table)\b", re.I)
_CHECKLIST_WORDS = re.compile(r"\b(check\s?list|checklists)\b", re.I)


def wanted_kind(query: str) -> str | None:
    """"table", "checklist" or None -- what the wording asks to SEE.

    Only fires on an explicit ask. "What is the operating temperature?"
    wants a sentence; "show me the operating temperature table" wants the
    table. Guessing from topic alone would attach a wall of markdown to
    ordinary questions.
    """
    q = query or ""
    if _CHECKLIST_WORDS.search(q):
        return "checklist"
    if _TABLE_WORDS.search(q):
        return "table"
    return None


def _doc_title(source: str) -> str:
    """A readable name for a document, for use when a block has no caption."""
    s = re.sub(r"\.(pdf|docx|txt)$", "", source or "", flags=re.I)
    s = re.sub(r"^CS-", "", s)
    s = re.sub(r"-\d{6,}[-\d]*$", "", s)      # trailing export stamps
    return s.strip(" -") or (source or "document")


def collect(kind: str, cited: list[tuple[str, int]], doc_dir: str,
            limit: int = 6) -> list[dict]:
    """Verbatim blocks of `kind` from the (source, page) pairs an answer cited.

    Deduped on title+page so the same table found via two chunks appears
    once, and capped so a broad question cannot return the whole manual.
    """
    fn = tables_on_page if kind == "table" else checklists_on_page
    seen, out = set(), []
    for source, page in cited:
        if not source or not page:
            continue
        for b in fn(os.path.join(doc_dir, source), int(page)):
            # A one- or two-row "table" is a stray box or a lead-in line the
            # extractor boxed, not a checklist anyone asked to see. Counted
            # in DATA rows: markdown carries a header and a separator.
            rows = b.get("markdown", "").count("\n") - 1
            if kind == "checklist" and rows < 3:
                continue
            sig = (b.get("title", ""), b.get("page"), b.get("markdown", "")[:80])
            if sig in seen:
                continue
            seen.add(sig)
            # A block starting at the top of a page has no caption above it --
            # it is usually a continuation. Falling back to the document's own
            # name keeps every option in a "which did you mean?" list
            # distinguishable, which is the whole point of collecting titles.
            title = b.get("title") or _doc_title(source)
            out.append({**b, "source": source, "title": title})
            if len(out) >= limit:
                return out
    return out
