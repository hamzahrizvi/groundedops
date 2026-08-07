"""GroundedOps ingest + retrieval diagnostic (v10.9).

Run INSIDE the backend container to check the full pipeline against your
actual ChromaDB — no eval corpus needed:

    docker compose exec backend python diagnose.py
    docker compose exec backend python diagnose.py "what network does MyCheckr support"

Checks, in order:
  1. Collection exists and how many chunks / query-entries it holds.
  2. Which category/product tags are present (proves tagging worked).
  3. FAQ store contents (proves doc2query ran).
  4. A live retrieval for a query, showing scores + which sources came
     back (proves retrieval + scoping are functional).
"""
import sys, os, json

def main():
    from db import get_collection
    col = get_collection()

    print("=" * 60)
    print("1. COLLECTION")
    got = col.get(include=["metadatas"])
    metas = got.get("metadatas", [])
    ids = got.get("ids", [])
    print(f"   total entries: {len(ids)}")
    kinds = {}
    for m in metas:
        kinds[m.get("kind", "?")] = kinds.get(m.get("kind", "?"), 0) + 1
    print(f"   by kind: {kinds}")
    if not ids:
        print("   ⚠ EMPTY collection — nothing ingested. Upload a doc first.")
        return

    print("=" * 60)
    print("2. TAGS (category / product per source)")
    by_src = {}
    for m in metas:
        s = m.get("source", "?")
        if s not in by_src:
            by_src[s] = {"category": m.get("category", ""), "product": m.get("product", ""),
                         "products": m.get("products", ""), "chunks": 0}
        by_src[s]["chunks"] += 1
    for s, info in by_src.items():
        tag = f"{info['category'] or '—'} / {info['product'] or info['products'] or '—'}"
        flag = "" if (info["product"] or info["products"]) else "  ⚠ UNTAGGED"
        print(f"   {s[:45]:45}  [{tag}]  {info['chunks']} chunks{flag}")

    print("=" * 60)
    print("3. FAQ STORE (doc2query output)")
    try:
        import faq_store
        faqs = faq_store._load()
        print(f"   FAQ entries: {len(faqs)}")
        if faqs:
            for f in faqs[:5]:
                print(f"     [{f.get('products','?')}] {f['question'][:60]}")
        else:
            print("   ⚠ EMPTY — doc2query generated nothing. Check backend logs")
            print("     during ingest for 'doc2query provider = ...' and any")
            print("     '0 questions generated' warning.")
    except Exception as e:
        print(f"   faq_store error: {e}")

    print("=" * 60)
    print("4. LIVE RETRIEVAL")
    from retrieval_db import retrieve_from_db
    q = sys.argv[1] if len(sys.argv) > 1 else "what is this device"
    print(f"   query: {q!r}")
    for scope_label, scope in [("UNSCOPED", None)]:
        res = retrieve_from_db(q, top_k=5, scope=scope)
        print(f"   [{scope_label}] {len(res)} results")
        for r in res[:5]:
            src = r.get("source", "?")
            sc = r.get("score", r.get("rerank_score", "?"))
            print(f"       {sc}  {src[:50]}")
    print("=" * 60)
    print("Done. If (1) has chunks, (2) shows tags, (3) has FAQ entries,")
    print("and (4) returns results with sane scores, the pipeline is")
    print("functional. Empty (3) = doc2query issue; empty (4) with full")
    print("(1) = retrieval/scoring issue.")

if __name__ == "__main__":
    main()
