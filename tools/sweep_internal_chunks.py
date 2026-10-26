"""Take the chunks of documents already marked internal out of the index.

One-off, after D-20261024-06 is merged: from then on marking a document
removes its chunks and ingest never indexes a marked one, but documents
marked before that still have chunks (hidden at read time). Originals stay in
the document store; a marked document with NO retained original keeps its
chunks, since deleting them would lose it for good.

Since D-20261026-91 marking also marks every stored byte-identical copy, so
the dry run lists those too, and the duplicate groups the index answers from
one copy of.

Run with the backend STOPPED (as reindex.py): a second process writing Chroma
leaves the server's handle stale.

  GO_CODE  code folder (default <repo>/src), GO_DATA  working folder holding
  chroma_db and internal_sources.json (default <repo>/src); SOURCE_FILE_DIR
  as the server has it (default <code>/../documents).

  python tools/sweep_internal_chunks.py            # dry run: list what would go
  python tools/sweep_internal_chunks.py --apply    # take the chunks out
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = os.environ.get("GO_CODE") or os.path.join(ROOT, "src")
os.chdir(os.environ.get("GO_DATA") or os.path.join(ROOT, "src"))
sys.path.insert(0, CODE)

import docindex  # noqa: E402
import docstore  # noqa: E402
import retrieval_db as rdb  # noqa: E402


def report(marked) -> int:
    keys = {rdb._name_key(n) for n in marked}
    total = 0
    for row in docindex.source_index():
        if rdb._name_key(row["source"]) not in keys:
            continue
        total += row["chunks"]
        kept = "original kept" if docstore.find(row["source"]) else "NO ORIGINAL: left indexed"
        print(f"  {row['source']}: {row['chunks']} chunk(s), {kept}")
    print(f"{len(marked)} marked, {total} chunk(s) of them indexed")
    return total


def copies_report(marked) -> None:
    extra = [n for n in rdb.same_content(sorted(marked)) if not rdb.is_marked_internal(n)]
    for n in sorted(extra):
        print(f"  copy to mark: {n}")
    print(f"{len(extra)} unmarked copies of marked documents (--apply marks them)")
    groups: dict[str, list] = {}
    for dup, keep in rdb.copies_of().items():
        groups.setdefault(keep, []).append(dup)
    for keep in sorted(groups):
        print(f"  answers: {keep} ({len(groups[keep])} copies)")
    print(f"{len(groups)} duplicate groups, {sum(map(len, groups.values()))} copies left out of answers")


def main() -> int:
    marked = rdb.internal_sources()
    if isinstance(marked, rdb._Everything):
        print("internal_sources.json is unreadable; repair it first")
        return 2
    report(marked)
    copies_report(marked)
    if "--apply" not in sys.argv[1:]:
        print("dry run: add --apply to take them out")
        return 0
    rdb.set_internal(sorted(marked), True)
    docindex.invalidate()
    print("after:")
    report(marked)
    return 0


if __name__ == "__main__":
    sys.exit(main())
