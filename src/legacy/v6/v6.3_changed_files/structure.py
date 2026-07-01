"""
Structured block extractor.

Detects checklist/step-list content in retrieved chunks by combining
the chunk's actual semantic relevance (rerank_score, from reranker.py)
with structural heuristics (verb-led lines, list density, consistent
line length) and lexical query overlap.
"""

import re
from collections import Counter

from text_utils import (
    LIST_LINE_RE,
    STEP_HEADER_RE,
    TRAILING_STOPWORDS,
    fix_camel_case,
    clean_table_artifacts,
)

# Minimum total score (see extract_structured_block) for a chunk's content
# to be returned as a structured answer rather than falling through to
# generative answering. With the scoring formula below, a chunk that
# barely clears the retrieval gate (rerank_score == 0.5, contributing 12.5)
# needs a small additional structural/query contribution to pass — pure
# rerank relevance alone, with no list shape at all, won't trigger this
# path (it's also blocked earlier by the verb_starts/list_lines gate).
MIN_EXTRACTION_SCORE = 15


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def normalize_line(line: str) -> str:
    line = fix_camel_case(line)
    line = re.sub(r"^\d+[\.\)]\s*", "", line)
    line = re.sub(r"[^\w\s]", "", line)
    return line.strip().lower()


# Imperative verbs that open a genuine checklist/step item. Hoisted to
# module level so both starts_with_verb (which also has a looser
# "short line, no period" fallback for headings like "Network status
# indicator") and _looks_like_title_case_heading's gate (which needs
# the STRICT check only — see is_bad_line) can share one definition.
_COMMON_VERBS = {
    "check", "verify", "ensure", "confirm", "connect",
    "install", "open", "close", "restart", "power",
    "set", "enter", "select", "press", "run",
    "make", "use", "add", "remove", "test", "enable",
    "download", "launch", "scan", "tap", "attach",
    "choose", "mount", "wait", "approach", "assign",
}


def starts_with_verb(line: str) -> bool:
    line = line.strip().lower()
    if not line:
        return False

    first_word = line.split()[0]

    if first_word in _COMMON_VERBS:
        return True

    if len(line.split()) <= 6 and not line.endswith("."):
        return True

    return False


def _starts_with_imperative_verb(line: str) -> bool:
    """
    Strict version of starts_with_verb: True only if the first word is
    an actual imperative verb from _COMMON_VERBS — none of
    starts_with_verb's "short line with no trailing period" fallback.

    That fallback exists so a short heading-shaped line like "Network
    status indicator" is treated as legitimate checklist content
    elsewhere in this module, but it ALSO matches genuine document
    section titles ("MyConnect Environment Installation - Advance
    Pre-requisites" is 6 words with no trailing period). Using the loose
    version here would let those titles slip past the Title-Case heading
    filter in is_bad_line just because they happen to be short.
    """
    line = line.strip().lower()
    if not line:
        return False
    return line.split()[0] in _COMMON_VERBS


# Connector words ignored when checking whether every significant word
# in a line is capitalized (Title Case) — these are conventionally
# lowercase even within an actual title ("Advance Pre-requisites" has
# no connector, but "Steps to Install and Verify" would).
_HEADING_CONNECTOR_WORDS = {
    "a", "an", "the", "and", "or", "of", "to", "for",
    "in", "on", "with", "from", "as", "is", "are",
}


def _looks_like_title_case_heading(line: str) -> bool:
    """
    True if every significant word in `line` is capitalized — the shape
    of a document section title ("MyConnect Environment Installation -
    Advance Pre-requisites") rather than a sentence-case checklist item
    ("Confirm that a dedicated Ethernet cable is available...").

    Deliberately requires at least 2 significant (non-connector) words
    so it can't fire on a single capitalized word, and the caller
    additionally requires the line not be verb-led/list-like before
    treating this as disqualifying — see is_bad_line.
    """
    words = line.strip().split()
    if len(words) < 2:
        return False

    significant = [
        w.strip("-\u2013\u2014,:;()")
        for w in words
        if w.lower().strip("-\u2013\u2014,:;()") not in _HEADING_CONNECTOR_WORDS
    ]
    significant = [w for w in significant if w and w[0].isalpha()]

    if len(significant) < 2:
        return False

    return all(w[0].isupper() for w in significant)


