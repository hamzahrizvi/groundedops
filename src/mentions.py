"""Honest "not mentioned" for a confident "No" (PENDING.md 8.3).

The model is told to open a yes/no question with a plain Yes or No, and it
does so even when its context never mentions the thing asked about: "Does
the MyCheckr have a backup battery?" -> "No." while the MyCheckr manual says
nothing about batteries at all (blind set 2; a regression since v16.3, see
M5). The verifiers let such a "No" through: NLI has nothing to contradict,
and the fast LLM verdict reads an absence as support (M8: 15/15 absence
"No"s accepted).

So the check here is not a verifier. It asks one question of the corpus: is
the feature the "No" denies mentioned ANYWHERE in this product's own
documentation? If it is, the "No" stands and is verified like any other
answer -- "does the Mini do Ethernet or only Wi-Fi?" is answered "No" from
a page that names both, and that is a documented No. If the word never
appears in the product's documents, nothing in them supports a No, and the
honest reply is that they do not mention it.

Scope decided 2026-09-29 by the product owner: "not mentioned" only when
the feature appears nowhere in that product's manual; a plain "No" stays
when the manual lists the full set (interfaces, accepted media) and the
feature is not in it. Measured against the 2026-09-28 labelled set before
shipping: the two absence "No"s the owner marked right (#45 Mini
Ethernet/Wi-Fi, present in the manual) keep their No; #30 (coins on the
NV9USB+, "coin" nowhere in that manual) becomes "not mentioned", which is
also what blind set 1 expects for it.

Deterministic and cheap: a regex over the answer (or the question, when the
answer is a bare "No."), then a word search over the product's text, which
is fetched once per product and cached until the index changes.
"""
from __future__ import annotations

import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

# The answer denies something. Only an OPENING "No" counts: "No." / "No, the
# BV30 does not..." A "no" later in a sentence ("there is no separate fuse")
# is a stated fact from the passage, not a yes/no verdict.
_OPENS_NO = re.compile(r"^\W*no\b", re.I)

# What the answer says the product lacks: the object of its negated verb.
# "does not accept coins", "doesn't support Bluetooth connectivity", "has no
# backup battery", "cannot connect to a phone over Bluetooth".
_DENIED = re.compile(
    r"\b(?:does\s+not|doesn'?t|do\s+not|don'?t|cannot|can'?t|is\s+not|isn'?t"
    r"|has\s+no|have\s+no|will\s+not|won'?t|not)\s+"
    r"(?:\w+\s+){0,2}?"
    r"(?:support|have|has|include|offer|provide|accept|take|feature|come\s+with"
    r"|connect(?:\s+(?:to|over|via|with))?|use|handle|read|validate|work\s+with"
    r"|communicate\s+(?:over|via|with)|run\s+on|contain|carry|ship\s+with"
    r"|be\s+(?:connected|used|fitted|configured)\s+(?:to|over|via|with))\s+"
    r"(?:a\s+|an\s+|any\s+|the\s+|its\s+|with\s+)?"
    r"([^.;:\n]+)", re.I)

# The same object, read from the QUESTION, for an answer that is just "No."
# A verb that takes the feature as its object, then the object, cut at the
# first word that starts a purpose/condition clause: "a backup battery SO it
# keeps working", "coins AS WELL as notes", "ethernet OR ONLY wifi". Two
# regexes because the verbs nest -- "DOES the MyCheckr HAVE a backup
# battery" -- and the innermost one is the one whose object is the feature.
_ASKED_VERB = re.compile(
    r"\b(?:have|has|got|support|supports|include|includes|come\s+with|comes\s+with"
    r"|take|takes|accept|accepts|offer|offers|provide|provides|feature|features"
    r"|use|uses|handle|handles|read|reads|validate|validates|do|does|run\s+on"
    r"|connect(?:\s+(?:to|over|via|with))?|work\s+with|compatible\s+with"
    r"|equipped\s+with|fitted\s+with|capable\s+of|built[- ]in)\s+"
    r"(?:a\s+|an\s+|any\s+|the\s+|its\s+|with\s+)?", re.I)
# What follows the asked verb, up to the first clause word or punctuation.
# A word scan, not a regex: the lazy `(.+?)(?=\s(?:so|if|...)\b|[?.,;!]|$)`
# it replaces was flagged twice by CodeQL as polynomial on user input.
# Same results (fuzzed against the regex when it was replaced).
_STOP_WORDS = frozenset("so if when instead rather for to in on at with "
                        "without from too also either".split())
