"""Requests for a document itself, rather than for something inside it.

"Can you give me the MyCheckr manual?" was sent through retrieval like any
other question. Retrieval found passages OF the manual, and the model
summarised them -- when what the visitor wanted was the file. Every source
already carries a download link (/source_file, member-token gated), so the
honest answer is the link, with no model call at all.

The detector is deliberately narrow. "What does the manual say about
pinouts?" and "give me the steps from the manual for a reset" mention a
manual but ask for its CONTENT, and must keep reaching the pipeline. Only
a request whose object IS the document matches:

    <request phrase> [the|a copy of|...] [<= 4 words] <document noun> [for|of <product>]

Pure functions, no imports from the app, so the tests need no stubs.
"""
from __future__ import annotations

import os
import re

# What the visitor asked for -> filename fragments that identify it. Order
# matters only for display; matching takes every hit.
KINDS: dict[str, tuple[str, ...]] = {
    "manual": ("manual", "user guide", "handbook"),
    "datasheet": ("data sheet", "datasheet", "technical data", "spec sheet",
                  "specification"),
    "guide": ("guide", "quick start", "installation", "checklist"),
}

_NOUNS = {
    "manual": "manual", "manuals": "manual", "user guide": "manual",
    "user manual": "manual", "handbook": "manual",
    "datasheet": "datasheet", "datasheets": "datasheet",
    "data sheet": "datasheet", "data sheets": "datasheet",
    "spec sheet": "datasheet", "spec sheets": "datasheet",
    "guide": "guide", "guides": "guide", "quick start guide": "guide",
    "installation guide": "guide",
    # No particular kind: every document for the product.
    "documentation": None, "document": None, "documents": None,
    "docs": None, "pdf": None, "pdfs": None, "brochure": None,
}
_NOUN_RE = "|".join(sorted((re.escape(n) for n in _NOUNS), key=len, reverse=True))

# The request itself. Anchored at the start: a request phrase in the middle
# of a sentence ("how do I reset it, can you send me...") is rare enough
# that missing it costs less than hijacking a content question.
_ASK = re.compile(
    r"^(?:hi|hello|hey)?[\s,!]*(?:please\s+)?(?:"
    r"(?:can|could|would|will)\s+(?:you|u)\s+(?:please\s+)?"
    r"(?:give|send|share|provide|email|e-mail|forward|get|link)(?:\s+(?:me|us))?"
    r"|(?:give|send|share|provide|email|e-mail|forward)(?:\s+(?:me|us))?"
    r"|(?:can|could|may)\s+(?:i|we)\s+(?:please\s+)?(?:have|get|download|see|access)"
    r"|(?:i|we)\s+(?:need|want|would\s+like|'d\s+like|am\s+looking\s+for|'m\s+looking\s+for)"
    r"(?:\s+to\s+(?:get|download|have|see))?"
    r"|(?:where|how)\s+(?:can|do|could)\s+(?:i|we)\s+(?:get|find|download|obtain|access)"
    r"|(?:is\s+there|do\s+you\s+have|have\s+you\s+got)"
    r"|download|link\s+to"
    r")\b\s*",
    re.I)

# Words that turn "the manual" into a pointer at its CONTENT.
_CONTENT_WORDS = re.compile(
    r"\b(?:from|in|inside|within|section|page|pages|chapter|part|steps?|"
    r"instructions?|procedure|info|information|details?|what|how|which)\b",
    re.I)

_OBJECT = re.compile(
    rf"^(?P<pre>(?:[\w+.\-']+\s+){{0,6}}?)(?P<noun>{_NOUN_RE})\b"
    rf"(?:\s+(?:for|of|on|about)\s+(?P<post>[\w+.\-' ]{{1,40}}?))?"
    rf"(?:\s+please)?[\s?.!]*$",
    re.I)


def document_request(q: str) -> dict | None:
    """{"kind": "manual"|"datasheet"|"guide"|None} when `q` asks for a
    document itself, else None. kind None means "whatever you hold"."""
    text = " ".join((q or "").strip().split())
    if not text or len(text) > 160:
        return None
    ask = _ASK.match(text)
    if not ask:
        return None
    obj = _OBJECT.match(text[ask.end():])
    if not obj:
        return None
    if _CONTENT_WORDS.search(obj.group("pre")):
        return None
    return {"kind": _NOUNS[obj.group("noun").lower()]}


def pick_documents(kind: str | None, sources: list[str],
                   limit: int = 6) -> tuple[list[str], bool]:
    """The documents to offer, and whether they are the kind asked for.

    A request for "the manual" of a product whose only document is an
    installation checklist still gets the checklist -- labelled as what we
    DO hold (matched=False), never passed off as a manual.
    """
    uniq = sorted(set(s for s in sources if s), key=str.lower)
    if not kind:
        return uniq[:limit], True
    frags = KINDS.get(kind, ())
    hits = [s for s in uniq if any(f in s.lower() for f in frags)]
    if hits:
        return hits[:limit], True
    return uniq[:limit], False


def display_name(source: str) -> str:
    """"MyCheckr User Manual-v7.pdf" -> "MyCheckr User Manual-v7"."""
    return os.path.splitext(source)[0].replace("_", " ").strip()


def reply(product_label: str, kind: str | None, docs: list[str],
          matched: bool) -> str:
    what = f" for the {product_label}" if product_label else ""
    names = [display_name(d) for d in docs]
    if not matched:
        noun = {"manual": "a manual", "datasheet": "a data sheet",
                "guide": "a guide"}.get(kind or "", "that document")
        lead = (f"I don't hold {noun}{what}, but these are the documents I "
                f"do have — use the Download link beside each one:")
    elif len(names) == 1:
        return f"Here is the {names[0]} — use the Download link below to get it."
    else:
        lead = (f"These are the documents I hold{what} — use the Download "
                f"link beside each one:")
    return lead + "\n" + "\n".join(f"- {n}" for n in names)
