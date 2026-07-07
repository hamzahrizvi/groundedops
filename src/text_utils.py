"""
Pure text-processing utilities — no ML dependencies.

Kept separate from grounding.py/llm.py/structure.py so this logic can be
unit-tested without pulling in sentence-transformers, chromadb, or hitting
Ollama/DeepSeek.
"""

import re

REFUSAL_PHRASE = "i don't have the answer for that. please contact support as above."

REFUSAL_PHRASE_VARIANTS = [
    REFUSAL_PHRASE,
    "i don't have the answer for that",
    "please contact support as above",
    "there is no information about",
    "the context does not contain",
    "does not contain this information",
    "no information about this in the",
]

MIN_UNIT_LEN = 12

PROTECTED_TERMS = ["MyConnect", "MyCheckr", "WiFi", "GPIO"]

TRAILING_STOPWORDS = {
    "and", "or", "the", "a", "an", "with", "to", "of", "in", "on",
    "for", "but", "is", "are", "was", "were", "if", "from",
}

LIST_LINE_RE = re.compile(r"^\s*(?:[-*•☐]|\d+[.)])\s+")
STEP_HEADER_RE = re.compile(r"^step\s+\d+", re.IGNORECASE)


def fix_camel_case(line: str) -> str:
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
    line = re.sub(r"(\S)☐", r"\1\n☐", line)
    line = re.sub(r"☐(\S)", r"☐ \1", line)

    if line.count("(") > line.count(")"):
        line = re.sub(r"\s*\([^)]*$", "", line)

    return line.strip()


def split_units(answer: str, min_len: int = MIN_UNIT_LEN) -> list[str]:
    lines = [l.strip() for l in answer.split("\n") if l.strip()]

    units = []
    for line in lines:
        cleaned = re.sub(r"^(\d+[\.\)]|\-|\*)\s*", "", line)
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        for s in sentences:
            s = s.strip().rstrip(".")
            if len(s) >= min_len:
                units.append(s)

    return units


def is_refusal(answer: str, phrases: list[str] | None = None) -> bool:
    phrases = phrases or REFUSAL_PHRASE_VARIANTS
    lower = answer.lower()
    return any(p in lower for p in phrases)


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
    phrases = phrases or TEMPLATE_LEAK_PHRASES
    lower = answer.lower()
    return any(p in lower for p in phrases)


def truncate_after_refusal(
    text: str,
    refusal_phrases: list[str] | None = None,
) -> str:
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


def rrf_merge(*rankings: list, k: int = 60) -> dict:
    scores: dict = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return scores


def passes_retrieval_gate(results: list[dict], threshold: float = 0.5) -> bool:
    if not results:
        return False
    return results[0].get("rerank_score", 0.0) >= threshold


def retrieval_confidence_band(results: list[dict], gate_threshold: float = 0.5,
                               ambiguous_ceiling: float = 0.65) -> str:
    if not results or not passes_retrieval_gate(results, gate_threshold):
        return "none"

    top_score = results[0].get("rerank_score", 0.0)
    if top_score >= ambiguous_ceiling:
        return "confident"

    top_sources = {r.get("source") for r in results[:4] if r.get("source")}
    if len(top_sources) >= 3:
        return "ambiguous"

    return "confident"


CONDENSE_PROMPT_TEMPLATE = """You are a query rewriting assistant for a document search system.

Conversation history:
{history_text}

Latest user message: "{current_query}"

Task: If the latest message depends on the conversation history to make sense (for example it uses words like "that", "it", "more", "step 1", or is otherwise incomplete on its own), rewrite it into a single, self-contained search query that includes the necessary context from the history.

If the latest message is ALREADY a complete, self-contained question that does not depend on the history, return it EXACTLY AS-IS, unchanged.

Output ONLY the final query text. No explanation, no preamble, no quotation marks.

Rewritten query:"""


def build_condense_prompt(
    current_query: str,
    history: list[dict],
    max_history_turns: int = 2,
) -> str:
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
    if not raw_output:
        return fallback_query

    first_line = next((l.strip() for l in raw_output.split("\n") if l.strip()), "")
    if not first_line:
        return fallback_query

    text = first_line.strip("\"'")
    text = re.sub(r"^(rewritten query|query)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = text.strip().strip("\"'")

    if not text:
        return fallback_query

    return text


_REFERENCE_PATTERNS = [
    re.compile(r"^(more|elaborate|continue|further)\b", re.IGNORECASE),
    re.compile(r"\btell me more\b", re.IGNORECASE),
    re.compile(r"\b(step \d+|from step|from above|the above|as above|as mentioned|from that)\b", re.IGNORECASE),
    re.compile(r"^(and |also |but )\b", re.IGNORECASE),
    re.compile(r"\b(give me that|show me that|what about that|more about (that|it)|more context|more detail)\b", re.IGNORECASE),
    re.compile(r"^\s*(it|they|them|those|these)\b", re.IGNORECASE),
    re.compile(r"\b(it|its|they|them|those|these|that one)\b", re.IGNORECASE),
    re.compile(r"\bi need more context\b", re.IGNORECASE),
]

_SHORT_ONLY_PATTERNS = {6}
_SHORT_QUERY_MAX_WORDS = 8


_DOMAIN_VOCABULARY = {
    "device", "devices", "hub", "tablet", "mycheckr", "myconnect",
    "install", "installation", "installer", "verify", "verification",
    "registration", "register", "system", "network", "wifi", "app",
    "connect", "connection", "connected", "power", "relay", "log",
    "alert", "configure", "configured", "checklist", "sign", "signoff",
    "firmware", "multicast", "discovery", "ethernet",
}


def has_domain_vocabulary(query: str) -> bool:
    tokens = {re.sub(r"[^\w]", "", t).lower() for t in query.split()}
    return any(t in _DOMAIN_VOCABULARY for t in tokens)


def _product_label_for_source(source: str) -> str | None:
    if not source:
        return None
    s = source.lower()
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


def has_reference_markers(query: str) -> bool:
    n_words = len(query.split())
    for i, p in enumerate(_REFERENCE_PATTERNS):
        if i in _SHORT_ONLY_PATTERNS and n_words > _SHORT_QUERY_MAX_WORDS:
            continue
        if p.search(query):
            return True
    return False


def is_followup_turn(raw_query: str, history: list, resolved_query: str) -> bool:
    if not history:
        return False
    return has_reference_markers(raw_query) or resolved_query != raw_query


def classify_by_similarity(
    query_vec,
    category_vectors: dict,
    min_confidence: float = 0.30,
    default: str = "accurate",
) -> str:
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