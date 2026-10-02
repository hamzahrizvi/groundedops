"""The source inventory: what is ingested, filed where, in how many chunks.

/catalog, /widget/catalog, /admin/sources, /stats and the "give me the
manual" path each used to fetch EVERY chunk's metadata from Chroma on every
call -- 45ms on a 700-chunk corpus, for a list of a dozen sources. One walk
now feeds all of them.

The cache key is the collection's identity and size, so an ingest, delete
or reset made by any process is seen on the next call. The in-process
writers whose edits leave the count unchanged (retag, reassign) drop it
through db.invalidate_retrieval_cache, and the short TTL covers the one
case left: a metadata edit made by another process.

db.py is imported inside the functions, not at the top: db imports
product_keys from here, and the test harness swaps db for a stub.
"""

import os
import threading
import time

_TTL = float(os.getenv("SOURCE_INDEX_TTL_SECS", "30"))
_cache: dict = {"key": None, "at": 0.0, "rows": None}
_lock = threading.Lock()


def product_keys(meta: dict) -> list[str]:
    """The product tags on a chunk.

    Reads BOTH "products" and "product": ingest.py writes the plural and
    older paths wrote the singular, so anything that only checks one key
    silently misses half the corpus. That mismatch has bitten this project
    before (see PROJECT_MAP's note on the product-metadata key).
    """
    raw = meta.get("products") or meta.get("product") or ""
    return [k.strip() for k in str(raw).split(",") if k.strip()]


def invalidate() -> None:
    with _lock:
        _cache["rows"] = None


def source_index() -> list[dict]:
    """One row per ingested source, in first-seen order:
    source, category, product (the raw singular tag), products (parsed),
    chunks, version, and meta -- the first chunk's metadata, where the scope
    flags live. The rows are shared: read them, never modify them."""
    from db import get_collection
    col = get_collection()
    key = (getattr(col, "id", None), col.count())
    now = time.monotonic()
    with _lock:
        if (_cache["rows"] is not None and _cache["key"] == key
                and now - _cache["at"] < _TTL):
            return _cache["rows"]
    got = col.get(include=["metadatas"])
    rows: dict[str, dict] = {}
    for m in got.get("metadatas") or []:
        src = m.get("source")
        if not src:
            continue
        row = rows.get(src)
        if row is None:
            row = rows[src] = {
                "source": src,
                "category": m.get("category", ""),
                "product": m.get("product", ""),
                "products": product_keys(m),
                "chunks": 0,
                "version": m.get("document_version") or m.get("content_sha256"),
                "meta": m,
            }
        row["chunks"] += 1
    out = list(rows.values())
    with _lock:
        _cache.update(key=key, at=now, rows=out)
    return out


def doc_counts() -> tuple[dict[str, int], dict[str, int]]:
    """Distinct ingested sources per product key and per category key. A
    document tagged to several products counts under EACH of them --
    counting the comma-joined value as one key filed a shared manual under
    a product nobody has and showed 0 against the products that carry it."""
    by_product: dict[str, set] = {}
    by_category: dict[str, set] = {}
    for row in source_index():
        for key in row["products"]:
            by_product.setdefault(key, set()).add(row["source"])
        ckey = (row["category"] or "").strip()
        if ckey:
            by_category.setdefault(ckey, set()).add(row["source"])
    return ({k: len(v) for k, v in by_product.items()},
            {k: len(v) for k, v in by_category.items()})


def source_text(source: str, max_chunks: int, cap: int) -> tuple[str, int]:
    """Real chunk text for one source -- breadcrumb prefixes stripped and
    synthetic doc2query chunks skipped, so a draft is written from what the
    document says rather than from questions already generated about it.
    Returns (text, number of real chunks the source has)."""
    from db import get_collection
    got = get_collection().get(where={"source": source},
                               include=["documents", "metadatas"])
    texts = []
    for d, m in zip(got.get("documents") or [], got.get("metadatas") or []):
        if m.get("kind") == "query":
            continue
        if d.startswith("["):
            nl = d.find("\n")
            if nl != -1:
                d = d[nl + 1:]
        texts.append(d)
    return "\n\n".join(texts[:max_chunks])[:cap], len(texts)
