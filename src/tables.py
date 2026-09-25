"""Tables that read the same way to a search engine, a model and a person.

WHY THIS EXISTS. Measured on the index on 2026-09-25: 479 of 1749 chunks
hold a pipe table, and three separate faults made the facts in them hard
to reach. None is a ranking problem, so no reranker change could fix them.

  1. A table that runs onto the next page loses its heading and header.
     ingest extracts page by page, so the rows on page 32 start a "new"
     table with no column names and no section: 192 chunks. The BV30 row
     "4 | 1 | PSU Voltage too Low" sat under "[BV30 User Manual-v1]" alone
     and ranked 44th for "4 red flashes then 1 blue flash". Nothing in it
     said "Red Flashes" or "Blue Flashes".
  2. Merged cells arrive as blanks. "| 2 | Interface Checksum" means "red
     flashes 3, blue flashes 2" -- the 3 is in a row above, possibly on the
     previous page. 67 chunks have rows like this, and the model has to
     count upwards to read them.
  3. A two-dimensional table reads wrongly. The NV9USB+ flash codes are a
     matrix of long flashes (rows) by short flashes (columns); the model
     read the wrong column and answered "Note Path Open" for 1 long + 2
     short (the manual says Note Path Jam).

So a continuation carries its table's context (carry_table_context, at
ingest and by backfill_table_context.py for an existing index), merged
cells are filled, and a matrix is also read out cell by cell (spell_out,
at ingest and again on the passages handed to the model, which is
idempotent). Pure text in, text out; nothing here touches the index.
"""
from __future__ import annotations

import re

READ_MARK = "Read cell by cell:"
_EMPTY = {"", "-", "—", "n/a", "na"}


def is_row(line: str) -> bool:
    """A pipe-table row: at least one separator between two cells."""
    s = (line or "").strip()
    return s.count("|") >= 1 and len(s) > 1 and not s.startswith("[")


def cells(line: str) -> list[str]:
    """The cells of a row. A leading "|" is an EMPTY first cell (a merged
    cell), not decoration; a trailing one likewise."""
    return [c.strip() for c in line.strip().split("|")]


def _join(cs: list[str]) -> str:
    return " | ".join(cs)


def _blocks(lines: list[str]) -> list[tuple[int, int]]:
    """(start, end) line ranges of consecutive table rows."""
    out, i = [], 0
    while i < len(lines):
        if is_row(lines[i]):
            j = i
            while j < len(lines) and is_row(lines[j]):
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def _is_column_labels(row: list[str]) -> bool:
    rest = row[1:]
    return (bool(row) and row[0] == "" and len(rest) >= 2
            and all(c and len(c) <= 15 for c in rest))


def _matrix_axes(header: list[str]) -> tuple[str, str] | None:
    """A matrix header names its two axes and leaves the other cells empty:
    "Number of Long Flashes | Number of Short Flashes |  |  |"."""
    named = [c for c in header if c]
    if len(named) == 2 and header[0] and header[1] and not any(header[2:]):
        return header[0], header[1]
    return None


def _fill_down(rows: list[list[str]], width: int,
               above: list[str] | None = None) -> list[list[str]]:
    """Fill the leading blank cells of a row from the row above -- the
    merged-cell convention. Only LEADING blanks, and only when the row is as
    wide as the table: a blank in the middle is a real empty cell."""
    out, prev = [], above
    for r in rows:
        if prev and len(r) == width and r and r[0] == "":
            k = 0
            while k < len(r) - 1 and r[k] == "":
                k += 1
            r = prev[:k] + r[k:]
        out.append(r)
        if any(r):
            prev = r
    return out


