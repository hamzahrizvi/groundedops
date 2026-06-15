from embeddings import embed_query
from bm25 import bm25_search
from db import get_collection

def retrieve_from_db(query: str, top_k: int = 10) -> list[dict]:
    collection = get_collection()
    if collection.count() == 0:
        return []

    # Dense retrieval
    q_vec = embed_query(query)
    res = collection.query(
        query_embeddings=[q_vec.tolist()],
        n_results=top_k,
        include=["documents", "metadatas"]
    )

    dense_chunks = []
    for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
        dense_chunks.append({
            "text": doc,
            "source": meta.get("source", "unknown"),
        })

    # BM25 on the same dense results (cheap hybrid)
    bm25_chunks = bm25_search(query, dense_chunks, top_k=top_k)

    # Merge: union (preserve order from BM25, then add missing dense)
    seen = set()
    merged = []
    for c in bm25_chunks + dense_chunks:
        key = c["text"]
        if key not in seen:
            seen.add(key)
            merged.append(c)

    return merged[:top_k]