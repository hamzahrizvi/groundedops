"""The product catalogue: the public picker tree and the console's category,
product and champion management.

Moved out of main.py verbatim (2026-09-25). Registered on the app through
app.include_router; paths, parameters and responses are unchanged.
"""

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import accounts
import catalog as catalog_mod
import faq_store
from guards import _require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/catalog")
def get_catalog():
    """Category -> product tree for the pre-chat picker (v10.3).
    v10.14: each product/category is annotated with doc_count (distinct
    ingested sources tagged to it) so the UI can block starting a chat
    for a product that has no documents."""
    cat = catalog_mod.catalog()
    try:
        from db import get_collection
        col = get_collection()
        got = col.get(include=["metadatas"])
        prod_sources, cat_sources = {}, {}
        for m in got.get("metadatas", []) or []:
            src = m.get("source")
            if not src:
                continue
            p, c = m.get("product", ""), m.get("category", "")
            # A document tagged to several products counts under EACH of them.
            # Counting the comma-joined value as one key filed a shared manual
            # under a product nobody has, and showed 0 against the products
            # that actually carry it.
            for key in [k.strip() for k in (p or "").split(",") if k.strip()]:
                prod_sources.setdefault(key, set()).add(src)
            if c:
                cat_sources.setdefault(c, set()).add(src)
        for category in cat["categories"]:
            ccount = len(cat_sources.get(category["key"], set()))
            category["doc_count"] = ccount
            for prod in category["products"]:
                prod["doc_count"] = len(prod_sources.get(prod["key"], set()))
    except Exception as e:
        logger.warning(f"catalog doc_count enrichment failed (non-fatal): {e}")
    return cat


class CategoryReq(BaseModel):
    key: str
    name: str


class ProductReq(BaseModel):
    category_key: str
    key: str
    name: str
    sources: list[str] | None = None


class AttachReq(BaseModel):
    category_key: str
    product_key: str
    source: str


@router.post("/admin/category")
def admin_add_category(payload: CategoryReq, x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    try:
        return catalog_mod.add_category(payload.key, payload.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/admin/category")
def admin_rename_category(payload: CategoryReq, x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    try:
        return catalog_mod.rename_category(payload.key, payload.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/admin/category/{key}")
def admin_delete_category(key: str, x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    return catalog_mod.delete_category(key)


@router.post("/admin/product")
def admin_add_product(payload: ProductReq, x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    try:
        return catalog_mod.add_product(payload.category_key, payload.key,
                                       payload.name, payload.sources)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ChampionReq(BaseModel):
    product_key: str
    email: str = ""          # "" clears the assignment
    digest: str = "weekly"   # off | daily | weekly


@router.post("/admin/product/champion")
def admin_set_champion(payload: ChampionReq,
                       x_admin_password: str | None = Header(default=None)):
    """Assign the person who owns a product's unanswered questions.

    The email must belong to an existing console account. Accepting a free
    address would mean a digest addressed to someone who cannot open the
    page it links to, and a typo that fails silently -- neither is worth the
    flexibility.
    """
    me = _require_admin(x_admin_password)
    email = (payload.email or "").strip().lower()
    if email:
        known = {u.get("email", "").lower() for u in accounts.list_users()
                 if not u.get("disabled")}
        if email not in known:
            raise HTTPException(status_code=400, detail={
                "error": "not_an_account",
                "message": f"{email} is not an active console account. Add "
                           f"them under Accounts first, so the digest goes "
                           f"to someone who can open what it links to.",
            })
    try:
        tree = catalog_mod.set_champion(payload.product_key, email,
                                        payload.digest)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("champion for %s set to %r (%s) by %s", payload.product_key,
                email or "nobody", payload.digest, me.get("email"))
    return tree


@router.get("/admin/champions")
def admin_champions(x_admin_password: str | None = Header(default=None)):
    """What a delivery job would send, and to whom. Exposed now so the
    assignment can be verified before anything sends -- the schedule is
    real even though delivery is not wired up yet."""
    _require_admin(x_admin_password)
    rows = catalog_mod.champions()
    return {"champions": rows, "count": len(rows), "delivery": "not configured"}


@router.get("/admin/product/{product_key}/contents")
def admin_product_contents(product_key: str,
                           x_admin_password: str | None = Header(default=None)):
    """What is filed under a product: chunks, answers, questions, sources.

    The console calls this before offering to delete, so the confirmation
    can say "this will affect 82 document chunks and 18 questions" rather
    than asking someone to agree to an unknown quantity.
    """
    _require_admin(x_admin_password)
    return {"product": product_key,
            "contents": catalog_mod.product_contents(product_key)}


@router.delete("/admin/product/{category_key}/{product_key}")
def admin_delete_product(category_key: str, product_key: str,
                         reassign_to: str | None = None,
                         delete_content: bool = False,
                         x_admin_password: str | None = Header(default=None)):
    """Delete a product AND deal with everything filed under it.

    One of `reassign_to` or `delete_content` is required. Deleting only the
    catalogue row is what produced the orphaned `nv9st` and `coin_hoppers`
    keys: real documents and questions behind a product that no longer
    exists, invisible to a console that builds its lists from the
    catalogue. A 400 asking which you meant is better than either default.
    """
    _require_admin(x_admin_password)
    try:
        return catalog_mod.delete_product(
            category_key, product_key,
            reassign_to=reassign_to, delete_content=delete_content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class RetagReq(BaseModel):
    from_key: str
    to_key: str | None = None


@router.post("/admin/product/retag")
def admin_product_retag(payload: RetagReq,
                        x_admin_password: str | None = Header(default=None)):
    """Move everything filed under one product key to another.

    Exists for the keys that are already orphaned — content tagged to a
    product that was deleted before deletion cascaded. `to_key` may name a
    product that is not in the catalogue only if it is null (untag); moving
    content ONTO a nonexistent key would just create the same problem
    again.
    """
    me = _require_admin(x_admin_password)
    if payload.to_key:
        known = {p["key"] for c in catalog_mod.catalog().get("categories", [])
                 for p in c.get("products", [])}
        if payload.to_key not in known:
            raise HTTPException(
                status_code=400,
                detail=f"'{payload.to_key}' is not a product in the catalogue. "
                       f"Create it first, or pass to_key=null to untag.")
    # Imported here rather than at module scope: this is a rare admin
    # operation, and a top-level import ties main.py's import list to what
    # every test stub of `db` happens to provide.
    from db import retag_product as _retag
    chunks = _retag(payload.from_key, payload.to_key)
    faq = faq_store.retag_product(payload.from_key, payload.to_key)
    logger.info(f"retag {payload.from_key!r} -> {payload.to_key!r} by {me['email']}")
    return {"chunks": chunks, "answers": faq["answers"],
            "questions": faq["questions"]}


@router.post("/admin/attach_source")
def admin_attach_source(payload: AttachReq, x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    try:
        return catalog_mod.attach_source(payload.category_key, payload.product_key, payload.source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
