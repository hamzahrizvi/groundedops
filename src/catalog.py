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

import jsonstore

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
    data = jsonstore.load(_PATH, None, label="catalogue")
    if not (isinstance(data, dict) and data.get("categories")):
        data = json.loads(json.dumps(_SEED))  # deep copy
    return _with_shared(data)


def _with_shared(data: dict) -> dict:
    """Every range has its "<range>_general" entry, the one retrieval treats
    as shared by all the range's products (retrieval_db._matches_scope).
    Coin Hoppers had none -- its seed entry was "ch_shared", which retrieval
    never read, and that was gone too -- so nothing could be filed as shared
    across the range. Added here, so it holds for a catalogue of any age; it
    is written out with the next save."""
    for c in data.get("categories", []):
        prods = c.setdefault("products", [])
        if not any(p.get("key") == f"{c['key']}_general" for p in prods):
            prods.insert(0, {"key": f"{c['key']}_general",
                             "name": "General (shared docs)", "sources": []})
    return data


def _save(data: dict) -> None:
    """Atomic, and refuses to overwrite an unreadable catalogue. Without
    that, one bad read replaced the real product tree with the seed and the
    next edit made it permanent."""
    jsonstore.save(_PATH, data, label="catalogue")
    _invalidate_derived()


def _invalidate_derived() -> None:
    """Two classifiers memoise a view of the catalogue for the life of the
    process: the product terms the follow-up gate reads (text_utils) and
    the product->category map that scopes shared documents (retrieval_db).
    Neither knew about a product added or renamed in the console until a
    restart. Only a module already loaded can hold a stale view, so this
    never imports one: importing retrieval_db just to clear its cache
    pulled in rank_bm25 and numpy, and the shared-process test runner then
    dropped numpy from sys.modules with its C extension still loaded, so
    the next `import numpy` failed with "cannot load module more than once
    per process" (test_reindex_safety, after test_catalog_multi_product)."""
    import sys
    for mod, attr in (("text_utils", "_PRODUCT_TERMS"),
                      ("retrieval_db", "_PRODUCT_CATEGORY")):
        m = sys.modules.get(mod)
        if m is not None and hasattr(m, attr):
            setattr(m, attr, None)


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
                       "aliases": p.get("aliases", []),
                       # Who owns this product's unanswered questions, and
                       # how often they hear about them. Projected because
                       # the console renders it on the product row.
                       "champion": p.get("champion", ""),
                       "digest": p.get("digest", "off"),
                       # Projected so the picker can label it and the
                       # console can show its settings (set_obsolete).
                       **({"obsolete": p["obsolete"]} if p.get("obsolete") else {}),
                       **({"obsolete_dismissed": p["obsolete_dismissed"]}
                          if p.get("obsolete_dismissed") else {})}
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


def is_shared_product(key: str | None) -> bool:
    """True for a category's synthetic shared-documents entry ("General
    (shared docs)"), which holds documents rather than naming something we
    make. It must never be listed or offered as a product."""
    k = (key or "").lower()
    return k.endswith("_general") or k.endswith("_shared")


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


DIGEST_CADENCE = ("off", "daily", "weekly")


def set_champion(product_key: str, email: str, digest: str = "weekly") -> dict:
    """Assign a product's champion -- the person whose job it is to answer
    what customers asked about it and nobody could.

    Deliberately stores an ACCOUNT EMAIL rather than a free-text address.
    The console picks from existing console accounts, so the address is one
    that already signs in here, and "who is allowed to see this" needs no
    second answer. A typo becomes an impossible choice rather than a digest
    quietly posted into the void.

    Storage only for now -- nothing sends. The cadence is recorded so the
    delivery job, when it exists, reads its schedule from here rather than
    inventing one.
    """
    digest = (digest or "off").strip().lower()
    if digest not in DIGEST_CADENCE:
        raise ValueError(f"digest must be one of {', '.join(DIGEST_CADENCE)}")
    email = (email or "").strip().lower()
    with _lock:
        data = _load()
        for c in data["categories"]:
            for p in c.get("products", []):
                if p["key"] == product_key:
                    if email:
                        p["champion"] = email
                        p["digest"] = digest
                    else:
                        # Clearing the champion clears the schedule with it:
                        # a cadence with nobody to send to is a trap for
                        # whoever reads this file next.
                        p.pop("champion", None)
                        p["digest"] = "off"
                    _save(data)
                    return catalog()
        raise ValueError(f"unknown product '{product_key}'")


# ── Obsolete products (CEO 2026-11-05) ─────────────────────────────────
# A product ticked obsolete in the console carries:
#   date          "YYYY-MM", when it was (or will be) made obsolete
#   service_end   "YYYY-MM" or "", when service and repairs stop, if later
#   versions      which units, when not all ("Blue label, made before 2016")
#   replacement   what replaces it, said in the reply
#   block_answers on by default: from `date`, its documents answer nobody
#                 and a question about it gets the obsolete reply instead
#   confluence_import  off: the Confluence sync skips pages filed under it
#                 (confluence_sync.import_blocked)

