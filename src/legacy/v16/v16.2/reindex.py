#!/usr/bin/env python3
"""reindex.py — rebuild the vector index from the retained originals.

WHY THIS EXISTS

Chunk geometry, the parser and the embedder only affect documents ingested
AFTERWARDS. Before this script the only way to apply a change was to delete a
document in the console and upload it again by hand — which meant nobody did
it, so the index held whatever settings happened to be current when each file
was first uploaded. That makes any tuning question ("did 1200-char chunks
help?") unanswerable, because the index is a mix.

It also makes the index safe to throw away, which is the point: the documents
are the durable asset, the index is derived. If this script cannot rebuild it,
the store is incomplete and that is a bug worth knowing about BEFORE you need
it.

SAFETY

  * Refuses to run if any indexed source has no retained original, unless
    --allow-loss is given. Wiping first and discovering the gap afterwards is
    unrecoverable.
  * --dry-run reports exactly what would happen and touches nothing.
  * Rebuilds into the live collection, so run it with the backend stopped.

USAGE

  python reindex.py --dry-run           # what would be rebuilt, and from where
  python reindex.py --check             # is every indexed source recoverable?
  python reindex.py                     # rebuild everything
  python reindex.py --only "NV9*"       # rebuild matching sources only
  CHUNK_SIZE=1600 python reindex.py     # rebuild at different geometry
  python reindex.py --from-store        # rebuild from the document store,
                                        # ignoring the current index entirely
                                        # (use when the index is damaged/empty)
  python reindex.py --migrate           # copy documents out of the legacy path
"""
import fnmatch
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logging.getLogger("chromadb").setLevel(logging.WARNING)
logger = logging.getLogger("reindex")

import docstore                                              # noqa: E402


def indexed_sources() -> dict[str, dict]:
    """source -> {chunks, category, product} straight from the collection."""
    from db import get_collection
    col = get_collection()
    got = col.get(include=["metadatas"])
    out: dict[str, dict] = {}
    for meta in (got.get("metadatas") or []):
        if not meta:
            continue
        src = meta.get("source")
        if not src:
            continue
        row = out.setdefault(src, {"chunks": 0, "category": "", "product": ""})
        row["chunks"] += 1
        row["category"] = row["category"] or (meta.get("category") or "")
        row["product"] = row["product"] or (meta.get("product") or "")
    return out


def catalog_assignment() -> dict[str, tuple[str, str]]:
    """source -> (category_key, product_key) from catalog_config.json.

    The catalogue is the durable record of which product a document belongs
    to. Reading the assignment from the INDEX means a damaged index takes the
    filing with it -- which is exactly the situation this mode exists for.
    """
    out: dict[str, tuple[str, str]] = {}
    try:
        import catalog
        cfg = catalog.load_catalog() if hasattr(catalog, "load_catalog") else None
    except Exception:
        cfg = None
    if not cfg:
        import json
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "catalog_config.json"), encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception as exc:
            logger.warning(f"Could not read the catalogue ({exc}); documents "
                           f"will be rebuilt unfiled")
            return out

    for cat in (cfg.get("categories") or []):
        for prod in (cat.get("products") or []):
            for src in (prod.get("sources") or []):
                out[os.path.basename(src)] = (cat.get("key") or "",
                                              prod.get("key") or "")
    return out


def from_store() -> list[dict]:
    """Everything in the document store, filed per the catalogue.

    Independent of the current index, so it works when the index is empty,
    damaged, or being deliberately rebuilt from scratch.
    """
    assign = catalog_assignment()
    out = []
    for item in docstore.inventory():
        cat, prod = assign.get(item["source"], ("", ""))
        out.append({"source": item["source"], "path": item["path"],
                    "chunks": (item.get("manifest") or {}).get("chunks", 0),
                    "category": cat, "product": prod,
                    "filed": bool(prod)})
    return out


def survey() -> tuple[list[dict], list[dict]]:
    """(recoverable, orphaned) for everything currently indexed."""
    recoverable, orphaned = [], []
    for src, row in sorted(indexed_sources().items()):
        path = docstore.find(src)
        entry = dict(row, source=src, path=path)
        (recoverable if path else orphaned).append(entry)
    return recoverable, orphaned


