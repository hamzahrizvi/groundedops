import re
from collections import Counter

LIST_LINE_RE = re.compile(r"^\s*(?:[-*•☐]|\d+[.)])\s+")


def normalize_line(line: str) -> str:
    line = re.sub(r"([a-z])([A-Z])", r"\1 \2", line)
    line = re.sub(r"^\d+[\.\)]\s*", "", line)
    line = re.sub(r"[^\w\s]", "", line)
    return line.strip().lower()


def starts_with_verb(line: str) -> bool:
    line = line.strip().lower()
    if not line:
        return False

    first_word = line.split()[0]
    common_verbs = {
        "check", "verify", "ensure", "confirm", "connect",
        "install", "open", "close", "restart", "power",
        "set", "enter", "select", "press", "run",
        "make", "use", "add", "remove", "test", "enable",
    }

    if first_word in common_verbs:
        return True

    if len(line.split()) <= 6 and not line.endswith("."):
        return True

    return False


def is_bad_line(line: str) -> bool:
    l = line.lower()

    if any(k in l for k in [
        "introduction", "overview", "table of contents",
        "download manual", "for advance checklist please refer",
    ]):
        return True

    if re.match(r"^\d+[a-z]?\.\s*[A-Z]", line):
        return True

    if len(line.split()) <= 3:
        return True

    return False


def is_meaningful_line(line: str) -> bool:
    l = line.lower()
    return any(k in l for k in [
        "check", "verify", "ensure", "confirm",
        "test", "connected", "configured",
        "working", "trigger", "available",
        "power", "network", "device", "hub",
        "tablet", "alert", "relay", "log",
        "mycheckr", "myconnect",
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

    score = 0.0
    joined = " ".join(normalize_line(l) for l in lines)

    for tok in tokens:
        if tok in joined:
            score += 1.0

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
        line = re.sub(r"([a-z])([A-Z])", r"\1 \2", line)
        line = re.sub(r"^\s*(?:[-*•☐]|\d+[.)])\s*", "", line).strip()
        if not line:
            continue
        if is_bad_line(line):
            continue
        cleaned.append(line)

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


def extract_structured_block(chunks: list[dict], query: str | None = None) -> str | None:
    best_block: list[str] | None = None
    best_score = 0.0
    total_chunks = max(len(chunks), 1)

    for idx, chunk in enumerate(chunks):
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

        avg_len = sum(lengths) / len(lengths)
        length_consistency = sum(
            1 for l in lengths if abs(l - avg_len) < avg_len * 0.5
        )

        starts = [l.split()[0] for l in normalized if l]
        common = Counter(starts).most_common(1)
        start_score = common[0][1] if common else 0

        independent = sum(1 for l in normalized if len(l.split()) > 4)
        verb_starts = sum(1 for l in filtered_lines if starts_with_verb(l))
        list_lines = sum(1 for l in filtered_lines if LIST_LINE_RE.match(l))

        similarity_score = 0.0
        for i in range(len(normalized) - 1):
            words_i = normalized[i].split()[:3]
            words_j = normalized[i + 1].split()[:3]
            if words_i == words_j:
                similarity_score += 1.5
            if simple_similarity(normalized[i], normalized[i + 1]) > 0.5:
                similarity_score += 1.0

        query_score = _query_overlap_score(query, filtered_lines)
        position_weight = idx / total_chunks

        score = (
            length_consistency * 1.5
            + start_score * 2.0
            + independent * 1.0
            + verb_starts * 3.0
            + list_lines * 2.5
            + similarity_score * 1.5
            + query_score * 2.0
            + position_weight * 1.0
        )

        if verb_starts < 2 and list_lines < 2:
            continue

        if score > best_score:
            best_score = score
            best_block = filtered_lines

    if best_block and best_score > 12:
        meaningful = [l for l in best_block if is_meaningful_line(l) or LIST_LINE_RE.match(l)]
        return _format_as_list(meaningful)

    return None