def set_obsolete(product_key: str, info: dict | None) -> dict:
    """Tick (info) or untick (None) a product as obsolete."""
    import re as _re
    with _lock:
        data = _load()
        p = _product_by_key(data, product_key)
        if not p:
            raise ValueError(f"unknown product '{product_key}'")
        if not info:
            p.pop("obsolete", None)
        else:
            out = {}
            for k in ("date", "service_end"):
                v = str(info.get(k) or "").strip()
                if v and not _re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v):
                    raise ValueError(f"{k} must be a month, YYYY-MM")
                out[k] = v
            if not out["date"]:
                raise ValueError("say when it was made obsolete")
            out["versions"] = str(info.get("versions") or "").strip()[:200]
            out["replacement"] = str(info.get("replacement") or "").strip()[:120]
            out["block_answers"] = bool(info.get("block_answers", True))
            out["confluence_import"] = bool(info.get("confluence_import", True))
            p["obsolete"] = out
        _save(data)
        return catalog()


def dismiss_obsolete_suggestion(product_key: str, date: str) -> None:
    """The support hub's list said this product went obsolete on `date`,
    and someone decided otherwise: not offered again until the list changes."""
    with _lock:
        data = _load()
        p = _product_by_key(data, product_key)
        if not p:
            raise ValueError(f"unknown product '{product_key}'")
        p["obsolete_dismissed"] = str(date or "")[:7]
        _save(data)


def _this_month() -> str:
    import datetime
    return datetime.date.today().strftime("%Y-%m")


def obsolete_of(product_key: str | None) -> dict | None:
    """The product's obsolete settings with its name, once its date has
    come; None for a current product or one obsolete only from a later date."""
    if not product_key:
        return None
    p = _product_by_key(_load(), product_key)
    o = (p or {}).get("obsolete")
    if not o or (o.get("date") or "9999") > _this_month():
        return None
    return dict(o, key=product_key, name=p["name"])


def blocked_products() -> set[str]:
    """Products whose documents may answer nobody: obsolete, date come,
    answers blocked."""
    now = _this_month()
    return {p["key"] for c in _load()["categories"] for p in c.get("products", [])
            if (p.get("obsolete") or {}).get("block_answers")
            and (p["obsolete"].get("date") or "9999") <= now}


def month_name(ym: str) -> str:
    """"2021-06" -> "June 2021"."""
    import calendar
    try:
        y, m = ym.split("-")
        return f"{calendar.month_name[int(m)]} {y}"
    except Exception:
        return ym or ""


def champions() -> list[dict]:
    """Every product with a champion, for the delivery job to iterate."""
    out = []
    for c in _load()["categories"]:
        for p in c.get("products", []):
            if p.get("champion") and p.get("digest", "off") != "off":
                out.append({"product": p["key"], "product_name": p["name"],
                            "category": c["key"], "category_name": c["name"],
                            "champion": p["champion"],
                            "digest": p.get("digest", "off")})
    return out


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
        if product_key == f"{category_key}_general":
            raise ValueError("a range's shared documents entry cannot be deleted; "
                             "move its documents instead")
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


def load_catalog() -> dict:
    """The raw tree (categories -> products -> sources), for reindex.py."""
    return _load()


def _product_by_key(data: dict, key: str) -> dict | None:
    return next((p for c in data["categories"] for p in c.get("products", [])
                 if p["key"] == key), None)


def _split_keys(product_key: str | None) -> list[str]:
    return list(dict.fromkeys(
        k.strip() for k in (product_key or "").split(",") if k.strip()))


def attach_source(category_key: str, product_key: str, source: str) -> dict:
    """Tag an (already-ingested) source filename to one or more products.

    "a,b" files it under both, the same form ingest_file writes as one
    prod_* flag per key, so an upload into several products records all of
    them here and reindex.py rebuilds the same scoping.
    """
    with _lock:
        data = _load()
        if not _find_category(data, category_key):
            raise ValueError(f"unknown category '{category_key}'")
        for key in _split_keys(product_key):
            prod = _product_by_key(data, key)
            if not prod:
                raise ValueError(f"unknown product '{key}'")
            if source not in prod["sources"]:
                prod["sources"].append(source)
        _save(data)
    return catalog()


def refile_source(source: str, product_keys: list[str]) -> dict:
    """Make the catalogue say exactly which products hold `source`: removed
    from every product, then listed under each of `product_keys` (an empty
    list leaves it unfiled).

    The index carries the same tags (db.with_product_tags), but the
    catalogue is what reindex.py rebuilds from: a tag that lived only in
    Chroma was lost on the next rebuild, which is how five documents came
    to be filed differently in the two places.
    """
    with _lock:
        data = _load()
        for c in data["categories"]:
            for p in c.get("products", []):
                p["sources"] = [s for s in p.get("sources", []) if s != source]
        for key in _split_keys(",".join(product_keys)):
            prod = _product_by_key(data, key)
            if not prod:
                raise ValueError(f"unknown product '{key}'")
            prod["sources"].append(source)
        _save(data)
    return catalog()
