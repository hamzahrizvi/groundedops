"""Public widget API (v12.0).

This module is the ONLY part of GroundedOps intended to be reachable from
the internet. Everything else - upload, FAQ curation, catalog management,
delete, reset - stays on the support department's LAN.

That split is the main security control in this design, and it is worth
being explicit about why: the admin surface has a single shared password
and no per-user accounts, so the cheapest way to make it safe is to never
expose it. Hardening an endpoint is a promise; not routing to it is a
fact.

Deployment: the reverse proxy should forward ONLY /widget/* to this
service from the public interface. The middleware in main.py is a second
line of defence for when the proxy is misconfigured, not a substitute for
configuring it.

Endpoints
    GET  /widget/config   branding + this caller's quota (no answer)
    GET  /widget/quota    remaining credits
    POST /widget/ask      ask a question (costs credits)
"""
import logging
import os
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import faq_store
import quota

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/widget", tags=["widget"])

# Maximum question length. Prompt cost scales with input, and nobody
# legitimately pastes 4,000 characters into a support widget.
MAX_QUESTION_CHARS = int(os.getenv("WIDGET_MAX_QUESTION_CHARS", "500"))


class AskRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS)
    session_id: str | None = None
    visitor_id: str | None = None
    product: str | None = None
    category: str | None = None
    # "standard" or "deep". The MODEL is chosen server-side from this
    # name - see quota.EFFORT. The client never names a model, or anyone
    # could select the most expensive one available.
    effort: str = "standard"
    # FAQ disambiguation passthrough.
    faq_id: str | None = None
    skip_faq: bool = False


def _client_ip(request: Request) -> str:
    """Real client IP behind a reverse proxy.

    Only honours X-Forwarded-For when TRUST_PROXY is set: if the app is
    ever reachable directly, an attacker could otherwise spoof the header
    and mint unlimited anonymous identities.
    """
    # CF-Connecting-IP first: Cloudflare sets it and a client cannot forge
    # it, unlike X-Forwarded-For which any caller can prepend to. Getting
    # this wrong lets anonymous visitors mint unlimited identities and
    # bypass the per-IP ceiling.
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    if os.getenv("TRUST_PROXY", "").strip().lower() in ("1", "true", "yes"):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _caller(request: Request, visitor_id: str | None) -> dict:
    # Bearer token issued by the company website when a user signs in.
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    return quota.identify(token, visitor_id, _client_ip(request))


@router.get("/config")
def widget_config(request: Request, visitor_id: str | None = None):
    """Branding and quota state, fetched once when the widget opens. Lets
    the widget show remaining questions and the 'sign in for more' upsell
    before the visitor has spent anything."""
    caller = _caller(request, visitor_id)
    return {
        "title": os.getenv("WIDGET_TITLE", "Product support"),
        "agent_name": os.getenv("WIDGET_AGENT_NAME", "Assistant"),
        "accent": os.getenv("WIDGET_ACCENT", "#E4002B"),
        "greeting": os.getenv("WIDGET_GREETING", ""),
        "sign_in_url": os.getenv("WIDGET_SIGN_IN_URL", ""),
        "quota": quota.status(caller),
    }


@router.get("/quota")
def widget_quota(request: Request, visitor_id: str | None = None):
    return quota.status(_caller(request, visitor_id))


