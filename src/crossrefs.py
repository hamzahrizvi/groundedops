"""Documents the corpus points at but does not contain.

Manuals cross-reference each other constantly -- "Refer to the MyCheckr
Range Technical Data for the dimensions of the device" -- and when the
referenced document has not been ingested, every question it would have
answered becomes a refusal that reads like ignorance rather than a gap.

Two jobs, and the second is why this is not merely a console report:

  scan()          every referenced title, whether we hold it, and who cites
                  it. An operator's list of documents worth chasing.
  deferral_for()  given the chunks a question actually retrieved, the
                  specific missing document THIS question was deferred to,
                  so the refusal can name it.

No model, no embeddings -- string work over text already indexed. The
matching is deliberately conservative: a false "you are missing this" sends
an operator hunting for a document that exists under another name, which is
worse than saying nothing.
"""
import logging
import os
import re

logger = logging.getLogger(__name__)

# The phrasings a manual uses to hand a topic to another document. Ordered
# from most to least explicit; all require a TITLE-SHAPED tail, which is
# what keeps "refer to the diagram below" out.
_DEFERRAL = re.compile(
    r"(?i:\b(?:refer\s+to|see|described\s+in|detailed\s+in|listed\s+in"
    r"|specified\s+in|as\s+per|in\s+accordance\s+with|available\s+in)\s+"
    r"(?:the\s+)?)"
    # THE TITLE IS CASE-SENSITIVE. This was the whole first draft's problem:
    # with IGNORECASE covering the title too, "refer to the relevant manual"
    # and "see the table below for screw specification" both parsed as
    # document titles, and the report filled up with things that are not
    # documents. A real title is capitalised.
    #
    # A dot is allowed only inside a version ("v1.0.50"), never as sentence
    # punctuation -- "...Update request. Document" used to parse as a title
    # spanning two sentences.
    r"((?:[A-Z][\w+/-]*(?:\.\d+)*\s+){1,6}"
    r"(?:Manual|Guide|Data|Datasheet|Sheet|Specification|Spec|Document"
    r"|Note|Notes|Checklist|Instructions|Drawing|Schedule))\b")

# An INTERNAL pointer is not a missing document: "see the table below",
# "refer to Section 5", "as described in Appendix B". These name a part of
# the document you are already reading.
_INTERNAL = re.compile(
    r"\b(?:table|figure|fig|diagram|drawing|section|chapter|page|appendix"
    r"|step|paragraph|clause|item)\b|\b(?:below|above|overleaf|opposite"
    r"|following|preceding)\b", re.IGNORECASE)

# What the deferral was ABOUT, when the sentence says so: "...for the
# dimensions of the device". Used to tell a refusal whether the missing
# document is relevant to the question that was asked.
_DEFERRAL_TOPIC = re.compile(r"\bfor\s+(?:the\s+)?([\w\s-]{3,40}?)(?:\s+of\b|[.,;)]|$)",
                             re.IGNORECASE)

# Words that appear in document names without identifying anything. Without
# these, "MyConnect Environment Manual" scored 0.67 against the held
# "MyConnect Environment-v4.pdf" and was reported missing on a rounding
# error -- "manual" was being counted as if it distinguished them.
_STOP = {"the", "a", "an", "of", "for", "and", "to", "in",
         "range", "user", "pdf", "docx",
         "manual", "guide", "data", "datasheet", "sheet", "specification",
         "spec", "document", "note", "notes", "checklist", "instructions",
         "drawing", "schedule"}
_VERSION = re.compile(r"^v?[\d.]+$")


def _key(title: str) -> frozenset:
    """A title reduced to its content words, for tolerant comparison.

    "MyCheckr Range Technical Data" and "MyCheckr Technical Data v2.pdf"
    have to land on each other; "MyCheckr User Manual" must not.
    """
    words = re.findall(r"[a-z0-9.+]+", (title or "").lower())
    return frozenset(w for w in words
                     if w not in _STOP and len(w) > 1
                     and not _VERSION.match(w))


def _ingested_titles() -> list[tuple[frozenset, str]]:
    """(content words, filename) for every document actually held."""
    out = []
    try:
        import docstore
        dirs = docstore.read_dirs()
    except Exception as exc:                       # pragma: no cover
        logger.warning(f"crossrefs: docstore unavailable: {exc}")
        dirs = []
    seen = set()
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if name.lower().endswith((".pdf", ".docx", ".txt", ".md")) \
                    and name not in seen:
                seen.add(name)
                out.append((_key(os.path.splitext(name)[0]), name))
    return out


def _match(title: str, ingested: list[tuple[frozenset, str]]) -> str | None:
    """The held document this reference probably means, or None.

    Containment rather than equality, in the reference's favour: a manual
    citing "SSP implementation guide" when the corpus holds "NV200 Spectral
    SSP Manual v.1.pdf" should be flagged as a POSSIBLE match rather than
    reported missing, because sending an operator after a document they
    already have is the failure mode that gets a report ignored.
    """
    want = _key(title)
    if not want:
        return None
    best, best_overlap = None, 0.0
    for have, name in ingested:
        if not have:
            continue
        overlap = len(want & have) / len(want)
        if overlap > best_overlap:
            best, best_overlap = name, overlap
    # Two thirds of the reference's content words present. Below that the
    # names are about different documents.
    return best if best_overlap >= 0.67 else None



