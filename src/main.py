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
from llm import generate, generate_with_fallback, warmup_local_models, RETHINK_OPTIONS
from memory import add_to_memory, get_memory_context, clear_memory, should_use_memory, get_last_query
from ingest import ingest_file
from retrieval_db import retrieve_from_db
from text_utils import (
    passes_retrieval_gate,
    retrieval_confidence_band,
    is_refusal,
    build_retrieval_query,
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

APP_STATE = {
    "ready": False,
    "progress": 0,
    "message": "Starting",
    "error": None,
}
APP_STATE_LOCK = threading.Lock()


class QueryRequest(BaseModel):
    q: str
    deepseek_api_key: str | None = None
    # "Rethink with a different model": when set, skips routing/fallback
    # and calls this exact (provider, model) directly. Retrieval still
    # runs fresh (the API is stateless), but the SAME prompt-construction
    # path is used so the answer is directly comparable to the original.
    force_provider: str | None = None
    force_model: str | None = None
    # Scope retrieval to one previously-seen source (from a clickable
    # source link) — "ask more about this document".
    source_filter: str | None = None


class DeleteSourceRequest(BaseModel):
    source: str


class SourceChunksRequest(BaseModel):
    chunk_ids: list[str]


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
    reset_collection()
    clear_memory()
    return {"status": "reset"}


@app.post("/delete_source")
def remove_source(payload: DeleteSourceRequest):
    removed = delete_source(payload.source)
    clear_memory()
    return {"removed_chunks": removed, "source": payload.source}


@app.post("/source_chunks")
def source_chunks(payload: SourceChunksRequest):
    """
    Fetch full chunk text for the given chunk ids — backs the clickable
    "view source" feature in the UI. The UI calls this with the chunk_ids
    returned alongside a query's `sources` field.
    """
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
    short snippet from its best-scoring chunk (for an inline preview
    without an extra round-trip).
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
    deepseek_api_key = payload.deepseek_api_key
    start_total = time.time()

    # ── Conversational query rewriting ───────
    # A short/pronoun-heavy follow-up ("give me that from step 1") has
    # almost no retrieval signal on its own. If memory holds a recent
    # query, fold it in for retrieval purposes only — the LLM prompt
    # below still gets the raw memory context separately, this is just
    # to give the search step something concrete to match against.
    retrieval_query = build_retrieval_query(q, get_last_query())

    # ── Retrieval ────────────────────────────
    t1 = time.time()
    results = retrieve_from_db(retrieval_query, top_k=10, source_filter=payload.source_filter)
    results = rerank(retrieval_query, results, top_k=5)
    retrieval_time = time.time() - t1

    top_score = results[0].get("rerank_score", 0.0) if results else 0.0
    confidence = retrieval_confidence_band(results, RETRIEVAL_GATE_THRESHOLD, AMBIGUOUS_CEILING)

    # ── No relevant content at all ───────────
    if confidence == "none":
        total_time = time.time() - start_total
        answer = "I could not find that in the knowledge base."
        log_interaction(q, answer, "none", "none", [], grounding_score=None, flagged=False)
        return {
            "answer": answer,
            "model": "none",
            "role": "rejected",
            "reason": "low_retrieval_confidence",
            "retrieval_score": round(top_score, 4),
            "sources": [],
            "response_time": round(total_time, 3),
        }

    # ── Ambiguous: ask a clarifying question instead of guessing ──
    # Borderline relevance score AND results scattered across several
    # distinct sources/sections — a sign the query plausibly matches more
    # than one topic in this corpus. Rather than silently picking one
    # (which is how Bug 1 happened), ask which the user means.
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
            "sources": _build_sources(results),
            "retrieval_score": round(top_score, 4),
            "response_time": round(total_time, 3),
        }

    # ── Routing ──────────────────────────────
    if payload.force_provider and payload.force_model:
        role = "rethink"
    else:
        role, (provider, model) = route_model(q)

    # ── Structured extraction shortcut ───────
    # Skipped on a forced rethink — if the user explicitly asked to
    # rethink with a specific model, they want a generated answer from
    # THAT model, not the deterministic structured-extraction shortcut.
    if role != "rethink":
        t2 = time.time()
        extracted = extract_structured_block(results[:5], query=q)
        extraction_time = time.time() - t2

        if extracted and role == "extract":
            total_time = time.time() - start_total
            sources = _build_sources(results)
            log_interaction(q, extracted, "extract", "structured",
                            [s["source"] for s in sources],
                            grounding_score=None, flagged=False)
            return {
                "answer": extracted,
                "mode": "extracted",
                "model": "structured",
                "role": "extract",
                "provider": "local",
                "fallback_used": False,
                "response_time": round(total_time, 3),
                "sources": sources,
            }
    else:
        extraction_time = 0.0

    # ── Context + memory ──────────────────────
    top_chunks = results[:3]
    context = "\n\n".join(r["text"][:250] for r in top_chunks)

    memory_context = get_memory_context()
    combined_context = (
        memory_context + "\n\n" + context
        if (memory_context and should_use_memory(q))
        else context
    )

    prompt = f"""<context>
{combined_context}
</context>

Using ONLY the information inside <context> above, answer the question below.
If the context does not contain enough information, respond with exactly:
"I could not find that in the knowledge base."
Do not use any knowledge from outside the context.

Question: {q}
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

    if refusal:
        is_grounded, grounding_score = True, None
        flagged = False
    else:
        is_grounded, grounding_score = check_grounding(
            answer, top_chunks, threshold=GROUNDING_THRESHOLD
        )
        flagged = not is_grounded

    # ── DeepSeek escalation on grounding failure ──
    # Skipped on a forced rethink — escalating past the model the user
    # explicitly chose would be confusing ("I asked for phi's answer, why
    # did I get DeepSeek's?").
    if flagged and role != "rethink" and output.get("provider") == "local":
        logger.warning(f"Grounding score {grounding_score:.3f} — escalating to DeepSeek for: {q[:60]}")
        deepseek_result = generate("deepseek", prompt, "deepseek-chat", deepseek_api_key=deepseek_api_key)
        if deepseek_result and deepseek_result.get("text"):
            output = deepseek_result
            answer = output["text"].strip()
            is_grounded, grounding_score = check_grounding(
                answer, top_chunks, threshold=GROUNDING_THRESHOLD
            )
            flagged = not is_grounded

    # ── Memory + logging ──────────────────────
    add_to_memory(q, answer)
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
        "timing": {
            "retrieval_time": round(retrieval_time, 3),
            "extraction_time": round(extraction_time, 3),
            "llm_time": round(llm_time, 3),
            "total_time": round(total_time, 3),
        },
        "sources": sources,
    }