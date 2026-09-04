"""Cross-product questions: "what do you sell?", "which one supports X?"

WHY THIS EXISTS SEPARATELY FROM RETRIEVAL. The pipeline answers questions
ABOUT a product: it scopes to one manual and searches inside it. A sales
question is the other shape -- it ranges ACROSS products and the answer is
often a list of product names, which appears in no single chunk. Measured
before this module, every question of that shape was refused:

    "Do you have anything that sorts and pays out coins?"  -> refused
    "I have a 24V supply. Which validators can I use?"     -> refused

Neither needs reasoning. The first is a catalogue lookup, the second is a
table lookup, and the data for both is already extracted -- product
overviews from each manual's opening page, and 532 spec tables. What was
missing was somewhere to ask them as a SET rather than one product at a
time.

Nothing here is generated. Product names come from the catalogue and every
quoted value is a cell from a table, so an answer cannot invent a product
or a specification.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

# ── what a sales question looks like ──────────────────────────────────────
#
# Deliberately requires a CROSS-PRODUCT signal ("which/what products", "do
# you have", "anything that"). "What is the supply voltage?" is a question
# about one product and belongs to the normal pipeline; "which products run
# on 24V?" is this module's.
# Up to three words may sit between "which" and the noun -- "which of your
# note validators" -- which a tighter pattern missed.
_CROSS = re.compile(
    r"\b(which|what)\b(?:\s+\w+){0,3}\s+"
    r"\b(products?|models?|validators?|devices?|units?|options?|hoppers?|"
    r"ranges?|ones?)\b"
    r"|\bdo\s+you\s+(have|make|sell|offer|do|stock)\b"
    r"|\banything\s+(that|which|for)\b"
    r"|\brecommend\b|\bsuitable\s+for\b|\bi\s+need\s+(a|an|to)\b",
    re.I)

_LISTING = re.compile(
    r"\b(what|which)\s+(products?|models?|devices?|ranges?)\b.*\b(do\s+you|"
    r"are\s+(there|available)|have)\b|\bfull\s+range\b|\bproduct\s+range\b|"
    r"\bwhat\s+do\s+you\s+(make|sell|offer)\b", re.I)


def is_sales_question(q: str) -> bool:
    return bool(_CROSS.search(q or ""))


# ── spec index ────────────────────────────────────────────────────────────

_INDEX: list[dict] | None = None
_INDEX_KEY: tuple | None = None


def _rows_from_markdown(md: str) -> list[tuple[str, list[str]]]:
    """(row label, other cells) for each data row of a markdown table."""
    out = []
    for i, line in enumerate((md or "").split("\n")):
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells or set("".join(cells)) <= {"-", " "}:
            continue
        if i == 0:                      # header row
            continue
        label, rest = cells[0], [c for c in cells[1:] if c and c != "---"]
        if label and rest:
            out.append((label, rest))
    return out


def build_index(doc_dir: str, source_to_product: dict) -> list[dict]:
    """Flatten every extracted table into product/attribute/value rows.

    source_to_product maps a filename to (product_key, product_name). A
    document with no mapping is skipped rather than filed under a guess --
    an unattributed spec is worse than a missing one in a sales answer.
    """
    import structures

    rows: list[dict] = []
    for fname in sorted(os.listdir(doc_dir)):
        if not fname.lower().endswith(".pdf"):
            continue
        key, name, cat = source_to_product.get(fname, ("", "", ""))
        if not key:
            continue
        path = os.path.join(doc_dir, fname)
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                npages = len(pdf.pages)
        except Exception:
            continue
        for pno in range(1, npages + 1):
            for b in structures.tables_on_page(path, pno):
                title = b.get("title") or ""
                for label, cells in _rows_from_markdown(b.get("markdown", "")):
                    rows.append({
                        "product": key, "product_name": name,
                        "category": cat,
                        "table": title, "attribute": label,
                        "values": cells, "source": fname, "page": pno,
                    })
    logger.info("spec index: %d rows across %d products",
                len(rows), len({r["product"] for r in rows}))
    return rows


def get_index(doc_dir: str, source_to_product: dict) -> list[dict]:
    """Cached index, rebuilt when the document set changes."""
    global _INDEX, _INDEX_KEY
    try:
        key = tuple(sorted(
            (f, os.path.getmtime(os.path.join(doc_dir, f)))
            for f in os.listdir(doc_dir) if f.lower().endswith(".pdf")))
    except OSError:
        key = ()
    if _INDEX is None or key != _INDEX_KEY:
        _INDEX = build_index(doc_dir, source_to_product)
        _INDEX_KEY = key
    return _INDEX


# ── matching a question against the index ─────────────────────────────────

# "24V", "24 volt", "12 VDC", "1.5A", "50°C", "300 notes"
_QTY = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(v\b|volts?\b|vdc\b|a\b|amps?\b|ma\b|kg\b|g\b|mm\b|°?c\b|notes?\b|%)",
    re.I)

_UNIT_ALIASES = {
    "volt": "v", "volts": "v", "vdc": "v", "v": "v",
    "amp": "a", "amps": "a", "a": "a", "ma": "ma",
    "kg": "kg", "g": "g", "mm": "mm", "c": "c", "°c": "c",
    "note": "notes", "notes": "notes", "%": "%",
}


def _quantities(text: str) -> list[tuple[str, str]]:
    out = []
    for num, unit in _QTY.findall(text or ""):
        u = _UNIT_ALIASES.get(unit.lower().strip("."), unit.lower())
        out.append((num, u))
    return out


def find_by_spec(question: str, index: list[dict]) -> list[dict]:
    """Products whose spec tables contain the quantity the question names.

    Matches the NUMBER and its unit, so "24V" finds a 24 V DC row and not a
    24-coins-per-second one. Returns one entry per product with the row that
    matched, because a sales answer names products, not table rows.
    """
    wanted = _quantities(question)
    if not wanted:
        return []
    hits: dict[str, dict] = {}
    for r in index:
        blob = " ".join(r["values"]) + " " + r["attribute"] + " " + r["table"]
        for num, unit in wanted:
            for cnum, cunit in _quantities(blob):
                if cnum == num and cunit == unit:
                    hits.setdefault(r["product"], {
                        "product": r["product"],
                        "product_name": r["product_name"],
                        "category": r.get("category", ""),
                        "attribute": r["attribute"],
                        "table": r["table"],
                        "values": r["values"],
                        "source": r["source"], "page": r["page"],
                    })
                    break
    return list(hits.values())


# ── building the answer ───────────────────────────────────────────────────

_STOP = {"the", "a", "an", "of", "for", "to", "in", "on", "is", "are", "do",
         "you", "your", "i", "have", "any", "anything", "that", "which",
         "what", "with", "and", "or", "me", "my", "need", "want", "can",
         "use", "used", "at", "my", "we", "products", "product", "offer",
         "make", "sell", "does", "it", "its", "there", "how"}


# Promoted to text_utils once more_context.py needed the same thing; kept
# under the private name here so this module's call sites did not have to
# change with it.
from text_utils import stem as _stem


def _terms(q: str) -> set:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", (q or "").lower())
            if len(w) > 2 and w not in _STOP}


def _stems_in(text: str) -> set:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower())}


def _overviews(faq_items: list[dict]) -> list[dict]:
    """Harvested product definitions, which are verbatim from the manuals."""
    return [f for f in faq_items
            if f.get("origin") == "harvested"
            and re.match(r"^what is the .+ and what does it do\?$",
                         (f.get("question") or "").strip(), re.I)]


def answer(question: str, faq_items: list[dict], index: list[dict],
           catalog_tree: dict | None = None) -> dict | None:
    """A cross-product answer, or None to let the normal pipeline run.

    Three shapes, in order of specificity:

      a quantity  -> which products meet it, from the spec index
      a need      -> which product descriptions match, from the overviews
      neither     -> the catalogue, when the question is "what do you sell?"

    Every product name comes from the catalogue and every quoted value from
    a table cell, so this cannot name a product that does not exist or
    attribute a specification to the wrong one.
    """
    if not is_sales_question(question):
        return None

    # 1. A concrete requirement ("I have a 24V supply").
    hits = find_by_spec(question, index)
    if hits:
        terms = _terms(question)
        # When the question names a product TYPE ("note validators"), keep
        # only products whose name or table mentions it -- otherwise a 24V
        # biometric reader is offered to someone buying a note validator.
        # Match the product TYPE against the catalogue category first.
        # Matching the product name and table caption alone sent someone
        # asking for "note validators" to the SMART Coin System, whose own
        # manual calls it a "bulk coin validator" -- the word matched, the
        # product category did not.
        typed = [h for h in hits if terms & _terms(h.get("category", ""))]
        if not typed:
            typed = [h for h in hits
                     if terms & _terms(h["product_name"] + " " + h["table"])]
        chosen = typed or hits
        lines = ["Based on the specifications in our documentation:", ""]
        for h in sorted(chosen, key=lambda x: x["product_name"]):
            vals = ", ".join(v for v in h["values"] if v and v != "---")
            lines.append(f"- **{h['product_name']}** — {h['attribute']}: "
                         f"{vals}  _(page {h['page']})_")
        if typed and len(typed) < len(hits):
            others = sorted({h["product_name"] for h in hits} -
                            {h["product_name"] for h in typed})
            lines += ["", "Other products that also match: "
                      + ", ".join(others) + "."]
        return {"answer": "\n".join(lines), "kind": "spec_match",
                "products": [h["product"] for h in chosen]}

    # 2. A need described in words ("anything that sorts and pays out coins").
    terms = _terms(question)
    if terms:
        scored = []
        for f in _overviews(faq_items):
            body = _stems_in((f.get("question") or "") + " "
                             + (f.get("answer") or ""))
            overlap = len(terms & body)
            if overlap:
                scored.append((overlap, f))
        scored.sort(key=lambda t: -t[0])
        # One shared content word is enough here, and the earlier
        # third-of-the-question bar was not: overviews are a handful of
        # entries describing distinct products, so "coin" or "age" already
        # identifies one. Requiring two terms filtered out every real match
        # ("anything that sorts and pays out coins" -> the coin system scored
        # 1) and the question fell through to a generic catalogue listing.
        # Dedupe on the answer text: the same product definition is
        # harvested from every manual that prints it, so MyCheckr's overview
        # was listed twice under one question.
        best, seen_body = [], set()
        for n, f in scored:
            if n < 1:
                continue
            body = re.sub(r"\s+", " ", (f.get("answer") or "")[:120]).strip()
            if body in seen_body:
                continue
            seen_body.add(body)
            best.append(f)
            if len(best) == 2:
                break
        if best:
            lines = ["Here is what we make that fits:", ""]
            for f in best:
                body = re.sub(r"\n\n\(Source:.*?\)\s*$", "",
                              f.get("answer") or "", flags=re.S).strip()
                lines += [f"- {body}", ""]
            return {"answer": "\n".join(lines).strip(),
                    "kind": "overview_match"}

    # 3. Plain "what do you sell?".
    if _LISTING.search(question) and catalog_tree:
        lines = ["Our product ranges:", ""]
        for c in catalog_tree.get("categories", []):
            prods = [p["name"] for p in c.get("products", [])
                     if p.get("name")]
            if prods:
                lines.append(f"- **{c['name']}**: " + ", ".join(prods))
        if len(lines) > 2:
            lines += ["", "Tell me which one you are interested in and I can "
                      "answer from its manual."]
            return {"answer": "\n".join(lines), "kind": "catalogue"}
    return None
