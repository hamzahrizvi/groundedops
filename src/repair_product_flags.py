"""One-off repair: delete prod_* flags that a chunk's product tags no longer name.

Retagging used to pop the old flags from the metadata dict before
col.update(), and Chroma keeps a key that is merely absent, so every flag
from a previous assignment stayed in the index (PENDING.md 8.2). The dense
retrieval arm matches on those flags. Measured 2026-09-29: 440 chunks across
four documents, all prod_biometrics_general.

Stop the backend first, and take a copy of chroma_db/. Metadata only: no
re-embed, nothing else about the chunk changes.

Usage (from src/):
    python repair_product_flags.py            # dry run: counts only
    python repair_product_flags.py --apply
"""
import argparse
import collections
import sys

import db

BATCH = 500


def plan(ids: list[str], metas: list[dict]) -> list[tuple[str, dict, list[str]]]:
    """(id, update, stale keys) for every chunk carrying a stale flag. The
    update names only the stale keys, set to None: Chroma merges, so
    everything else on the chunk is left exactly as it is."""
    out = []
    for cid, meta in zip(ids, metas):
        stale = db.stale_product_flags(meta or {})
        if stale:
            out.append((cid, {k: None for k in stale}, stale))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="write the repair (default is a dry run)")
    args = ap.parse_args(argv)

    col = db.get_collection()
    got = col.get(include=["metadatas"])
    ids, metas = got.get("ids") or [], got.get("metadatas") or []
    todo = plan(ids, metas)

    by_doc = collections.Counter()
    src_of = dict(zip(ids, metas))
    for cid, _, stale in todo:
        for k in stale:
            by_doc[((src_of.get(cid) or {}).get("source"), k)] += 1
    print(f"{len(ids)} chunks, {len(todo)} with a stale flag, "
          f"{sum(by_doc.values())} flag(s) to delete")
    for (source, key), n in by_doc.most_common():
        print(f"  {n:5d}  {key}  {source}")

    if not args.apply or not todo:
        if todo:
            print("dry run -- nothing written; pass --apply to repair")
        return 0

    for i in range(0, len(todo), BATCH):
        part = todo[i:i + BATCH]
        col.update(ids=[t[0] for t in part], metadatas=[t[1] for t in part])
    db.invalidate_retrieval_cache()

    after = col.get(ids=ids, include=["metadatas"])
    left = plan(after.get("ids") or [], after.get("metadatas") or [])
    print(f"repaired; {len(left)} chunk(s) still carry a stale flag")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
