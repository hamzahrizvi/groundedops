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
    # Nouns that only ever mean "across the catalogue".
    r"\b(which|what)\b(?:\s+\w+){0,3}\s+"
    r"\b(products?|models?|validators?|hoppers?|ranges?)\b"
    # "device", "unit", "option" and "one" are also ordinary manual
    # vocabulary -- "which device settings", "what options are available in
    # Ads mode", "what units is the threshold in" are all questions about
    # ONE product. Measured: they were 4 of the 10 support questions this
    # module was hijacking. So those nouns count only with an explicit
    # "your" framing, which no in-manual question has.
    r"|\b(which|what)\b(?:\s+of)?\s+your\s+(?:\w+\s+){0,2}"
    r"\b(devices?|units?|options?|ones?|machines?)\b"
    r"|\bdo\s+you\s+(have|make|sell|offer|do|stock)\b"
    r"|\banything\s+(that|which|for)\b"
    # "I need A validator" is someone buying. "I need TO mount it" is
    # someone who already owns it and is reading the manual -- the single
    # biggest source of misrouted support questions, and the reason
    # "what size screws do I need to mount the MyCheckr?" was answered
    # with a product list instead of the mounting instructions on page 6.
    r"|\brecommend\b|\bsuitable\s+for\b|\bi\s+need\s+(a|an)\b",
    re.I)

_LISTING = re.compile(
    r"\b(what|which)\s+(products?|models?|devices?|ranges?)\b.*\b(do\s+you|"
    r"are\s+(there|available)|have)\b|\bfull\s+range\b|\bproduct\s+range\b|"
    r"\bwhat\s+do\s+you\s+(make|sell|offer)\b", re.I)


def is_sales_question(q: str) -> bool:
    return bool(_CROSS.search(q or ""))


# COMMERCIAL questions are a different thing from the catalogue-navigation
# ones _CROSS matches, and conflating them is what let a price question reach
# the model. "which products run on 24V" is answerable FROM THE DOCUMENTS;
# "what is the price for an NV9" is not in the corpus at any scope, because
# no manual contains a price.
#
# Observed before this existed: "what is the price for a nv9 st" matched
# nothing here, so _sales_answer returned None before it read sales_mode, the
# operator's configured deflect never fired, and the pipeline answered from
# the manual -- "The NV9 Spectral is offered at a mid-range price, delivering
# casino-level security", with six pages cited behind it. A marketing claim
# invented to fill a price question is the worst thing this system can do:
# it is confident, it is sourced, and it is not an answer.
#
# Deliberately NOT in this pattern:
#   "how much" on its own  -- "how much does it weigh", "how much power does
#                             it draw" are ordinary spec questions
#   "order"  on its own    -- "in order to" appears throughout these manuals
#   "warranty"             -- warranty TERMS may genuinely be documented;
#                             needs checking against the corpus before it is
#                             treated as a question only sales can take
# REWRITTEN 2026-09-19 from a list of observed words into the semantic
# field, matched on STEMS. The list version missed "are there any recurring
# fees for using it" -- found by a scripted customer conversation -- and a
# fees question reaching the model is the same failure "price" was added to
# stop, through a word nobody had typed yet. Listing words customers have
# already used cannot cover the next one; listing the field, with
# morphology, comes much closer.
#
# Four groups, because they are four different questions a visitor asks and
# each is one the documentation cannot answer at ANY scope:
#
#   MONEY    what does it cost me      price, fee, subscription, licence
#   COMMERCE how do I buy it           buy, purchase, order, quote
#   SUPPLY   when can I have it        lead time, stock, delivery
#   CHANNEL  who do I buy it from      reseller, distributor, dealer
#
# Stems rather than whole words: "fee" covers fees, "licen" covers licence /
# license / licensing, "subscri" covers subscribe / subscription. The cost
# of a prefix is a false positive, so anything ambiguous IN THIS CORPUS is
# excluded below and only admitted in a phrase that pins the money sense.
# "fee" is spelled out rather than given the \w* treatment the others get:
# the prefix matched "FEEDs it a torn fiver", which is a note-handling
# question, not a commercial one. Found by scenario 03 on 2026-09-19.
# "deposit" is left out for the same class of reason -- in a coin hopper's
# manual it is what the customer does with a coin.
# `quot\w*` matched "the quotes around the value" and `subscri\w*` matched
# "subscribe to age result events" -- both API questions this corpus really
# gets. A quote is commercial when it is a thing you get or ask for; a
# subscription is, a subscribe verb is not.
_MONEY = (r"\bfees?\b"
          # Warranty: no manual in the corpus states one (0 hits for
          # "warrant" on 2026-09-25), so it was refused with FAQ
          # suggestions instead of reaching the operator. "guarantee"
          # is a VERB in the manuals ("to guarantee the best
          # performance"), admitted only as the noun.
          r"|\bwarrant(?:y|ies)\b|\bguarantee\s+(?:period|terms?|cover)\b"
          r"|\b(?:a|any|the|with|under|what)\s+guarantee\b"
          r"|\bquotations?\b|\b(?:a|another|for\s+a|get\s+a|request\s+a"
          r"|send\s+(?:me\s+)?a|provide\s+a|give\s+(?:me\s+)?a)\s+quote\b"
          r"|\bquote\s+(?:for|on)\b"
          r"|\b(?:pric|cost|tariff|rebate|discount|surcharg"
          r"|subscription|licen[cs]|rental|renting|leas(?:e|ing)|hire\s+charge"
          r"|invoic|budget|afford|expensive|cheap)\w*"
          r"|\bhow\s+much\s+(?:is|are|does\s+it\s+cost|would|will)\b"
          r"|[£$€]\s*\d|\bex\s+vat\b|\bplus\s+vat\b"
          r"|\bper\s+(?:unit|device|licence|license|seat|month|year)\b")

