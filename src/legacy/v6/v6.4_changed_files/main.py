import logging
import threading
import time

from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel

from db import get_collection, reset_collection, get_stats, delete_source, get_chunks_by_ids
from embeddings import _get_model as _get_embedding_model
from reranker import rerank, _get as _get_reranker_model
from structure import extract_structured_block
from logger import log_interaction
from router import route_model
from grounding import check_grounding, _get_nli_model
from llm import generate, generate_with_fallback, warmup_local_models, RETHINK_OPTIONS, condense_query
from memory import add_to_memory, clear_memory, get_history, get_last_query
from ingest import ingest_file
from retrieval_db import retrieve_from_db
from text_utils import (
    passes_retrieval_gate,
    retrieval_confidence_band,
    is_refusal,
    is_followup_turn,
    has_domain_vocabulary,
    is_template_leak,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

GROUNDING_THRESHOLD = 0.55

# Sigmoid-calibrated reranker score (0.5 = the model's own relevance
# boundary). Below this, the top chunk is judged irrelevant — refuse
# BEFORE generation rather than after, so out-of-domain queries don't
# trigger a 50s+ generation call that produces rambling output.
RETRIEVAL_GATE_THRESHOLD = 0.5

# Above this, treat retrieval as unambiguous even with a borderline score,
# as long as results aren't scattered across many sources (see
# text_utils.retrieval_confidence_band).
AMBIGUOUS_CEILING = 0.65

SNIPPET_LEN = 160

# session_id is required for correct multi-turn behaviour. Callers that
# omit it land in this shared bucket — fine for a one-off manual request,
# but it means unrelated callers can see each other's "previous query"
# during condensation. Every real client (app.py, test_queries.py)
# generates and sends its own id.
DEFAULT_SESSION_ID = "default"

APP_STATE = {
    "ready": False,
    "progress": 0,
    "message": "Starting",
    "error": None,
}
APP_STATE_LOCK = threading.Lock()


class QueryRequest(BaseModel):
    q: str
    session_id: str | None = None
    deepseek_api_key: str | None = None
    # "Rethink with a different model": when set, skips routing/fallback
    # and calls this exact (provider, model) directly.
    force_provider: str | None = None
    force_model: str | None = None
    # Scope retrieval to one previously-seen source (from a clickable
    # source link) — "ask more about this document".
    source_filter: str | None = None


class DeleteSourceRequest(BaseModel):
    source: str


class SourceChunksRequest(BaseModel):
    chunk_ids: list[str]


class ClearSessionRequest(BaseModel):
    session_id: str


def _set_app_state(*, ready=None, progress=None, message=None, error=None):
    with APP_STATE_LOCK:
        if ready is not None:
            APP_STATE["ready"] = ready
        if progress is not None:
            APP_STATE["progress"] = progress
        if message is not None:
            APP_STATE["message"] = message
        if error is not None:
            APP_STATE["error"] = error


def _warmup_stack():
    try:
        _set_app_state(progress=5, message="Initializing database")
        get_collection()

        _set_app_state(progress=20, message="Loading embeddings")
        _get_embedding_model()

        _set_app_state(progress=45, message="Loading reranker")
        _get_reranker_model()

        _set_app_state(progress=70, message="Loading grounding model")
        _get_nli_model()

        _set_app_state(progress=85, message="Warming local LLMs")
        warmup_local_models(["phi", "mistral"])

        _set_app_state(progress=100, message="Ready", ready=True, error=None)
        logger.info("System warmup complete")
    except Exception as e:
        logger.exception("Startup warmup failed")
        _set_app_state(ready=False, progress=100, message="Startup failed", error=str(e))


@app.on_event("startup")
def startup_event():
    thread = threading.Thread(target=_warmup_stack, daemon=True)
    thread.start()


@app.get("/status")
def status():
    with APP_STATE_LOCK:
        return dict(APP_STATE)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats")
def stats():
    return get_stats()


@app.get("/rethink_options")
def rethink_options():
    """Models available for the 'rethink with a different model' feature."""
    return {"options": [{"provider": p, "model": m} for p, m in RETHINK_OPTIONS]}


@app.post("/reset")
def reset():
    """Full reset: wipes the document collection AND every conversation
    session's memory."""
    reset_collection()
    clear_memory()
    return {"status": "reset"}


@app.post("/clear_session")
def clear_session(payload: ClearSessionRequest):
    """Clear one conversation's memory without touching the document
    collection — backs a "New conversation" button."""
    clear_memory(payload.session_id)
    return {"status": "cleared", "session_id": payload.session_id}


@app.post("/delete_source")
def remove_source(payload: DeleteSourceRequest):
    removed = delete_source(payload.source)
    clear_memory()
    return {"removed_chunks": removed, "source": payload.source}


@app.post("/source_chunks")
def source_chunks(payload: SourceChunksRequest):
    """Fetch full chunk text for the given chunk ids — backs the
    clickable "view source" feature in the UI."""
    return {"chunks": get_chunks_by_ids(payload.chunk_ids)}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    if not APP_STATE["ready"]:
        raise HTTPException(status_code=503, detail="System is still loading")

    content = await file.read()
    count = ingest_file(content, file.filename)

    if count == 0:
        return {"chunks_added": 0, "warning": "File already exists or no usable text found"}

    return {"chunks_added": count, "file": file.filename}


def _build_sources(results: list[dict]) -> list[dict]:
    """
    Build clickable source objects: one entry per unique source filename,
    with the chunk ids belonging to it (for /source_chunks lookup) and a
    short snippet from its best-scoring chunk.
    """
    by_source: dict[str, dict] = {}

    for r in results:
        src = r.get("source", "unknown")
        if src not in by_source:
            by_source[src] = {
                "source": src,
                "chunk_ids": [],
                "snippet": r["text"][:SNIPPET_LEN].strip() + ("…" if len(r["text"]) > SNIPPET_LEN else ""),
            }
        by_source[src]["chunk_ids"].append(r.get("id"))

    return list(by_source.values())


@app.post("/query")
def query(payload: QueryRequest):
    if not APP_STATE["ready"]:
        raise HTTPException(status_code=503, detail="System is still loading")

    q = payload.q
    session_id = payload.session_id or DEFAULT_SESSION_ID
    deepseek_api_key = payload.deepseek_api_key
    start_total = time.time()

    # ── Conversational query resolution (Rewrite-Retrieve-Read) ──
    # Resolves pronoun/ellipsis-dependent follow-ups ("give me that from
    # step 1") into a standalone query using THIS session's own history,
    # via a fast local LLM call rather than a surface-level heuristic.
    # See llm.condense_query / text_utils' "conversational query
    # condensation" section for the full rationale and references.
    #
    # `resolved_query` is used for everything downstream (retrieval,
    # routing, extraction, the final generation prompt) — by the time
    # we've worked out what the user actually means, that's the query
    # that matters everywhere. The RAW `q` is what gets stored back into
    # memory and logged, so future condensation prompts see the
    # conversation as the user actually typed it.
    history = get_history(session_id)
    resolved_query = condense_query(q, history)

    # ── Retrieval ────────────────────────────
    t1 = time.time()
    results = retrieve_from_db(resolved_query, top_k=10, source_filter=payload.source_filter)
    results = rerank(resolved_query, results, top_k=5)
    retrieval_time = time.time() - t1

    top_score = results[0].get("rerank_score", 0.0) if results else 0.0
    confidence = retrieval_confidence_band(results, RETRIEVAL_GATE_THRESHOLD, AMBIGUOUS_CEILING)

    # ── No relevant content at all ───────────
    if confidence == "none":
        total_time = time.time() - start_total

        # A query that referenced prior context (or got rewritten by the
        # condensation step) is a follow-up, not a fresh out-of-domain
        # question. Retrieval failing on it doesn't mean the topic is
        # outside the knowledge base — it usually means the rewrite/
        # retrieval pairing didn't land. Treating it the same as a
        # genuinely standalone miss (e.g. "capital of France") produces
        # the flat, conversation-breaking "I could not find that"
        # response the user is reporting. Ask for clarification instead;
        # standalone misses (no reference markers, no history) are
        # completely unaffected and still get the blunt rejection.
        is_followup = is_followup_turn(q, history, resolved_query)

        # A STANDALONE query (no history dependency) can still be too
        # vague to retrieve well while clearly being about something in
        # our domain — "explain why device registration might fail"
        # never says WHICH device, "post installation verification
        # installer sign off" never says which product's installation.
        # These aren't out-of-domain misses like "capital of France"
        # (zero domain vocabulary); they're underspecified ones. Ask
        # which device/product instead of flatly rejecting, and surface
        # whatever low-scoring candidate sources retrieval DID turn up
        # as a concrete hint rather than a generic "which one?".
        is_vague_in_domain = (not is_followup) and has_domain_vocabulary(q)

        if is_followup:
            # is_followup_turn requires non-empty history, so this is
            # always available here — referencing the actual prior topic
            # instead of a generic line is what makes this feel like a
            # continued conversation rather than a second flat rejection.
            last_topic = history[-1]["q"]
            answer = (
                f"I don't have more detail beyond what we already covered for "
                f'"{last_topic}" — could you tell me more concretely what you\'d '
                f"like me to check or expand on?"
            )
            role_out = "clarify"
            reason = "low_retrieval_confidence_followup"
            needs_clarification = True
        elif is_vague_in_domain:
            candidate_sources = sorted({r.get("source") for r in results[:4] if r.get("source")})
            hint = f" The closest matches I found were in: {', '.join(candidate_sources)}." if candidate_sources else ""
            answer = (
                "I'm not sure which specific device or product area you mean here "
                "— could you say which one you're asking about (e.g. the MyConnect "
                "Hub, MyCheckr, or MyCheckr Mini)?" + hint
            )
            role_out = "clarify"
            reason = "ambiguous_in_domain_query"
            needs_clarification = True
        else:
            answer = "I could not find that in the knowledge base."
            role_out = "rejected"
            reason = "low_retrieval_confidence"
            needs_clarification = False

        log_interaction(q, answer, role_out, "none", [], grounding_score=None, flagged=False)
        return {
            "answer": answer,
            "model": "none",
            "role": role_out,
            "needs_clarification": needs_clarification,
            "reason": reason,
            "retrieval_score": round(top_score, 4),
            "resolved_query": resolved_query if resolved_query != q else None,
            "sources": [],
            "response_time": round(total_time, 3),
        }

    # ── Ambiguous: ask a clarifying question instead of guessing ──
    if confidence == "ambiguous":
        candidate_sources = sorted({r.get("source") for r in results[:4] if r.get("source")})
        total_time = time.time() - start_total
        clarifying = (
            "I found a few different sections that could be relevant — "
            "could you clarify which part you mean? "
            f"Possible areas: {', '.join(candidate_sources)}."
        )
        log_interaction(q, clarifying, "clarify", "none", candidate_sources,
                        grounding_score=None, flagged=False)
        return {
            "answer": clarifying,
            "model": "none",
            "role": "clarify",
            "needs_clarification": True,
            "candidate_sources": candidate_sources,
            "resolved_query": resolved_query if resolved_query != q else None,
            "sources": _build_sources(results),
            "retrieval_score": round(top_score, 4),
            "response_time": round(total_time, 3),
        }

    # ── Routing ──────────────────────────────
    if payload.force_provider and payload.force_model:
        role = "rethink"
    else:
        role, (provider, model) = route_model(resolved_query)

    # ── Structured extraction shortcut ───────
    if role != "rethink":
        t2 = time.time()
        extracted = extract_structured_block(results[:5], query=resolved_query)
        extraction_time = time.time() - t2

        if extracted and role == "extract":
            total_time = time.time() - start_total
            sources = _build_sources(results)
            log_interaction(q, extracted, "extract", "structured",
                            [s["source"] for s in sources],
                            grounding_score=None, flagged=False)
            add_to_memory(session_id, q, extracted)
            return {
                "answer": extracted,
                "mode": "extracted",
                "model": "structured",
                "role": "extract",
                "provider": "local",
                "fallback_used": False,
                "resolved_query": resolved_query if resolved_query != q else None,
                "response_time": round(total_time, 3),
                "sources": sources,
            }
    else:
        extraction_time = 0.0

    # ── Context ──────────────────────────────
    # No separate memory-context injection here: resolved_query already
    # carries whatever context was needed from prior turns (that's the
    # whole point of the condensation step above), so the document
    # context retrieved against IT is what the model should answer from.
    top_chunks = results[:3]
    context = "\n\n".join(r["text"][:250] for r in top_chunks)

    prompt = f"""<context>
{context}
</context>

Using ONLY the information inside <context> above, answer the question below.
If the context does not contain enough information, respond with exactly:
"I could not find that in the knowledge base."
Do not use any knowledge from outside the context.

Question: {resolved_query}
Answer:"""

    # ── LLM ──────────────────────────────────
    t3 = time.time()
    if role == "rethink":
        output = generate(payload.force_provider, prompt, payload.force_model, deepseek_api_key)
        if not output:
            output = {"text": "", "model": payload.force_model, "provider": payload.force_provider}
        output["fallback_used"] = False
    else:
        output = generate_with_fallback(role, prompt, deepseek_api_key=deepseek_api_key)
    llm_time = time.time() - t3

    answer = output.get("text", "").strip() or "I could not generate a response."

    # ── Grounding check ──────────────────────
    refusal = is_refusal(answer)

    # Checked BEFORE the NLI grounding call: a known chat-template leak
    # (e.g. "...a chat between a curious user and an artificial
    # intelligence assistant...") makes no concrete factual claim, so
    # NLI entailment has nothing to contradict and can score it as
    # "grounded" — reproduced in production at 0.934, well above
    # threshold. is_template_leak catches this deterministically; the
    # semantic check is skipped entirely when it fires, same as for a
    # clean refusal.
    if is_template_leak(answer):
        is_grounded, grounding_score = False, 0.0
        flagged = True
        refusal = False
    elif refusal:
        is_grounded, grounding_score = True, None
        flagged = False
    else:
        is_grounded, grounding_score = check_grounding(
            answer, top_chunks, threshold=GROUNDING_THRESHOLD
        )
        flagged = not is_grounded

    # ── DeepSeek escalation on grounding failure ──
    if flagged and role != "rethink" and output.get("provider") == "local":
        logger.warning(f"Grounding score {grounding_score:.3f} — escalating to DeepSeek for: {resolved_query[:60]}")
        deepseek_result = generate("deepseek", prompt, "deepseek-chat", deepseek_api_key=deepseek_api_key)
        if deepseek_result and deepseek_result.get("text"):
            output = deepseek_result
            answer = output["text"].strip()
            is_grounded, grounding_score = check_grounding(
                answer, top_chunks, threshold=GROUNDING_THRESHOLD
            )
            flagged = not is_grounded

    # ── Memory + logging ──────────────────────
    add_to_memory(session_id, q, answer)
    sources = _build_sources(results)
    log_interaction(q, answer, role, output.get("model"),
                    [s["source"] for s in sources],
                    grounding_score=grounding_score, flagged=flagged)

    total_time = time.time() - start_total

    return {
        "answer": answer,
        "role": role,
        "model": output.get("model"),
        "provider": output.get("provider"),
        "fallback_used": output.get("fallback_used", False),
        "grounding_score": grounding_score,
        "flagged": flagged,
        "retrieval_score": round(top_score, 4),
        "resolved_query": resolved_query if resolved_query != q else None,
        "timing": {
            "retrieval_time": round(retrieval_time, 3),
            "extraction_time": round(extraction_time, 3),
            "llm_time": round(llm_time, 3),
            "total_time": round(total_time, 3),
        },
        "sources": sources,
    }
