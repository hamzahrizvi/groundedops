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
    """Full tree for the picker UI: categories -> products (names/keys)."""
    data = _load()
    return {"categories": [
        {"key": c["key"], "name": c["name"],
         "products": [{"key": p["key"], "name": p["name"]}
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


def delete_product(category_key: str, product_key: str) -> dict:
    with _lock:
        data = _load()
        cat = _find_category(data, category_key)
        if cat:
            cat["products"] = [p for p in cat["products"] if p["key"] != product_key]
            _save(data)
    return catalog()


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