_COMMERCE = (r"\b(?:buy|buying|purchas|reorder)\w*"
             r"|\bplace\s+an?\s+order\b|\bminimum\s+order\b|\bmoq\b"
             r"|\border\s+(?:one|some|them|a\s+few|\d+)\b")

# "availability" alone matched "the availability of the RS232 port"; it is
# commercial only next to stock, delivery or ordering.
_SUPPLY = (r"\blead[\s-]?time\b|\bin\s+stock\b|\bout\s+of\s+stock\b"
           r"|\bstock\s+levels?\b"
           r"|\b(?:stock|product|unit|order|purchase)\s+availability\b"
           r"|\bavailability\s+(?:of\s+stock|to\s+(?:order|buy|purchase)"
           r"|for\s+(?:order|purchase|sale))\b"
           r"|\bavailable\s+to\s+(?:buy|order|purchase)\b"
           r"|\bhow\s+soon\s+can\s+(?:you|we|i)\b"
           r"|\b(?:when|how\s+quickly)\s+can\s+you\s+(?:deliver|ship|send)\b"
           r"|\bdelivery\s+(?:time|date|lead|cost|charge)\w*")

# "supplier" on its own is a power supplier in an installation question;
# the channel sense is "your/a/local supplier", "who supplies".
_CHANNEL = (r"\b(?:reseller|distributor|dealer|stockist)s?\b"
            r"|\b(?:your|a|an|local|nearest|authori[sz]ed|official|approved"
            r"|uk|find\s+a)\s+suppliers?\b|\bwho\s+(?:supplies|sells|stocks)\b")

# AMBIGUOUS IN THIS CORPUS, so admitted only in a phrase that fixes the
# money sense. Each of these appears in the manuals meaning something else:
#   charge   battery/capacitor charging
#   rate     baud rate, error rate, acceptance rate
#   pay      "pay out" is what a coin hopper does
#   free     "free of debris", "free-running"
#   order    "in order to", covered above by its money phrasings only
_MONEY_SENSE_ONLY = (r"\b(?:extra|additional|service|monthly|annual|any|hidden)"
                     r"\s+charges?\b|\bcharges?\s+for\s+(?:the\s+)?(?:use|using"
                     r"|support|service|licen[cs]e)\b"
                     r"|\bpay(?:ment|able)\b|\bpay\s+for\b"
                     r"|\brate\s+card\b|\bday\s+rate\b")

