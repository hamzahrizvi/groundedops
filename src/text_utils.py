"""
Pure text-processing utilities — no ML dependencies.

Kept separate from grounding.py/llm.py/structure.py so this logic can be
unit-tested without pulling in sentence-transformers, chromadb, or hitting
Ollama/DeepSeek.
"""

import re

REFUSAL_PHRASE = "i could not find that in the knowledge base"

REFUSAL_PHRASE_VARIANTS = [
    REFUSAL_PHRASE,
    "there is no information about",
    "the context does not contain",
    "does not contain this information",
    "no information about this in the",
    # v10.2.1: observed paraphrased give-ups from API models (which don't
    # always echo our exact refusal template despite the prompt). These
    # must be RECOGNIZED as refusals so follow-up turns route to the
    # clarify path instead of displaying an unhelpful dead-end verbatim
    # ("I don't have the answer for that. Please contact support...").
    "i don't have the answer",
    "i do not have the answer",
    "i don't have that information",
    "i do not have that information",
    "please contact support",
    # The customer-facing rewrite of the canonical refusal above. Listed here
    # so is_refusal() still recognises a refusal AFTER it has been reworded
    # for the reader: offer_support, the follow-up handling and the logs all
    # key off that check, and none of them may stop working because the
    # wording became friendlier.
    "i don't have that in the product documentation",
]

MIN_UNIT_LEN = 12

# Known multi-word brand/technical terms that must survive the camelCase
# splitter intact (see fix_camel_case below).
PROTECTED_TERMS = ["MyConnect", "MyCheckr", "WiFi", "GPIO"]

# Words that, when they're the LAST word of a line, strongly suggest the
# line was cut off mid-thought (table-cell or chunk-boundary truncation)
# rather than being a genuine, complete checklist item.
TRAILING_STOPWORDS = {
    "and", "or", "the", "a", "an", "with", "to", "of", "in", "on",
    "for", "but", "is", "are", "was", "were", "if", "from",
}

LIST_LINE_RE = re.compile(r"^\s*(?:[-*•☐]|\d+[.)])\s+")
STEP_HEADER_RE = re.compile(r"^step\s+\d+", re.IGNORECASE)


# ── camelCase / merge-artifact cleanup ──────────────────────────────────

# "Tell me more" is a request to EXPAND THE LAST ANSWER, not a new question.
# Treated as one, it retrieves on the literal words -- which carry no content
# at all -- and refuses: measured 0.0001 retrieval score straight after
# answering the same topic. Resolving it against history is not enough
# either, because that just re-serves the previous answer verbatim, which is
# not "more".
#
# Anchored at the start so "tell me more about the hopper capacity" is NOT
# caught: that names its own subject and is a real question.
_MORE_RE = re.compile(
    r"^\s*(?:and\s+|ok(?:ay)?[,\s]+|so\s+)?"
    r"(?:tell\s+me\s+more|more\s+(?:info(?:rmation)?|detail|on\s+(?:this|that|it))"
    r"|elaborate|go\s+on|continue|expand(?:\s+on\s+(?:this|that|it))?"
    r"|anything\s+else|what\s+else|say\s+more)"
    r"(?:\s+(?:about|on)\s+(?:it|this|that|the\s+above))?"
    # ONE class for the tail, not `\s*[.?!]*\s*`: two whitespace runs either
    # side of an optional one can split a long run of spaces n^2 ways before
    # failing, so "go on" + 10k spaces + "x" was quadratic (CodeQL
    # py/polynomial-redos). Same strings accepted.
    r"[\s.?!]*$", re.I)


def is_more_request(q: str) -> bool:
    """True when the user is asking to expand the previous answer."""
    return bool(_MORE_RE.match(q or ""))


def stem(w: str) -> str:
    """Crude suffix strip, enough to match a question to a manual.

    Customers type plurals and manuals print singulars: "anything that sorts
    and pays out COINS" against "bulk COIN validator", "check customer AGES"
    against "AGE estimation". Exact word matching missed both and the
    questions were refused. Not linguistics -- just enough that "coins",
    "coin" and "sorting" land on the same key.

    Lives here because three separate places needed it: sales.py to match a
    need to a product, more_context.py to tell genuinely new material from a
    reworded restatement ("operating" vs "operates" made a restatement look
    40% novel), and anything that compares typed text to document text next.
    """
    w = w.lower()
    for suf in ("ing", "ers", "er", "es", "s"):
        if len(w) - len(suf) >= 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def fix_camel_case(line: str) -> str:
    """
    Insert a space at lower->upper case boundaries to fix PDF extraction
    artifacts like "deviceCategory" -> "device Category", WITHOUT breaking
    known brand/technical terms that happen to contain such a boundary
    themselves.
    """
    placeholders: dict[str, str] = {}
    for i, term in enumerate(PROTECTED_TERMS):
        if term in line:
            placeholder = f"\x00{i}\x00"
            line = line.replace(term, placeholder)
            placeholders[placeholder] = term

    line = re.sub(r"([a-z])([A-Z])", r"\1 \2", line)

    for placeholder, term in placeholders.items():
        line = line.replace(placeholder, term)

    return line


def clean_table_artifacts(line: str) -> str:
    """
    Fix checkbox/table-column-merge artifacts and dangling truncated
    parentheses commonly produced by PDF table extraction.
    """
    line = re.sub(r"(\S)☐", r"\1\n☐", line)
    line = re.sub(r"☐(\S)", r"☐ \1", line)

    if line.count("(") > line.count(")"):
        line = re.sub(r"\s*\([^)]*$", "", line)

    return line.strip()


# Models occasionally know exactly which rows belong in an answer but emit
# invalid Markdown around them. The production NV9 power answer was the sharp
# example: the "12 V DC operation" heading and its four-column header were
# appended to the final four-column row of the 24 V table, producing one
# eight-column row and a visibly broken table. This is presentation repair,
# not factual rewriting: it only inserts structural line breaks, restores a
# leading escaped table pipe, pads short rows and moves cells beyond the
# declared header width back to prose.
_MD_HEADER_HINTS = {
    "parameter", "minimum", "nominal", "maximum", "feature", "dimension",
    "configuration", "mode", "pin", "signal", "direction", "description",
    "length", "width", "item", "value", "type", "voltage", "current",
}
_MD_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
_MD_BOLD_HEADING_WITH_TABLE = re.compile(
    r"^(.*)(\*\*[^*\n]{2,90}\*\*:?)\s*(\|.*\|)\s*$")