@router.get("/catalog")
def widget_catalog():
    """Product ranges for the widget's picker.

    A public, MINIMAL view: keys, display names and a document count. The
    admin /catalog also exposes the source FILENAMES behind each product,
    which on a public endpoint is information disclosure - it reveals
    internal document names and makes guessing /source_file URLs trivial.

    Ranges with no ingested documents are omitted, because offering a
    product the system cannot answer about is worse than not listing it.
    """
    try:
        import catalog as catalog_mod
        cat = catalog_mod.catalog()
    except Exception as e:
        logger.error(f"widget catalog failed: {e}")
        raise HTTPException(status_code=503, detail={
            "error": "unavailable",
            "message": "Product list is temporarily unavailable.",
        })

    # Count distinct ingested sources per product/category.
    #
    # Reads BOTH "products" and "product" metadata keys: ingest.py writes
    # the plural, while the admin catalog endpoint reads the singular, so
    # doc_count there is 0 for everything ingested normally. Accepting both
    # keeps this correct regardless of which path wrote the document.
    prod_sources, cat_sources = {}, {}
    try:
        from db import get_collection
        got = get_collection().get(include=["metadatas"])
        for m in (got.get("metadatas") or []):
            src = m.get("source")
            if not src:
                continue
            raw = m.get("products") or m.get("product") or ""
            for key in [k.strip() for k in str(raw).split(",") if k.strip()]:
                prod_sources.setdefault(key, set()).add(src)
            ckey = (m.get("category") or "").strip()
            if ckey:
                cat_sources.setdefault(ckey, set()).add(src)
    except Exception as e:
        logger.warning(f"widget catalog doc_count failed (non-fatal): {e}")

    cats = []
    for c in (cat.get("categories") or []):
        prods = [{"key": p["key"], "name": p["name"],
                  "doc_count": len(prod_sources.get(p["key"], set()))}
                 for p in (c.get("products") or [])]
        prods = [p for p in prods if p["doc_count"] > 0]
        ccount = len(cat_sources.get(c["key"], set()))
        # Keep a range if either it or any of its products has documents -
        # a document tagged only at category level still makes the range
        # answerable.
        if prods or ccount:
            cats.append({"key": c["key"], "name": c["name"],
                         "doc_count": max(ccount, sum(p["doc_count"] for p in prods)),
                         "products": prods})

    if not cats:
        logger.warning("widget catalog: no product ranges have documents - "
                       "check that uploads were assigned to a category AND product")
    return {"categories": cats}


@router.get("/faq")
def widget_faq(product: str | None = None, limit: int = 4):
    """Curated questions for a product, shown as starting suggestions.

    Only entries with a human-reviewed answer are returned - an unanswered
    question offered as a suggestion leads straight to a dead end. Answers
    are included so tapping one can render instantly without a round trip.
    """
    try:
        import faq_store
        items = [f for f in faq_store.list_for_product(product)
                 if (f.get("answer") or "").strip()]
    except Exception as e:
        logger.error(f"widget faq failed: {e}")
        return {"faq": []}
    return {"faq": [{"id": f["id"], "question": f["question"],
                     "answer": f["answer"]}
                    for f in items[:max(1, min(limit, 10))]]}


def _public_sources(sources) -> list:
    """Strip internals the public has no business seeing - chunk ids,
    retrieval scores, model and provider names, timings."""
    return [{
        "source": s.get("source"),
        "page_label": s.get("page_label"),
        "snippet": s.get("snippet"),
        "download_url": s.get("download_url"),
    } for s in (sources or [])]


def _faq_response(answer: str, caller: dict, matched: str | None = None,
                  candidates: list | None = None, clarify: bool = False,
                  needs_sign_in: bool = False) -> dict:
    return {
        "answer": answer,
        "sources": [],
        "from_faq": not needs_sign_in and not clarify,
        "faq_matched_question": matched,
        "faq_candidates": candidates,
        "needs_clarification": clarify,
        "needs_sign_in": needs_sign_in,
        "flagged": False,
        "effort": "faq_only",
        "quota": quota.status(caller),
        "sign_in_url": os.getenv("WIDGET_SIGN_IN_URL", "") if needs_sign_in else "",
    }