_COMMERCIAL = re.compile(
    "|".join((_MONEY, _COMMERCE, _SUPPLY, _CHANNEL, _MONEY_SENSE_ONLY)), re.I)


def is_commercial_question(q: str) -> bool:
    """Price, availability, or who-to-buy-from — a question the product
    documentation cannot answer at any scope."""
    return bool(_COMMERCIAL.search(q or ""))


# ── spec index ────────────────────────────────────────────────────────────

_INDEX: list[dict] | None = None
_INDEX_KEY: str | None = None

# The built index, persisted. Same reasoning as the FAQ vector cache in
# faq_store: building it is the slowest single thing in a cold process --
# 151s measured, 832 pages of pdfplumber table extraction across 14 manuals
# -- and it was being paid on EVERY start, because the cache above is a
# module global and dies with the process. Warm, loading it is milliseconds.
_INDEX_CACHE = os.getenv("SPEC_INDEX_CACHE", "spec_index.json")


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
        # Opened ONCE for the whole document and handed to tables_on_page,
        # which would otherwise reopen it for every page -- this used to be
        # one open to count the pages plus one per page after it.
        try:
            import pdfplumber
            pdf = pdfplumber.open(path)
        except Exception:
            continue
        try:
            for pno in range(1, len(pdf.pages) + 1):
                for b in structures.tables_on_page(path, pno, pdf=pdf):
                    title = b.get("title") or ""
                    for label, cells in _rows_from_markdown(b.get("markdown", "")):
                        rows.append({
                            "product": key, "product_name": name,
                            "category": cat,
                            "table": title, "attribute": label,
                            "values": cells, "source": fname, "page": pno,
                        })
        finally:
            pdf.close()
    logger.info("spec index: %d rows across %d products",
                len(rows), len({r["product"] for r in rows}))
    return rows