def is_bad_line(line: str) -> bool:
    """
    True if `line` should be excluded from a structured/checklist answer.

    Beyond the original header/short-line filters, this now also catches:
      - long narrative/explanatory prose mixed into a checklist
        (e.g. "This final section is a short, single checklist
        ensuring nothing has been forgotten.")
      - mid-length lines with no terminating punctuation that almost
        certainly got cut off at a chunk or table-cell boundary (this
        check is skipped for lines that clearly start with an imperative
        verb — "Confirm hub IP is reachable from tablet" is a complete,
        legitimate checklist item even without a trailing period)
      - lines starting with a lowercase letter — these are essentially
        always the tail end of the previous line's sentence (a PDF
        line-wrap artifact), not a new standalone item
      - lines whose last word is a conjunction/preposition/article — a
        strong truncation signal ("All devices powered, connected, and")
      - PDF running-header artifacts of the form "<Document Title> – N"
        (a page header/footer repeated on every page, ending in a bare
        page number after a dash) — these were surviving every previous
        filter because they're short and don't look like truncated
        prose, then getting marked "meaningful" downstream purely
        because they contain the product name. Reproduced directly from
        production output: "MyConnect Environment – 9", "MyConnect
        Environment – 17", "MyConnect Environment – 18" all passed this
        function unfiltered before this fix.
      - Title-Case section headings (e.g. "MyConnect Environment
        Installation - Advance Pre-requisites") — every significant word
        capitalized, not an imperative checklist item, not a list line.
        Real checklist content in this corpus is consistently
        sentence-case ("Confirm that a dedicated Ethernet cable is
        available...") even when extracted oddly from PDF tables, so
        this is a deliberately narrow signal: it only fires when there
        are no lowercase significant words AND the line isn't verb-led
        AND isn't already recognized as a list/step line, to avoid
        catching genuine imperative items that happen to be short.

    NOTE: this is a heuristic, not a parser. Severely table-heavy PDF
    sections can still produce an occasional truncated fragment that
    slips through (see tests/test_structure.py for a documented residual
    case) — the real fix for that class of issue is row-aware PDF table
    extraction, which needs the actual source document to build against.
    """
    l = line.lower()
    stripped = line.strip()

    if any(k in l for k in [
        "introduction", "overview", "table of contents",
        "download manual", "for advance checklist please refer",
    ]):
        return True

    if re.match(r"^\d+[a-z]?\.\s*[A-Z]", line):
        return True

    if len(line.split()) <= 3:
        return True

    # PDF running-header artifact: "<title words> – <page number>".
    # Matches an en-dash, em-dash, or hyphen followed by a bare trailing
    # number and nothing else — the exact shape of a repeated page
    # header, not of any real checklist content in this corpus.
    if re.match(r"^[A-Za-z][\w\s]*[-\u2013\u2014]\s*\d+\s*$", stripped):
        return True

    words = line.split()

    if (
        len(words) >= 12
        and not starts_with_verb(line)
        and not LIST_LINE_RE.match(line)
        and not STEP_HEADER_RE.match(line)
    ):
        return True

    if (
        len(words) > 6
        and not starts_with_verb(line)
        and not LIST_LINE_RE.match(line)
        and not STEP_HEADER_RE.match(line)
        and not line.rstrip().endswith((".", "!", "?", ":", ")"))
    ):
        return True

    if line[:1].islower() and not LIST_LINE_RE.match(line):
        return True

    last_word = re.sub(r"[^\w]", "", words[-1]).lower() if words else ""
    if last_word in TRAILING_STOPWORDS:
        return True

    if (
        _looks_like_title_case_heading(line)
        and not _starts_with_imperative_verb(line)
        and not LIST_LINE_RE.match(line)
        and not STEP_HEADER_RE.match(line)
    ):
        return True

    return False




def is_meaningful_line(line: str) -> bool:
    """Return True if the line contains a domain-relevant action or status term."""
    l = line.lower()
    return any(k in l for k in [
        "check", "verify", "ensure", "confirm",
        "test", "connected", "configured",
        "working", "trigger", "available",
        "power", "network", "device", "hub",
        "tablet", "alert", "relay", "log",
        "mycheckr", "myconnect", "connect",
        "download", "launch", "wifi", "app", "sign",
    ])


def simple_similarity(a: str, b: str) -> float:
    a_words = set(a.split())
    b_words = set(b.split())
    if not a_words or not b_words:
        return 0.0
    return len(a_words & b_words) / len(a_words | b_words)


