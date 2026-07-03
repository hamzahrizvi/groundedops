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


def chunk_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    if not text or not text.strip():
        return []

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
