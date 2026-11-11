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
    POST /widget/chat     start a chat: a new id and the token that can delete it
    POST /widget/forget   delete this chat (needs that token)
"""
import asyncio
import collections
import hashlib
import hmac
import re
import threading
import uuid
import logging
import os
import time

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, StringConstraints
from typing import Annotated, Literal

import faq_store
import pipeline_trace
import feedback_store
import jsonstore
import quota
import stepwise
import text_utils
from guards import client_ip as _client_ip, is_local_caller

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/widget", tags=["widget"])

# Maximum question length. Prompt cost scales with input, and nobody
# legitimately pastes 4,000 characters into a support widget.
MAX_QUESTION_CHARS = int(os.getenv("WIDGET_MAX_QUESTION_CHARS", "500"))

# D-20261020-06: caps for the id-like fields the widget sends. Its ids are
# 36-char UUIDs and catalogue keys are under 20, so 128 is headroom, not a
# limit a real embed meets; 35 is the BCP 47 tag length for the word fields.
MAX_ID_CHARS = 128
MAX_WORD_CHARS = 35


# D-20261008-12: one bound for a chat transcript the widget sends (lead form
# and enquiry draft). The widget sends 12 turns; 50 turns and 100 000
# characters a field leave real chats untouched and refuse a megabyte pasted
# into one turn. widget_config.add_lead still trims what it keeps.
_TurnValue = Annotated[str, StringConstraints(max_length=100_000)] | int | float | bool | None
Transcript = Annotated[list[dict[str, _TurnValue]], Field(max_length=50)]


class AskRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS)
    session_id: str | None = Field(None, max_length=MAX_ID_CHARS)
    # D-20261108-01: the token /widget/chat gave with this id.
    delete_token: str | None = Field(None, max_length=MAX_ID_CHARS)
    visitor_id: str | None = Field(None, max_length=MAX_ID_CHARS)
    product: str | None = Field(None, max_length=MAX_ID_CHARS)
    category: str | None = Field(None, max_length=MAX_ID_CHARS)
    # "standard" or "deep". The MODEL is chosen server-side from this
    # name - see quota.EFFORT. The client never names a model, or anyone
    # could select the most expensive one available.
    effort: str = Field("standard", max_length=MAX_WORD_CHARS)
    # FAQ disambiguation passthrough.
    faq_id: str | None = Field(None, max_length=MAX_ID_CHARS)
    skip_faq: bool = False
    # The visitor's language, as the widget itself determined it (an
    # explicit embed override, the host page's own declared locale, or the
    # browser's) -- see groundedops-widget.js's detectedLanguage(). Used
    # only on the anonymous FAQ-only path; the signed-in pipeline has its
    # own, LLM-based language handling (language.py's looks_foreign/
    # to_english). Any shape survives here -- normalized once, in
    # _resolve_anon_language.
    language: str | None = Field(None, max_length=MAX_WORD_CHARS)


class FeedbackRequest(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=64)
    session_id: str | None = Field(None, max_length=MAX_ID_CHARS)
    visitor_id: str | None = Field(None, max_length=MAX_ID_CHARS)
    vote: Literal["up", "down"]
    note: str = Field("", max_length=280)


class ForgetRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=MAX_ID_CHARS)
    delete_token: str = Field("", max_length=MAX_ID_CHARS)
    visitor_id: str | None = Field(None, max_length=MAX_ID_CHARS)


class RatingRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=MAX_ID_CHARS)
    visitor_id: str | None = Field(None, max_length=MAX_ID_CHARS)
    stars: int = Field(..., ge=1, le=5)
    comment: str = Field("", max_length=500)
    delete_token: str = Field("", max_length=MAX_ID_CHARS)


# 8.10: answers a vote may refer to, so /widget/feedback cannot be fed
# made-up ids. Filled when /ask returns a pipeline answer.
# ponytail: in memory, so a restart forgets them and a vote on an answer
# given before it is refused with 404; persist if that starts to matter.
_RECENT_ANSWERS: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
_RECENT_MAX = 5000
_recent_lock = threading.Lock()
_FEEDBACK_PATH = os.getenv("WIDGET_FEEDBACK_PATH", "widget_feedback.json")
_feedback_lock = threading.Lock()


def _remember_answer(request_id, q, scope, session_id, answer="") -> None:
    if not request_id:
        return
    with _recent_lock:
        _RECENT_ANSWERS[request_id] = {"q": q, "scope": scope,
                                       "session_id": session_id,
                                       "answer": (answer or "")[:4000]}
        while len(_RECENT_ANSWERS) > _RECENT_MAX:
            _RECENT_ANSWERS.popitem(last=False)


# D-20261030-09: the deep search is compared with this chat's standard answer
# to the same question. ponytail: in memory like _RECENT_ANSWERS; after a
# restart a deep answer is charged only if it answers at all.
NO_MORE_DETAILS = "I don't have more details on this."
_STANDARD_ANSWERS: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
_FACT = re.compile(r"\b\w*\d\w*\b")                       # numbers, codes, page and step numbers
_STEP = re.compile(r"^\s*(?:\d+[.)]|[-*•]|step\s+\d+)", re.I | re.M)
_WORD = re.compile(r"[a-z]{4,}")


def _deep_key(caller: dict, payload) -> str:
    q = " ".join(payload.q.lower().split())
    return quota._h(f"{caller['identity']}|{payload.session_id or ''}|"
                    f"{payload.product or payload.category or ''}|{q}")


def _answered(r: dict) -> bool:
    a = (r.get("answer") or "").strip()
    return bool(a) and not (
        r.get("offer_support") or r.get("flagged") or r.get("system_refusal")
        or r.get("needs_clarification") or r.get("service_degraded")
        or r.get("from_faq") or r.get("faq_candidates")
        or r.get("role") in ("handoff", "clarify", "rejected")
        or text_utils.is_refusal(a))


def _remember_standard(key: str, r: dict) -> None:
    with _recent_lock:
        _STANDARD_ANSWERS[key] = {"answer": (r.get("answer") or "")[:4000],
                                  "answered": _answered(r)}
        while len(_STANDARD_ANSWERS) > _RECENT_MAX:
            _STANDARD_ANSWERS.popitem(last=False)


def _adds_detail(std: dict | None, deep: dict) -> bool:
    """Strict: a new fact (number, code, page), more steps, or a sentence
    mostly made of words the standard answer did not use. Rewording is not."""
    if not _answered(deep):
        return False
    if not std or not std["answered"]:
        return True
    s, d = std["answer"].lower(), deep["answer"].lower()
    if set(_FACT.findall(d)) - set(_FACT.findall(s)):
        return True
    if len(_STEP.findall(d)) > len(_STEP.findall(s)):
        return True
    known = set(_WORD.findall(s))
    for sent in re.split(r"(?<=[.!?])\s+|\n+", d):
        words = set(_WORD.findall(sent))
        if len(words) >= 4 and 2 * len(words - known) > len(words):
            return True
    return False


def _save_vote(request_id: str, row: dict) -> None:
    """One row per answer, so a changed mind replaces the first vote."""
    with _feedback_lock:
        data = jsonstore.load(_FEEDBACK_PATH, {}, label="widget feedback")
        if not isinstance(data, dict):
            data = {}
        data[request_id] = row
        jsonstore.save(_FEEDBACK_PATH, data, label="widget feedback")


def prune_before(cutoff) -> int:
    """D-15: drop votes (question, answer, note) older than `cutoff`, an
    aware datetime. Returns how many went."""
    from datetime import timezone
    key = cutoff.astimezone(timezone.utc).isoformat()
    with _feedback_lock:
        data = jsonstore.load(_FEEDBACK_PATH, {}, label="widget feedback")
        if not isinstance(data, dict):
            return 0
        kept = {k: v for k, v in data.items()
                if isinstance(v, dict) and (v.get("created_at") or "") >= key}
        if len(kept) < len(data):
            jsonstore.save(_FEEDBACK_PATH, kept, label="widget feedback")
        return len(data) - len(kept)


def erase_sessions(session_ids) -> int:
    """Erasure on request: drop the votes of these conversations, and the
    answers held in memory for voting on. Returns how many votes went."""
    ids = {s for s in session_ids if s}
    if not ids:
        return 0
    with _recent_lock:
        for rid in [k for k, v in _RECENT_ANSWERS.items() if v.get("session_id") in ids]:
            del _RECENT_ANSWERS[rid]
    with _feedback_lock:
        data = jsonstore.load(_FEEDBACK_PATH, {}, label="widget feedback")
        if not isinstance(data, dict):
            return 0
        kept = {k: v for k, v in data.items()
                if not (isinstance(v, dict) and v.get("session_id") in ids)}
        if len(kept) < len(data):
            jsonstore.save(_FEEDBACK_PATH, kept, label="widget feedback")
        return len(data) - len(kept)


def delete_token(session_id: str) -> str:
    """D-20261104-08: proof that the caller is the browser that started this
    chat: /widget/forget and /widget/rating (D-20261106-11) both require it.
    An HMAC of the id under the server's session secret, handed out by
    /widget/chat, or by an ask that started a fresh chat (D-20261108-01),
    only for an id the server itself chose, so the id alone (which a
    visitor quotes to support) deletes or rates nothing. '' when no secret is set;
    /widget/forget then refuses every visitor delete."""
    import keystore
    secret = keystore.get_session_secret()
    if not (secret and session_id):
        return ""
    return hmac.new(secret.encode(), b"widget-forget|" + session_id.encode(),
                    hashlib.sha256).hexdigest()


def _own_chat(payload, request):
    """D-20261108-01: the ask routes act on a chat id only with the token
    /widget/chat gave for it, the proof /widget/forget and /widget/rating
    already ask for. An id without it (quoted, guessed, or from an old cached
    widget) gets a fresh server-chosen chat instead of an error, so it adds
    nothing to that chat and spends none of its caps. Kept as named: no id;
    a comparison part (its id derives from an already checked one); no
    SESSION_SECRET (no chat can carry a proof then); a test-prefixed id from
    this box or the LAN (run_live --path widget, D-20261008-14's rule)."""
    sid = payload.session_id
    if not sid or stepwise.PART.get():
        return payload
    expected = delete_token(sid)
    local = is_local_caller(request)
    pipeline_trace.set_local_caller(local)
    if (not expected
            or hmac.compare_digest(expected.encode(), (payload.delete_token or "").encode())
            or (local and pipeline_trace.origin_for(sid, "widget") != "widget")):
        return payload
    fresh = str(uuid.uuid4())
    logger.info("widget ask: a chat id came without its token; started a fresh chat")
    return payload.model_copy(update={"session_id": fresh,
                                      "delete_token": delete_token(fresh)})


# 8.11: what the visitor is told while the pipeline runs. A stage is marked
# when it FINISHES, so each one names the step that starts next. Stages not
# listed say nothing new and are not sent.
PROGRESS_LINES = {
    "condense":   "Searching the manuals",
    "retrieve":   "Reading the pages that matched",
    "procedures": "Writing the answer",
    "generate":   "Checking the answer against the manual",
    "escalate":   "Trying our backup service",
    "retry":      "Rewriting the answer and checking it again",
    "widen":      "Widening the search to the whole manual",
}


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

    # Distinct ingested sources per product/category, from the shared
    # inventory (docindex) -- the same numbers the admin /catalog shows.
    try:
        from docindex import doc_counts
        prod_counts, cat_counts = doc_counts()
    except Exception as e:
        logger.warning(f"widget catalog doc_count failed (non-fatal): {e}")
        prod_counts, cat_counts = {}, {}

    cats = []
    for c in (cat.get("categories") or []):
        prods = [{"key": p["key"], "name": p["name"],
                  "doc_count": prod_counts.get(p["key"], 0),
                  # Its date only, for the picker's "(obsolete)" label.
                  **({"obsolete": {"date": p["obsolete"].get("date", "")}}
                     if p.get("obsolete") else {})}
                 for p in (c.get("products") or [])]
        # A range's shared-documents entry is a bucket, not something a
        # visitor owns (catalog.is_shared_product); its documents already
        # answer for every product in the range.
        prods = [p for p in prods if p["doc_count"] > 0
                 and not catalog_mod.is_shared_product(p["key"])]
        ccount = cat_counts.get(c["key"], 0)
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
def widget_faq(product: str | None = None, limit: int = 4,
              language: str | None = None):
    """Curated questions for a product, shown as starting suggestions.

    Only entries with a human-reviewed answer are returned - an unanswered
    question offered as a suggestion leads straight to a dead end. Answers
    are included so tapping one can render instantly without a round trip.
    """
    try:
        import faq_store
        import catalog
        # An obsolete product's answers are blocked: suggesting its FAQ
        # would answer what the chat itself declines to.
        if product and product in catalog.blocked_products():
            return {"faq": []}
        # No question text to run the no-LLM detector on here (this is a
        # GET with no visitor input), so only the widget's own explicit
        # hint is honoured, and only once the feature is actually on --
        # same gate as the ask path. Always resolves to a CONCRETE code
        # (never None/"no filter"): starter suggestions must be one
        # language, not English and translations mixed together.
        lang = "en"
        if quota.multilingual_faq_enabled():
            code = _normalize_lang(language)
            if code and (code == "en" or code in quota.faq_enabled_languages()):
                lang = code
        # display_only: on guest chat this list IS the interface, so a
        # harvested table caption ("Operation: Temperature, Humidity") shown
        # as a suggested question is worse than one fewer suggestion. Those
        # entries stay fully searchable -- see faq_store.is_displayable.
        items = [f for f in faq_store.list_for_product(product,
                                                       display_only=True,
                                                       language=lang)
                 if (f.get("answer") or "").strip()]
    except Exception as e:
        logger.error(f"widget faq failed: {e}")
        return {"faq": []}
    return {"faq": [{"id": f["id"], "question": f["question"],
                     "answer": f["answer"], "edited": bool(f.get("edited"))}
                    for f in items[:max(1, min(limit, 10))]]}


def _public_sources(sources) -> list:
    """Strip internals the public has no business seeing - chunk ids,
    retrieval scores, model and provider names, timings."""
    return [{
        "source": s.get("source"),
        "page_label": s.get("page_label"),
        # Page numbers as ints, so the widget can open the PDF at the first one (D-20261010-14). Same numbers page_label already prints.
        "pages": s.get("pages") or [],
        "snippet": s.get("snippet"),
        "download_url": s.get("download_url"),
        # Crops from the cited pages: url, page, caption. Already public
        # in form -- no chunk ids, no scores.
        "figures": s.get("figures") or [],
    } for s in (sources or [])]


def _public_family_options(opts) -> list:
    out = []
    for o in (opts or [])[:8]:
        pic = o.get("picture") or ""
        out.append({
            "label": str(o.get("label") or "")[:80],
            "features": str(o.get("features") or "")[:600],
            "note": str(o.get("note") or "")[:300],
            "picture": pic if isinstance(pic, str) and pic.startswith("/figure/") else None,
            "send": str(o.get("send") or "")[:600],
        })
    return out
def _public_product_options(opts) -> list[dict]:
    """Catalogue keys and their display names, at most six, nothing else."""
    return [{"key": str(o["key"])[:80], "label": str(o.get("label") or o["key"])[:80]}
            for o in (opts or [])[:6] if isinstance(o, dict) and o.get("key")]


def _public_more_context(mc) -> dict | None:
    """The follow-up offer, minus the internals.

    chunk_ids go, for the same reason _public_sources drops them; the
    passages stay, because they are the document text the offer exists to
    show and the widget has no id-based way to fetch it.
    """
    if not isinstance(mc, dict):
        return None
    return {
        "kind": mc.get("kind"),
        "label": mc.get("label"),
        "support": bool(mc.get("support")),
        "document": mc.get("document"),
        "passages": mc.get("passages") or [],
    }


_LANG_CODE_RE = re.compile(r"^[a-z]{2}$")


def _normalize_lang(code: str | None) -> str:
    c = (code or "").strip().lower().split("-")[0]
    return c if _LANG_CODE_RE.match(c) else ""


def _resolve_anon_language(payload_language: str | None, question: str) -> str:
    """Which FAQ-language pool an anonymous question should match against.

    "en" (the default, always allowed) unless the feature is switched on
    AND the visitor's language -- from the widget's own hint, or failing
    that a local no-LLM guess -- is one the operator has enabled. A guess
    that doesn't match an enabled language falls back to "en" rather than
    matching nothing: the pool is scoped, not the visitor turned away.
    """
    if not quota.multilingual_faq_enabled():
        return "en"
    code = _normalize_lang(payload_language)
    if not code:
        import language
        code = language.detect_language_no_llm(question) or "en"
    return code if (code == "en" or code in quota.faq_enabled_languages()) else "en"


def _faq_turn(payload, *args, **kwargs) -> dict:
    """_faq_response for a guest turn, logged. These returns never reach
    query(), so no trace is open and nothing used to record them; the row
    carries M2's field names (session_id, origin, outcome) so the guest
    funnel reads from logs.jsonl like every other turn."""
    resp = _faq_response(*args, **kwargs)
    try:
        import pipeline_trace as ptrace
        from logger import log_interaction
        outcome = ("clarify" if resp["needs_clarification"] else
                   ("faq.picked" if payload.faq_id else "faq.answer")
                   if resp["from_faq"] else "refusal")
        tok = ptrace.start()
        try:
            ptrace.set_meta(surface="widget", session_id=payload.session_id,
                            origin=ptrace.origin_for(payload.session_id, "widget"))
            ptrace.mark(outcome)
            log_interaction(payload.q, resp["answer"], role="faq_only")
        finally:
            ptrace.reset(tok)
    except Exception as exc:
        logger.warning(f"guest FAQ turn not logged: {exc}")
    return resp


def _faq_response(answer: str, caller: dict, matched: str | None = None,
                  candidates: list | None = None, clarify: bool = False,
                  needs_sign_in: bool = False, faq_language: str = "en",
                  reviewed: bool = False) -> dict:
    return {
        "answer": answer,
        "sources": [],
        "from_faq": not needs_sign_in and not clarify,
        # 9.6: the widget shows "Reviewed answer" only when this is true.
        "faq_reviewed": reviewed,
        "faq_matched_question": matched,
        "faq_candidates": candidates,
        # A curated FAQ answer ran no retrieval, so there is no spare
        # material to offer and claiming otherwise would be a lie. Support is
        # the honest offer -- but only once an answer has actually been
        # given: a clarify is a live conversation and a sign-in prompt is not
        # an answer, so neither is a dead end needing an escape hatch.
        "more_context": None if (clarify or needs_sign_in) else {
            "kind": "support", "support": True, "document": None,
            "passages": [], "label": "Contact support for more information",
        },
        "needs_clarification": clarify,
        # Always present so a client can read it unconditionally. The FAQ
        # path disambiguates through faq_candidates, which carry ids; these
        # are the free-text options the generation path builds, and there
        # are none here.
        "clarification_options": [],
        "product_options": [],
        "suggested_replies": [],
        "needs_sign_in": needs_sign_in,
        "flagged": False,
        "effort": "faq_only",
        "quota": quota.status(caller),
        "sign_in_url": os.getenv("WIDGET_SIGN_IN_URL", "") if needs_sign_in else "",
        # Which language pool actually answered -- transparency for anyone
        # debugging a "wrong language" report, not read by the widget itself.
        "faq_language": faq_language,
    }


class DraftRequest(BaseModel):
    kind: str = Field(..., max_length=MAX_WORD_CHARS)  # "sales" | "support"
    visitor_id: str | None = Field(None, max_length=MAX_ID_CHARS)
    product: str | None = Field(None, max_length=MAX_ID_CHARS)
    # What the visitor typed themselves, when they chose to write their own
    # description rather than have the chat summarised.
    notes: str | None = Field(default=None, max_length=2000)
    # Whether to draft from the conversation or from `notes`.
    source: str = Field("chat", max_length=MAX_WORD_CHARS)  # "chat" | "written"
    # The conversation so far, sent by the widget — the same shape and the
    # same trust level as the transcript /widget/lead already accepts.
    # Bounded here because prompt cost scales with it, and because there is
    # no legitimate 200-turn support chat.
    transcript: Transcript | None = None


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

        # The console switch, or the env the site-bot instance sets: the
        # env alone left drafting shut after the console opened /ask.
        anon_ok = quota.anon_llm_enabled() or os.getenv(
            "WIDGET_AI_DRAFT_ANONYMOUS", "").strip().lower() in ("1", "true", "yes")
        if draft_enquiry is None or (tier == "anonymous" and not anon_ok):
            return {"draft": assembled, "written_by": "assembled",
                    "quota": quota.status(caller)}

        gate = quota.check(caller, 1, reserve=True)
        if not gate["allowed"]:
            # Not a 429: the visitor can still submit the assembled version,
            # and blocking a support request because a drafting allowance ran
            # out would be an absurd place to stop someone.
            return {"draft": assembled, "written_by": "assembled",
                    "quota": quota.status(caller)}

        written = ""
        try:
            written = await draft_enquiry(
                kind=payload.kind, product=payload.product or "",
                notes=notes, transcript=transcript)
        except Exception as e:
            logger.warning(f"/widget/draft_enquiry: generation failed ({e})")
        finally:
            if not (written or "").strip():
                quota.consume(caller, -1, win=gate["window"])  # nothing written: give it back

        if not (written or "").strip():
            return {"draft": assembled, "written_by": "assembled",
                    "quota": quota.status(caller)}
        return {"draft": written.strip()[:4000], "written_by": "model",
                "quota": quota.status(caller)}

    @router.post("/ask")
    async def widget_ask(payload: AskRequest, request: Request):
        asked = payload.session_id
        payload = _own_chat(payload, request)
        result = await _widget_ask(payload, request)
        if payload.session_id != asked:
            # The fresh chat's id and token, so the widget carries on in it.
            result = result | {"session_id": payload.session_id,
                               "delete_token": payload.delete_token}
        return result

    async def _widget_ask(payload: AskRequest, request: Request):
        # D-20261008-14: a test session prefix counts only from the box or the
        # LAN. Set in this request's context; run_in_threadpool copies it into
        # the pipeline, and /ask/stream runs this handler in its own copy.
        pipeline_trace.set_local_caller(is_local_caller(request))
        caller = _caller(request, payload.visitor_id)
        tier = caller["tier"]
        level, spec = quota.resolve_effort(payload.effort, tier)
        sign_in = os.getenv("WIDGET_SIGN_IN_URL", "")

        # ── Per-session caps ─────────────────────────────────────────
        # Checked before anything else: if this conversation has used up its
        # allowance there is no point resolving effort or touching the FAQ.
        # A 429 with a distinct reason, so the widget can say "this chat has
        # reached its limit, start a new one" rather than the daily wording.
        # This handler is `async`, so anything that blocks here blocks EVERY
        # other request on the event loop: the quota sqlite calls (a new
        # connection each), and the FAQ lookup, which embeds the question
        # and can rebuild the FAQ vector cache after an edit. Threadpool.
        sess = await run_in_threadpool(quota.session_check, payload.session_id)
        if not sess["allowed"]:
            raise HTTPException(status_code=429, detail={
                "error": "quota_exceeded",
                "reason": sess["reason"],
                "tier": tier,
                "limit": (sess["questions_limit"] if sess["reason"] == "session_questions"
                          else sess["tokens_limit"]),
                "reset_at": sess["reset_at"],
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
        # 9.4: a guest who reaches the site-wide daily cap is served exactly
        # as if the switch were off -- never a 429 for the site's own spend.
        guest_capped = (tier == "anonymous" and quota.anon_llm_enabled()
                        and (await run_in_threadpool(
                            quota.check, caller, spec["credits"]))["reason"]
                        == "global_quota")
        if tier == "anonymous" and (not quota.anon_llm_enabled() or guest_capped):
            gate = await run_in_threadpool(quota.check_faq_lookup, caller)
            if not gate["allowed"]:
                raise HTTPException(status_code=429, detail={
                    "error": "quota_exceeded",
                    "reason": "faq_quota",
                    "tier": tier,
                    "reset_at": gate["reset_at"],
                    "sign_in_url": sign_in,
                    "message": "You have reached today's limit for FAQ lookups.",
                })

            await run_in_threadpool(quota.consume_faq_lookup, caller)

            # Selecting a specific curated question is served by id -- the
            # id already came from a language-scoped suggest_candidates call
            # in an earlier turn, so no language re-check is needed here.
            if payload.faq_id:
                entry = faq_store.get_by_id(payload.faq_id)
                if entry:
                    return _faq_turn(payload, 
                        entry["answer"], caller, matched=entry["question"],
                        faq_language=faq_store.norm_lang(entry.get("language")) or "en",
                        reviewed=faq_store.is_reviewed(entry))
                raise HTTPException(status_code=404, detail={
                    "error": "not_found", "message": "That answer is no longer available."})

            lang = _resolve_anon_language(payload.language, payload.q)
            faq = await run_in_threadpool(
                faq_store.suggest_candidates, payload.q,
                payload.product or payload.category, True, lang)

            if faq["mode"] == "answer":
                return _faq_turn(payload, faq["entry"]["answer"], caller,
                                     matched=faq["entry"]["question"],
                                     faq_language=lang,
                                     reviewed=faq_store.is_reviewed(faq["entry"]))

            # "Can I have the MyCheckr manual?" -- the file is member-only
            # (/source_file is token gated), so say that plainly rather than
            # the generic "no reviewed answer", which reads as "we have no
            # manual". A curated FAQ about manuals still wins, above.
            import doc_request
            if doc_request.document_request(payload.q):
                return _faq_turn(payload, 
                    "Product manuals and documents are available to account "
                    "holders. Sign in and I can give you the download link.",
                    caller, needs_sign_in=True, faq_language=lang)

            if faq["mode"] == "disambiguate":
                return _faq_turn(payload, 
                    "These FAQs match your query - please select the one you meant:",
                    caller, candidates=faq["candidates"], clarify=True,
                    faq_language=lang)

            # Nothing curated covers it in this language. Not a fall-through
            # to the English pool: scoring non-English text against
            # English-worded entries is exactly the "same topic, wrong
            # answer" failure suggest_candidates's own design exists to
            # prevent. record_gap (inside suggest_candidates) already
            # captured this as language-tagged demand.
            return _faq_turn(payload, 
                "I don't have a reviewed answer for that yet. Sign in to your "
                "account and I can search the full product documentation for you.",
                caller, needs_sign_in=True, faq_language=lang)

        # ── Member / staff: full pipeline, charged in credits ─────────
        gate = quota.check(caller, spec["credits"], reserve=True)
        if not gate["allowed"]:
            raise HTTPException(status_code=429, detail={
                "error": "quota_exceeded",
                "reason": gate["reason"],
                "tier": tier,
                "limit": gate["limit"],
                "remaining": gate["remaining"],
                "reset_at": gate["reset_at"],
                "message": ("The assistant has reached its limit for today. "
                            "Please try again tomorrow, or contact our team."
                            if gate["reason"] == "global_quota" else
                            "You have used your allowance for today. It resets in 24 hours. "
                            "For help with your question, contact our team."),
            })

        reserved, settled = spec["credits"], False
        dkey = _deep_key(caller, payload)
        try:
            claim = (await run_in_threadpool(quota.deep_claim, dkey, caller)
                     if level == "deep" else "ok")
            if claim == "capped":
                # Today's free deep runs are used: refused before the pipeline.
                raise HTTPException(status_code=429, detail={
                    "error": "quota_exceeded",
                    "reason": "deep_daily",
                    "tier": tier,
                    "limit": quota.free_deep_limit(),
                    "remaining": gate["remaining"],
                    "reset_at": gate["reset_at"],
                    "message": ("You have used today's deeper searches. "
                                "Normal questions still work."),
                })
            deep_again = claim == "again"

            started = time.time()
            try:
                if deep_again:
                    # Second press refused server-side: no pipeline, 0 credits.
                    result = {"answer": NO_MORE_DETAILS, "role": "no_more_details",
                              "offer_support": True}
                else:
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
                if level == "deep" and not deep_again:
                    await run_in_threadpool(quota.deep_release, dkey, caller)
                raise
            except Exception as e:
                if level == "deep" and not deep_again:
                    await run_in_threadpool(quota.deep_release, dkey, caller)
                # Don't charge for our own failure, and don't leak internals.
                logger.exception(f"widget ask failed: {e}")
                raise HTTPException(status_code=503, detail={
                    "error": "unavailable",
                    "message": "Support is temporarily unavailable. Please try again shortly.",
                })

            # Curated answers and disambiguation prompts involve no LLM call,
            # so they cost nothing - cheap for us, and it steers people towards
            # the reviewed answers.
            # ...and neither is our own outage: service_degraded means no
            # provider answered, and quota.py promises a failed request does not
            # burn the visitor's allowance.
            no_more = deep_again
            if level == "deep" and not deep_again:
                if result.get("service_degraded"):
                    await run_in_threadpool(quota.deep_release, dkey, caller)
                elif not _adds_detail(_STANDARD_ANSWERS.get(dkey), result):
                    result = {**result, "answer": NO_MORE_DETAILS, "sources": [],
                              "role": "no_more_details", "flagged": False,
                              "more_context": None, "suggested_replies": [],
                              "needs_clarification": False, "clarification_options": []}
                    no_more = True
            elif level != "deep" and not (result.get("from_faq") or result.get("faq_candidates")):
                _remember_standard(dkey, result)
            charged = 0 if (result.get("from_faq") or result.get("faq_candidates")
                            or result.get("service_degraded") or no_more) \
                      else spec["credits"]
            if level == "deep" and not deep_again and charged:
                await run_in_threadpool(quota.deep_paid, caller)
            state = quota.consume(caller, charged - reserved, win=gate["window"])
            settled = True
        finally:
            if not settled:
                # Our failure, a refused deep run or a cancelled request gives the
                # reservation back. Called directly, not via run_in_threadpool: an
                # await in a cancelled task's finally is itself cancelled.
                quota.consume(caller, -reserved, win=gate["window"])

        # Count this turn against the conversation's own caps. Only when it
        # actually cost something: a curated FAQ answer involves no model and
        # should not use up a session that is limited to bound model spend.
        # A refunded deep run counts as well.
        # A free deep run still ran the costliest pipeline, so the chat's caps
        # count it too (Security, D-20261030-09 round 2).
        if charged or (level == "deep" and no_more and not deep_again):
            quota.session_record(payload.session_id,
                                 tokens_used=int(result.get("total_tokens") or 0))

        # The shortcut replies (steps, "tell me more", handoff) return before
        # main.query mints a request_id; a vote still needs one to name.
        if not result.get("request_id"):
            result["request_id"] = uuid.uuid4().hex[:12]
        if not (result.get("from_faq") or result.get("faq_candidates")):
            _remember_answer(result.get("request_id"), payload.q,
                             payload.product or payload.category,
                             payload.session_id, result.get("answer"))

        logger.info(f"widget ask tier={tier} effort={level} charged={charged} "
                    f"remaining={state['remaining']} "
                    f"ms={round((time.time() - started) * 1000)}")

        return {
            "answer": result.get("answer"),
            "sources": _public_sources(result.get("sources")),
            "from_faq": bool(result.get("from_faq")),
            "faq_reviewed": bool(result.get("faq_reviewed")),
            "faq_candidates": result.get("faq_candidates"),
            # 8.1: /query decided these but this endpoint builds its own dict,
            # so they never reached the surface customers actually use. Most
            # visibly, a handoff turn's own "I'll hand this over to a person"
            # was followed by the widget's own "that is not something I have
            # in my knowledge base" -- the offer_support refusal copy -- since
            # the widget could not tell a handoff from a plain refusal without
            # `role`. `reason` and `service_degraded` are the same drop for
            # the console's/instrumentation's sake; `request_id` lets a
            # visitor's report be matched to a log line.
            "role": result.get("role"),
            "reason": result.get("reason"),
            "request_id": result.get("request_id"),
            "service_degraded": bool(result.get("service_degraded")),
            # v15.2: passed through so the widget can offer a human instead of
            # dead-ending on a refusal. It was already computed by /query but
            # this endpoint builds its own response dict, so it never reached
            # the one surface customers actually use.
            "offer_support": bool(result.get("offer_support")),
            # "Which product?" buttons. Dropped here until D-20261021-07 let a
            # visitor type before picking a product; an unscoped question can
            # now come back asking which product, and the widget shows the
            # choices as chips that set the product and ask again.
            "product_options": _public_product_options(result.get("product_options")),
            # Passed through for the same reason offer_support had to be: this
            # endpoint builds its own dict, so anything /query adds stops at
            # the console unless it is named here -- and the widget is the
            # surface customers actually use. chunk_ids are dropped in line
            # with _public_sources; the passages carry the text instead.
            "more_context": _public_more_context(result.get("more_context")),
            "needs_clarification": bool(result.get("needs_clarification")),
            # The options themselves, not just the boolean. They have been
            # built on every clarify turn since v12 and dropped here ever
            # since -- so the widget rendered "which did you mean?" as prose
            # and the visitor had to type the answer to a question we had
            # already enumerated. Strings only, and short ones: they are
            # product labels or the visitor's own earlier questions, so
            # nothing here is new information leaving the backend.
            "clarification_options": [
                str(o)[:120] for o in
                (result.get("clarification_options") or [])[:5]],
            # "Which SMART Hopper do you have?" (families.py): a card per
            # member with its picture and what tells it apart. Strings only,
            # bounded; the picture is a /figure URL like the sources' own.
            "family_options": _public_family_options(result.get("family_options")),
            # Replies the answer asked for ("Would you like them?"), shown as
            # buttons and as the composer's right-arrow suggestion.
            "suggested_replies": [
                str(o)[:80] for o in
                (result.get("suggested_replies") or [])[:3]],
            "flagged": bool(result.get("flagged")),
            "effort": level,
            "effort_downgraded": level != (payload.effort or "standard").lower(),
            "deep_done": quota.deep_done(dkey),
            "quota": quota.status(caller),
            "session": quota.session_check(payload.session_id),
        }


    @router.post("/feedback")
    def widget_feedback(payload: FeedbackRequest, request: Request):
        """PUBLIC -- a thumbs up or down on one answer.

        Only for an answer this process gave, in the same conversation, and
        capped by the same limiter as the contact form. A down vote also
        flags the question on the console's FAQs-from-customers page.
        """
        with _recent_lock:
            seen = _RECENT_ANSWERS.get(payload.request_id)
        if not seen or (seen["session_id"] or None) != (payload.session_id or None):
            raise HTTPException(status_code=404, detail="No such answer.")
        gate = quota.check_public_write("feedback", payload.visitor_id,
                                        _client_ip(request))
        if not gate["allowed"]:
            raise HTTPException(status_code=429,
                                detail="Too many votes today. Thank you, though.")
        from datetime import datetime, timezone
        _save_vote(payload.request_id, {
            "vote": payload.vote,
            "note": payload.note.strip(),
            "question": seen["q"],
            "answer": seen.get("answer", ""),
            "scope": seen["scope"],
            "session_id": payload.session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        if payload.vote == "down":
            faq_store.record_gap(seen["q"], seen["scope"],
                                 reason="visitor_flagged", flag=True)
            # 10.2: the reviewer needs to see what the visitor was shown.
            if seen.get("answer"):
                faq_store.stamp_gap({"flagged_answer": seen["answer"]},
                                    question=seen["q"])
        import logger as interaction_log
        interaction_log.log_feedback(payload.request_id, payload.vote,
                                     payload.session_id)
        return {"received": True}

    @router.post("/chat")
    def widget_chat():
        """PUBLIC -- start a chat. The server picks the id, so the delete token
        in this reply (which also rates the chat) only ever reaches the browser
        that started the chat. Nothing is stored."""
        sid = str(uuid.uuid4())
        return {"session_id": sid, "delete_token": delete_token(sid)}

    @router.post("/forget")
    def widget_forget(payload: ForgetRequest, request: Request):
        """A visitor deletes this conversation: its questions, answers,
        votes and rating, at once rather than after 90 days. Needs the delete token
        /widget/chat gave this browser; the bare id is refused (403), so a
        quoted id cannot delete anything."""
        gate = quota.check_public_write("forget", payload.visitor_id, _client_ip(request))
        if not gate["allowed"]:
            raise HTTPException(status_code=429, detail="Too many requests today.")
        expected = delete_token(payload.session_id)
        if not expected or not hmac.compare_digest(expected.encode(),
                                                   payload.delete_token.encode()):
            raise HTTPException(status_code=403, detail=(
                "This chat can only be deleted by our support team. "
                "Quote its id when you contact them."))
        import retention
        retention.erase(session_ids=[payload.session_id], source="visitor")
        return {"deleted": True}

    @router.post("/rating")
    def widget_rating(payload: RatingRequest, request: Request):
        """PUBLIC -- 1 to 5 stars for a whole conversation, asked when the
        visitor closes or restarts it, with an optional comment.

        Not tied to a remembered answer like /feedback: a guest's chat is
        curated FAQs only and never lands in _RECENT_ANSWERS, and guests are
        most visitors. A rating needs the delete token /widget/chat gave the
        browser that started this chat (403 without it), so a quoted or
        made-up id cannot replace a rating or push real ones out. The daily
        limit (quota "rating") and RATINGS_MAX stay as backstops.
        """
        expected = delete_token(payload.session_id)
        if not expected or not hmac.compare_digest(expected.encode(),
                                                   payload.delete_token.encode()):
            raise HTTPException(status_code=403,
                                detail="This chat cannot be rated.")
        gate = quota.check_public_write("rating", payload.visitor_id,
                                        _client_ip(request))
        if not gate["allowed"]:
            raise HTTPException(status_code=429,
                                detail="Too many ratings today. Thank you, though.")
        with _recent_lock:
            asked = [v for v in _RECENT_ANSWERS.values()
                     if v["session_id"] == payload.session_id]
        feedback_store.save_rating(payload.session_id, payload.stars,
                                   payload.comment,
                                   asked[-1]["scope"] if asked else None,
                                   [v["q"] for v in asked])
        return {"received": True}

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
        asked = payload.session_id
        payload = _own_chat(payload, request)
        new_chat = ({"session_id": payload.session_id, "delete_token": payload.delete_token}
                    if payload.session_id != asked else {})
        from fastapi.responses import StreamingResponse
        import json as _json

        def sse(event, data):
            return ("event: " + event + "\n"
                    + "data: " + _json.dumps(data) + "\n\n")

        async def answer_of(p, box, part=False):
            """widget_ask(p), yielding its progress lines while it works. The
            result lands in box["result"]; a failure yields an "error" event
            and leaves box empty. `part`: one product's step of a comparison
            (stepwise.PART)."""
            # The pipeline reports each stage through pipeline_trace's
            # listener; the answer runs as a task so its progress can be
            # sent while it is still working.
            import contextvars
            import pipeline_trace
            loop = asyncio.get_running_loop()
            progress: asyncio.Queue = asyncio.Queue()
            ctx = contextvars.copy_context()
            ctx.run(pipeline_trace.listen, loop, progress.put_nowait)
            if part:
                ctx.run(stepwise.PART.set, True)
            task = loop.create_task(widget_ask(p, request), context=ctx)
            sent = set()
            while not task.done():
                getter = asyncio.ensure_future(progress.get())
                await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
                if not getter.done():
                    getter.cancel()
                    continue
                stage = getter.result()
                line = PROGRESS_LINES.get(stage.get("id"))
                if line and line not in sent:
                    sent.add(line)
                    data = {"stage": line, "id": stage["id"], "at_ms": stage.get("at_ms")}
                    if stage.get("found"):
                        data["found"] = [{"source": str(f.get("source") or ""), "pages": [p for p in (f.get("pages") or []) if isinstance(p, int)]}
                                         for f in stage["found"][:3]]
                    yield sse("status", data)
            try:
                box["result"] = task.result()
            except HTTPException as e:
                # A part refused (its chat cap, the quota) is not the
                # visitor's answer: the normal one, already paid for, stands.
                if not part:
                    yield sse("error", {"status": e.status_code,
                                        "detail": e.detail})
            except Exception:
                # This one is the PUBLIC surface, so the rule matters most
                # here: an anonymous visitor gets a status and nothing else.
                # The traceback is already in the log via logger.exception.
                logger.exception("widget ask/stream failed")
                if not part:
                    yield sse("error", {"status": 503, "detail":
                                        "The assistant is temporarily "
                                        "unavailable. Please try again."})

        async def run():
            yield sse("status", {"stage": "Searching the documentation",
                                 "id": "entry", "at_ms": 0})

            box = {}
            async for line in answer_of(payload, box):
                yield line
            if "result" not in box:
                return
            result = box["result"]

            # v17.4 trial: a comparison of two products whose normal answer
            # asked "which product?" or gave one spec-table row for a named
            # attribute is answered again per product, each as its own part,
            # then compared (stepwise.py). Only where the model is reachable
            # and this chat is under its cap, so the split call is never made
            # for a question that would be refused.
            steps, gate = [], None
            caller = _caller(request, payload.visitor_id)
            if (stepwise.enabled() and payload.session_id
                    and not payload.faq_id and not payload.skip_faq
                    and payload.effort in (None, "standard")
                    and (caller["tier"] != "anonymous"
                         or quota.anon_llm_enabled())):
                keys = await run_in_threadpool(stepwise.products, payload.q)
                from main import _product_names
                names = keys + [_product_names().get(k, k) for k in keys]
                if (keys and stepwise.worth_it(payload.q, result, names)
                        and (await run_in_threadpool(
                            quota.session_check, payload.session_id))["allowed"]):
                    # D-20261109-11: the split and the comparison are two model
                    # calls made for this visitor: one standard credit, reserved
                    # before the split, given back below unless a comparison is
                    # shown. No credit left, no split.
                    gate = await run_in_threadpool(
                        quota.check, caller, quota.EFFORT["standard"]["credits"], True)
                    if gate["allowed"]:
                        steps = await run_in_threadpool(stepwise.plan, payload.q)
                    else:
                        gate = None
            shown = []   # (name, result) of each part sent, unflagged
            for i, (key, name, own_q) in enumerate(steps):
                yield sse("status", {"stage": f"Looking up the {name}",
                                     "id": "step", "at_ms": None})
                # Its own conversation id, so the part's question does not
                # become the turn a follow-up is read against.
                # ponytail: the parts count against their own chat caps, not
                # this one's; the daily credit quota sees the normal answer
                # and both parts (refunding the replaced one touches billing).
                part = payload.model_copy(update={
                    "q": own_q, "product": key, "skip_faq": True,
                    "session_id": stepwise.part_session(payload.session_id, i + 1)})
                box = {}
                async for line in answer_of(part, box, part=True):
                    yield line
                if "result" not in box:
                    break
                r = box["result"]
                # "Which ICU do you have?" is not this product's half of the
                # answer; the comparison below asks it if it still needs to.
                if not stepwise.is_answer(r):
                    continue
                yield sse("part", {"title": name,
                                   "answer": (r.get("answer") or "").strip(),
                                   "sources": r.get("sources"),
                                   "flagged": bool(r.get("flagged"))})
                if not r.get("flagged"):
                    shown.append((name, r))
            if steps:
                yield sse("status", {"stage": "Comparing the two",
                                     "id": "step", "at_ms": None})

            # Both halves answered: the comparison is written from them, so
            # it cannot contradict what the visitor just read. No sources of
            # its own (each part shows its pages) and no chat turn of its own;
            # its credit was reserved before the split. Anything less, and
            # the normal answer stands.
            if len(shown) == 2:
                text = await run_in_threadpool(
                    stepwise.compare, [s[2] for s in steps],
                    [(n, (r.get("answer") or "").strip()) for n, r in shown])
                if text:
                    import pipeline_trace
                    pipeline_trace.set_local_caller(is_local_caller(request))
                    origin = pipeline_trace.origin_for(payload.session_id, "widget")
                    rid = uuid.uuid4().hex[:12]
                    await run_in_threadpool(stepwise.record, payload.session_id,
                                            payload.q, text, rid, origin)
                    _remember_answer(rid, payload.q, payload.product or payload.category,
                                     payload.session_id, text)
                    last = shown[-1][1]
                    result = {"answer": "### Comparison\n\n" + text, "role": "comparison",
                              "request_id": rid, "sources": [], "flagged": False,
                              "session": last.get("session")}
            if gate:
                if result.get("role") != "comparison":
                    await run_in_threadpool(lambda: quota.consume(
                        caller, -quota.EFFORT["standard"]["credits"], win=gate["window"]))
                result = {**result, "quota": await run_in_threadpool(quota.status, caller)}

            answer = (result.get("answer") or "").strip()
            yield sse("meta", {k: result.get(k) for k in
                               ("sources", "from_faq", "faq_reviewed", "faq_candidates",
                                "request_id", "role", "reason",
                                "needs_sign_in", "sign_in_url",
                                "service_degraded", "more_context",
                                "offer_support", "needs_clarification",
                                "clarification_options", "family_options",
                                "suggested_replies",
                                "flagged", "quota", "session", "deep_done")}
                      | {"product_options": _public_product_options(result.get("product_options"))} | new_chat)

            # Whole sentences, not tokens: a sentence is the unit the
            # grounding gate verifies, so releasing anything smaller would
            # show text that has not been checked.
            # Lossless: the widget rebuilds the answer from these pieces, and
            # the old pattern dropped a leading "." (".NET") or a "?" after a
            # newline.
            _SENTENCE = re.compile(r"[^\n]*?(?:[.!?]+(?:\s+|$)|\n+|$)")
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
