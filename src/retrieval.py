def search(query, chunks, k=10):
    scored = []

    q_words = query.lower().split()

    for c in chunks:
        text = c["text"].lower()

        score = sum(word in text for word in q_words)

        if score > 0:
            scored.append((score, c))

    scored.sort(reverse=True, key=lambda x: x[0])

    return [c for _, c in scored[:k]]