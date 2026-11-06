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


# FLASH-CODE TABLES READ OUT ROW BY ROW (2026-09-27). Most flash-code tables
# have a TWO-ROW header -- a spanning label over two sub-columns:
#
#     Flashes |  | Indicated Status / Error | Recommended Action
#     Red | Blue |  |
#     1 | 3 | Unit Not Initialised | ...
#
# (NV200S bezel and BNF, NV9 Spectral, NV200S payout: Red|Blue, Yellow|Cyan,
# Long|Short). Nothing here read them, and "1 | 3" under that header was
# answered as "red 3 flashes = Unit Not Initialised" -- the manual (NV200S
# p.83) says red 1 + blue 3. The fast verifier accepted that 3/3; only a
# thinking one caught it. So each row whose two code cells are numbers is
# also written out as "Red Flashes 1 and Blue Flashes 3: Unit Not
# Initialised", the form the NV9USB+ matrix already gets. A single-row
# header gets the same only when it names flashes or colours ("Red Flashes
# | Blue Flashes", BV30), so spec and pin tables are left alone.
_CODE_AXIS = re.compile(r"flash|long|short|red|blue|green|yellow|cyan|amber|"
                        r"orange|white|purple", re.I)


def _is_int(v: str) -> bool:
    return v.strip().isdigit()


def _code_labels(header: list[str], sub: list[str] | None
                 ) -> tuple[tuple[str, str], int] | None:
    """((label A, label B), first data row) for a flash-code table, else None."""
    if len(header) < 3 or not header[0]:
        return None
    if (sub and not header[1] and len(sub) >= 2 and sub[0] and sub[1]
            and not any(sub[2:])
            and all(len(c) <= 15 and not _is_int(c) for c in sub[:2])):
        return (f"{sub[0]} {header[0]}", f"{sub[1]} {header[0]}"), 2
    # The two count columns need not come first: the NV4000 conveyor table
    # is "Green LED | Red LED | Orange LED | Description", green a state
    # (Solid/Flashing) and red/orange the counts. Read as columns 0-1 it gave
    # nothing, and performance mode answered red 1 + orange 2 with the
    # "2 | 1" row (S30, N4-21). The pair is the last two code columns before
    # the description; the columns before them lead each read-out line.
    for i in range(len(header) - 2):
        if (header[i] and header[i + 1] and not _is_int(header[i])
                and not _is_int(header[i + 1])
                and _CODE_AXIS.search(header[i]) and _CODE_AXIS.search(header[i + 1])
                and not _CODE_AXIS.search(header[i + 2])):
            if i == 0:
                return (header[0], header[1]), 1
            return (header[i], header[i + 1], i, tuple(header[:i])), 1
    return None


def _code_readout(labels: tuple, rows: list[list[str]]) -> list[str]:
    i = labels[2] if len(labels) > 2 else 0
    lead = labels[3] if len(labels) > 3 else ()
    out = []
    for r in rows:
        if len(r) >= i + 3 and _is_int(r[i]) and _is_int(r[i + 1]) and r[i + 2]:
            pre = ", ".join(f"{h} {r[j]}" for j, h in enumerate(lead) if r[j])
            out.append((pre + ", " if pre else "")
                       + f"{labels[0]} {r[i]} and {labels[1]} {r[i + 1]}: {r[i + 2]}")
    return out


# COUNT TABLES UNDER A NON-BLANK HEADER (D-20261026-04). The shared "ITL
# Device Flash Codes" document writes its tables as
#
#     BV Range (BV20, BV30, BV50 and BV100)          <- a plain text line
#     LED Colour Status (Flashes) | Description | Recommended Action
#     Long | Short |
#     4x | 1x | Power Supply to Low | Check power supply
#     2x | Power Supply to high                      <- 4 long, 2 short
#
# _code_labels wants a blank header[1] and plain integers, so none of it was
# read out; "4 long 2 short" was answered with the 1-long-4-short row. The
# product line is not a section either (every chunk has section ""), and it
# is often in the chunk before. code_groups reads these tables line by line,
# from the chunk above through this one, and names the product line each
# table sits under.
_COUNT_RE = re.compile(r"^(\d+)\s*x?$", re.I)
_MODEL_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,4}-?\d{1,4}[A-Z]*\+?(?![A-Za-z0-9])")
_CODES_LINE = re.compile(r"\bcodes?\s*$", re.I)
# A read-out line spell_out wrote (at ingest, or on an earlier pass): not
# document text, so it neither ends a table nor names one.
_READOUT = re.compile(r"^(Read cell by cell\b.*:|.*\b\d+ and .*\b\d+: .*"
                      r"|(?:[^:|]{1,40} )?\d+ flash(?:es)?: .*)$")

