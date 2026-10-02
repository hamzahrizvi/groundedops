#!/usr/bin/env python3
"""Apply tables.py to an index that was built before it existed.

New uploads get carry_table_context + spell_out at ingest. This rewrites
the chunks already indexed so continuations carry their heading and column
names, and merged cells are filled -- the part that retrieval needs, since
a chunk's text is what BM25 and the embedder see. Only changed chunks are
rewritten and re-embedded; ids, pages and product tags are untouched.

    cd src
    ../.venv/Scripts/python.exe backfill_table_context.py --dry-run
    ../.venv/Scripts/python.exe backfill_table_context.py

STOP THE BACKEND FIRST and take a copy of the index (CHROMA_DIR, default
./chroma_db): a second process writing the same Chroma store is not safe.
Idempotent: a second run changes nothing.
"""
import argparse
import collections
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.WARNING)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    except Exception:
        pass
    import db
    import tables
    from retrieval_db import _order_key

    col = db.get_collection()
    got = col.get(include=["documents", "metadatas"])
    by_src = collections.defaultdict(list)
    for cid, doc, meta in zip(got["ids"], got["documents"], got["metadatas"]):
        by_src[meta.get("source") or ""].append((_order_key(cid), cid, doc, meta))

    changed = []   # (id, new_text, new_meta)
    for src, items in by_src.items():
        items.sort()
        texts = [d for _, _, d, _ in items]
        secs = [(m.get("section") or "") for *_, m in items]
        doc = os.path.splitext(src)[0]
        new_texts, new_secs = tables.carry_table_context(texts, secs, doc)
        for (_, cid, old, meta), t, sec in zip(items, new_texts, new_secs):
            t = tables.spell_out(t)
            if t != old or sec != (meta.get("section") or ""):
                m = dict(meta)
                m["section"] = sec
                changed.append((cid, t, m))

    print(f"{len(got['ids'])} chunks, {len(changed)} to rewrite")
    for cid, t, _ in changed[:5]:
        print(f"--- {cid}\n{t[:240]}")
    if a.dry_run or not changed:
        return 0

    from embeddings import embed_texts
    batch = 16
    for i in range(0, len(changed), batch):
        part = changed[i:i + batch]
        vecs = embed_texts([t for _, t, _ in part])
        col.update(ids=[c for c, _, _ in part],
                   documents=[t for _, t, _ in part],
                   metadatas=[m for _, _, m in part],
                   embeddings=[list(map(float, v)) for v in vecs])
        print(f"  rewrote {min(i + batch, len(changed))}/{len(changed)}")
    try:
        db.invalidate_retrieval_cache()
    except Exception:
        pass
    print("done - restart the backend so it rebuilds its BM25 index")
    return 0


if __name__ == "__main__":
    sys.exit(main())
