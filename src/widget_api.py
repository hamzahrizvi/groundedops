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
import asyncio
import re
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


# GET /widget/config used to live here, serving branding from environment
# variables. v13.0 made that branding editable in the admin console
# (widget_config.py, persisted to WIDGET_CONFIG_PATH), so two modules defined
# the same path and the one registered first silently won. The route now lives
# in main.py, next to the admin routes that write the same config, and returns
# the console's values plus the sign_in_url and quota block this version
# supplied — so no field was lost in the move.


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


class DraftRequest(BaseModel):
    kind: str                                   # "sales" | "support"
    visitor_id: str | None = None
    product: str | None = None
    # What the visitor typed themselves, when they chose to write their own
    # description rather than have the chat summarised.
    notes: str | None = Field(default=None, max_length=2000)
    # Whether to draft from the conversation or from `notes`.
    source: str = "chat"                        # "chat" | "written"
    # The conversation so far, sent by the widget — the same shape and the
    # same trust level as the transcript /widget/lead already accepts.
    # Bounded here because prompt cost scales with it, and because there is
    # no legitimate 200-turn support chat.
    transcript: list | None = None


def _assemble_enquiry(kind: str, product: str, notes: str,
                      transcript: list) -> str:
    """The no-LLM enquiry body: the visitor's own words, or a plain
    transcript, with a one-line header. Used for anonymous callers, and as
    the fallback whenever generation is unavailable — a form that cannot be
    submitted because the model is down would be a worse failure than an
    unpolished enquiry."""
    head = f"{'Sales' if kind == 'sales' else 'Support'} enquiry"
    if product:
        head += f" — {product}"
    parts = [head, ""]
    if notes.strip():
        parts.append(notes.strip())
    elif transcript:
        parts.append("From the visitor's conversation with the assistant:")
        parts.append("")
        for m in transcript[-12:]:
            role = "Visitor" if (m.get("role") == "user" or m.get("q")) else "Assistant"
            text = (m.get("text") or m.get("q") or m.get("a") or "").strip()
            if text:
                parts.append(f"{role}: {text}")
    else:
        parts.append("(no description given)")
    return "\n".join(parts)[:4000]


