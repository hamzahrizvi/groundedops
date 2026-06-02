import re
from collections import Counter
import spacy

nlp = spacy.load("en_core_web_sm")


def normalize_line(line):
    line = re.sub(r"^\d+\.\s*", "", line)  # remove numbered prefix
    line = re.sub(r"[^\w\s]", "", line)   # remove punctuation
    return line.strip().lower()


def starts_with_verb(line):
    doc = nlp(line)
    return len(doc) > 0 and doc[0].pos_ == "VERB"

def simple_similarity(a, b):
    a_words = set(a.split())
    b_words = set(b.split())
    if not a_words or not b_words:
        return 0
    return len(a_words & b_words) / len(a_words | b_words)


def extract_structured_block(chunks):
    best_block = None
    best_score = 0

    for idx, c in enumerate(chunks):
        raw_lines = [l.strip() for l in c["text"].split("\n") if l.strip()]

        if len(raw_lines) < 3:
            continue

        normalized = [normalize_line(l) for l in raw_lines if l]

        # check: length consistency
        lengths = [len(l.split()) for l in normalized if l]
        if not lengths:
            continue

        avg_len = sum(lengths) / len(lengths)

        length_consistency = sum(
            1 for l in lengths if abs(l - avg_len) < avg_len * 0.5
        )

        #check: structure repeat
        starts = [l.split()[0] for l in normalized if l]
        common_start_count = Counter(starts).most_common(1)
        start_score = common_start_count[0][1] if common_start_count else 0

        # check: sentence independence
        independent = sum(
            1 for l in normalized if len(l.split()) > 4
        )

        # check: verb
        verb_starts = sum(
            1 for l in raw_lines if starts_with_verb(l)
        )

        # check: line similar
        similarity_score = 0

        for i in range(len(normalized) - 1):
            structural = normalized[i][:10] == normalized[i + 1][:10]
            semantic = semantic_similarity(normalized[i], normalized[i + 1]) > 0.5

            if structural:
                similarity_score += 1.5  # stronger signal

            if semantic:
                similarity_score += 1
       
       
        #check: position bias
        position_weight = idx / max(len(chunks), 1)
        
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

   
    if best_block and best_score > 8:
        return "\n".join(best_block[:10])

    return None