def spell_out(text: str) -> str:
    """Fill merged cells and read matrices out; idempotent."""
    if not text or "|" not in text:
        return text
    lines = text.split("\n")
    blocks = _blocks(lines)
    if not blocks:
        return text
    out: list[str] = []
    pos = 0
    for start, end in blocks:
        out.extend(lines[pos:start])
        rows = [cells(l) for l in lines[start:end]]
        header = rows[0]
        axes = _matrix_axes(header)
        if axes and len(rows) >= 3 and _is_column_labels(rows[1]):
            # A matrix is never filled down: its column-label row starts
            # with a blank on purpose. Already read out -> leave it alone.
            out.extend(lines[start:end])
            if READ_MARK in text:
                pos = end
                continue
            col_labels = rows[1]
            reading = []
            for r in rows[2:]:
                if not r or not r[0]:
                    continue
                for ci in range(1, min(len(r), len(col_labels))):
                    v = r[ci]
                    if v.lower() in _EMPTY:
                        continue
                    reading.append(f"{axes[0]} {r[0]} and {axes[1]} "
                                   f"{col_labels[ci]}: {v}")
            if reading:
                out.append(READ_MARK)
                out.extend(reading)
        else:
            width = len(header)
            filled = _fill_down(rows, width) if header and header[0] else rows
            out.extend(_join(r) if r != cells(l) else l
                       for r, l in zip(filled, lines[start:end]))
        pos = end
    out.extend(lines[pos:])
    return "\n".join(out)


def split_prefix(text: str) -> tuple[str, str]:
    """("[Doc — Section]", body) for an indexed chunk; ("", text) if none."""
    if text.startswith("[") and "\n" in text:
        first, rest = text.split("\n", 1)
        if first.endswith("]"):
            return first, rest
    return "", text


def _table_tail(text: str) -> tuple[list[str] | None, list[str] | None]:
    """(header cells, last row cells) of the LAST table in `text`."""
    lines = split_prefix(text)[1].split("\n")
    blocks = _blocks(lines)
    if not blocks:
        return None, None
    s, e = blocks[-1]
    # The table must END the chunk (a page footer line may follow it); a
    # table followed by prose has finished, and what comes next is not its
    # continuation.
    if sum(1 for l in lines[e:] if l.strip()) > 1:
        return None, None
    rows = [cells(l) for l in lines[s:e]]
    filled = _fill_down(rows, len(rows[0])) if rows[0] and rows[0][0] else rows
    return rows[0], filled[-1]


def _starts_with_rows(body: str) -> bool:
    first = next((l for l in body.split("\n") if l.strip()), "")
    return is_row(first)


def carry_table_context(texts: list[str], sections: list[str],
                        doc: str) -> tuple[list[str], list[str]]:
    """Give a table's continuation chunk the context its first part had.

    `texts` are the ENRICHED chunks of one document in order ("[doc — s]\\n
    body"), `sections` their section metadata. A chunk with no section whose
    body opens with table rows, following a chunk that ends in a table of the
    same width, is a continuation: it takes the previous chunk's section and
    breadcrumb, the table's header row, and the merged-cell values of the
    previous chunk's last row. Chunks are processed in order, so a table
    spanning three pages is carried through all of them.
    """
    texts, sections = list(texts), list(sections)
    for i in range(1, len(texts)):
        if sections[i]:
            continue
        prefix, body = split_prefix(texts[i])
        if not _starts_with_rows(body):
            continue
        header, last = _table_tail(texts[i - 1])
        if not header:
            continue
        lines = body.split("\n")
        s, e = _blocks(lines)[0]
        rows = [cells(l) for l in lines[s:e]]
        width = len(header)
        if not any(abs(len(r) - width) <= 0 for r in rows[:3]):
            continue                      # a different table, not a continuation
        if rows[0] != header:
            rows = [header] + _fill_down(rows, width, above=last)
        new_body = "\n".join(lines[:s] + [_join(r) for r in rows] + lines[e:])
        sec = sections[i - 1]
        if sec:
            prefix = f"[{doc} — {sec}]"
            sections[i] = sec
        texts[i] = (prefix + "\n" if prefix else "") + new_body
    return texts, sections
