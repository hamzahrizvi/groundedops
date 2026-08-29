"""Category -> Product catalog with persistence (v10.3).

Two-level hierarchy:
  Category (Note Validators, Coin Hoppers, Biometrics)
    └─ Product (e.g. NV200, SMART Hopper, MyCheckr)
         └─ doc sources (filename substrings)

A chat scopes to a CATEGORY (searches every product's docs in it) or
narrows to one PRODUCT. Category-level docs (shared across the category,
e.g. a common protocol manual) live under a synthetic "_shared" product
so they're always in scope for the category.

Persisted to catalog_config.json so the admin panel's changes survive
restarts. Falls back to seed defaults on first run.

Supersedes products.py: sources_for() is kept as a thin shim so existing
callers (retrieval) keep working, now resolving product OR category keys.
"""
import json
import os
import threading
import logging

logger = logging.getLogger(__name__)

_PATH = os.getenv("CATALOG_CONFIG", "catalog_config.json")
_lock = threading.Lock()

# Seed catalog. Note Validators / Coin Hoppers / Biometrics as requested;
# the age-verification devices we already have docs for sit under
# Biometrics (MyCheckr is biometric age estimation). Add products/docs
# via the admin panel — this is only the starting point.
_SEED = {
    "categories": [
        {
            "key": "note_validators",
            "name": "Note Validators",
            "products": [
                {"key": "nv_shared", "name": "General", "sources": []},
            ],
        },
        {
            "key": "coin_hoppers",
            "name": "Coin Hoppers",
            "products": [
                {"key": "ch_shared", "name": "General", "sources": []},
            ],
        },
        {
            "key": "biometrics",
            "name": "Biometrics",
            "products": [
                {"key": "biometrics_general", "name": "General (shared docs)",
                 "sources": []},
                {"key": "mycheckr", "name": "MyCheckr",
                 "sources": ["MyCheckr_User_Manual"]},
                {"key": "mycheckr_mini", "name": "MyCheckr Mini",
                 "sources": ["MyCheckr_Mini"]},
                {"key": "bio_shared", "name": "Shared / MyConnect",
                 "sources": ["MyConnect_Environment", "ICU_Network_API",
                             "Certificate_and_USB"]},
            ],
        },
    ]
}


def _load() -> dict:
    if os.path.exists(_PATH):
        try:
            with open(_PATH) as f:
                data = json.load(f)
            if data.get("categories"):
                return data
        except Exception as e:
            logger.warning(f"catalog read failed, using seed: {e}")
    return json.loads(json.dumps(_SEED))  # deep copy


def _save(data: dict) -> None:
    with open(_PATH, "w") as f:
        json.dump(data, f, indent=2)


def catalog() -> dict:
    """Full tree for the picker UI: categories -> products (names/keys).

    `aliases` is projected too: it is part of what identifies a product, not
    decoration. main._products_named_in reads this view, so a short code the
    manuals use ("NV9S" for the NV9 Spectral) is invisible to question
    matching unless it survives the projection -- which is exactly how "how
    much does the NV9S weigh" ended up asking the visitor to choose between
    two products they had already been specific about.
    """
    data = _load()
    return {"categories": [
        {"key": c["key"], "name": c["name"],
         "products": [{"key": p["key"], "name": p["name"],
                       "aliases": p.get("aliases", [])}
                      for p in c.get("products", [])]}
        for c in data["categories"]
    ]}


def _find_category(data: dict, cat_key: str) -> dict | None:
    return next((c for c in data["categories"] if c["key"] == cat_key), None)


def sources_for(scope_key: str | None, category_key: str | None = None) -> list[str] | None:
    """Resolve a scope to source substrings.
    - scope_key is a CATEGORY key  -> union of ALL its products' sources
    - scope_key is a PRODUCT key   -> that product's sources (category_key
      optional, disambiguates if product keys ever repeat across cats)
    - None / "all"                 -> None (whole corpus)
    """
    if not scope_key or scope_key == "all":
        return None
    data = _load()

    # category match -> union of all product sources in it
    cat = _find_category(data, scope_key)
    if cat:
        srcs = []
        for p in cat.get("products", []):
            srcs.extend(p.get("sources", []))
        return srcs or None

    # product match (optionally within a given category)
    cats = [c for c in data["categories"]
            if not category_key or c["key"] == category_key]
    for c in cats:
        for p in c.get("products", []):
            if p["key"] == scope_key:
                return p.get("sources") or None
    logger.warning(f"Unknown scope '{scope_key}' — searching whole corpus")
    return None


def product_for_source(source: str) -> list[str]:
    """Which product keys a source belongs to (for ingest tagging)."""
    data = _load()
    keys = []
    for c in data["categories"]:
        for p in c.get("products", []):
            if any(s.lower() in source.lower() for s in p.get("sources", [])):
                keys.append(p["key"])
    return keys


# ── Admin mutations (guarded by the password gate in main.py) ──────────

def add_category(key: str, name: str) -> dict:
    with _lock:
        data = _load()
        if _find_category(data, key):
            raise ValueError(f"category '{key}' already exists")
        data["categories"].append({"key": key, "name": name,
            "products": [{"key": f"{key}_general", "name": "General (shared docs)", "sources": []}]})
        _save(data)
    return catalog()


def rename_category(key: str, name: str) -> dict:
    with _lock:
        data = _load()
        cat = _find_category(data, key)
        if not cat:
            raise ValueError(f"unknown category '{key}'")
        cat["name"] = name
        _save(data)
    return catalog()