_MD_PROSE_WITH_TABLE = re.compile(r"^([^|\n].*?\S)\s+(\|.*\|)\s*$")
_MD_SECTION_HEADING = re.compile(
    r"\b(?:operation|requirements?|specifications?|options?|notes?|pinout|"
    r"dimensions?|limits?|settings?|installation|configuration)\b", re.I)
_MD_INLINE_HEADING = re.compile(r"(\S)[ \t]+(?=#{1,4}\s+\S)")


def _markdown_cells(line: str) -> list[str]:
    """Split a pipe row without treating escaped/code pipes as columns."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith(r"\|"):
        body = body[:-1]

    cells: list[str] = []
    cell: list[str] = []
    in_code = False
    i = 0
    while i < len(body):
        char = body[i]
        if char == "\\" and i + 1 < len(body) and body[i + 1] == "|":
            cell.extend(("\\", "|"))
            i += 2
            continue
        if char == "`":
            in_code = not in_code
        if char == "|" and not in_code:
            cells.append("".join(cell).strip())
            cell = []
        else:
            cell.append(char)
        i += 1
    cells.append("".join(cell).strip())
    return cells


def _looks_like_markdown_header(row: str) -> bool:
    cells = _markdown_cells(row)
    if len(cells) < 2:
        return False
    hits = 0
    for cell in cells:
        words = set(re.findall(r"[a-z]+", re.sub(r"[*_`]", "", cell.lower())))
        if words & _MD_HEADER_HINTS:
            hits += 1
    return hits >= 2


def _format_markdown_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def normalize_markdown_tables(text: str) -> str:
    """Repair common model-produced table joins without changing facts.

    Valid Markdown is returned unchanged apart from consistent row spacing.
    Malformed rows are constrained to the first row's column count, so one
    accidental extra cell can never make the entire rendered table crooked.
    """
    if not text:
        return text

    # A heading only has Markdown meaning at the beginning of a line. Small
    # models regularly append it to the sentence or list item before it:
    # "For the SCS: ### Operating temperature". Split at the marker while
    # preserving the preceding content byte-for-byte.
    expanded = _MD_INLINE_HEADING.sub(r"\1\n", text)
    if "|" not in expanded:
        return expanded

    split_lines: list[str] = []
    for raw in expanded.splitlines():
        line = raw.rstrip()
        # A model sometimes escapes the first pipe, making a real row render
        # literally. Only repair row-shaped lines with several delimiters.
        if re.match(r"^\s*\\\|", line) and line.count("|") >= 3:
            line = re.sub(r"^(\s*)\\\|", r"\1|", line, count=1)

        # Prefer the LAST bold span before a header-shaped pipe run. That
        # leaves ordinary emphasis earlier in the prose alone.
        merged = _MD_BOLD_HEADING_WITH_TABLE.match(line)
        embedded_row = bool(merged and merged.group(1).lstrip().startswith("|"))
        section_heading = bool(merged and _MD_SECTION_HEADING.search(merged.group(2)))
        if (merged and _looks_like_markdown_header(merged.group(3))
                and (not embedded_row or section_heading)):
            prefix, heading, table = (part.strip() for part in merged.groups())
            if prefix:
                split_lines.append(prefix)
            split_lines.extend((heading, table))
            continue

        # Also repair "Intro sentence: | Column | Column |" without requiring
        # a heading. The prefix cannot itself contain a pipe, so valid rows do
        # not match this branch.
        inline = _MD_PROSE_WITH_TABLE.match(line)
        if inline and _looks_like_markdown_header(inline.group(2)):
            split_lines.extend((inline.group(1).strip(), inline.group(2).strip()))
            continue
        split_lines.append(line)

    out: list[str] = []
    expected_columns: int | None = None
    for line in split_lines:
        stripped = line.strip()
        is_row = stripped.startswith("|") and stripped.count("|") >= 2
        if not is_row:
            expected_columns = None
            out.append(line)
            continue

        cells = _markdown_cells(stripped)
        separator = bool(cells) and all(
            not cell or _MD_SEPARATOR_CELL.fullmatch(cell)
            for cell in cells)
        if expected_columns is None:
            expected_columns = len(cells)

        overflow: list[str] = []
        if len(cells) > expected_columns:
            if not separator:
                overflow = cells[expected_columns:]
            cells = cells[:expected_columns]
        elif len(cells) < expected_columns:
            cells.extend([""] * (expected_columns - len(cells)))
        out.append(_format_markdown_row(cells))

        if overflow:
            prose = " | ".join(cell for cell in overflow if cell).strip()
            if prose:
                out.extend(("", prose))
            expected_columns = None

    return "\n".join(out)


# ── split_units (for grounding NLI checks) ──────────────────────────────

def split_units(answer: str, min_len: int = MIN_UNIT_LEN) -> list[str]:
    """
    Split an answer into checkable units for grounding verification.
    Handles both prose (sentence-boundary split) and numbered/bulleted
    lists (newline split + strip list markers).
    """
    lines = [l.strip() for l in answer.split("\n") if l.strip()]

    units = []
    for line in lines:
        # The answer prompt asks for markdown: "### Heading", "**bold**",
        # and tables with a "|---|---|" separator row. A separator row or a
        # bare heading makes no claim, and NLI scores it near zero -- and
        # since the gate takes the MINIMUM over units, every table answer
        # failed verification on its punctuation, whatever its facts said.
        if re.fullmatch(r"\|?[\s:|\-]+\|?", line):
            continue
        cleaned = re.sub(r"^(#{1,6}\s+|\d+[\.\)]|\-|\*)\s*", "", line)
        cleaned = cleaned.replace("**", "")
        if not re.search(r"[A-Za-z]{2}", cleaned):
            continue
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        for s in sentences:
            s = s.strip().rstrip(".")
            if len(s) >= min_len:
                units.append(s)

    return units


# ── refusal handling ─────────────────────────────────────────────────────

def is_refusal(answer: str, phrases: list[str] | None = None) -> bool:
    """True if the answer text contains any known refusal phrasing."""
    phrases = phrases or REFUSAL_PHRASE_VARIANTS
    lower = answer.lower()
    return any(p in lower for p in phrases)


# Known chat-template/system-prompt boilerplate that local models
# (especially phi via Ollama) occasionally emit verbatim or near-
# verbatim when the retrieved context is too thin to actually answer
# from — e.g. "MyConnect System is a chat between a curious user and
# an artificial intelligence assistant. The assistant gives helpful
# answers..." for "introduction of myconnect system". This is the
# default Vicuna-style system message baked into several Ollama model
# templates, not anything our own prompt contains (grepped — not in
# this codebase), so it can only be coming from the model itself.
#
# Why this needs its own check rather than relying on the NLI grounding
# check: this kind of generic, low-content boilerplate makes no
# specific factual claim, so an NLI entailment model has nothing
# concrete to contradict — it scored 0.934 (well above the 0.35
# threshold) in production despite having zero actual relationship to
# the retrieved context. Pattern-matching known leak phrases, the same
# approach already used for is_refusal/REFUSAL_PHRASE_VARIANTS, is a
# deterministic catch for a deterministic failure mode that a semantic
# similarity/entailment check isn't designed to catch.
TEMPLATE_LEAK_PHRASES = [
    "curious user and an artificial intelligence assistant",
    "i am an ai language model",
    "i am a large language model",
    "as an ai language model",
    "i'm an ai assistant",
    "i don't have personal",
    "as a language model",
]


def is_template_leak(answer: str, phrases: list[str] | None = None) -> bool:
    """True if `answer` contains known model chat-template/system-prompt
    boilerplate rather than an actual answer derived from context."""
    phrases = phrases or TEMPLATE_LEAK_PHRASES
    lower = answer.lower()
    return any(p in lower for p in phrases)


def truncate_after_refusal(
    text: str,
    refusal_phrases: list[str] | None = None,
) -> str:
    """
    Small local models sometimes emit a correct(-ish) refusal sentence and
    then continue rambling into unrelated content. Checks each phrase in
    `refusal_phrases` and, for whichever appears EARLIEST in the text, cuts
    everything after the sentence containing it.
    """
    phrases = refusal_phrases or REFUSAL_PHRASE_VARIANTS
    lower = text.lower()

    earliest_idx = None
    earliest_end = None

    for phrase in phrases:
        idx = lower.find(phrase)
        if idx != -1 and (earliest_idx is None or idx < earliest_idx):
            earliest_idx = idx
            earliest_end = idx + len(phrase)

    if earliest_idx is None:
        return text

    sentence_end = text.find(".", earliest_end)
    if sentence_end == -1:
        sentence_end = len(text)
    else:
        sentence_end += 1

    return text[:sentence_end].strip()


# ── retrieval fusion / gating ────────────────────────────────────────────

def rrf_merge(*rankings: list, k: int = 60) -> dict:
    """Reciprocal Rank Fusion over any number of ranked lists."""
    scores: dict = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return scores


def passes_retrieval_gate(results: list[dict], threshold: float = 0.5) -> bool:
    """
    True if the top reranked chunk meets the relevance threshold.
    Expects results sorted best-first with a sigmoid-calibrated
    'rerank_score' in [0,1] (0.5 = the reranker's own relevance boundary).
    """
    if not results:
        return False
    return results[0].get("rerank_score", 0.0) >= threshold


def retrieval_confidence_band(results: list[dict], gate_threshold: float = 0.5,
                               ambiguous_ceiling: float = 0.65,
                               ambiguous_floor: float = 0.5) -> str:
    """
    Classify retrieval confidence into one of three bands: "confident",
    "ambiguous" (borderline score AND results scattered across several
    distinct sources — worth asking the user to narrow down), or "none"
    (failed the gate, refuse).
    """
    if not results or not passes_retrieval_gate(results, gate_threshold):
        return "none"

    top_score = results[0].get("rerank_score", 0.0)
    if top_score >= ambiguous_ceiling:
        return "confident"

    top_sources = {r.get("source") for r in results[:4] if r.get("source")}
    if len(top_sources) >= 3:
        # Scattered across several sources AND weak is ABSENCE, not
        # ambiguity, and the two look identical from a source count alone.
        # When nothing matches, results scatter precisely BECAUSE nothing
        # matches, so the spread is wide for the opposite reason.
        #
        # It matters because RETRIEVAL_GATE_THRESHOLD is 0.0001 -- close
        # enough to off that "none" is only reached when retrieval scores a
        # literal zero. So everything weak-but-nonzero landed here. Measured:
        # "how do I reset the password on my Cisco router" scored 0.204 and
        # was asked which of four products it meant; eval.py expects it
        # REJECTED, and being asked to pick a product for a question no
        # product answers is a loop that cannot end.
        #
        # The floor is the reranker's OWN decision boundary: reranker.py
        # sigmoids its logits so 0.5 means a raw logit of zero, the point
        # where the model stops calling a passage relevant. To be worth
        # disambiguating, the best candidate should at least be more likely
        # relevant than not.
        if top_score < ambiguous_floor:
            return "none"
        return "ambiguous"

    return "confident"


# ── conversational query condensation (Rewrite-Retrieve-Read) ───────────
#
# REPLACES the previous approach, which tried to GUESS whether a query
# was a "follow-up" using surface heuristics (word count <= 10, or
# membership in a fixed pronoun/keyword list). That heuristic flagged
# essentially every short, complete, self-contained question as a
# follow-up — e.g. "how to connect tablet to hub" (6 words) — causing it
# to be silently concatenated with whatever unrelated query happened to
# run before it in the same (unscoped, never-cleared) memory buffer.
#
# The replacement follows the "Rewrite-Retrieve-Read" pattern (Ma et al.,
# 2023, arXiv:2305.14283) used by LangChain's history-aware retriever and
# documented across multiple production conversational-RAG write-ups: a
# fast LLM call is ALWAYS given the conversation history and the current
# message, and is explicitly instructed to return the message UNCHANGED
# if it's already self-contained, or rewritten into a standalone query if
# it depends on prior context. The decision and the fix happen in the
# same step — there is no separate, brittle "is this a follow-up?"
# classifier to get wrong.
#
# This module only contains the PURE, unit-testable pieces (prompt
# construction and output parsing). The actual model call lives in
# llm.condense_query, which calls these.

CONDENSE_PROMPT_TEMPLATE = """You are a query rewriting assistant for a document search system.

Conversation history:
{history_text}

Latest user message: "{current_query}"

Task: If the latest message depends on the conversation history to make sense (for example it uses words like "that", "it", "more", "step 1", or is otherwise incomplete on its own), rewrite it into a single, self-contained search query that includes the necessary context from the history.

IMPORTANT — entity switches: if the latest message names a DIFFERENT product or thing than the history was about (e.g. "what about the MyCheckr?" after discussing the MyCheckr Mini), the rewritten query MUST be about the NEW entity, carrying over only the TOPIC from history. Examples:
- History about "what network does MyCheckr Mini support"; latest: "what about the MyCheckr?" -> "what network does the MyCheckr support"
- History about "how do I reset the Hub"; latest: "and the app?" -> "how do I reset the MyConnect app"
- History about "MyCheckr Mini weight"; latest: "tell me more about what network connections it supports" -> "what network connections does the MyCheckr Mini support"

If the latest message is ALREADY a complete, self-contained question that does not depend on the history, return it EXACTLY AS-IS, unchanged.

Output ONLY the final query text. No explanation, no preamble, no quotation marks.

Rewritten query:"""


def build_condense_prompt(
    current_query: str,
    history: list[dict],
    max_history_turns: int = 2,
) -> str:
    """
    Build the prompt for LLM-based query condensation. Only the most
    recent `max_history_turns` are included — older turns are rarely
    needed to resolve an immediate follow-up, and keeping this prompt
    short matters since it runs as an extra latency-sensitive call on
    every turn beyond the first.
    """
    recent = history[-max_history_turns:] if history else []

    if recent:
        history_text = "\n".join(
            f'User: {h["q"]}\nAssistant: {h["a"]}' for h in recent
        )
    else:
        history_text = "(none — this is the first message)"

    return CONDENSE_PROMPT_TEMPLATE.format(
        history_text=history_text,
        current_query=current_query,
    )


def parse_condense_output(raw_output: str, fallback_query: str) -> str:
    """
    Clean up the LLM's rewritten-query output. Falls back to the
    original query if the output is empty or clearly degenerate.

    BUG FIXED: phi tends to continue generating past the rewritten
    query and outputs the rest of the prompt template (rules, examples,
    etc.) as additional lines. Taking only the FIRST non-empty line
    prevents the entire prompt from leaking into the resolved_query
    field and downstream retrieval.
    """
    if not raw_output:
        return fallback_query

    # Take only the first non-empty line — the rewritten query is
    # always a single line; everything after is phi continuing the prompt
    first_line = next((l.strip() for l in raw_output.split("\n") if l.strip()), "")
    if not first_line:
        return fallback_query

    text = first_line.strip("\"'")
    text = re.sub(r"^(rewritten query|query)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = text.strip().strip("\"'")

    if not text:
        return fallback_query

    return text


# ── is this question leaning on the previous turn? ────────────────────
#
# REWRITTEN 2026-09-19 from a list of observed phrasings into four rules
# over CLOSED word classes. The old list had grown one entry per bug
# report -- "what about", then "both", then a stacked-opener fix -- and ten
# scripted customer conversations found four more gaps in an afternoon
# ("is THAT configurable", "which ONE would you recommend"). A list of
# things customers have already said cannot cover what the next one says.
#
# The rules, each a closed class of English function words plus one
# structural test:
#
#   R1 OPENER          the question begins with discourse connectives
#                      and/or the elliptical "what about" frame.
#   R2 PRO-FORM        it contains a pro-form doing referential work, and
#                      does not name a product of its own.
#   R3 DISCOURSE DEIXIS it points at the conversation itself -- "the
#                      above", "step 3", "as mentioned".
#   R4 CONTINUATION    it asks for more of the same -- "tell me more".
#
# R2 is where the old code went wrong twice. It gated pronouns on query
# LENGTH (8 words) as a proxy for "a long question probably names its own
# subject" -- so "does it need a separate supply from the host board" (9
# words, subject is a bare pronoun) read as standalone while "can my staff
# use it without any training" (8 words, "it" in object position) read as
# a follow-up. Both wrong, in opposite directions. The proxy is gone: we
# test the thing itself, whether the question names a product, and whether
# the pro-form is in subject position.

# Discourse connectives people stack in chat: "ok so what about...",
# "right, and the other one?". Closed class, repeatable.
_CONNECTIVE = r"(?:and|so|ok(?:ay)?|right|then|also|but|plus|now|well)"

_R1_OPENER = re.compile(
    rf"^\W*(?:{_CONNECTIVE}\b[,\s]+)*(?:(?:what|how)\s+about\b|{_CONNECTIVE}\b)",
    re.IGNORECASE)

# R3: reference to the conversation's own structure, not to the world.
_R3_DEIXIS = re.compile(
    # "step 3" and "step three" are the same reference; the spelled-out
    # numbers are a closed class, so both forms are covered here rather
    # than waiting for someone to type the other one.
    r"\b(?:step\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"|from\s+step|(?:from|as)\s+above|the\s+above|as\s+mentioned"
    r"|from\s+that|previously|earlier\s+you|you\s+said|i\s+need\s+more\s+context)\b",
    re.IGNORECASE)

# R4: asking for more of what was just given. "more" heads the request --
# unanchored so "can you please tell me more" counts, but "more context"
# as part of a longer standalone question does not get a free pass.
_R4_CONTINUATION = re.compile(
    r"^\W*(?:more|elaborate|continue|further|go\s+on)\b"
    r"|\btell\s+me\s+more\b|\bmore\s+(?:about\s+(?:that|it)|context|detail)\b"
    r"|\b(?:give|show)\s+me\s+that\b",
    re.IGNORECASE)

# R2, part one: the pro-forms. A closed class -- English has no more of
# these, which is the point: this list cannot fall behind the customers.
_PROFORM_ALWAYS = r"(?:they|them|their|theirs|those|these)"
# Set anaphora: a quantifier standing in for things named earlier.
# "together" is deliberately absent -- "screw it together", "put it
# together" are assembly instructions all over these manuals.
_PROFORM_SET = (r"(?:both|either\s+of\s+them|neither\s+of\s+them|each\s+of\s+them"
                r"|the\s+two|the\s+pair|the\s+other\s+one|the\s+former|the\s+latter)")

# "it" and "that"/"this" are only pro-forms in some positions, so each
# gets a structural test rather than a blanket match.
_AUX = (r"(?:do|does|did|is|are|was|were|can|could|will|would|should|has|have|had"
        r"|must|may|might)")

# Subject position: the question's subject IS the pro-form, so the
# question cannot be read without the previous turn. "does IT need a
# separate supply" / "can THOSE share a bus" / "is THAT configurable".
#
# Not anchored to the start of the question: the clause carrying the
# pro-form can be anywhere ("how many coins a second CAN IT pay out").
# Word ORDER is what makes this a subject test -- auxiliary then pro-form.
# "before it is powered" and "when it does jam" are pro-form then
# auxiliary, so they do not match, which is right: those have their
# antecedent inside the same sentence.
_R2_SUBJECT = re.compile(
    rf"\b{_AUX}\s+(?:it|that|this|they|them|those|these|both)\b",
    re.IGNORECASE)

# A subject pro-form is only UNRESOLVED if the question does not supply its
# own antecedent. "if the machine rejects a note, does IT give any
# indication why" reads perfectly on its own -- the antecedent is one
# clause to the left. The general signal is another clause before this one,
# which English introduces with a closed set of subordinators and
# coordinators, carrying at least one word of its own.
#
# Coordinators are included because the same thing happens with "or":
# "does the hopper need emptying manually or does IT self-level" names the
# hopper in its first clause. They cannot be confused with R1's openers,
# which are sentence-INITIAL and are tested before this.
_SUBORDINATOR = re.compile(
    r"\b(?:if|when|whenever|after|before|while|once|unless|although|though"
    r"|since|because|where|as\s+soon\s+as)\b", re.IGNORECASE)
# Coordinators overlap with R1's openers, so they only count as a clause
# break MID-sentence: "and does it need a plug" is a continuation of the
# previous turn, while "...or does it self-level" is a second clause of
# this one.
_COORDINATOR = re.compile(r"\b(?:and|or|but)\b", re.IGNORECASE)


def _has_local_antecedent(query: str, match) -> bool:
    """Does the question supply the pro-form's antecedent itself?

    True when another clause runs BEFORE the one the pro-form heads --
    "does the hopper need emptying manually OR does it self-level", "IF the
    machine rejects a note, does it...". The content has to sit before the
    connective, not after it: a coordinated second clause often starts
    straight in on the auxiliary, so requiring words between the two was
    what missed the hopper question.

    A connective at position 0 is not a clause break, it is R1's opener --
    and R1 has already been tested by the time this is called.
    """
    before = query[:match.start()]
    if len(before.split()) < 2:
        return False
    if _SUBORDINATOR.search(before):
        return True
    m = _COORDINATOR.search(before)
    return bool(m) and m.start() > 0
# ...or the pro-form opens the question outright: "those need a plug?"
_R2_INITIAL = re.compile(
    r"^\W*(?:it|its|they|them|those|these|that|this)\b", re.IGNORECASE)

# EXPLETIVE "it" is not referential: "how long does it take to...", "is it
# safe to...". The frame is impersonal -- a placeholder subject with the
# real content in an infinitival clause -- so it is detected by the frame,
# not by listing the verbs people use in it.
_EXPLETIVE_IT = re.compile(
    r"\bit\s+(?:takes?|took)\b(?=.*\bto\s+\w)"
    r"|\b{aux}\s+it\s+(?:take|takes|took)\b(?=.*\bto\s+\w)"
    r"|\b(?:is|was|s)\s+it\s+\w+\s+to\s+\w"
    r"|\bit\s+(?:is|was|'s)\s+\w+\s+to\s+\w".format(aux=_AUX),
    re.IGNORECASE)

# "one"/"ones" is a pro-form only when it heads an ELIDED noun phrase --
# "which one", "the mini one", "the other ones" -- where the noun it stands
# for was supplied earlier. English marks that with a determiner or
# wh-word, optionally with adjectives between, and that is the test.
#
# Tried first and rejected: "one is pronominal unless a noun follows it".
# That reads "to get it working on day ONE" as a pro-form, because nothing
# follows it. A numeral ("share ONE RS232 bus") and an idiom ("day ONE")
# both lack the determiner, so requiring one separates all three.
_DETERMINER = (r"(?:the|this|that|these|those|which|another|other|each|either"
               r"|any|same|both)")
_R2_ONE = re.compile(rf"\b{_DETERMINER}\b(?:\s+\w+){{0,2}}\s+ones?\b",
                     re.IGNORECASE)



# Generic domain vocabulary for this corpus (MyConnect/MyCheckr
# installation, networking, and registration docs). Deliberately broad
# and topic-level rather than product-specific — the point is NOT to
# match a specific manual, just to tell apart "this query is clearly
# about something in our domain but underspecified" (e.g. "explain why
# device registration might fail" — which device? MyCheckr? the Hub?)
# from "this query has nothing to do with our domain at all" (e.g.
# "what is the capital of france").
_DOMAIN_VOCABULARY = {
    "device", "devices", "hub", "tablet", "mycheckr", "myconnect",
    "install", "installation", "installer", "verify", "verification",
    "registration", "register", "system", "network", "wifi", "app",
    "connect", "connection", "connected", "power", "relay", "log",
    "alert", "configure", "configured", "checklist", "sign", "signoff",
    "firmware", "multicast", "discovery", "ethernet",
}


def has_domain_vocabulary(query: str) -> bool:
    """
    True if `query` contains at least one term from this corpus's
    domain vocabulary, even if retrieval couldn't actually find a good
    match for it. Used in main.py's "none" confidence branch to decide
    between asking a clarifying question (vague-but-in-domain) and a
    flat rejection (genuinely out-of-domain, e.g. "capital of France").
    """
    tokens = {re.sub(r"[^\w]", "", t).lower() for t in query.split()}
    return any(t in _DOMAIN_VOCABULARY for t in tokens)


def has_reference_markers(query: str) -> bool:
    """
    True if the query contains specific linguistic signals that it is
    referencing prior conversation context (pronouns, 'step N', 'from
    above', 'give me that', continuation conjunctions, etc.) and may
    therefore need to be rewritten into a standalone query.

    Returns False for queries that are clearly self-contained, which
    short-circuits the condense_query model call entirely and prevents
    phi from incorrectly rewriting standalone queries like "post
    installation verification installer sign off" into whatever topic
    happened to be discussed in the previous turn.

    See the rule block above for R1-R4. `why_reference_markers` returns
    which rule fired, for diagnosis.
    """
    return bool(why_reference_markers(query))


def why_reference_markers(query: str) -> str | None:
    """Which rule says this is a follow-up, or None. Same decision as
    has_reference_markers, with its reason -- so a misclassification can be
    argued about without re-deriving it from the regexes."""
    q = (query or "").strip()
    if not q:
        return None

    if _R1_OPENER.search(q):
        return "R1 opener"
    if _R3_DEIXIS.search(q):
        return "R3 discourse deixis"
    if _R4_CONTINUATION.search(q):
        return "R4 continuation"

    # R2. A question that NAMES ITS OWN SUBJECT is self-contained whatever
    # pro-forms it also contains: "is the BV30 any good with these new
    # polymer notes" needs no previous turn. This replaces the word-count
    # proxy, and it is why R2 is checked last -- naming a product does not
    # rescue "and what about the BV30?", which is still a continuation.
    if _names_a_product(q):
        return None

    if _PROFORM_SET_RE.search(q):
        return "R2 set anaphora"
    if _R2_ONE.search(q):
        return "R2 pronominal one"
    if _PROFORM_ALWAYS_RE.search(q):
        return "R2 plural pro-form"
    if _R2_INITIAL.match(q):
        return "R2 sentence-initial pro-form"
    _subj = _R2_SUBJECT.search(q)
    if (_subj and not _EXPLETIVE_IT.search(q)
            and not _has_local_antecedent(q, _subj)):
        # The pro-form IS the subject, so the question cannot be read on its
        # own: "does IT need a separate supply". Object-position pronouns
        # are left alone -- "can my staff use it without training" carries
        # its own subject and reads fine as a standalone question.
        return "R2 subject pro-form"
    return None


_PROFORM_ALWAYS_RE = re.compile(rf"\b{_PROFORM_ALWAYS}\b", re.IGNORECASE)
_PROFORM_SET_RE = re.compile(rf"\b{_PROFORM_SET}\b", re.IGNORECASE)


def _names_a_product(query: str) -> bool:
    """Does the question name a product from the catalogue, by name or by
    one of the short codes its manual uses?

    Catalogue-driven rather than a hand-kept list, so adding a product in
    the console also teaches this. Lazy and guarded for the same reason as
    _product_label_for_source: text_utils stays a leaf module.
    """
    low = f" {query.lower()} "
    for term in _product_terms():
        if f" {term} " in low or f" {term}?" in low or f" {term}," in low:
            return True
    return False


_PRODUCT_TERMS: tuple[str, ...] | None = None


def _product_terms() -> tuple[str, ...]:
    global _PRODUCT_TERMS
    if _PRODUCT_TERMS is not None:
        return _PRODUCT_TERMS
    terms: set[str] = set()
    try:
        import catalog
        for cat in catalog.catalog().get("categories", []):
            for prod in cat.get("products", []):
                name = (prod.get("name") or "").strip().lower()
                # "General (shared docs)" is a bucket, not a product name.
                if name and not name.startswith("general"):
                    terms.add(name)
                    # The head word alone: customers type "NV9" and "MyCheckr"
                    # far more often than the full catalogue name.
                    head = name.split()[0]
                    if len(head) > 2:
                        terms.add(head)
                for alias in prod.get("aliases", []) or []:
                    a = str(alias).strip().lower()
                    if a:
                        terms.add(a)
    except Exception:
        pass
    _PRODUCT_TERMS = tuple(sorted(terms, key=len, reverse=True))
    return _PRODUCT_TERMS


# ── capability questions ──────────────────────────────────────────────
#
# "Does ICU work with Linux?" was refused while retrieval returned, at rank
# one, a document called "Accessing my device in Linux Environment" -- and
# the refusal then offered "How do I access my ICU device in a Linux
# environment?" as a suggestion. The answer was on screen twice.
#
# It reads like a question needing judgement about compatibility, and it is
# not. "There is a documented procedure for Linux" is a fact about the
# CORPUS, not an inference about the world, so answering it needs no new
# grounding contract: we report what we hold. Deciding compatibility where
# no procedure exists is a different and much riskier feature.
#
# A closed set of frames, and the answer is whatever follows the verb.
# "how can I connect to MyCheckr using linux?" matched NOTHING, because the
# frame was anchored on the closed-question openers only. So "can I connect
# to X using Y" was recognised and answered while "HOW can I connect to X
# using Y" -- the phrasing that actually wants the steps -- was refused.
# Seen in the widget transcript of 2026-09-22, one turn after the closed
# form had just been answered, which is the worst possible way to meet it.
_CAPABILITY = re.compile(
    r"^\W*(?:how\s+(?:can|do|would)\s+\w+\s+|"
    r"(?:does|do|is|are|can|could|will|would|has|have)\b)[^?]{0,60}?"
    r"\b(?:work|works|run|runs|runnable|compatible|compatibility|integrate"
    r"|integrates|integrated|use|used|usable|support|supports|supported"
    r"|talk|talks|connect|connects)\b(?P<tail>[^?]*)\??$",
    re.IGNORECASE)
# The tail used to end `(?P<tail>[^?]*)\??\s*$`, and `[^?]*` matches spaces
# too -- so on a question with a long run of spaces and no "?" the two
# could split that run every possible way before failing. Quadratic, on a
# string a visitor types.
#
# The first attempt at this collapsed whitespace at the CALL SITE, which
# removes the attack but not the ambiguity: CodeQL still saw a
# user-provided value reaching an ambiguous pattern, and was right to --
# the next caller would not know to collapse first. Fixing the PATTERN is
# the durable half. `\s*$` is gone, and nothing is lost: `[^?]*` already
# absorbs any trailing spaces, and capability_target strips the tail
# immediately below. `[^?]*` and `\??` cannot overlap, because the one
# character `\??` matches is the one `[^?]*` cannot.

# The companion preposition, taken as the PIVOT: in "can I use MyCheckr
# with linux" the product sits between the verb and the preposition, so
# reading the target straight off the verb gave "MyCheckr with linux". The
# LAST such preposition is the one that introduces what is being asked
# about.
#
# `using`, `via`, `through`, `over` and `in` were missing, and the gap was
# visible in the widget transcript of 2026-09-21: "Can I connect to
# MyCheckr using linux?" found no preposition, so the target was read
# straight off the verb as "MyCheckr using linux", which _names_a_product
# then rejected -- and the turn got the bare refusal with no capability
# line at all. "in" earns its place on the commonest phrasing of the lot,
# "can I use MyCheckr in a linux environment".
_COMPANION_PREP = re.compile(
    r"\b(?:with|on|alongside|against|onto|into|using|via|through|over|in)\b",
    re.IGNORECASE)

# Words that are never the subject of a "does it work with X" -- they are
# the product or a filler, not the thing being asked about.
_NOT_A_TARGET = {"it", "this", "that", "them", "these", "those", "one",
                 "me", "us", "you", "anything", "everything", "the"}


def capability_target(query: str) -> str | None:
    """The thing a capability question is asking about, or None.

    "does ICU lite work with android?" -> "android"
    "can I use MyCheckr with linux?"   -> "linux"
    "is the NV200S compatible with a note recycler?" -> "a note recycler"

    Returns the raw phrase, lowercased and trimmed. The CALLER decides
    whether anything is documented about it -- this function only reads the
    question, and a target it cannot resolve is the caller's cue to fall
    through to ordinary retrieval rather than to claim anything.
    """
    # Whitespace collapsed before matching. Not the ReDoS fix -- that is in
    # the pattern itself, see _CAPABILITY -- but it is what lets the
    # pattern end at `$` with no `\s*` in front of it, and the function
    # normalised whitespace a few lines further down anyway, so this only
    # moves work earlier.
    m = _CAPABILITY.match(" ".join((query or "").split()))
    if not m:
        return None
    tail = m.group("tail") or ""
    preps = list(_COMPANION_PREP.finditer(tail))
    # After the last companion preposition when there is one ("...use
    # MyCheckr WITH linux"), otherwise straight off the verb ("...SUPPORT
    # polymer notes").
    target = tail[preps[-1].end():] if preps else tail
    target = " ".join(target.split()).strip(" ?.,").lower()
    # A preposition the pivot did not consume ("...connect TO ethernet"),
    # then the determiner.
    target = re.sub(r"^(?:to|for|in|at|from)\s+", "", target)
    target = re.sub(r"^(?:a|an|the|my|our|your)\s+", "", target)
    if len(target.split()) > 4:
        # Too long to be the thing asked about; treating it as one would
        # mean searching the corpus for a sentence.
        return None
    if not target or target in _NOT_A_TARGET:
        return None
    # A target that is just the product again ("does the NV9 work with the
    # NV9") carries no question.
    if _names_a_product(target) and len(target.split()) <= 3:
        return None
    return target


def _product_label_for_source(source: str) -> str | None:
    """Map a source filename to a friendly product/area label, or None.

    e.g. "MyCheckr Mini User Manual-v2.pdf" -> "MyCheckr Mini".
    Deliberately lightweight/string-based — used only to offer
    human-readable clarification choices, never for retrieval.
    """
    if not source:
        return None
    # THE CATALOGUE FIRST. The hardcoded list below covers four products;
    # the corpus has eleven documents, so an NV9, SMART Coin System or BV30
    # source returned None and the "which did you mean?" chips came back
    # empty for most of the range -- silently, because an empty option list
    # is a documented outcome. The catalogue already maps sources to
    # products and carries their display names; it is the same mapping the
    # console shows an operator.
    #
    # Imported lazily and guarded: text_utils is a leaf module that every
    # other one imports, and it stays that way. catalog itself imports only
    # json and os, so there is no cycle -- the guard is for a missing or
    # unreadable catalogue file, not for an import loop.
    try:
        import catalog
        for key in catalog.product_for_source(source):
            for cat in catalog.catalog().get("categories", []):
                for prod in cat.get("products", []):
                    if prod.get("key") == key and prod.get("name"):
                        name = prod["name"]
                        # "General" / "General (shared docs)" is a bucket,
                        # not something to offer as a choice.
                        if not name.lower().startswith("general"):
                            return name
    except Exception:
        pass
    s = source.lower()
    # Order matters: check more specific labels before their prefixes
    # ("MyCheckr Mini" before "MyCheckr").
    known = [
        ("mycheckr mini", "MyCheckr Mini"),
        ("mycheckr", "MyCheckr"),
        ("myconnect", "MyConnect"),
        ("biometrics", "Biometrics Range"),
    ]
    for needle, label in known:
        if needle in s:
            return label
    return None


def build_clarification_options(
    kind: str,
    history: list[dict] | None,
    results: list[dict] | None,
    max_options: int = 5,
) -> list[str]:
    """
    Build a short list of concrete, pickable clarification options for a
    clarify turn, so the GUI can show a dropdown ("did you mean…?")
    instead of only free-text.

    kind:
      - "followup": offer the recent conversation topics the user might
        be referring back to (most recent first), so "give me step 1
        from that" can be pinned to a specific earlier question.
      - "ambiguous_in_domain": offer the product/area labels derived
        from whatever candidate sources retrieval turned up, so "explain
        why device registration might fail" becomes a choice between
        MyConnect / MyCheckr / MyCheckr Mini etc.

    Always returns distinct, human-readable strings (no "Other"/"Skip" —
    those are GUI affordances the client adds, not data). May be empty if
    there's nothing concrete to offer, in which case the GUI falls back
    to a plain free-text clarification.
    """
    options: list[str] = []
    seen: set[str] = set()

    def add(item: str | None):
        if not item:
            return
        norm = item.strip()
        key = norm.lower()
        if norm and key not in seen:
            seen.add(key)
            options.append(norm)

    if kind == "followup":
        for turn in reversed(history or []):
            add(turn.get("q"))
            if len(options) >= max_options:
                break
    elif kind == "ambiguous_in_domain":
        for r in (results or []):
            add(_product_label_for_source(r.get("source", "")))
            if len(options) >= max_options:
                break

    return options[:max_options]


def is_followup_turn(raw_query: str, history: list, resolved_query: str) -> bool:
    """
    True if this turn was dependent on conversation history rather than a
    fresh, standalone question — either the raw query had reference
    markers, or condense_query actually rewrote it into something
    different.

    Used in main.py's retrieval_confidence_band == "none" branch to tell
    apart two cases that look identical from a bare retrieval score but
    are NOT the same situation:
      - a genuinely out-of-domain standalone query (e.g. "capital of
        France") — the flat "I could not find that" rejection is correct.
      - an in-context follow-up whose condensed/rewritten query still
        failed to retrieve anything (e.g. "is there anything else, I
        checked the above and they're fine") — flatly rejecting this the
        same way breaks the conversational flow; asking a clarifying
        question is the right response instead.

    Requires `history` to be non-empty: with no prior turns there is
    nothing to be a follow-up to, regardless of surface phrasing (a
    first-ever message containing "the above" with no history is just a
    malformed standalone query, not a follow-up).
    """
    if not history:
        return False
    return (has_reference_markers(raw_query)
            or _rewrite_folded_in_history(raw_query, resolved_query, history))


def _rewrite_folded_in_history(raw_query: str, resolved_query: str,
                               history: list) -> bool:
    """True if condensation actually pulled context out of the history.

    `resolved_query != raw_query` used to stand in for this, and it is too
    cheap a test. It counts any difference at all as evidence the
    conversation was needed, including differences that carry no such
    meaning: `_normalize_query` alone lowercases "DEFAULT LOGIN???", and a
    rewriter asked to return a self-contained question "EXACTLY AS-IS" still
    routinely returns it reflowed or repunctuated.

    That was survivable while a regex gated condensation, because the model
    only ran on queries that had already been judged history-dependent. With
    that gate gone (2026-09-22) the rewriter runs on every turn with
    history, so a fresh standalone question would otherwise read as a
    follow-up on nothing more than phi tidying its punctuation -- which is
    the 2026-09-19 fault ("how sturdy are nv9 st" answered with a clarifying
    question about the previous turn's pricing) arriving through a different
    door.

    So the test is what a rewrite MEANS: it added content words, and those
    words came from the conversation. Reflowing, recasing and repunctuating
    add nothing and are correctly ignored; genuinely resolving "and the
    app?" against "how do I reset the Hub" is not.
    """
    def _words(text: str) -> set:
        return set(re.findall(r"[a-z0-9]+", (text or "").lower()))

    added = {w for w in _words(resolved_query) - _words(raw_query) if len(w) > 3}
    if not added:
        return False

    # The same two turns build_condense_prompt shows the model: context it
    # was never given cannot be where the rewrite got a word.
    hist_words: set = set()
    for turn in history[-2:]:
        hist_words |= _words(turn.get("q", "")) | _words(turn.get("a", ""))
    return bool(added & hist_words)


# ── semantic query routing ────────────────────────────────────────────
#
# REPLACES keyword-list classification (router.py used to check whether
# any of a fixed set of strings like "why", "how does", "checklist"
# appeared in the query). That has the same structural weakness the old
# follow-up detector had: phrasing that doesn't happen to contain one of
# the listed words/phrases gets misclassified regardless of actual
# intent, and it's brittle to paraphrasing ("what's the reason device
# registration fails" contains none of the _REASONING_KW strings even
# though it's clearly asking "why").
#
# Replacement: embed a small set of canonical example queries per role
# ONCE (router.py does this, cached), embed the incoming query with the
# SAME embedding model already loaded for retrieval (no extra model
# load, no extra LLM call), and classify by nearest-neighbor cosine
# similarity. This generalizes to paraphrasing in a way keyword lists
# structurally cannot.
#
# This function is the pure, ML-free piece: given already-computed
# vectors (numpy arrays), do the actual classification math. The
# embedding-model calls live in router.py and require sentence-
# transformers, which isn't available in this sandbox — see
# tests/test_router.py for the documented verification boundary.

def classify_by_similarity(
    query_vec,
    category_vectors: dict,
    min_confidence: float = 0.30,
    default: str = "accurate",
) -> str:
    """
    Classify `query_vec` against `category_vectors` (role -> list of
    example-query vectors, all pre-normalized) by cosine similarity.

    Since vectors are expected pre-normalized (unit length — this is
    what embeddings.embed_query/embed_texts already produce), cosine
    similarity is just a dot product.

    For each category, uses the BEST-matching example (max similarity)
    rather than the average — a query only needs to closely resemble
    ONE good canonical example of a category to belong to it; averaging
    would penalize categories with more diverse examples for no good
    reason.

    Falls back to `default` if no category's best match clears
    `min_confidence` — i.e. the query doesn't closely resemble ANY
    canonical example of ANY specific category, so the safe behaviour is
    the general-purpose role rather than a confident wrong guess.
    """
    if not category_vectors:
        return default

    best_role = default
    best_score = min_confidence

    for role, vectors in category_vectors.items():
        if not vectors:
            continue
        for v in vectors:
            score = float(query_vec @ v)
            if score > best_score:
                best_score = score
                best_role = role

    return best_role