def register(app, answer_query):
    """Attach the router.

    `answer_query` is injected rather than imported so this module has no
    dependency on main.py - it stays unit-testable, and the pipeline can be
    stubbed in tests without loading three ML models.
    """

    @router.post("/ask")
    async def widget_ask(payload: AskRequest, request: Request):
        caller = _caller(request, payload.visitor_id)
        tier = caller["tier"]
        level, spec = quota.resolve_effort(payload.effort, tier)
        sign_in = os.getenv("WIDGET_SIGN_IN_URL", "")

        # ── Anonymous: curated FAQ only ───────────────────────────────
        # No LLM call is reachable without an account. That removes the
        # public cost exposure entirely and means untrusted input never
        # reaches a prompt, so there is no public prompt-injection path.
        if tier == "anonymous":
            gate = quota.check_faq_lookup(caller)
            if not gate["allowed"]:
                raise HTTPException(status_code=429, detail={
                    "error": "quota_exceeded",
                    "reason": "faq_quota",
                    "tier": tier,
                    "reset_at": gate["reset_at"],
                    "sign_in_url": sign_in,
                    "message": "You have reached today's limit for FAQ lookups.",
                })

            quota.consume_faq_lookup(caller)

            # Selecting a specific curated question is served by id.
            if payload.faq_id:
                entry = faq_store.get_by_id(payload.faq_id)
                if entry:
                    return _faq_response(entry["answer"], caller,
                                         matched=entry["question"])
                raise HTTPException(status_code=404, detail={
                    "error": "not_found", "message": "That answer is no longer available."})

            faq = faq_store.suggest_candidates(payload.q, payload.product or payload.category)

            if faq["mode"] == "answer":
                return _faq_response(faq["entry"]["answer"], caller,
                                     matched=faq["entry"]["question"])

            if faq["mode"] == "disambiguate":
                return _faq_response(
                    "These FAQs match your query - please select the one you meant:",
                    caller, candidates=faq["candidates"], clarify=True)

            # Nothing curated covers it. This is the upsell moment, and the
            # honest one: we are not refusing, we simply have no reviewed
            # answer and a full answer needs an account.
            return _faq_response(
                "I don't have a reviewed answer for that yet. Sign in to your "
                "account and I can search the full product documentation for you.",
                caller, needs_sign_in=True)

        # ── Member / staff: full pipeline, charged in credits ─────────
        gate = quota.check(caller, spec["credits"])
        if not gate["allowed"]:
            raise HTTPException(status_code=429, detail={
                "error": "quota_exceeded",
                "reason": gate["reason"],
                "tier": tier,
                "limit": gate["limit"],
                "remaining": gate["remaining"],
                "reset_at": gate["reset_at"],
                "message": "You have used your allowance for today. It resets in 24 hours.",
            })

        started = time.time()
        try:
            result = await answer_query(
                q=payload.q,
                session_id=payload.session_id,
                product=payload.product,
                category=payload.category,
                faq_id=payload.faq_id,
                skip_faq=payload.skip_faq,
                role=spec["role"],
                top_k=spec["top_k"],
                model_env=spec.get("model_env"),
                user_id=caller.get("uid"),
            )
        except HTTPException:
            raise
        except Exception as e:
            # Don't charge for our own failure, and don't leak internals.
            logger.exception(f"widget ask failed: {e}")
            raise HTTPException(status_code=503, detail={
                "error": "unavailable",
                "message": "Support is temporarily unavailable. Please try again shortly.",
            })

        # Curated answers and disambiguation prompts involve no LLM call,
        # so they cost nothing - cheap for us, and it steers people towards
        # the reviewed answers.
        charged = 0 if (result.get("from_faq") or result.get("faq_candidates")) \
                  else spec["credits"]
        state = quota.consume(caller, charged) if charged else quota.status(caller)

        logger.info(f"widget ask tier={tier} effort={level} charged={charged} "
                    f"remaining={state['remaining']} "
                    f"ms={round((time.time() - started) * 1000)}")

        return {
            "answer": result.get("answer"),
            "sources": _public_sources(result.get("sources")),
            "from_faq": bool(result.get("from_faq")),
            "faq_candidates": result.get("faq_candidates"),
            "needs_clarification": bool(result.get("needs_clarification")),
            "flagged": bool(result.get("flagged")),
            "effort": level,
            "effort_downgraded": level != (payload.effort or "standard").lower(),
            "quota": quota.status(caller),
        }

    app.include_router(router)
    logger.info("Public widget API registered at /widget")