_STOP_PAIRS = frozenset({("as", "well"), ("or", "only"), ("or", "just")})


def _word_head(w: str) -> str:
    """The leading word characters of `w`, lower-cased: what `kw\\b` sees."""
    n = 0
    while n < len(w) and (w[n].isalnum() or w[n] == "_"):
        n += 1
    return w[:n].lower()


def _asked_object(text: str) -> str:
    """`text` up to (not including) the space before a clause word, or the
    first ? . , ; ! -- at least one character, as `(.+?)` took. Expects
    whitespace already collapsed to single spaces."""
    if not text:
        return ""
    cut = len(text)
    for ch in "?.,;!":
        i = text.find(ch, 1)
        if i != -1 and i < cut:
            cut = i
    words = text[:cut].split(" ")
    pos = 0
    for i, w in enumerate(words):
        start = pos
        pos += len(w) + 1
        # The space before this word sits at start-1; the first character
        # is always taken, so a stop needs that space at position >= 1.
        if i == 0 or start - 1 < 1:
            continue
        nxt = words[i + 1] if i + 1 < len(words) else None
        if _word_head(w) in _STOP_WORDS:
            return text[:start - 1]
        if nxt is not None and (w.lower(), _word_head(nxt)) in _STOP_PAIRS:
            return text[:start - 1]
    return text[:cut]

# Words that qualify a feature without naming it. "Bluetooth CONNECTIVITY",
# "Ethernet CONFIGURATION", "battery BACKUP OPTION": the feature is the
# other word, and these are in every manual whatever it covers.
_GENERIC = {
    "connectivity", "connection", "connections", "configuration", "support",
    "supported", "capability", "capabilities", "function", "functionality",
    "feature", "features", "option", "options", "mode", "modes", "port",
    "ports", "interface", "interfaces", "built", "in", "directly", "natively",
    "any", "kind", "type", "sort", "form", "of", "its", "own", "the", "a",
    "an", "via", "through", "over", "with", "to", "for", "and", "or", "as",
    "well", "it", "itself", "device", "unit", "machine", "product", "system",
    "systems", "this", "that", "these", "those", "also", "either", "at",
    "all", "some", "other", "such", "on", "onto", "into", "from", "by",
    "using", "use", "can", "be", "is", "are", "does", "do", "have", "has",
    "yes", "no", "not", "notes", "note",   # "coins as well as NOTES": the
                                           # thing it does take is not the
                                           # feature being denied
}

_WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _tokens(phrase: str) -> list[str]:
    return _WORD.findall((phrase or "").lower())


_CONFESSED = re.compile(r"\b(?:no mention|not mentioned|no information)\b", re.I)


def enabled() -> bool:
    """Kill switch. NOT_MENTIONED_CHECK=off restores the plain "No"."""
    return os.getenv("NOT_MENTIONED_CHECK", "on").strip().lower() not in (
        "off", "0", "no", "false")


def denied_feature(answer: str, question: str = "") -> str | None:
    """The feature a "No" answer denies, as the answer (or, for a bare "No.",
    the question) words it -- or None when the answer is not an opening No,
    or names nothing it denies."""
    if not answer or not _OPENS_NO.match(answer):
        return None
    first = " ".join(answer.strip().split("\n", 1)[0].split())
    question = " ".join((question or "").split())
    m = _DENIED.search(first)
    # An answer that says the absence itself ("No. It has no cashbox, and
    # there is no mention of a touchscreen", gpt-5-mini, S26) may deny some
    # OTHER, documented thing first; the feature in doubt is the one asked.
    phrase = m.group(1) if m and not _CONFESSED.search(first) else None
    if not phrase and question:
        # The LAST verb, which is the innermost: "Does the MyCheckr have a
        # backup battery" has a verb at "does" (whose object would be the
        # whole clause, subject included) and at "have"; the feature is
        # what follows "have". Scanned from the last verb back, stopping at
        # the first object: matching from every verb was quadratic.
        for v in reversed(list(_ASKED_VERB.finditer(question))):
            o = _asked_object(question[v.end():])
            if o.strip():
                phrase = o
                break
    if not phrase:
        return None
    phrase = re.sub(r"\s+", " ", phrase).strip(" \"'()")
    # "coins as well as notes" -> "coins"; "Ethernet or Wi-Fi configuration
    # through IMS" keeps both alternatives, they are both being denied. An
    # aside after a dash or in brackets is the answer explaining itself.
    # Single spaces: the line above collapsed every run (CodeQL ReDoS).
    phrase = re.split(r" (?:as well as|rather than|instead of|but) "
                      r"| [—–-]+ | ?\(",
                      phrase, maxsplit=1, flags=re.I)[0]
    phrase = re.sub(r"^(?:a|an|any|the|its)\s+", "", phrase, flags=re.I)
    return phrase.strip(" ,") or None


