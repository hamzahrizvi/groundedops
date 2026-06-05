"""
Structured block extractor.

Detects checklist/step-list content in retrieved chunks by scoring
for verb-leading lines, consistent length, and positional density.

BUG FIXED: is_meaningful_line() was defined at top level but the final
`if best_block` return block was indented inside it, making it dead code.
extract_structured_block() was silently returning None every time.
"""

import re
from collections import Counter


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def normalize_line(line: str) -> str:
    line = re.sub(r"^\d+[\.\)]\s*", "", line)
    line = re.sub(r"[^\w\s]", "", line)
    return line.strip().lower()


def starts_with_verb(line: str) -> bool:
    line = line.strip().lower()
    if not line:
        return False

    words = line.split()
    first_word = words[0]

    common_verbs = {
        "check", "verify", "ensure", "confirm", "connect",
        "install", "open", "close", "restart", "power",
        "set", "enter", "select", "press", "run",
        "make", "use", "add", "remove", "test", "enable",
    }

    if first_word in common_verbs:
        return True

    if len(words) <= 6 and not line.endswith("."):
        return True

    return False


def is_bad_line(line: str) -> bool:
    l = line.lower()

    if any(k in l for k in [
        "introduction", "overview", "table of contents", "download manual",
    ]):
        return True

    if re.match(r"^\d+[a-z]?\.\s*[A-Z]", line):
        return True

    if len(line.split()) <= 3:
        return True

    return False


def is_meaningful_line(line: str) -> bool:
    """Return True if the line contains a domain-relevant action or status term."""
    l = line.lower()
    return any(k in l for k in [
        "check", "verify", "ensure", "confirm",
        "test", "connected", "configured",
        "working", "trigger", "available",
        "power", "network", "device",
    ])


def simple_similarity(a: str, b: str) -> float:
    a_words = set(a.split())
    b_words = set(b.split())
    if not a_words or not b_words:
        return 0.0
    return len(a_words & b_words) / len(a_words | b_words)


# ─────────────────────────────────────────────
# Main extractor
# ─────────────────────────────────────────────

def extract_structured_block(chunks: list[dict]) -> str | None:
    best_block: list[str] | None = None
    best_score: float = 0.0

    total_chunks = max(len(chunks), 1)

    for idx, c in enumerate(chunks):
        raw_lines = [l.strip() for l in c["text"].split("\n") if l.strip()]

        if len(raw_lines) < 4:
            continue

        filtered_lines = [l for l in raw_lines if not is_bad_line(l)]

        if len(filtered_lines) < 3:
            continue

        normalized = [normalize_line(l) for l in filtered_lines]
        lengths    = [len(l.split()) for l in normalized if l]

        if not lengths:
            continue

        avg_len            = sum(lengths) / len(lengths)
        length_consistency = sum(1 for l in lengths if abs(l - avg_len) < avg_len * 0.5)

        starts     = [l.split()[0] for l in normalized if l]
        common     = Counter(starts).most_common(1)
        start_score = common[0][1] if common else 0

        independent  = sum(1 for l in normalized if len(l.split()) > 4)
        verb_starts  = sum(1 for l in filtered_lines if starts_with_verb(l))

        similarity_score = 0.0
        for i in range(len(normalized) - 1):
            words_i = normalized[i].split()[:3]
            words_j = normalized[i + 1].split()[:3]
            if words_i == words_j:
                similarity_score += 1.5
            if simple_similarity(normalized[i], normalized[i + 1]) > 0.5:
                similarity_score += 1.0

        position_weight = idx / total_chunks

        score = (
            length_consistency * 1.5
            + start_score      * 2.0
            + independent      * 1.0
            + verb_starts      * 3.0
            + similarity_score * 1.5
            + position_weight  * 1.5
        )

        if verb_starts < 2:
            continue

        if score > best_score:
            best_score = score
            best_block = filtered_lines

    # ← Fixed: was unreachable in original (buried inside is_meaningful_line)
    if best_block and best_score > 12:
        meaningful = [l for l in best_block if is_meaningful_line(l)]
        if len(meaningful) < 3:
            return None
        return "\n".join(meaningful[:10])

    return None