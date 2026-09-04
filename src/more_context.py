"""What to offer a visitor AFTER an answer, so the reply is never a dead end.

Three outcomes, in descending order of usefulness, and the point is that the
first one is only claimed when it is TRUE:

  "detail"    Retrieval genuinely found material the answer did not use, from
              a document the answer actually drew on. Offer to show it --
              verbatim, not re-generated.
  "document"  Nothing further was retrieved, but we know which document and
              page the answer came from. Offer the original at that page.
  "support"   Neither. Offer a person.

WHY THERE IS SPARE MATERIAL TO OFFER AT ALL

RETRIEVE_K is 16 and CONTEXT_K is 8, so every query already fetches eight
chunks and throws them away. This costs no extra retrieval: it inspects what
has already been paid for.

WHY NOVELTY IS CHECKED RATHER THAN ASSUMED

"Unused" is not the same as "adds something". The discarded chunks overlap
heavily with the used ones -- neighbouring chunks share an overlap window by
construction, and a spec often appears in both a table and its surrounding
prose. Offering "more detail" that restates the answer in different words is
worse than offering nothing, because it spends the visitor's click and
teaches them the button lies. So a chunk has to carry content words the
answer does not already contain before it counts.

The same reasoning caps how much is offered: three chunks, best first. A
"more detail" button that dumps eleven chunks is a wall of text, not context.
"""
from __future__ import annotations

import os
import re

from text_utils import stem

# Content words only. The stop list is deliberately short -- this is a
# novelty test, not retrieval, and being slightly too strict about what
# counts as new just makes the feature quieter.
_STOP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "can", "could", "will", "would", "should", "may",
    "might", "must", "have", "has", "had", "of", "to", "in", "on", "at",
    "for", "with", "from", "by", "about", "as", "into", "and", "or", "but",
    "if", "then", "so", "than", "that", "this", "these", "those", "it",
    "its", "there", "which", "when", "where", "how", "what", "not", "no",
    "you", "your", "we", "our", "they", "their", "see", "page", "also",
}

_WORD = re.compile(r"[a-z0-9]+(?:[./-][a-z0-9]+)*")

# A chunk must be at least this fraction NEW to count as more context.
# 0.35 was chosen against the real corpus: below ~0.25 almost every
# discarded chunk qualifies (they share an overlap window), above ~0.5
# genuine extra tables start being rejected for sharing a product name.
MIN_NOVELTY = 0.35

# Enough new material to be worth a click.
MIN_NEW_WORDS = 8

# The cross-encoder's OWN decision boundary. reranker.py squashes its logits
# through a sigmoid specifically so that 0.5 means "raw logit == 0", i.e. the
# point where the model stops calling a passage relevant. Using that rather
# than a tuned constant, because a number picked to make two example queries
# look good is a number that will be wrong on the third.
#
# Note this bounds how good the "detail" offer can be: the reranker scored
# "Cleaning the Product" at 0.80 against "how do I install and mount the
# NV9USB+", so that passage passes. That is the reranker's judgement, and
# overriding it here with keyword hacks would be replacing a measured model
# with a guess.
MIN_RELEVANCE = float(os.getenv("MORE_CONTEXT_MIN_RELEVANCE", "0.5"))

MAX_DETAIL_CHUNKS = 3


def _words(text: str) -> set[str]:
    """Content-word STEMS.

    Stemming is load-bearing, not a nicety. Without it "The operating
    temperature is 0 to 50 C" scored 40% novel against an answer saying "the
    NV9USB+ OPERATES from 0 to 50 degrees Celsius" -- purely because
    "operating" and "operates" are different strings -- so a pure
    restatement would have been offered as more detail.
    """
    return {stem(w) for w in _WORD.findall((text or "").lower())
            if w not in _STOP and len(w) > 1}


def novelty(chunk_text: str, answer: str) -> tuple[float, int]:
    """(fraction of this chunk's content words absent from the answer, count).

    Directional on purpose. Asking "how much of the chunk is new" is the
    question a visitor's click is about; the symmetric similarity would be
    dominated by answer length.
    """
    cw = _words(chunk_text)
    if not cw:
        return 0.0, 0
    new = cw - _words(answer)
    return len(new) / len(cw), len(new)