# ONE-COUNT TABLES AND THEIR TITLES (D-20261105-08). The same document also
# writes tables with a single count column, under a title with no model code:
#
#     Smart Hopper Range                              <- a plain title line
#     LED Colour | Status (Flashes) | Description
#     RED | 4x | Coin Jam
#     ...
#     SMART Coin System Range
#     LED Colour | Status (Flashes) | Description | Recommended Action
#     RED | 4x | Feeder Calibration Error
#
# Nothing read them, and nothing said which product a "RED | 4x" row is for:
# "red 4 flashes" on a SMART Hopper was answered with the SMART Coin System
# row. Now a short title line names the table under it, and each row is read
# out as "RED 4 flashes: Coin Jam" under that title. The header must name
# flashes in exactly one cell (two is a long/short matrix) followed by a
# column that says what they mean (not "Flashes | Interface", a settings
# table read the same way the document already writes it).
_FLASHES_CELL = re.compile(r"\bflashes\b", re.I)
_MEANING = re.compile(r"descri|error|status|fault|meaning|indicat|cause", re.I)


def _is_title(s: str) -> bool:
    """A short heading line: "Smart Hopper Range", "Bunch Note Feeder"."""
    return (len(s) <= 60 and s[:1].isupper()
            and not s.endswith((".", ":", ",", ";")))


def _single_col(cs: list[str]) -> int | None:
    """The count column of a one-count table header, else None."""
    if sum(1 for c in cs if c) < 2 or not cs[0] or _count(cs[0]) is not None:
        return None
    hits = [i for i, c in enumerate(cs) if _FLASHES_CELL.search(c)]
    if (len(hits) == 1 and hits[0] + 1 < len(cs)
            and _MEANING.search(cs[hits[0] + 1])):
        return hits[0]
    return None


def _count(v: str) -> int | None:
    """4 for "4", "4x", "4 x"; else None."""
    m = _COUNT_RE.match((v or "").strip())
    return int(m.group(1)) if m else None


def _sub_header(line: str) -> tuple[str, str] | None:
    """("Long", "Short") for a sub-header row "Long | Short |", else None."""
    if not is_row(line):
        return None
    cs = cells(line)
    while cs and not cs[-1]:
        cs.pop()
    if (len(cs) == 2 and all(c and len(c) <= 15 and _count(c) is None
                             and _CODE_AXIS.search(c) for c in cs)):
        return cs[0], cs[1]
    return None


def _code_rows(lines: list[str]) -> list:
    """Per line: (table no, heading, read-out line) for a count row, else None."""
    recs: list = [None] * len(lines)
    pending, table, no = None, None, 0
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or (not is_row(s) and _READOUT.match(s)):
            continue
        if not is_row(s):
            table = None
            if len(s) <= 80 and not s.endswith((".", ":")) and _MODEL_RE.search(s):
                pending = s
            elif _CODES_LINE.search(s):
                pass                  # "Error Flash Codes" keeps the title above
            elif _is_title(s):
                pending = s
            else:
                pending = None
            continue
        cs = cells(s)
        if table and cs == table["header"]:
            continue                  # a header repeated on the next page
        nxt = next((l.strip() for l in lines[i + 1:] if l.strip()), "")
        sub = _sub_header(nxt)
        if sub and len(cs) >= 2 and cs[0] and cs[1] and _count(cs[0]) is None:
            no += 1
            table = {"no": no, "heading": pending, "header": cs, "first": None,
                     "act": None, "sub": nxt, "labels": [l if "flash" in l.lower()
                                            else f"{l} flashes" for l in sub]}
            pending = None            # one heading feeds one table
            continue
        col = None if sub else _single_col(cs)
        if col is not None:
            no += 1
            table = {"no": no, "heading": pending, "header": cs, "single": col,
                     "lead": "", "recs": []}
            pending = None
            continue
        if table and table.get("single") is not None:
            k = table["single"]
            n = _count(cs[k]) if len(cs) > k + 1 else None
            if n is None:
                continue
            desc = cs[k + 1]
            if _count(desc) is not None:
                # Two counts side by side: a long/short table whose header
                # was cut off, not this one. Unsay what it was read as.
                for j in table["recs"]:
                    recs[j] = None
                table = None
                continue
            lead = " ".join(c for c in cs[:k] if c) or table["lead"]
            table["lead"] = lead
            if desc:
                recs[i] = (table["no"], table["heading"],
                           f"{lead + ' ' if lead else ''}{n} "
                           f"flash{'' if n == 1 else 'es'}: {desc}")
                table["recs"].append(i)
            continue
        if not table or s == table["sub"]:
            continue
        while cs and not cs[0]:
            cs = cs[1:]
        a = _count(cs[0]) if len(cs) >= 2 else None
        if a is None:
            continue
        b = _count(cs[1])
        act = ""
        if b is not None:
            table["first"] = a
            desc = cs[2] if len(cs) >= 3 else ""
            table["act"] = cs[3] if len(cs) >= 4 else None
        elif table["first"] is not None:
            a, b, desc = table["first"], a, cs[1]
            # A short row with no action cell under an action column: the
            # document merged its group's action over it, the way it merged
            # the long count ("4x | 1x | Power Supply to Low | Check power
            # supply" then "2x | Power Supply to high"). Name it here, or the
            # verifier refuses the action for the 4-long-2-short row.
            if len(cs) == 2 and len(table["header"]) >= 3 and table["act"]:
                act = f". {table['header'][-1]}: {table['act']}"
        else:
            continue
        if desc:
            l0, l1 = table["labels"]
            recs[i] = (table["no"], table["heading"], f"{l0} {a} and {l1} {b}: {desc}{act}")
    return recs


