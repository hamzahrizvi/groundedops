
def search(query, chunks, k=5):
    scored = []

    for c in chunks:
        text = c["text"]
        score = sum(
            word in text.lower()
            for word in query.lower().split()
        )

        if score > 0:
            scored.append((score, text))  # only store text

    scored.sort(key=lambda x: x[0], reverse=True)

    return [text for _, text in scored[:k]]