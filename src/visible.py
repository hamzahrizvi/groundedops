"""May a visitor be given this document? (D-20261106-07)

The rule main._drop_hidden applies to answer passages: no internal or
excluded document (retrieval_db.is_excluded), and none filed only under
obsolete products whose answers are blocked (catalog.blocked_products). A
document shared with a current product stays. Every helper or route that
hands out document text or pictures asks here. main._drop_hidden asks here too (D-20261106-15).
"""
import logging

logger = logging.getLogger(__name__)


_last_blocked: set = set()


def blocked() -> set:
    """catalog.blocked_products; on an error, the last set it gave, never
    "nothing" (D-20261107-08)."""
    global _last_blocked
    try:
        import catalog
        _last_blocked = set(catalog.blocked_products())
    except Exception as exc:
        logger.warning("obsolete products unreadable, keeping the last blocked "
                       "set %s: %s", sorted(_last_blocked), exc)
    return set(_last_blocked)


def _key(name: str) -> str:
    # The rule is docstore.name_key, shared with retrieval_db._name_key.
    import docstore
    return docstore.name_key(str(name or ""))


def products_of(source: str) -> list:
    """The product keys the index files a source under; [] if not indexed."""
    import docindex
    k = _key(source)
    for row in docindex.source_index():
        if _key(row.get("source")) == k:
            return list(row.get("products") or [])
    return []


def may_show(source, products=None, blocked_now=None) -> bool:
    """True when a visitor may be given this document. `products` is a
    list or a comma string ("a,b"); None looks it up in the index (only
    when some product is blocked). Pass `blocked_now` when checking many."""
    import retrieval_db
    if retrieval_db.is_excluded(source):
        return False
    b = blocked() if blocked_now is None else blocked_now
    if not b:
        return True
    if products is None:
        products = products_of(source)
    if isinstance(products, str):
        products = [k.strip() for k in products.split(",") if k.strip()]
    return not (products and all(k in b for k in products))