def report(recoverable, orphaned) -> None:
    store = docstore.store_dir()
    print(f"document store : {store}")
    legacy = [d for d in docstore.read_dirs()[1:]]
    if legacy:
        print(f"also reading    : {', '.join(legacy)}  (legacy, migrate with --migrate)")
    print()
    print(f"{'SOURCE':<48} {'CHUNKS':>7}  ORIGINAL")
    print("-" * 78)
    for e in recoverable:
        where = "legacy" if os.path.abspath(os.path.dirname(e["path"])) == \
            os.path.abspath(docstore.LEGACY_DIR) else "store"
        print(f"{e['source'][:48]:<48} {e['chunks']:>7}  {where}")
    for e in orphaned:
        print(f"{e['source'][:48]:<48} {e['chunks']:>7}  *** MISSING ***")
    print()

    stale = docstore.stale_documents()
    if stale:
        print("Ingest settings differing from current (or unrecorded):")
        for s in stale:
            if s["unknown"]:
                print(f"  {s['source']}: no manifest entry — geometry unknown")
            else:
                bits = ", ".join(f"{k}: {was} -> {now}"
                                 for k, (was, now) in s["differs"].items())
                print(f"  {s['source']}: {bits}")
        print()


def rebuild(targets: list[dict], dry_run: bool) -> int:
    from db import reset_collection
    from ingest import ingest_file

    if dry_run:
        print(f"[dry run] would reset the collection and re-ingest "
              f"{len(targets)} document(s) at "
              f"{docstore.current_settings()}")
        for e in targets:
            print(f"  {e['source']}  <- {e['path']}")
        return 0

    settings = docstore.current_settings()
    print(f"Rebuilding {len(targets)} document(s) at {settings}")
    reset_collection()
    print("collection reset")

    total = 0
    for e in targets:
        with open(e["path"], "rb") as fh:
            content = fh.read()
        n = ingest_file(content, e["source"],
                        category_key=e.get("category") or None,
                        product_key=e.get("product") or None)
        total += n
        print(f"  {e['source']}: {n} chunks "
              f"(was {e['chunks']})")
    print(f"\ndone — {total} chunks across {len(targets)} document(s)")
    return total


def main() -> int:
    argv = sys.argv[1:]
    dry_run = "--dry-run" in argv
    check_only = "--check" in argv
    allow_loss = "--allow-loss" in argv
    do_migrate = "--migrate" in argv

    only = None
    if "--only" in argv:
        i = argv.index("--only")
        if i + 1 >= len(argv):
            print("--only needs a pattern, e.g. --only 'NV9*'")
            return 2
        only = argv[i + 1]

    if do_migrate:
        moved = docstore.migrate_legacy(dry_run=dry_run)
        if not moved:
            print("nothing to migrate")
        for src, dst in moved:
            print(f"{'[dry run] ' if dry_run else ''}copied {src} -> {dst}")
        if not dry_run:
            print(f"\n{len(moved)} file(s) copied. The legacy directory was NOT "
                  f"deleted — verify the copies, then remove it yourself.")
        return 0

    if "--from-store" in argv:
        targets = from_store()
        if not targets:
            print(f"No documents in {docstore.store_dir()} "
                  f"(or {docstore.LEGACY_DIR}) — nothing to rebuild from.")
            return 1
        print(f"document store : {docstore.store_dir()}")
        print(f"{'SOURCE':<48} {'FILED AS':<22} ORIGINAL")
        print("-" * 82)
        for e in targets:
            filed = f"{e['category']}/{e['product']}" if e["filed"] else "*** unfiled ***"
            print(f"{e['source'][:48]:<48} {filed:<22} {e['path']}")
        print()
        unfiled = [e for e in targets if not e["filed"]]
        if unfiled:
            print(f"NOTE: {len(unfiled)} document(s) have no catalogue entry and "
                  f"will be searchable but never offered for a product.")
            print()
        rebuild(targets, dry_run)
        return 0

    recoverable, orphaned = survey()
    if not recoverable and not orphaned:
        print("The index reports no documents.")
        print("If that is unexpected, the index may be damaged — rebuild "
              "straight from the document store instead:")
        print("    python reindex.py --from-store --dry-run")
        print("    python reindex.py --from-store")
        return 1

    report(recoverable, orphaned)

    if check_only:
        if orphaned:
            print(f"FAIL: {len(orphaned)} indexed source(s) have no retained "
                  f"original. A rebuild would lose them.")
            return 1
        print("OK: every indexed source can be rebuilt from the document store.")
        return 0

    targets = recoverable
    if only:
        targets = [e for e in targets if fnmatch.fnmatch(e["source"], only)]
        if not targets:
            print(f"No retained document matches {only!r}")
            return 1
        # A partial rebuild cannot reset the whole collection.
        print("Partial rebuild is not supported: resetting the collection would "
              "drop the documents you did not select. Re-upload that one "
              "document through the console instead, or run without --only.")
        return 2

    if orphaned and not allow_loss:
        print(f"REFUSING TO REBUILD: {len(orphaned)} indexed source(s) have no "
              f"retained original, and a rebuild resets the collection — their "
              f"content would be lost permanently.\n"
              f"Recover the file(s) into {docstore.store_dir()}, or pass "
              f"--allow-loss if you accept losing them.")
        return 1

    rebuild(targets, dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
