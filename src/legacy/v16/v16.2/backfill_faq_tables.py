"""Add every table and checklist in the corpus to the FAQ store, verbatim.

    python backfill_faq_tables.py --dry-run     # what it would add
    python backfill_faq_tables.py               # add them
    python backfill_faq_tables.py --only "NV9*"

WHY: a table served from the FAQ store is exact, instant and costs no
tokens -- no model reads it, so it cannot be paraphrased wrongly, refused
by the grounding gate, or hallucinated. The answer IS the document's own
table.

Non-destructive. faq_store.merge_questions only adds questions that are not
already present in the same product scope and never edits an existing entry,
so this is safe to re-run after adding documents.

Filing matters: entries inherit the document's catalogue category/product, so
an UNFILED document produces FAQ entries no product-scoped chat will offer.
Those are reported at the end rather than silently added.
"""

import fnmatch
import os
import sys

import docstore
import faq_store
import structures
# reindex.from_store() resolves each document's catalogue filing, which
# docstore.inventory() alone does not -- it returns category/product as None
# for everything. Reusing it keeps this script and a rebuild agreeing about
# which product a document belongs to.
from reindex import from_store


def main() -> int:
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    only = None
    if "--only" in argv:
        i = argv.index("--only")
        if i + 1 >= len(argv):
            print("--only needs a pattern, e.g. --only 'NV9*'")
            return 2
        only = argv[i + 1]

    inventory = from_store()
    if only:
        inventory = [e for e in inventory
                     if fnmatch.fnmatch(e["source"], only)]
    if not inventory:
        print("No documents matched.")
        return 1

    print(f"document store: {docstore.store_dir()}")
    print(f"{'(dry run) ' if dry else ''}scanning {len(inventory)} document(s)\n")

    total = added = 0
    unfiled = []
    for e in inventory:
        src, path = e["source"], e["path"]
        if not os.path.exists(path):
            print(f"  {src[:52]:54} MISSING FILE")
            continue
        pairs = structures.faq_pairs_for_document(path, src)
        total += len(pairs)
        n_check = sum(1 for p in pairs if p.get("verbatim") == "checklist")
        filed = bool(e.get("product") or e.get("category"))
        if not filed and pairs:
            unfiled.append(src)

        if dry:
            print(f"  {src[:52]:54} {len(pairs):3} pairs "
                  f"({n_check} checklist){'' if filed else '   UNFILED'}")
            for p in pairs[:3]:
                print(f"        {p['question'][:88]}")
            continue

        res = faq_store.merge_questions(
            src, e.get("product") or e.get("category") or "",
            [{"question": p["question"], "answer": p["answer"]} for p in pairs],
            category=e.get("category") or "")
        n_new = res.get("added", res.get("new", len(pairs)))
        added += n_new if isinstance(n_new, int) else 0
        print(f"  {src[:52]:54} {len(pairs):3} found, {n_new} added"
              f"{'' if filed else '   UNFILED'}")

    print(f"\n{total} table/checklist pair(s) found"
          + ("" if dry else f", {added} added to the FAQ store"))

    if unfiled:
        print(f"\nNOTE: {len(unfiled)} document(s) have no catalogue product, so "
              f"their entries will never be offered in a product-scoped chat:")
        for s in unfiled:
            print(f"  - {s}")
        print("File them in the console (Documents -> Move to...) and re-run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