def _fingerprint(doc_dir: str, source_to_product: dict) -> str:
    """What the index was built FROM, as one comparable string.

    The document set, and also the filename -> product mapping: filing a
    manual under a different product rewrites every row it contributed. The
    in-process cache could ignore that and usually get away with it, since a
    reassignment in the console was followed by a restart soon enough. A
    cache that SURVIVES the restart cannot, so the mapping is in the key.
    """
    import hashlib
    import json as _json
    try:
        files = sorted((f, os.path.getmtime(os.path.join(doc_dir, f)))
                       for f in os.listdir(doc_dir) if f.lower().endswith(".pdf"))
    except OSError:
        files = []
    mapping = sorted((k, list(v)) for k, v in (source_to_product or {}).items())
    blob = _json.dumps({"v": 1, "files": files, "mapping": mapping},
                       sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _load_index_from_disk(key: str) -> list[dict] | None:
    """The cached index if it was built from exactly this input, else None.

    A missing, stale or unreadable cache is a miss, never an error: the
    worst case is paying the build once, which is what happened every time
    before this existed.
    """
    import jsonstore
    data = jsonstore.load(_INDEX_CACHE, None, label="spec index cache")
    if not isinstance(data, dict) or data.get("key") != key:
        return None
    rows = data.get("rows")
    return rows if isinstance(rows, list) else None


def _save_index_to_disk(key: str, rows: list[dict]) -> None:
    import jsonstore
    try:
        jsonstore.save(_INDEX_CACHE, {"key": key, "rows": rows},
                       label="spec index cache", indent=None)
    except Exception as exc:
        # Non-fatal by design: without it the next start pays the build.
        logger.warning("could not write the spec index cache: %s", exc)


def get_index(doc_dir: str, source_to_product: dict) -> list[dict]:
    """Cached index, rebuilt when the documents or their filing change."""
    global _INDEX, _INDEX_KEY
    key = _fingerprint(doc_dir, source_to_product)
    if _INDEX is not None and key == _INDEX_KEY:
        return _INDEX

    rows = _load_index_from_disk(key)
    if rows is None:
        rows = build_index(doc_dir, source_to_product)
        _save_index_to_disk(key, rows)
    else:
        logger.info("spec index loaded from disk: %d rows", len(rows))
    _INDEX, _INDEX_KEY = rows, key
    return _INDEX


# ── matching a question against the index ─────────────────────────────────

# "24V", "24 volt", "12 VDC", "1.5A", "50°C", "300 notes"
#
# Number and unit are matched in two steps, not one `(\d+...)\s*(unit)`
# pattern under findall. That one restarted at every digit of a run and
# re-scanned the rest of it before failing, so a long unit-less digit string
# was quadratic (CodeQL py/polynomial-redos) -- and `text` includes whole spec
# table rows, not just the question. A number match here never fails once
# started, so finditer walks each digit run once.
_NUM = re.compile(r"\d+(?:\.\d+)?")
_UNIT = re.compile(
    r"(v\b|volts?\b|vdc\b|a\b|amps?\b|ma\b|kg\b|g\b|mm\b|°?c\b|notes?\b|%)",
    re.I)

_UNIT_ALIASES = {
    "volt": "v", "volts": "v", "vdc": "v", "v": "v",
    "amp": "a", "amps": "a", "a": "a", "ma": "ma",
    "kg": "kg", "g": "g", "mm": "mm", "c": "c", "°c": "c",
    "note": "notes", "notes": "notes", "%": "%",
}


def _quantities(text: str) -> list[tuple[str, str]]:
    text = text or ""
    out = []
    for m in _NUM.finditer(text):
        j = m.end()
        while j < len(text) and text[j].isspace():
            j += 1
        um = _UNIT.match(text, j)
        if not um:
            continue
        unit = um.group(1)
        u = _UNIT_ALIASES.get(unit.lower().strip("."), unit.lower())
        out.append((m.group(0), u))
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
    from catalog import is_shared_product
    hits: dict[str, dict] = {}
    for r in index:
        # A shared document's table is real, but its "product" is the
        # category's document bucket, and an answer that names products
        # cannot recommend "General (shared docs)".
        if is_shared_product(r.get("product")):
            continue
        blob =" ".join(r["values"]) + " " + r["attribute"] + " " + r["table"]
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


# ── comparisons ───────────────────────────────────────────────────────────
#
# "What is the difference between the NV9 and the NV22?" is this module's
# shape too: it ranges ACROSS products and the answer appears in no single
# chunk. It arrives here rather than at retrieval because one top-k over the
# whole corpus does not guarantee both products are represented in it --
# measured, "how does the NV9USB+ differ from the NV9 Spectral?" retrieved
# both manuals and still refused, because nothing made the coverage
# symmetric. Asking each product separately does.
#
# Two shapes, and the first matters most in this corpus:
#
#   SAME TABLE   a range manual prints its own comparison -- "Product |
#                Interfaces" lists NV9 Spectral, NV11 Spectral and NV22 as
#                ROW LABELS. Both sides are already side by side and
#                attributed; the answer is to quote those rows. This also
#                covers products with no manual of their own, which no
#                per-product fan-out could reach.
#
#   PER PRODUCT  otherwise, take each named product's spec rows and keep the
#                attributes BOTH have and DISAGREE on. That is what a
#                difference is, and confining it to shared attributes stops
#                the answer listing one product's features as though the
#                other lacked them when its manual simply never says.
#
# Nothing is generated either way: every value is a table cell and every row
# stays attributed to the manual it came from, so a specification cannot
# migrate to the wrong product -- the mistake a comparison is most likely to
# make, and the most convincing when it makes it.

_COMPARE = re.compile(
    r"\b(difference|differences|differ|differs|compare|comparison|compared)\b"
    r"|\bvs\.?\b|\bversus\b"
    r"|\bbetter\s+than\b", re.I)


def is_comparison(question: str) -> bool:
    return bool(_COMPARE.search(question or ""))


def _label_token(label: str) -> str:
    """The model designator a person would actually type: "NV9 Spectral" ->
    "nv9". Matching the whole label fails the common case, because nobody
    writes the range name out in full."""
    toks = re.findall(r"[a-z0-9]+", (label or "").lower())
    return toks[0] if toks else ""


def _named_in(question: str, label: str) -> bool:
    """Whether the question names this row label, as a WORD.

    Word boundaries matter more here than anywhere else in this module:
    flattened matching makes "nv9" a substring of "nv9usb", which would
    quietly answer about one product with another product's row.
    """
    tok = _label_token(label)
    if not tok:
        return False
    return bool(re.search(r"\b" + re.escape(tok) + r"\b", question or "", re.I))


def _same_table_rows(question: str, index: list[dict]) -> list[dict]:
    """Rows of ONE table whose labels the question names, when it names 2+."""
    by_table: dict[tuple, list[dict]] = {}
    for r in index:
        by_table.setdefault((r["source"], r["page"], r["table"]), []).append(r)

    best: list[dict] = []
    for rows in by_table.values():
        hit = [r for r in rows if _named_in(question, r["attribute"])]
        # Distinct labels, so a table repeating one name does not qualify.
        if len({_label_token(r["attribute"]) for r in hit}) >= 2 \
                and len(hit) > len(best):
            best = hit
    return best


# Not every table in a manual is a specification. These documents also print
# change histories, cable schedules and part-number lists, and an unfiltered
# comparison offered "Change History > 1: 03 Nov 2025 against 18 Dec 2024" and
# a pair of cable part numbers as though they were product differences. True,
# grounded, and of no use to anyone deciding between two products.
_NOT_SPEC_TABLE = re.compile(
    r"change\s*history|revision|document\s*control|contact|"
    r"table\s*of\s*contents|cable|accessor|spare|order(ing)?\s*(code|info)",
    re.I)

# A row LABEL that is a part number ("CN00392", "WR02040") or a bare number is
# an entry in a list, not an attribute anything can be compared on.
_PART_NUMBER = re.compile(r"^[a-z]{2,3}[\s-]?\d{3,}[a-z0-9-]*$", re.I)
_DATE_ISH = re.compile(r"^\d{1,2}\s+\w{3,}\s+\d{4}", re.I)


def _is_spec_row(row: dict) -> bool:
    label = (row.get("attribute") or "").strip()
    if not label or len(label) < 2:
        return False
    if _NOT_SPEC_TABLE.search(row.get("table") or ""):
        return False
    if _PART_NUMBER.match(label) or label.isdigit():
        return False
    cells = [c.strip() for c in (row.get("values") or []) if c and c.strip()]
    vals = " ".join(cells)
    if _DATE_ISH.match(vals.strip()):
        return False
    # A value that is a part number makes this a parts list whatever the
    # table is called: "Cable: CN00392, Validator to USB Cable" is an
    # accessory, not a specification two products differ on.
    if any(_PART_NUMBER.match(c) for c in cells):
        return False
    # Header bleed: a sub-heading read as data repeats the row label back as
    # a cell, giving "Length: 115 mm, 167 mm, Length, 115 mm, 160 mm" --
    # two columns of a split table stitched into one unreadable run.
    if any(c.lower() == label.lower() for c in cells):
        return False
    return True


def _shared_differences(named: list[str], index: list[dict],
                        question: str) -> list:
    """(attribute, {product: value}) for attributes every named product has
    and does not agree on."""
    wanted = set(named)
    by_attr: dict = {}
    for r in index:
        if r["product"] not in wanted:
            continue
        if not _is_spec_row(r):
            continue
        vals = ", ".join(v for v in r["values"] if v and v != "---")
        if not vals:
            continue
        # Qualified by its table: "Temperature" under Operation and under
        # Storage are different rows, and merging them would compare one
        # product's operating limit against another's storage limit.
        table = (r.get("table") or "").strip()
        key = (table + " › " + r["attribute"]) if table else r["attribute"]
        by_attr.setdefault(key, {}).setdefault(r["product"], vals)

    documented, differing = [], []
    for attr, per_product in sorted(by_attr.items()):
        if len(per_product) < len(wanted):
            continue                      # not documented for both
        documented.append((attr, per_product))
        if len(set(per_product.values())) > 1:
            differing.append((attr, per_product))

    # When the question NAMES a dimension, answer that dimension -- including
    # when the two agree. "The difference in operating temperature" where both
    # manuals say +5°C to +50°C has the answer "there is none", and that is
    # worth saying: dropping equal rows here made the pipeline refuse a
    # question whose answer was sitting in both tables.
    terms = _terms(question)
    if terms:
        focused = [(a, v) for a, v in documented if terms & _terms(a)]
        if focused:
            return focused
    # Open-ended: the rows they disagree on ARE the difference.
    return differing[:12]                 # a table nobody reads is not an answer


def compare(question: str, index: list[dict], named: list,
            product_names: dict | None = None) -> dict | None:
    """A side-by-side answer built from table cells, or None to fall through.

    `named` is the catalogue products the QUESTION names, resolved by the
    caller: main.py already owns that resolver, aliases and all, and a second
    implementation here would be a second thing to keep correct.
    """
    if not is_comparison(question):
        return None
    names = product_names or {}

    # Shape 1: one table already puts them side by side.
    rows = _same_table_rows(question, index)
    if rows:
        head = rows[0]
        # The source and page, not the table caption: structures.py takes the
        # nearest heading as a title, which on page 47 is "Introduction" --
        # citing that as the table's name is wrong and looks careless.
        lines = ["From " + head["source"]
                 + ", page " + str(head["page"]) + ":", ""]
        for r in sorted(rows, key=lambda x: x["attribute"]):
            vals = ", ".join(v for v in r["values"] if v and v != "---")
            lines.append("- **" + r["attribute"] + "** — " + vals)
        return {"answer": "\n".join(lines), "kind": "comparison_table",
                "products": sorted({r["product"] for r in rows})}

    # Shape 2: ask each product separately, keep what they disagree on.
    if len(named) < 2:
        return None
    diffs = _shared_differences(named, index, question)
    if not diffs:
        return None
    labels = [names.get(k, k) for k in named]
    lines = ["| | " + " | ".join("**" + l + "**" for l in labels) + " |",
             "|---|" + "---|" * len(labels)]
    same = 0
    for attr, per_product in diffs:
        vals = [per_product.get(k, "not documented") for k in named]
        if len(set(vals)) == 1:
            same += 1
        lines.append("| " + attr + " | " + " | ".join(vals) + " |")
    if same == len(diffs):
        lines += ["", "These are the same for both products."]
    lines += ["", "Only the specifications both manuals state are compared "
                  "here; anything one manual does not mention is left out "
                  "rather than assumed."]
    return {"answer": "\n".join(lines), "kind": "comparison_specs",
            "products": list(named)}


def answer(question: str, faq_items: list[dict], index: list[dict],
           catalog_tree: dict | None = None,
           scoped: bool = False,
           named_products: list | None = None,
           product_names: dict | None = None) -> dict | None:
    """A cross-product answer, or None to let the normal pipeline run.

    Three shapes, in order of specificity:

      a quantity  -> which products meet it, from the spec index
      a need      -> which product descriptions match, from the overviews
      neither     -> the catalogue, when the question is "what do you sell?"

    Every product name comes from the catalogue and every quoted value from
    a table cell, so this cannot name a product that does not exist or
    attribute a specification to the wrong one.

    `scoped` says the visitor has already picked ONE product in the widget.
    That suppresses the overview branch below, which needs only a single
    shared word to fire: once someone has chosen the MyCheckr manual, "here
    is what we make that fits" is never the answer they were after, and the
    word it matched on was usually just the product's own name. The spec and
    catalogue branches still run -- both demand far more of the question,
    and "what else do you sell?" is a fair thing to ask mid-conversation.
    """
    # 0. A comparison: a cross-product question whose wording looks nothing
    #    like a sales one, so it is checked before that gate.
    if is_comparison(question):
        cmp_out = compare(question, index, named_products or [],
                          product_names)
        if cmp_out:
            return cmp_out

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
    if terms and not scoped:
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
        from catalog import is_shared_product
        lines = ["Our product ranges:", ""]
        for c in catalog_tree.get("categories", []):
            prods = [p["name"] for p in c.get("products", [])
                     if p.get("name") and not is_shared_product(p.get("key"))]
            if prods:
                lines.append(f"- **{c['name']}**: " + ", ".join(prods))
        if len(lines) > 2:
            lines += ["", "Tell me which one you are interested in and I can "
                      "answer from its manual."]
            return {"answer": "\n".join(lines), "kind": "catalogue"}
    return None