def _query_overlap_score(query: str | None, lines: list[str]) -> float:
    if not query:
        return 0.0

    tokens = {
        normalize_line(tok)
        for tok in query.split()
        if len(normalize_line(tok)) > 3
    }
    if not tokens:
        return 0.0

    joined = " ".join(normalize_line(l) for l in lines)
    score = sum(1.0 for tok in tokens if tok in joined)

    query_lower = query.lower()
    if "before leaving" in query_lower and "before leaving" in joined:
        score += 3.0
    if "sign off" in query_lower and "sign off" in joined:
        score += 2.0
    if "checklist" in query_lower and "checklist" in joined:
        score += 2.0
    if "verify" in query_lower and "verify" in joined:
        score += 1.5

    return score


def _format_as_list(lines: list[str]) -> str | None:
    cleaned: list[str] = []

    for line in lines:
        line = clean_table_artifacts(line)
        for sub in line.split("\n"):
            sub = fix_camel_case(sub)
            sub = re.sub(r"^\s*(?:[-*•☐]|\d+[.)])\s*", "", sub).strip()
            if not sub or is_bad_line(sub):
                continue
            cleaned.append(sub)

    deduped: list[str] = []
    seen = set()
    for line in cleaned:
        key = normalize_line(line)
        if key not in seen:
            seen.add(key)
            deduped.append(line)

    if len(deduped) < 3:
        return None

    return "\n".join(f"- {line}" for line in deduped[:10])


# ─────────────────────────────────────────────
# Main extractor
# ─────────────────────────────────────────────

def extract_structured_block(chunks: list[dict], query: str | None = None) -> str | None:
    """
    Score each chunk and return the best one's content as a markdown list,
    or None if no chunk looks sufficiently checklist-like and relevant.

    SCORING FIX: previously the structural score was an unbounded SUM over
    all lines in a chunk (verb-led lines * 3, list lines * 2.5, etc.) with
    no length normalization, and the chunk's actual semantic relevance
    (rerank_score, already computed by reranker.py) wasn't used at all.
    The practical effect: a long chunk packed with list-shaped lines could
    systematically out-score a short, genuinely on-topic chunk — including
    cases with ZERO lexical query overlap beating a chunk with clear query
    overlap, just by sheer line count. Verified against a reproduction of
    the actual production bug (a 9-line "relay/GPIO notes" chunk scoring
    48 vs a 5-line, query-relevant "Steps 4-8" chunk scoring 33, despite
    the relay chunk having a LOWER rerank_score and zero query overlap).

    FIX: rerank_score is now the dominant term, structural sub-scores are
    normalized by line count (so they measure "how list-shaped is this
    chunk" rather than "how many lines does this chunk have"), and the
    backwards position_weight term (which rewarded LATER, i.e. less
    relevant, chunks in the rerank-sorted input) has been removed.
    """
    best_block: list[str] | None = None
    best_score: float = 0.0

    for chunk in chunks:
        raw_lines = [l.strip() for l in chunk["text"].split("\n") if l.strip()]
        if len(raw_lines) < 3:
            continue

        filtered_lines = [l for l in raw_lines if not is_bad_line(l)]
        if len(filtered_lines) < 3:
            continue

        normalized = [normalize_line(l) for l in filtered_lines]
        lengths = [len(l.split()) for l in normalized if l]
        if not lengths:
            continue

        n = len(filtered_lines)
        avg_len = sum(lengths) / len(lengths)
        length_consistency = sum(
            1 for l in lengths if abs(l - avg_len) < avg_len * 0.5
        ) / n

        starts = [l.split()[0] for l in normalized if l]
        common = Counter(starts).most_common(1)
        start_score = (common[0][1] if common else 0) / n

        independent = sum(1 for l in normalized if len(l.split()) > 4) / n
        verb_starts = sum(1 for l in filtered_lines if starts_with_verb(l))
        list_lines = sum(1 for l in filtered_lines if LIST_LINE_RE.match(l))

        structural = (
            length_consistency * 1.5
            + start_score * 2.0
            + independent * 1.0
            + (verb_starts / n) * 3.0
            + (list_lines / n) * 2.5
        )

        query_score = _query_overlap_score(query, filtered_lines)
        rerank_score = chunk.get("rerank_score", 0.5)

        # rerank_score dominant (0.5-1.0 range -> 12.5-25 points), with
        # structural shape and lexical query overlap as secondary signals
        # that break ties between similarly-relevant chunks.
        score = rerank_score * 25 + structural * 3 + query_score * 4

        if verb_starts < 2 and list_lines < 2:
            continue

        if score > best_score:
            best_score = score
            best_block = filtered_lines

    if best_block and best_score > MIN_EXTRACTION_SCORE:
        meaningful = [
            l for l in best_block
            if is_meaningful_line(l) or LIST_LINE_RE.match(l) or STEP_HEADER_RE.match(l)
        ]
        return _format_as_list(meaningful)

    return None
