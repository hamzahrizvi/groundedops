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


def _unwrap_cell(text: str, vocab: set | None = None) -> str:
    """Join a cell's wrapped lines without splitting words.

    pdfplumber returns a cell's text with a newline wherever the PDF wrapped
    it inside the column. Replacing every newline with a space -- which this
    did -- breaks words at the wrap point: a narrow "Section" column came out
    as "Sectio n", and "Live Camera View" as "Live Camer a View", in both the
    harvested FAQ question AND the answer a customer reads.

    Telling a mid-word wrap from a word boundary needs a vocabulary, and the
    page itself is the right one: a genuine mid-word wrap rejoins into a word
    the page uses elsewhere ("Camer"+"a" -> "camera", which appears in the
    prose above the table), while a word boundary does not
    ("installation"+"to" -> "installationto", which appears nowhere). A
    simpler letter-adjacency rule got the first case right and turned
    "installation to ensure" into "installationto ensure".

    With no vocabulary supplied it falls back to space-joining, which is the
    old behaviour: readable, occasionally split.
    """
    t = (text or "").replace("\r", "")
    if not vocab:
        return re.sub(r"\s*\n\s*", " ", t).strip()

    # Walk the line junctions one at a time rather than regex-substituting
    # them all: a cell wraps in a CHAIN ("Live\nCamer\na View"), and a
    # pattern that consumes both sides of a break never gets to examine the
    # next one -- which left "Live Camer a View" half-repaired.
    parts = [p for p in t.split("\n")]
    out = parts[0]
    for nxt in parts[1:]:
        left = re.search(r"(\w+)$", out)
        right = re.match(r"^(\w+)", nxt)
        joined = False
        if left and right:
            l, r = left.group(1), right.group(1)
            # Join when the halves make a word the DOCUMENT uses elsewhere.
            # A "is the left half a real word?" fallback was tried and had to
            # go: the vocabulary is built from extracted text, which contains
            # the same wrapped fragments, so "sectio" and "indicat" are
            # themselves "words" and the test never fired.
            if (l + r).lower() in vocab:
                out += nxt
                joined = True
        if not joined:
            out += " " + nxt
    return re.sub(r"\s+", " ", out).strip()


def _render_markdown(rows: list[list], vocab: set | None = None) -> str:
    """A table as markdown, with the source's own cells and nothing added."""
    clean = []
    for row in rows or []:
        cells = [_unwrap_cell(c, vocab) for c in row]
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


_VOCAB_CACHE: dict[tuple, set] = {}


