import re

from text_utils import LIST_LINE_RE, STEP_HEADER_RE

# Cap on consecutive list lines grouped into one atomic unit when no
# "Step N" boundary is present to split on. Prevents a long, loosely
# blank-line-separated list section from becoming one giant indivisible
# block (see _split_into_units for the full explanation).
MAX_LIST_UNIT_LINES = 6


def _split_into_units(text: str) -> list[str]:
    """
    Split text into atomic "units" that the chunk-packer below will then
    pack up to `size` characters per chunk.

    BUG FIXED: previously, once a blank-line-separated block was judged
    "list-like" (>=2 list-marker lines, or >=3 short lines), the ENTIRE
    block became ONE atomic unit, regardless of length. If the source PDF
    extraction didn't insert blank lines between adjacent sections (a
    common artifact), an entire multi-step section — e.g. "Step 4" through
    "Step 8" plus "Installer Notes" — would be glued into a single
    indivisible chunk that could never be split, even when it badly
    exceeded the target chunk size. This meant a query about Step 4 alone
    would retrieve a chunk containing Step 4 through Step 8 and unrelated
    installer notes, and any per-chunk relevance/extraction scoring had no
    way to separate the genuinely relevant lines from the rest.

    FIX: list-like blocks are now split into sub-groups first at "Step N"
    headers (a natural, reliable section boundary in this domain), and
    further capped at MAX_LIST_UNIT_LINES lines per sub-group when no
    Step header appears for a while. This lets the size-based chunk
    packer below actually split between unrelated sections instead of
    being forced to keep them together.
    """
    blocks = re.split(r"\n\s*\n", text)
    units: list[str] = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        list_like_count = sum(1 for line in lines if LIST_LINE_RE.match(line))
        short_line_count = sum(1 for line in lines if len(line.split()) <= 8)

        if list_like_count >= 2 or (len(lines) >= 3 and short_line_count >= 2):
            sub_groups: list[list[str]] = []
            current_group: list[str] = []

            for line in lines:
                if STEP_HEADER_RE.match(line) and current_group:
                    sub_groups.append(current_group)
                    current_group = [line]
                else:
                    current_group.append(line)
                    if len(current_group) >= MAX_LIST_UNIT_LINES:
                        sub_groups.append(current_group)
                        current_group = []

            if current_group:
                sub_groups.append(current_group)

            units.extend("\n".join(g) for g in sub_groups if g)
        else:
            prose = " ".join(lines)
            sentences = re.split(r"(?<=[.!?])\s+", prose)
            units.extend([s.strip() for s in sentences if s.strip()])

    return units


TABLE_OPEN = "<<<GO_TABLE>>>"
TABLE_CLOSE = "<<</GO_TABLE>>>"
_TABLE_RE = re.compile(
    re.escape(TABLE_OPEN) + r"\s*(.*?)\s*" + re.escape(TABLE_CLOSE), re.DOTALL)


def strip_table_fences(text: str) -> str:
    """Remove the table sentinels, leaving the table text itself.

    Called on every chunk before it is stored, so the markers exist only
    between extraction and chunking and never reach the index, BM25 term
    counts, an embedding or a prompt.
    """
    return (text.replace(TABLE_OPEN + "\n", "").replace("\n" + TABLE_CLOSE, "")
                .replace(TABLE_OPEN, "").replace(TABLE_CLOSE, ""))


def _split_table_rows(body: str, size: int) -> list[str]:
    """Split an oversized table at ROW boundaries, repeating its first row.

    Only used when a single table exceeds the chunk size and therefore
    cannot be kept whole. Repeating the first row costs a little duplication
    and keeps each piece self-describing -- a bare run of "0.10 Kg" lines
    with the header left behind in the previous chunk is not retrievable by
    anything.
    """
    rows = [r for r in body.split("\n") if r.strip()]
    if len(rows) <= 1:
        return [body]

    # _render_table emits "label: value" lines for two-column tables and
    # pipe-separated lines for wider ones. Only the latter has a header row
    # worth repeating -- a two-column spec list is all data, and copying its
    # first line into every piece would duplicate a real measurement rather
    # than caption anything.
    has_header = " | " in rows[0]
    header = rows[0] if has_header else None
    rest = rows[1:] if has_header else rows

    def start():
        return [header] if header else []

    out, cur = [], start()
    for row in rest:
        candidate = "\n".join(cur + [row])
        if len(candidate) > size and len(cur) > (1 if header else 0):
            out.append("\n".join(cur))
            cur = start() + [row]
        else:
            cur.append(row)
    if len(cur) > (1 if header else 0):
        out.append("\n".join(cur))
    return out or [body]


def _extract_table_units(text: str, size: int) -> list[str]:
    """Split `text` into units, where each fenced table is ONE unit.

    Prose between tables is handed to the ordinary unit splitter; tables are
    emitted whole (or row-split if oversized), so the packer below can never
    cut through the middle of one.
    """
    units: list[str] = []
    pos = 0
    for m in _TABLE_RE.finditer(text):
        before = text[pos:m.start()]
        if before.strip():
            units.extend(_split_into_units(before))
        body = m.group(1).strip()
        if body:
            units.extend(_split_table_rows(body, size) if len(body) > size
                         else [body])
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        units.extend(_split_into_units(tail))
    return units


def chunk_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    if not text or not text.strip():
        return []

    # Tables first, so a spec table is never cut across two chunks.
    if TABLE_OPEN in text:
        units = _extract_table_units(text, size)
    else:
        units = _split_into_units(text)
    if not units:
        return []

    chunks: list[str] = []
    current = ""

    for unit in units:
        separator = "\n" if ("\n" in unit or "\n" in current) else " "
        proposed = (current + separator + unit).strip() if current else unit

        if len(proposed) > size and current:
            chunks.append(current.strip())

            if overlap and chunks:
                prev = chunks[-1]
                # Try to cut the carried-forward overlap at a word boundary
                # rather than mid-word.
                overlap_text = prev[-overlap:].strip()
                space_idx = overlap_text.find(" ")
                if space_idx > 0:
                    overlap_text = overlap_text[space_idx + 1:]

                if "\n" in unit:
                    current = (overlap_text + "\n" + unit).strip()
                else:
                    current = (overlap_text + " " + unit).strip()
            else:
                current = unit
        else:
            current = proposed

    if current:
        chunks.append(current.strip())

    return chunks
