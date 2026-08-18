#!/usr/bin/env python3
"""Document scope diagnostic and repair (v12.0).

Run from the app folder (next to db.py):

    python diag_scope.py           # report only
    python diag_scope.py --fix     # normalise metadata keys

WHAT THIS IS FOR
Documents only appear in the widget's product picker if their chunks carry
scope metadata. Two independent things stop that happening:

 1. UPLOADED WITHOUT A SCOPE. If the category/product headers didn't reach
    the backend at upload time, chunks were written with empty product and
    category. The document is searchable but belongs to nothing, so no
    product picker will ever offer it.

 2. KEY MISMATCH. ingest.py writes the metadata key "products" (plural);
    the admin catalog endpoint and /admin/reassign_source use "product"
    (singular). A document written by one path is invisible to the other.
    --fix writes BOTH keys so every reader agrees, which is safe because
    they always held the same value when present.

The report lists every ingested source with the scope its chunks actually
carry - which is the ground truth, regardless of what the admin UI shows.
"""
import argparse
import collections
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true",
                    help="write both product/products keys so all readers agree")
    args = ap.parse_args()

    try:
        from db import get_collection
    except Exception as e:
        print(f"Could not import db.py - run this from the app folder. ({e})")
        sys.exit(1)

    col = get_collection()
    got = col.get(include=["metadatas"])
    ids = got.get("ids") or []
    metas = got.get("metadatas") or []
    if not metas:
        print("No chunks in the collection at all. Nothing has been ingested.")
        return

    per_source = collections.defaultdict(lambda: {
        "chunks": 0, "products": set(), "product": set(),
        "category": set(), "ids": [],
    })
    for cid, m in zip(ids, metas):
        src = m.get("source") or "(no source)"
        e = per_source[src]
        e["chunks"] += 1
        e["ids"].append(cid)
        for k in ("products", "product", "category"):
            v = (m.get(k) or "").strip()
            if v:
                e[k].add(v)

    print(f"\n{len(per_source)} ingested document(s), {len(metas)} chunks\n")
    print(f"{'document':<44}{'chunks':>7}  {'product':<18}{'category':<16}status")
    print("-" * 104)

    unassigned, mismatched = [], []
    for src, e in sorted(per_source.items()):
        prod = ",".join(sorted(e["products"] | e["product"])) or "-"
        cat = ",".join(sorted(e["category"])) or "-"
        if not (e["products"] or e["product"]):
            status = "UNASSIGNED - will not appear in the picker"
            unassigned.append(src)
        elif bool(e["products"]) != bool(e["product"]):
            which = "plural only" if e["products"] else "singular only"
            status = f"KEY MISMATCH ({which})"
            mismatched.append(src)
        else:
            status = "ok"
        print(f"{src[:43]:<44}{e['chunks']:>7}  {prod[:17]:<18}{cat[:15]:<16}{status}")

    print()
    if unassigned:
        print(f"{len(unassigned)} document(s) have NO product. These are searchable but")
        print("invisible to the widget. Fix in Admin > Documents > Reassign, or")
        print("re-upload with a category AND product selected:")
        for s in unassigned:
            print(f"   - {s}")
        print()
    if mismatched:
        print(f"{len(mismatched)} document(s) carry only one of the two metadata")
        print("keys, so some readers see them and others do not. Run --fix.")
        print()
    if not unassigned and not mismatched:
        print("All documents are assigned and consistent.")

    if args.fix and mismatched:
        print("Repairing metadata...")
        fixed = 0
        for src in mismatched:
            e = per_source[src]
            value = ",".join(sorted(e["products"] | e["product"]))
            cat = ",".join(sorted(e["category"]))
            # Chroma update() replaces the metadata dict for the given ids,
            # so re-send every field we want to keep, not just the changed
            # ones - a partial dict silently drops the rest.
            for cid in e["ids"]:
                m = dict(metas[ids.index(cid)])
                m["products"] = value
                m["product"] = value
                if cat:
                    m["category"] = cat
                col.update(ids=[cid], metadatas=[m])
                fixed += 1
        print(f"Updated {fixed} chunk(s) across {len(mismatched)} document(s).")
        print("Restart the backend so cached indexes rebuild.")
    elif args.fix:
        print("Nothing to repair.")


if __name__ == "__main__":
    main()