def _is_reference_to_a_document(text: str, match, citing_source: str) -> bool:
    """Is this candidate really pointing at ANOTHER document?

    Two ways it is not, both of which filled the first run's report:

    INTERNAL. "see the table below for screw specification" parses as a
    title ending in "Specification" but names part of the page you are on.
    Judged on the words immediately around the phrase.

    SELF. "refer to Dataset/Firmware Programming, NV9USB+ Range User
    Manual" appears IN the NV9USB+ manual -- it is a section pointer that
    happens to name its own document. Detected structurally: if every
    identifying word of the CITING document is present in the reference,
    the reference is to that document, i.e. to itself.
    """
    window = text[max(0, match.start() - 40):match.end() + 20]
    if _INTERNAL.search(window):
        return False
    # A self-reference spells the citing document's name out INSIDE the
    # reference ("...Dataset/Firmware Programming, NV9USB+ Range User
    # Manual"), so the test is a contiguous name match, not a word-set
    # one. Word sets were too eager: "MyCheckr Range Technical Data" cited
    # by the MyCheckr manual shares "MyCheckr" and is a different document.
    base = re.sub(r"[-_\s]*v[\d.]+$", "",
                  os.path.splitext(citing_source or "")[0]).strip()
    if base and _flat(base) and _flat(base) in _flat(match.group(1)):
        return False
    return True


def _flat(s: str) -> str:
    return re.sub(r"[^a-z0-9+]+", "", (s or "").lower())


def _chunks():
    from db import get_collection
    col = get_collection()
    got = col.get(include=["documents", "metadatas"])
    return (got.get("documents") or []), (got.get("metadatas") or [])


def scan() -> list[dict]:
    """Every document the corpus refers to, most-cited first.

    Each entry: {title, citations: [{source, page}], count, held (filename
    or None)}. `held` is the point of the report -- entries with held=None
    are the gap.
    """
    documents, metadatas = _chunks()
    ingested = _ingested_titles()

    found: dict[frozenset, dict] = {}
    for text, meta in zip(documents, metadatas):
        meta = meta or {}
        text = text or ""
        for m in _DEFERRAL.finditer(text):
            if not _is_reference_to_a_document(text, m, meta.get("source")):
                continue
            title = " ".join(m.group(1).split())
            k = _key(title)
            if not k:
                continue
            entry = found.setdefault(k, {"title": title, "citations": [],
                                         "count": 0, "example": ""})
            if not entry["example"]:
                lo = max(0, m.start() - 60)
                entry["example"] = " ".join(
                    text[lo:m.end() + 70].split())
            entry["count"] += 1
            cite = {"source": meta.get("source") or "?",
                    "page": meta.get("page")}
            if cite not in entry["citations"]:
                entry["citations"].append(cite)
            # Keep the longest spelling seen: manuals abbreviate on later
            # mentions, and the fullest form is the one to go searching with.
            if len(title) > len(entry["title"]):
                entry["title"] = title

    out = []
    for entry in found.values():
        entry["held"] = _match(entry["title"], ingested)
        out.append(entry)
    out.sort(key=lambda e: (e["held"] is not None, -e["count"], e["title"]))
    return out


def missing() -> list[dict]:
    """Just the gap: referenced, not held."""
    return [e for e in scan() if not e["held"]]


def deferral_for(query: str, chunks: list[dict]) -> dict | None:
    """The missing document THIS question was handed off to, if any.

    Reads only the chunks retrieval actually returned, which is what makes
    this specific rather than a guess: the chunk that says "refer to the
    MyCheckr Range Technical Data for the dimensions of the device" is the
    chunk the question pulled up. When the question's own words overlap the
    topic of the deferral ("dimensions"), that is stronger still, and is
    reported so the caller can decide how confidently to phrase it.

    Returns {title, topic, source, page, on_topic} or None.
    """
    if not chunks:
        return None
    ingested = _ingested_titles()
    q_words = set(re.findall(r"[a-z]{3,}", (query or "").lower()))
    best = None
    for rank, c in enumerate(chunks):
        text = c.get("text") or c.get("document") or ""
        for m in _DEFERRAL.finditer(text):
            if not _is_reference_to_a_document(text, m, c.get("source")):
                continue
            title = " ".join(m.group(1).split())
            if _match(title, ingested):
                continue                       # we hold it; not a gap
            tail = text[m.end():m.end() + 120]
            topic_m = _DEFERRAL_TOPIC.search(tail)
            topic = (topic_m.group(1).strip() if topic_m else "")
            on_topic = bool(q_words & set(re.findall(r"[a-z]{3,}",
                                                     topic.lower())))
            hit = {"title": title, "topic": topic,
                   "source": c.get("source") or "?", "page": c.get("page"),
                   "on_topic": on_topic,
                   # Where the deferring passage sat in the retrieval order,
                   # so the caller can tell a reference in the best passage
                   # from one buried further down.
                   "rank": rank}
            # An on-topic deferral beats an incidental one.
            if best is None or (on_topic and not best["on_topic"]):
                best = hit
    return best


def refusal_line(deferral: dict | None) -> str:
    """One sentence for a refusal, or "" when there is nothing to say."""
    if not deferral:
        return ""
    what = f" for {deferral['topic']}" if deferral.get("topic") else ""
    return (f"The {deferral['source'].rsplit('.', 1)[0]} refers"
            f"{what} to the {deferral['title']}, which I don't hold.")