def register(app, answer_query, draft_enquiry=None):
    """Attach the router.

    `answer_query` is injected rather than imported so this module has no
    dependency on main.py - it stays unit-testable, and the pipeline can be
    stubbed in tests without loading three ML models. `draft_enquiry` is
    injected the same way and for the same reason; if it is None the draft
    endpoint still works, it just returns the assembled body instead of a
    written one.
    """

    @router.post("/draft_enquiry")
    async def widget_draft_enquiry(payload: DraftRequest, request: Request):
        """Write up a sales/support enquiry, either from the conversation or
        from what the visitor typed. Returns a DRAFT — the widget shows it and
        lets them edit before it is submitted, so a bad draft is a nuisance
        rather than a misrepresentation of what they wanted.

        ── Why anonymous callers get no model here ──
        /ask above keeps a hard invariant: no LLM call is reachable without
        an account, which is what removes public cost exposure and the public
        prompt-injection path. Drafting is the same shape of risk (untrusted
        text into a prompt, on a public endpoint) so it honours the same rule
        by default: anonymous gets the assembled body, signed-in gets a
        written one, charged against the same allowance as a question.
        Set WIDGET_AI_DRAFT_ANONYMOUS=1 to lift that deliberately.
        """
        if payload.kind not in ("sales", "support"):
            raise HTTPException(status_code=400,
                                detail="kind must be 'sales' or 'support'")

        caller = _caller(request, payload.visitor_id)
        tier = caller["tier"]
        notes = (payload.notes or "").strip()
        # Everything below is untrusted visitor text — the transcript no less
        # than the notes, since a visitor can type whatever they like into
        # either. Bounding it is the only mitigation that matters here; the
        # draft is shown back to them and never executed, and the tier gate
        # below is what keeps this off a fully public prompt.
        transcript = (payload.transcript or [])[-12:] if payload.source == "chat" else []

        assembled = _assemble_enquiry(payload.kind, payload.product or "",
                                      notes, transcript)

        anon_ok = os.getenv("WIDGET_AI_DRAFT_ANONYMOUS", "").strip().lower() \
            in ("1", "true", "yes")
        if draft_enquiry is None or (tier == "anonymous" and not anon_ok):
            return {"draft": assembled, "written_by": "assembled",
                    "quota": quota.status(caller)}

        gate = quota.check(caller, 1)
        if not gate["allowed"]:
            # Not a 429: the visitor can still submit the assembled version,
            # and blocking a support request because a drafting allowance ran
            # out would be an absurd place to stop someone.
            return {"draft": assembled, "written_by": "assembled",
                    "quota": quota.status(caller)}

        try:
            written = await draft_enquiry(
                kind=payload.kind, product=payload.product or "",
                notes=notes, transcript=transcript)
        except Exception as e:
            logger.warning(f"/widget/draft_enquiry: generation failed ({e})")
            written = ""

        if not (written or "").strip():
            return {"draft": assembled, "written_by": "assembled",
                    "quota": quota.status(caller)}

        state = quota.consume(caller, 1)
        return {"draft": written.strip()[:4000], "written_by": "model",
                "quota": state}

    @router.post("/ask")
    async def widget_ask(payload: AskRequest, request: Request):
        caller = _caller(request, payload.visitor_id)
        tier = caller["tier"]
        level, spec = quota.resolve_effort(payload.effort, tier)
        sign_in = os.getenv("WIDGET_SIGN_IN_URL", "")

        # ── Per-session caps ─────────────────────────────────────────
        # Checked before anything else: if this conversation has used up its
        # allowance there is no point resolving effort or touching the FAQ.
        # A 429 with a distinct reason, so the widget can say "this chat has
        # reached its limit, start a new one" rather than the daily wording.
        sess = quota.session_check(payload.session_id)
        if not sess["allowed"]:
            raise HTTPException(status_code=429, detail={
                "error": "quota_exceeded",
                "reason": sess["reason"],
                "tier": tier,
                "limit": (sess["questions_limit"] if sess["reason"] == "session_questions"
                          else sess["tokens_limit"]),
                "message": ("This conversation has reached its limit. "
                            "Start a new chat to carry on."),
            })

        # ── Anonymous: curated FAQ only, unless opened up ─────────────
        # By default no LLM call is reachable without an account. That
        # removes the public cost exposure entirely and means untrusted input
        # never reaches a prompt, so there is no public prompt-injection
        # path. An operator can lift it from the console (policy.py's
        # anon_llm_enabled) — a deliberate choice with a bill attached, which
        # is why it is off until someone turns it on.
        if tier == "anonymous" and not quota.anon_llm_enabled():
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

        # Count this turn against the conversation's own caps. Only when it
        # actually cost something: a curated FAQ answer involves no model and
        # should not use up a session that is limited to bound model spend.
        if charged:
            quota.session_record(payload.session_id,
                                 tokens_used=int(result.get("total_tokens") or 0))

        logger.info(f"widget ask tier={tier} effort={level} charged={charged} "
                    f"remaining={state['remaining']} "
                    f"ms={round((time.time() - started) * 1000)}")

        return {
            "answer": result.get("answer"),
            "sources": _public_sources(result.get("sources")),
            "from_faq": bool(result.get("from_faq")),
            "faq_candidates": result.get("faq_candidates"),
            # v15.2: passed through so the widget can offer a human instead of
            # dead-ending on a refusal. It was already computed by /query but
            # this endpoint builds its own response dict, so it never reached
            # the one surface customers actually use.
            #
            # product_options is deliberately NOT passed through: the widget
            # requires a product before it will ask anything, and the backend
            # only offers product choices when no product is set, so those
            # options can never be populated here.
            "offer_support": bool(result.get("offer_support")),
            "needs_clarification": bool(result.get("needs_clarification")),
            "flagged": bool(result.get("flagged")),
            "effort": level,
            "effort_downgraded": level != (payload.effort or "standard").lower(),
            "quota": quota.status(caller),
            "session": quota.session_check(payload.session_id),
        }


    @router.post("/ask/stream")
    async def widget_ask_stream(payload: AskRequest, request: Request):
        """Streamed answer for the widget, over server-sent events.

        DELIBERATELY NOT A SECOND PIPELINE. Every gate that /ask applies --
        per-session caps, the anonymous FAQ-only rule, the daily quota -- is
        applied by calling widget_ask() itself and streaming whatever it
        decided. Re-implementing those checks here is how a streaming path
        quietly stops charging credits or starts letting guests reach the
        model; the gates are billing and safety logic and they get ONE
        implementation.

        The trade is honest: the answer is produced in full before the first
        character is sent, so this does not lower time-to-first-token. What
        it does give the widget is a progress channel -- "searching",
        "checking" -- and an answer that arrives sentence by sentence instead
        of as one block, which is the part users read as "fast".

        A genuinely token-streamed widget needs the generation call itself
        pushed down into this path, which means the gates above it must be
        factored out first. That refactor touches the paid path, so it is not
        something to do unverified.
        """
        from fastapi.responses import StreamingResponse
        import json as _json

        def sse(event, data):
            return ("event: " + event + "\n"
                    + "data: " + _json.dumps(data) + "\n\n")

        async def run():
            yield sse("status", {"stage": "searching the documentation"})
            try:
                result = await widget_ask(payload, request)
            except HTTPException as e:
                yield sse("error", {"status": e.status_code,
                                    "detail": e.detail})
                return
            except Exception as e:
                logger.exception("widget ask/stream failed")
                yield sse("error", {"status": 503, "detail": str(e)[:160]})
                return

            answer = (result.get("answer") or "").strip()
            yield sse("meta", {k: result.get(k) for k in
                               ("sources", "from_faq", "faq_candidates",
                                "offer_support", "needs_clarification",
                                "flagged", "quota", "session")})

            # Whole sentences, not tokens: a sentence is the unit the
            # grounding gate verifies, so releasing anything smaller would
            # show text that has not been checked.
            _SENTENCE = re.compile(r"[^.!?\n]+[.!?]*\s*|\n+")
            for part in _SENTENCE.findall(answer) or [answer]:
                if part:
                    yield sse("delta", {"text": part})
                    await asyncio.sleep(0)
            yield sse("done", {"flagged": bool(result.get("flagged"))})

        return StreamingResponse(run(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    app.include_router(router)
    logger.info("Public widget API registered at /widget")