def code_groups(text: str, above: str | None = None
                ) -> list[tuple[str | None, list[str]]]:
    """[(heading or None, read-out lines)] for the count-table rows of `text`.

    `above` is the chunk before in its document: it sets the table, the long
    count and the heading that the first rows of `text` continue. A chunk
    opens with an overlap of the one above (the cut tail of a line, its last
    rows, a header carry_table_context put back); those rows keep the reading
    they had there, never one replayed from the end of `above`.
    """
    body = split_prefix(text)[1].split("\n")
    prev = split_prefix(above)[1].split("\n") if above else []
    tail = [l.strip() for l in prev[-40:] if l.strip()]
    k = 0
    while k < len(body) and (not body[k].strip()
                             or any(t.endswith(body[k].strip()) for t in tail)):
        k += 1
    recs = _code_rows(prev + body[k:])
    n = len(prev)
    mine = []
    for line in body[:k]:
        s = line.strip()
        if s and is_row(s):
            j = next((j for j in range(n - 1, max(n - 40, 0) - 1, -1)
                      if prev[j].strip().endswith(s)), None)
            if j is not None and recs[j]:
                mine.append(recs[j])
    mine += [r for r in recs[n:] if r]
    groups: dict[int, tuple[str | None, list[str]]] = {}
    for no, heading, ln in mine:
        g = groups.setdefault(no, (heading, []))
        if ln not in g[1]:
            g[1].append(ln)
    return list(groups.values())


def _context_above(above: str | None):
    """What a continuation chunk needs from the chunk before it:
    (width, flash-code labels or None, last data row filled), or None when
    `above` does not END in a table."""
    if not above:
        return None
    lines = split_prefix(above)[1].split("\n")
    blocks = _blocks(lines)
    if not blocks:
        return None
    s, e = blocks[-1]
    if sum(1 for l in lines[e:] if l.strip()) > 1:
        return None                       # the table finished; prose follows
    rows = [cells(l) for l in lines[s:e]]
    got = _code_labels(rows[0], rows[1] if len(rows) > 1 else None)
    labels, data = got if got else (None, 1)
    body = rows[data:] or rows
    filled = _fill_down(body, len(rows[0]))
    return len(rows[0]), labels, filled[-1]


def spell_out(text: str, above: str | None = None) -> str:
    """Fill merged cells, read matrices and flash-code tables out; idempotent.

    `above` is the text of the chunk BEFORE this one in its document. When
    this chunk opens with the rows of a table that began there, its merged
    cells are filled from that table's last row and its flash-code rows are
    read out under that table's labels."""
    if not text or "|" not in text:
        return text
    lines = text.split("\n")
    blocks = _blocks(lines)
    if not blocks:
        return text
    carried = (_context_above(above)
               if above and _starts_with_rows(split_prefix(text)[1]) else None)
    out: list[str] = []
    pos = 0
    for bi, (start, end) in enumerate(blocks):
        out.extend(lines[pos:start])
        rows = [cells(l) for l in lines[start:end]]
        header = rows[0]
        axes = _matrix_axes(header)
        # The Confluence copies write the column labels unpadded, one cell
        # short of the data rows: "1 | 2 | 3 | 4" (NV10USB+, BV50). Unread,
        # "4 | PSU too Low | PSU too High" was taken as "4 flashes = PSU".
        if (axes and len(rows) >= 3 and not _is_column_labels(rows[1])
                and all(_CODE_AXIS.search(a) for a in axes)
                and rows[1] and all(_is_int(c) for c in rows[1])
                and len(rows[1]) == len(rows[2]) - 1):
            rows = [header, [""] + rows[1]] + rows[2:]
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
            got = _code_labels(header, rows[1] if len(rows) > 1 else None)
            if got:
                # Fill from the data rows only: the sub-header row ("Red |
                # Blue") must never become the value of a merged cell.
                labels, data = got
                filled = rows[:data] + _fill_down(rows[data:], width)
            elif bi == 0 and carried and len(header) == carried[0]:
                # A continuation: the header and the merged cell's value
                # are in the previous chunk.
                width, labels, last = carried
                data = 0
                filled = _fill_down(rows, width, above=last)
            else:
                labels, data = None, 1
                filled = _fill_down(rows, width) if header and header[0] else rows
            out.extend(_join(r) if r != cells(l) else l
                       for r, l in zip(filled, lines[start:end]))
            if labels:
                reading = [ln for ln in _code_readout(labels, filled[data:])
                           if ln not in text]
                if reading:
                    out.append(READ_MARK)
                    out.extend(reading)
        pos = end
    out.extend(lines[pos:])
    done = "\n".join(out)
    for heading, reading in code_groups(text, above):
        new = [ln for ln in reading if ln not in done]
        if new:
            out.append(f'Read cell by cell under "{heading}":' if heading else READ_MARK)
            out.extend(new)
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