def feature_words(phrase: str, product_words: set[str] | None = None) -> list[str]:
    """The words of `phrase` that actually name a feature: no product name,
    no qualifier, nothing under three letters. Order kept, no duplicates."""
    out: list[str] = []
    skip = set(_GENERIC) | set(product_words or ())
    for tok in _tokens(phrase):
        if len(tok) < 3 or tok in skip or tok.replace("-", "") in skip:
            continue
        if tok not in out:
            out.append(tok)
    return out


def _forms(word: str) -> list[str]:
    """Spellings of one feature word to look for: the word, its stem
    (coins -> coin, batteries -> battery), and hyphen-free (wi-fi -> wifi)."""
    w = word.lower()
    forms = {w, w.replace("-", "")}
    if w.endswith("ies") and len(w) > 4:
        forms.add(w[:-3] + "y")
    elif w.endswith("es") and len(w) > 4:
        forms.add(w[:-2])
    elif w.endswith("y") and len(w) > 4:
        forms.add(w[:-1] + "i")            # battery -> batteri(es), not batter
    if w.endswith("s") and len(w) > 3:
        forms.add(w[:-1])
    return sorted(forms, key=len)


def word_in(text: str, word: str) -> bool:
    """Whether `word` (any form) appears in `text` as the start of a word:
    "coin" finds "coins" and "coinage"; "phone" does not find "telephone".
    Hyphens are dropped on both sides so "wifi" finds the manual's "Wi-Fi"."""
    text = text.lower().replace("-", "")
    for f in _forms(word):
        f = f.replace("-", "")
        if f and re.search(r"(?<![a-z0-9])" + re.escape(f), text):
            return True
    return False


# ── product text ─────────────────────────────────────────────────────────

_lock = threading.Lock()
_blobs: dict[str, str] = {}
_blob_count = -1     # collection size the blobs were built from


def invalidate() -> None:
    with _lock:
        _blobs.clear()


def product_text(product_key: str) -> str:
    """Every chunk in scope for `product_key`, lowercased and joined, judged
    by the same rule retrieval uses (retrieval_db._matches_scope), so a
    shared "<category>_general" document counts as the product's own.
    Cached per product until the index changes size or is invalidated."""
    global _blob_count
    import db
    from retrieval_db import _matches_scope
    col = db.get_collection()
    count = col.count()
    with _lock:
        if count != _blob_count:
            _blobs.clear()
            _blob_count = count
        if product_key in _blobs:
            return _blobs[product_key]
    got = col.get(include=["metadatas", "documents"])
    scope = {"product": product_key}
    parts = [t for m, t in zip(got.get("metadatas") or [], got.get("documents") or [])
             if t and _matches_scope(m or {}, None, scope)]
    blob = "\n".join(parts).lower()
    with _lock:
        _blobs[product_key] = blob
    return blob


def unmentioned_feature(answer: str, question: str, product_key: str,
                        product_words: set[str] | None = None,
                        lookup=None) -> str | None:
    """The feature a "No" denies, when the product's documentation never
    mentions it -- the case where "No" is a guess and "not mentioned" is the
    truth. None otherwise, including for every answer that is not an opening
    No, and for a No whose subject the documentation does name.

    `lookup(word) -> bool` overrides the corpus search (tests)."""
    phrase = denied_feature(answer, question)
    if not phrase:
        return None
    words = feature_words(phrase, product_words)
    if not words:
        return None
    if lookup is None:
        try:
            text = product_text(product_key)
        except Exception as exc:          # no index, no verdict
            logger.warning("not-mentioned check skipped: %s", exc)
            return None
        if not text:
            return None
        lookup = lambda w, _t=text: word_in(_t, w)   # noqa: E731
    if any(lookup(w) for w in words):
        return None
    return phrase


def reply(product_label: str, feature: str) -> str:
    """The customer-facing sentence. Deliberately NOT a phrase is_refusal()
    matches: the switchboard would otherwise reword it into the generic
    refusal and lose the one thing this says -- WHAT is not mentioned."""
    what = f"The {product_label} documentation" if product_label else "The documentation"
    return (f"{what} doesn't mention {feature}, so I can't confirm it either "
            f"way rather than guess.")
