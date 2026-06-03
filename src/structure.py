import re
from collections import Counter


def normalize_line(line):
    line = re.sub(r"^\d+\.\s*", "", line)  # remove numbered prefix
    line = re.sub(r"[^\w\s]", "", line)    # remove punctuation
    return line.strip().lower()


def starts_with_verb(line):
    line = line.strip().lower()

    if not line:
        return False

    words = line.split()
    first_word = words[0]

    common_verbs = {
        "check", "verify", "ensure", "confirm", "connect",
        "install", "open", "close", "restart", "power",
        "set", "enter", "select", "press", "run",
        "make", "use", "add", "remove"
    }

    if first_word in common_verbs:
        return True

    # fallback: short + instruction style
    if len(words) <= 6 and not line.endswith("."):
        return True

    return False


def simple_similarity(a, b):
    a_words = set(a.split())
    b_words = set(b.split())

    if not a_words or not b_words:
        return 0

    return len(a_words & b_words) / len(a_words | b_words)


def extract_structured_block(chunks):
    best_block = None
    best_score = 0

    total_chunks = max(len(chunks), 1)

    for idx, c in enumerate(chunks):
        raw_lines = [l.strip() for l in c["text"].split("\n") if l.strip()]

        if len(raw_lines) < 3:
            continue

        normalized = [normalize_line(l) for l in raw_lines]

        # --- 1. Length consistency ---
        lengths = [len(l.split()) for l in normalized if l]

        if not lengths:
            continue

        avg_len = sum(lengths) / len(lengths)

        length_consistency = sum(
            1 for l in lengths if abs(l - avg_len) < avg_len * 0.5
        )

        # --- 2. Repeated structure ---
        starts = [l.split()[0] for l in normalized if l]
        common = Counter(starts).most_common(1)
        start_score = common[0][1] if common else 0

        # --- 3. Sentence independence ---
        independent = sum(1 for l in normalized if len(l.split()) > 4)

        # --- 4. Verb detection ---
        verb_starts = sum(1 for l in raw_lines if starts_with_verb(l))

        # --- 5. Similarity (FIXED) ---
        similarity_score = 0

        for i in range(len(normalized) - 1):
            # structural similarity (word-level, not char-level)
            words_i = normalized[i].split()[:3]
            words_j = normalized[i + 1].split()[:3]

            structural = words_i == words_j

            semantic = simple_similarity(normalized[i], normalized[i + 1]) > 0.5

            if structural:
                similarity_score += 1.5

            if semantic:
                similarity_score += 1

        # --- 6. Position bias ---
        position_weight = idx / total_chunks

        # --- FINAL SCORE ---
        score = (
            length_consistency * 1.5 +
            start_score * 2 +
            independent * 1 +
            verb_starts * 2 +
            similarity_score * 1.5 +
            position_weight * 2
        )

        if score > best_score:
            best_score = score
            best_block = raw_lines

    # --- Threshold ---
    if best_block and best_score > 8:
        return "\n".join(best_block[:10])

    return None