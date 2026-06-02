#
def search(query, chunks, k=5):
    scored = []

    for c in chunks:
        score = sum (
            1 for word in query.lower().split()
            if word in c ["text"].lower()
        )
        if score > 0:
            scored.append((score, c))

    scored.sort(reverse=True)
    return [c["text"] for _, c in scored[:k]]