def build(results: list[dict], used_chunks: list[dict], answer: str,
          sources: list[dict], refused: bool = False) -> dict:
    """Decide what to offer. See the module docstring for the three modes.

    `results` is everything retrieval returned, `used_chunks` the subset that
    reached the prompt, `sources` the built source objects (they already
    carry pages and a download URL, so nothing is recomputed here).
    """
    used_ids = {c.get("id") for c in (used_chunks or []) if c.get("id")}
    # Restricted to documents the answer actually drew on. A high-ranking
    # chunk from an unrelated manual is not "more context on this answer",
    # it is a different subject, and offering it reads as a non-sequitur.
    cited = {c.get("source") for c in (used_chunks or []) if c.get("source")}

    detail = []
    if not refused:
        for r in results or []:
            if r.get("id") in used_ids or r.get("source") not in cited:
                continue
            # Relevance BEFORE novelty. Novelty alone offered exactly the
            # wrong things: asked how to install and mount an NV9USB+, it
            # proposed "Protocols and Interfacing", "SMART Update
            # currencies" and "Cleaning the Product" -- maximally novel
            # precisely BECAUSE they are about something else.
            if r.get("rerank_score", 0.0) < MIN_RELEVANCE:
                continue
            frac, count = novelty(r.get("text") or "", answer)
            if frac >= MIN_NOVELTY and count >= MIN_NEW_WORDS:
                detail.append(r)
    # Sorted by relevance, not novelty -- the visitor wants the most relevant
    # thing they have not been told, and ranking by novelty puts the most
    # off-topic passage first by construction.
    detail.sort(key=lambda r: -r.get("rerank_score", 0.0))
    detail = detail[:MAX_DETAIL_CHUNKS]

    # The document pointer is attached whenever it is known, in every mode:
    # "here is more detail, and the original is at page 30" is strictly more
    # useful than either alone, and it is free.
    doc = None
    for s in sources or []:
        if s.get("source") in cited or (refused and not cited):
            doc = {"source": s.get("source"),
                   "download_url": s.get("download_url"),
                   "pages": list(s.get("pages") or [])[:4]}
            break

    if detail:
        kind = "detail"
    elif doc:
        kind = "document"
    else:
        kind = "support"

    return {
        "kind": kind,
        # Two ways to read the same material, because the two callers differ.
        # chunk_ids are for POST /source_chunks -- the endpoint the clickable
        # sources already use, so there is no second way to fetch a chunk.
        "chunk_ids": [r.get("id") for r in detail],
        # The widget cannot use those: _public_sources strips chunk ids on
        # purpose. It also already pre-sends FAQ answers so a tap renders
        # without a round trip, so the passages travel inline for the same
        # reason. This is document text the visitor is being shown anyway.
        "passages": [{
            "source": r.get("source"),
            "page": r.get("page") if isinstance(r.get("page"), int) else None,
            "text": (r.get("text") or "").strip(),
        } for r in detail],
        "chars": sum(len(r.get("text") or "") for r in detail),
        "document": doc,
        "support": kind == "support" or bool(refused),
        "label": _label(kind, doc, refused),
    }


def for_source(source: str | None, pages: list | None = None) -> dict:
    """The offer for an answer that ran NO retrieval.

    A curated FAQ answer and a catalogue-built sales answer have no retrieved
    surplus, so "more detail" would be a lie -- but a curated answer still
    knows which document it was written from, and pointing at that beats
    dead-ending on a support button. Falls back to support when even the
    document is unknown.
    """
    if not source:
        return {"kind": "support", "chunk_ids": [], "passages": [], "chars": 0,
                "document": None, "support": True,
                "label": _label("support", None, False)}
    from urllib.parse import quote
    doc = {"source": source,
           "download_url": f"/source_file/{quote(source)}",
           "pages": list(pages or [])[:4]}
    return {"kind": "document", "chunk_ids": [], "passages": [], "chars": 0,
            "document": doc, "support": False,
            "label": _label("document", doc, False)}


def _label(kind: str, doc: dict | None, refused: bool) -> str:
    """Suggested wording. The client may ignore it, but shipping one keeps
    three surfaces from inventing three different phrasings."""
    if kind == "detail":
        return "There is more in the documentation on this — show it"
    if kind == "document":
        pages = (doc or {}).get("pages") or []
        where = f", page {pages[0]}" if len(pages) == 1 else (
            f", pages {', '.join(str(p) for p in pages)}" if pages else "")
        return f"Open {(doc or {}).get('source') or 'the document'}{where}"
    if refused:
        return "I could not answer that from the documentation — contact support"
    return "Contact support for more information"
