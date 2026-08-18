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


def register(app, answer_query):
    """Attach the router.

    `answer_query` is injected rather than imported so this module has no
    dependency on main.py - it stays unit-testable, and the pipeline can be
    stubbed in tests without loading three ML models.
    """

    @router.post("/ask")
    async def widget_ask(payload: AskRequest, request: Request):
        caller = _caller(request, payload.visitor_id)

        # Downgrade before charging: an anonymous caller asking for "deep"
        # gets a standard answer at standard cost, plus an upsell - never a
        # deep-priced charge for a standard answer.
        level, spec = quota.resolve_effort(payload.effort, caller["tier"])
        downgraded = level != (payload.effort or "standard").lower()

        gate = quota.check(caller, spec["credits"])
        if not gate["allowed"]:
            # 429 with structured detail so the widget can render a proper
            # message and a sign-in prompt instead of a generic failure.
            raise HTTPException(status_code=429, detail={
                "error": "quota_exceeded",
                "reason": gate["reason"],
                "tier": caller["tier"],
                "limit": gate["limit"],
                "remaining": gate["remaining"],
                "reset_at": gate["reset_at"],
                "sign_in_url": os.getenv("WIDGET_SIGN_IN_URL", ""),
                "message": (
                    "You've used your questions for today. Signing in on our "
                    "website gives you a larger allowance."
                    if caller["tier"] == "anonymous" else
                    "You've used your allowance for today. It resets in 24 hours."
                ),
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

        # A curated FAQ answer costs no LLM call, so it costs no credits.
        # Cheap for us, generous to the visitor, and it nudges people
        # towards the reviewed answers.
        charged = 0 if result.get("from_faq") else spec["credits"]
        # Disambiguation prompts aren't answers either.
        if result.get("faq_candidates"):
            charged = 0

        state = quota.consume(caller, charged) if charged else quota.status(caller)

        logger.info(
            f"widget ask tier={caller['tier']} effort={level} "
            f"charged={charged} remaining={state['remaining']} "
            f"ms={round((time.time() - started) * 1000)}"
        )

        # Strip internals the public has no business seeing: chunk ids,
        # scores, model names, provider, timings.
        sources = [{
            "source": s.get("source"),
            "page_label": s.get("page_label"),
            "snippet": s.get("snippet"),
            "download_url": s.get("download_url"),
        } for s in (result.get("sources") or [])]

        return {
            "answer": result.get("answer"),
            "sources": sources,
            "from_faq": bool(result.get("from_faq")),
            "faq_candidates": result.get("faq_candidates"),
            "needs_clarification": bool(result.get("needs_clarification")),
            "flagged": bool(result.get("flagged")),
            "effort": level,
            "effort_downgraded": downgraded,
            "quota": {
                "tier": caller["tier"],
                "remaining": state["remaining"],
                "limit": state["limit"],
                "reset_at": state["reset_at"],
                "deep_available": caller["tier"] != "anonymous",
                "deep_cost": quota.EFFORT["deep"]["credits"],
            },
            "sign_in_url": (os.getenv("WIDGET_SIGN_IN_URL", "")
                            if caller["tier"] == "anonymous" else ""),
        }

    app.include_router(router)
    logger.info("Public widget API registered at /widget")