def _doc_vocab(path: str, pdf) -> set:
    """Every word the WHOLE document uses, lowercased.

    Document-level, not page-level, because the page a table sits on is the
    one place its column headings are guaranteed to be wrapped -- "Section"
    appears only as "Sectio"+"n" there, so a page vocabulary can never
    confirm the rejoin. Elsewhere in the manual the same word is printed in
    full. Extracted once per file and cached; the cost is one full text pass
    per document, not per table.
    """
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return set()
    if key not in _VOCAB_CACHE:
        if len(_VOCAB_CACHE) > 16:
            _VOCAB_CACHE.clear()
        try:
            text = " ".join((p.extract_text() or "") for p in pdf.pages)
        except Exception:
            text = ""
        _VOCAB_CACHE[key] = {w.lower() for w in re.findall(r"\w+", text)}
    return _VOCAB_CACHE[key]


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
            vocab = _doc_vocab(path, pdf)
            for t in page.find_tables():
                md = _render_markdown(t.extract(), vocab)
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
            query: str = "", limit: int = 4,
            require_match: bool = False) -> list[dict]:
    """Verbatim blocks of `kind` from the (source, page) pairs an answer cited.

    Deduped on title+page so the same table found via two chunks appears
    once, then narrowed to the ones `query` is actually about (see
    _most_relevant) and capped, so a broad question cannot return the whole
    manual.
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
            out.append({**b, "source": source})
    # Relevance is judged BEFORE the document-name fallback is applied. That
    # fallback repeats the product name, which is usually in the question
    # too, so scoring it made every untitled block outrank the correctly
    # captioned one: "show me the operating temperature table for MyCheckr
    # Mini" ranked three blocks titled "MyCheckr Mini User Manual-v5" above
    # the table actually captioned "Operation".
    picked = _most_relevant(query, out, limit, require_match)
    for b in picked:
        if not b.get("title"):
            b["title"] = _doc_title(b.get("source", ""))
    return picked


_STOP = {"show", "me", "the", "a", "an", "of", "for", "on", "in", "is", "what",
         "give", "list", "table", "tables", "checklist", "checklists", "full",
         "and", "to", "please", "can", "you", "it", "its", "with", "all"}


def _most_relevant(query: str, blocks: list[dict], limit: int,
                   require_match: bool = False) -> list[dict]:
    """Keep the blocks the question is actually about.

    Every table on a cited page used to come back -- asking for the
    operating temperature table returned six, led by an unlabelled
    current-draw table, because the page also carries supply currents and
    casing temperatures. Scoring on the words the visitor used, against the
    caption first and the cells second, puts the right one at the top and
    drops the unrelated ones entirely.

    If nothing matches, the original order is kept: a weak guess at
    relevance is worse than the document's own order.
    """
    terms = {w for w in re.findall(r"[a-z0-9]+", (query or "").lower())
             if len(w) > 2 and w not in _STOP}
    if not terms or not blocks:
        return [] if require_match else blocks[:limit]

    scored = []
    for b in blocks:
        title = (b.get("title") or "").lower()
        body = (b.get("markdown") or "").lower()
        # Caption weighted above cells: "Operation" naming the table beats a
        # stray mention of the same word in some other table's rows.
        score = sum(3 for t in terms if t in title) + \
                sum(1 for t in terms if t in body)
        scored.append((score, b))

    # For a lookup WE initiated (require_match), one incidental word is not
    # relevance: "how do I reset the password on my Cisco router?" shares
    # "reset" with a coin-system error-code table and came back with three of
    # them. Demand that most of what the visitor actually asked about is
    # present, not that something matched.
    if require_match:
        need = max(1, (len(terms) + 1) // 2)
        scored = [(s, b) for s, b in scored
                  if sum(1 for t in terms
                         if t in (b.get("title") or "").lower()
                         or t in (b.get("markdown") or "").lower()) >= need]
        if not scored:
            return []

    best = max(s for s, _ in scored)
    if best == 0:
        # Nothing on the cited pages relates to the question. Returning the
        # blocks anyway is how "how do I reset my Cisco router password?"
        # came back with three SMART Coin System error-code tables, and how
        # "is MyCheckr better than Yoti?" came back with analytics tables --
        # a confidently irrelevant table is strictly worse than the refusal
        # it replaced, because it looks like an answer.
        return [] if require_match else blocks[:limit]
    keep = [b for s, b in scored if s == best]
    if len(keep) < limit:
        keep += [b for s, b in sorted(scored, key=lambda x: -x[0])
                 if s != best and s > 0][:limit - len(keep)]
    return keep[:limit]


# ── FAQ pairs from a document's own tables and checklists ────────────────

def _row_labels(markdown: str, limit: int = 8) -> list[str]:
    """The first cell of each data row -- what the table is ABOUT.

    These are the content words a customer's question will contain
    ("Temperature", "Humidity"), and FAQ matching embeds the QUESTION text
    only, so a question built from the caption alone ("Operation") would
    never match "what is the operating temperature". The labels are what
    make these entries findable.
    """
    out = []
    for line in markdown.split("\n")[2:]:          # skip header + separator
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] and cells[0] not in out:
            out.append(cells[0])
        if len(out) >= limit:
            break
    return out


def _header_labels(markdown: str, limit: int = 6) -> list[str]:
    head = markdown.split("\n", 1)[0]
    return [c.strip() for c in head.strip().strip("|").split("|") if c.strip()][:limit]


# Captions that are document furniture, never a customer question.
_SKIP_CAPTION = re.compile(
    r"^(change history|contents?|table of contents|revision|index|"
    r"disclaimer|copyright|document revision)\b", re.I)


def _is_useful_table(caption: str, markdown: str, captioned: bool) -> bool:
    """Whether a table is worth becoming an FAQ entry.

    A PDF's table detector finds real spec tables AND page furniture: the
    change-history block, the contents listing, and the invisible grids some
    layouts use for positioning. Left unfiltered those produced entries like
    "MyCheckr Mini User Manual-v5: 3, 4, 5" -- a question nobody will ask,
    answered by a table of page numbers, cluttering the FAQ a human then has
    to weed.

    Two signals do most of the work: a real spec table has at least a couple
    of data rows, and its ROW LABELS are words ("Temperature", "Humidity"),
    not numbers.
    """
    if _SKIP_CAPTION.search(caption or ""):
        return False
    rows = _row_labels(markdown, limit=12)
    if len(rows) < 2:
        return False
    worded = sum(1 for r in rows if re.search(r"[A-Za-z]{3}", r))
    if worded < max(2, len(rows) // 2):
        return False
    # An uncaptioned table whose labels are only a word or two long is
    # usually a layout grid; a real one either has a caption or has content.
    if not captioned and sum(len(r) for r in rows) < 40:
        return False
    return True


def faq_pairs_for_document(path: str, source: str,
                           max_pairs: int = 40) -> list[dict]:
    """Every table and checklist in a document, as ready-made FAQ entries.

    The point is to answer these from the FAQ store rather than the model:
    the answer IS the document's own table, so it is exact, instant, costs
    no tokens, and cannot be hallucinated or refused by the grounding gate.

    Questions carry the caption AND the row/column labels because FAQ
    matching embeds question text only -- "Operation" alone is unfindable,
    "Operation: Temperature, Humidity" matches the question a customer
    actually types.
    """
    if pdfplumber is None or not os.path.exists(path):
        return []
    try:
        with pdfplumber.open(path) as pdf:
            n_pages = len(pdf.pages)
    except Exception as exc:
        logger.warning(f"FAQ pairs: cannot open {path}: {exc}")
        return []

    pairs, seen = [], set()
    for pno in range(1, n_pages + 1):
        for b in tables_on_page(path, pno):
            md = b.get("markdown", "")
            rows = md.count("\n") - 1
            if rows < 1:
                continue
            checklist = _is_checklist_table(md)
            captioned = bool((b.get("title") or "").strip())
            caption = (b.get("title") or "").strip() or _doc_title(source)
            # Checklists are always worth keeping -- the tick column is
            # already strong evidence of intent. Ordinary tables have to
            # earn it.
            if not checklist and not _is_useful_table(caption, md, captioned):
                continue

            labels = _row_labels(md) or _header_labels(md)
            label_txt = ", ".join(labels[:6])
            # A caption is sometimes a sentence of lead-in prose rather than
            # a name ("The Page Help (?) icon in the top-right corner
            # provides detailed"). As a heading it is useless and as a
            # question it reads as nonsense, so fall back to the row labels,
            # which are the part a customer would actually type.
            if len(caption) > 60 or caption.count(" ") > 8:
                caption = _doc_title(source) if not label_txt else ""
            head = f"{caption} checklist".strip() if checklist else caption
            question = f"{head}: {label_txt}".strip(": ") if label_txt else head
            question = " ".join(question.split())[:220]
            if not question:
                continue
            key = question.lower()
            if key in seen:
                continue
            seen.add(key)

            kind = "checklist" if checklist else "table"
            # An uncaptioned table rendered as "**** (page 13)" -- four bare
            # asterisks, because the heading was empty. Fall back to the
            # document's own name so the answer always says what it is.
            heading = (caption or "").strip() or _doc_title(source)
            pairs.append({
                "question": question,
                "answer": f"**{heading}** (page {pno})\n\n{md}",
                "verbatim": kind,
                "kind": kind,
                "page": pno,
            })
            if len(pairs) >= max_pairs:
                return pairs
    return pairs
