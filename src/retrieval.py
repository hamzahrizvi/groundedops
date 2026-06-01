def search(query, documents, k=3):
    scored = []

    for doc in documents:
        score = sum(
            1 for word in query.lower().split()
            if word in doc["text"].lower()
        )
        scored.append((score, doc["text"]))

    scored.sort(reverse=True)
    return [text for _, text in scored[:k]]