from rank_bm25 import BM25Okapi

def build_bm25(chunks):
    corpus = [c["text"].lower().split() for c in chunks]
    return BM25Okapi(corpus)

def bm25_search(query: str, chunks: list[dict], top_k: int = 10) -> list[dict]:
    if not chunks:
        return []
    bm25 = build_bm25(chunks)
    tokenized_query = query.lower().split()
    scores = bm25.get_scores(tokenized_query)
    ranked = sorted(zip(scores, chunks), key=lambda x: x[0], reverse=True)
    return [chunk for _, chunk in ranked[:top_k]]