"""Product registry (v2.1 — per-product chat scoping).

Maps products to the document sources that belong to them. A source may
belong to MULTIPLE products (e.g. the shared MyConnect networking doc is
relevant to both MyCheckr and MyCheckr Mini), so this is many-to-many.

Editable here, or via the PRODUCTS_CONFIG json file if present (so
products can be added without a code change). Each product:
  key          stable id used in metadata + API (do not change once live)
  name         display name for the picker
  sources      list of source filename substrings that belong to it
               (substring match, case-insensitive, against chunk 'source')

The customer picks a product before a chat; every query in that chat is
scoped to that product's sources. "all" is a built-in that scopes to
nothing (searches the whole corpus) — useful for internal/admin use.
"""
import json
import os
import logging

logger = logging.getLogger(__name__)

_DEFAULT_PRODUCTS = [
    {
        "key": "mycheckr",
        "name": "MyCheckr",
        "sources": ["MyCheckr_User_Manual", "MyConnect_Environment", "ICU_Network_API",
                    "Certificate_and_USB"],
    },
    {
        "key": "mycheckr_mini",
        "name": "MyCheckr Mini",
        "sources": ["MyCheckr_Mini", "MyConnect_Environment", "ICU_Network_API",
                    "Certificate_and_USB"],
    },
    # Example of a product with no docs yet — appears in the picker only
    # if you add its sources. Kept commented so the picker doesn't offer
    # an empty product.
    # {"key": "nv4000", "name": "NV4000", "sources": ["NV4000"]},
]

_CONFIG_PATH = os.getenv("PRODUCTS_CONFIG", "products_config.json")


def _load() -> list[dict]:
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH) as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
        except Exception as e:
            logger.warning(f"Failed to read {_CONFIG_PATH}, using defaults: {e}")
    return _DEFAULT_PRODUCTS


def list_products() -> list[dict]:
    """Picker data: [{key, name}, ...] — sources omitted (internal)."""
    return [{"key": p["key"], "name": p["name"]} for p in _load()]


def sources_for(product_key: str | None) -> list[str] | None:
    """Return the source substrings for a product, or None for whole-corpus
    ('all', unknown, or empty)."""
    if not product_key or product_key == "all":
        return None
    for p in _load():
        if p["key"] == product_key:
            return p.get("sources") or None
    logger.warning(f"Unknown product key '{product_key}' — searching whole corpus")
    return None


def product_for_source(source: str) -> list[str]:
    """Which product keys a given source belongs to (for tagging at ingest)."""
    keys = []
    for p in _load():
        if any(s.lower() in source.lower() for s in p.get("sources", [])):
            keys.append(p["key"])
    return keys