def delete_category(key: str) -> dict:
    with _lock:
        data = _load()
        data["categories"] = [c for c in data["categories"] if c["key"] != key]
        _save(data)
    return catalog()


def add_product(category_key: str, key: str, name: str, sources: list[str] | None = None) -> dict:
    with _lock:
        data = _load()
        cat = _find_category(data, category_key)
        if not cat:
            raise ValueError(f"unknown category '{category_key}'")
        if any(p["key"] == key for p in cat["products"]):
            raise ValueError(f"product '{key}' already exists in {category_key}")
        cat["products"].append({"key": key, "name": name, "sources": sources or []})
        _save(data)
    return catalog()


def product_contents(product_key: str) -> dict:
    """What is filed under a product. Read this BEFORE deleting one, so the
    operator is told what they are about to affect rather than discovering
    it afterwards."""
    out = {"chunks": 0, "answers": 0, "questions": 0, "sources": []}
    try:
        import db
        out["chunks"] = db.count_by_product(product_key)
    except Exception as e:
        logger.warning(f"could not count chunks for {product_key!r}: {e}")
    try:
        import faq_store
        counts = faq_store.count_for_product(product_key)
        out["answers"] = counts["answers"]
        out["questions"] = counts["questions"]
    except Exception as e:
        logger.warning(f"could not count FAQ content for {product_key!r}: {e}")
    with _lock:
        data = _load()
        for cat in data.get("categories", []):
            for p in cat.get("products", []):
                if p.get("key") == product_key:
                    out["sources"] = list(p.get("sources") or [])
    return out


def delete_product(category_key: str, product_key: str,
                   reassign_to: str | None = None,
                   delete_content: bool = False) -> dict:
    """Remove a product, and deal with everything filed under it.

    Deleting only the catalogue row is what this used to do, and it is how
    `nv9st` and `coin_hoppers` came to exist: keys with real documents,
    answers and questions behind them and no product in front. The console
    builds every list from the catalogue, so that content became invisible
    without being gone -- the worst of both.

    So a caller must now say what happens to it:

        reassign_to="other_key"   move the content to another product
        delete_content=True       delete it along with the product

    Passing neither is refused rather than defaulted. Guessing wrong in
    either direction is bad -- silently deleting a customer's documents, or
    silently orphaning them again -- and the caller always knows which they
    meant.

    Content shared with another product is never deleted, only untagged
    from this one; see db.delete_by_product.
    """
    if reassign_to and delete_content:
        raise ValueError("choose one: reassign the content, or delete it")
    if not reassign_to and not delete_content:
        raise ValueError(
            "deleting a product must say what happens to its documents, "
            "answers and questions: pass reassign_to=<product key>, or "
            "delete_content=true")

    with _lock:
        data = _load()
        cat = _find_category(data, category_key)
        if not cat:
            raise ValueError(f"unknown category '{category_key}'")
        if not any(p.get("key") == product_key for p in cat.get("products", [])):
            raise ValueError(f"unknown product '{product_key}'")
        if reassign_to:
            known = {p.get("key")
                     for c in data.get("categories", [])
                     for p in c.get("products", [])}
            if reassign_to not in known:
                raise ValueError(f"cannot reassign to '{reassign_to}': "
                                 f"no such product")
            if reassign_to == product_key:
                raise ValueError("cannot reassign a product to itself")

    moved = {"chunks": 0, "answers": 0, "questions": 0}
    # Content first, catalogue last. If something below fails, the product
    # is still listed and the operator can retry -- the other order would
    # leave the catalogue row gone and the content orphaned, which is the
    # exact failure this function exists to prevent.
    import db
    import faq_store
    if reassign_to:
        moved["chunks"] = db.retag_product(product_key, reassign_to)
        r = faq_store.retag_product(product_key, reassign_to)
        moved["answers"], moved["questions"] = r["answers"], r["questions"]
    else:
        moved["chunks"] = db.delete_by_product(product_key)
        r = faq_store.delete_for_product(product_key)
        moved["answers"], moved["questions"] = r["answers"], r["questions"]

    with _lock:
        data = _load()
        cat = _find_category(data, category_key)
        if cat:
            # The doomed product's sources move with it, so the new product
            # lists the documents it has just inherited.
            if reassign_to:
                doomed = next((p for p in cat["products"]
                               if p["key"] == product_key), None)
                srcs = list(doomed.get("sources") or []) if doomed else []
                for c in data.get("categories", []):
                    for p in c.get("products", []):
                        if p.get("key") == reassign_to:
                            p.setdefault("sources", [])
                            for sname in srcs:
                                if sname not in p["sources"]:
                                    p["sources"].append(sname)
            cat["products"] = [p for p in cat["products"]
                               if p["key"] != product_key]
            _save(data)

    logger.info(f"deleted product {product_key!r} "
                f"({'reassigned to ' + reassign_to if reassign_to else 'content deleted'}): "
                f"{moved}")
    return {"catalog": catalog(), "affected": moved,
            "reassigned_to": reassign_to}


def attach_source(category_key: str, product_key: str, source: str) -> dict:
    """Tag an (already-ingested) source filename to a product."""
    with _lock:
        data = _load()
        cat = _find_category(data, category_key)
        if not cat:
            raise ValueError(f"unknown category '{category_key}'")
        prod = next((p for p in cat["products"] if p["key"] == product_key), None)
        if not prod:
            raise ValueError(f"unknown product '{product_key}'")
        if source not in prod["sources"]:
            prod["sources"].append(source)
        _save(data)
    return catalog()
