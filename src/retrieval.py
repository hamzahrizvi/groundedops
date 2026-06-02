#
def search(query, documents, k=5):
    scored = []

    for doc in documents:
        text = doc["text"].lower()
        score = sum(word in text for word in query.lower().split())

        if score > 0:
            scored.append((score, doc["text"]))

    scored.sort(reverse=True)
    return [t for _, t in scored[:k]]