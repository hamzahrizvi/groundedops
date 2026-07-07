import logging
import os
import re
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
from runtime_config import get_settings, set_generation_mode, set_local_models_loaded, set_online_provider
from memory import add_to_memory, clear_memory, get_history, get_last_query
from ingest import ingest_file
from retrieval_db import retrieve_from_db
from text_utils import (
    passes_retrieval_gate,
    retrieval_confidence_band,
    is_refusal,
    is_followup_turn,
    has_domain_vocabulary,
    has_reference_markers,
    is_template_leak,
    build_clarification_options,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

GROUNDING_THRESHOLD = 0.55

RETRIEVAL_GATE_THRESHOLD = 0.35

AMBIGUOUS_CEILING = 0.65

CONTEXT_FLOOR_RATIO = float(os.getenv("CONTEXT_FLOOR_RATIO", "0.5"))

EXCLUDED_SOURCES = [
    s.strip().lower()
    for s in os.getenv("EXCLUDED_SOURCES", "icu_network_api").split(",")
    if s.strip()
]

SNIPPET_LEN = 160
NOT_FOUND_ANSWER = "I don't have the answer for that. Please contact support as above."


_BREADCRUMB_RE = re.compile(r"^\[[^\]\n]*\]\n")


_PREAMBLE_RE = re.compile(
    r"^(?:"
    r"based\s+(?:solely\s+)?(?:on|upon)\s+(?:the\s+)?(?:provided\s+|given\s+)?(?:context|information|documents?|knowledge\s+base)"
    r"|according\s+to\s+(?:the\s+)?(?:provided\s+|given\s+)?(?:context|information|documents?)"
    r"|from\s+(?:the\s+)?(?:provided\s+|given\s+)?context"
    r")\s*[,:.]?\s*",
    re.IGNORECASE,
)


def _strip_preamble(answer: str) -> str:
    stripped = _PREAMBLE_RE.sub("", answer.strip(), count=1)
    if stripped and stripped != answer.strip():
        return stripped[0].upper() + stripped[1:]
    return answer


def _lexically_supported(answer: str, chunks: list[dict]) -> bool:
    numbers = re.findall(r"\d+(?:\.\d+)?", answer)
    if not numbers:
        return False
    if answer.count(".") > 3 or len(answer) > 400:
        return False
    context = " ".join(c.get("text", "") for c in chunks)
    return all(n in context for n in set(numbers))


def _normalize_query(q: str) -> str:
    s = q.strip()
    s = re.sub(r"([?!.,])\1+", r"\1", s)
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        s = s.lower()
    return s


def _strip_breadcrumb(result: dict) -> dict:
    text = result.get("text", "")
    stripped = _BREADCRUMB_RE.sub("", text, count=1)
    if stripped == text:
        return result
    out = dict(result)
    out["text"] = stripped
    return out


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
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    force_provider: str | None = None
    force_model: str | None = None
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

        _set_app_state(progress=85, message="Local LLMs available (load via settings)")

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
    return {"options": [{"provider": p, "model": m} for p, m in RETHINK_OPTIONS]}


@app.post("/reset")
def reset():
    reset_collection()
    clear_memory()
    return {"status": "reset"}


@app.post("/clear_session")
def clear_session(payload: ClearSessionRequest):
    clear_memory(payload.session_id)
    return {"status": "cleared", "session_id": payload.session_id}


@app.post("/delete_source")
def remove_source(payload: DeleteSourceRequest):
    removed = delete_source(payload.source)
    clear_memory()
    return {"removed_chunks": removed, "source": payload.source}


@app.post("/source_chunks")
def source_chunks(payload: SourceChunksRequest):
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


@app.get("/settings")
def settings():
    return get_settings()


class ModeRequest(BaseModel):
    mode: str


@app.post("/settings/mode")
def set_mode(payload: ModeRequest):
    try:
        mode = set_generation_mode(payload.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Generation mode switched to: {mode}")
    return {"mode": mode, **get_settings()}


class ModelsRequest(BaseModel):
    models: list[str] | None = None


class ProviderRequest(BaseModel):
    provider: str


@app.post("/settings/online_provider")
def set_provider(payload: ProviderRequest):
    try:
        p = set_online_provider(payload.provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Online provider set to: {p}")
    return get_settings()


_PULL_STATE: dict = {}
_PULL_LOCK = threading.Lock()


def _ollama_base() -> str:
    from llm import OLLAMA_URL
    return OLLAMA_URL.replace("/api/generate", "")


@app.get("/models/status")
def models_status():
    import requests as _requests
    try:
        r = _requests.get(f"{_ollama_base()}/api/tags", timeout=5)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        installed = {m: any(n.split(":")[0] == m for n in names)
                     for m in ("mistral", "phi")}
        return {"ollama_up": True, "installed": installed, **get_settings()}
    except Exception as e:
        return {"ollama_up": False, "installed": {"mistral": False, "phi": False},
                "error": str(e), **get_settings()}


def _pull_worker(model: str):
    import json as _json
    import requests as _requests
    try:
        with _requests.post(f"{_ollama_base()}/api/pull",
                            json={"name": model}, stream=True, timeout=3600) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    d = _json.loads(line)
                except Exception:
                    continue
                total, done = d.get("total"), d.get("completed")
                pct = round(done / total * 100, 1) if total and done else None
                with _PULL_LOCK:
                    st = _PULL_STATE.setdefault(model, {})
                    st["status"] = d.get("status", "downloading")
                    if pct is not None:
                        st["pct"] = pct
                if d.get("status") == "success":
                    break
        with _PULL_LOCK:
            _PULL_STATE[model] = {"status": "success", "pct": 100.0, "done": True}
    except Exception as e:
        logger.warning(f"Model pull failed for {model}: {e}")
        with _PULL_LOCK:
            _PULL_STATE[model] = {"status": "error", "error": str(e), "done": True}


@app.post("/models/pull")
def models_pull(payload: ModelsRequest = None):
    models = (payload.models if payload and payload.models else ["phi", "mistral"])
    models = [m for m in models if m in ("phi", "mistral")]
    with _PULL_LOCK:
        for m in models:
            _PULL_STATE[m] = {"status": "starting", "pct": 0.0, "done": False}
    for m in models:
        threading.Thread(target=_pull_worker, args=(m,), daemon=True).start()
    return {"pulling": models}


@app.get("/models/pull_status")
def models_pull_status():
    with _PULL_LOCK:
        return dict(_PULL_STATE)


@app.post("/models/warmup")
def models_warmup(payload: ModelsRequest = None):
    models = (payload.models if payload and payload.models else ["phi", "mistral"])
    models = [m for m in models if m in ("phi", "mistral")]
    results = warmup_local_models(models)
    ok = all(results.values()) and bool(results)
    set_local_models_loaded(ok)
    return {"loaded": ok, "models": results}


@app.post("/models/unload")
def models_unload(payload: ModelsRequest = None):
    import requests as _requests
    from llm import OLLAMA_URL
    models = (payload.models if payload and payload.models else ["phi", "mistral"])
    models = [m for m in models if m in ("phi", "mistral")]
    results = {}
    for model in models:
        try:
            _requests.post(OLLAMA_URL,
                           json={"model": model, "prompt": "", "keep_alive": 0},
                           timeout=15)
            results[model] = True
        except Exception as e:
            logger.warning(f"Unload failed for {model}: {e}")
            results[model] = False
    if set(models) >= {"phi", "mistral"} and all(results.values()):
        set_local_models_loaded(False)
    return {"unloaded": all(results.values()), "models": results}


@app.post("/query")
def query(payload: QueryRequest):
    if not APP_STATE["ready"]:
        raise HTTPException(status_code=503, detail="System is still loading")

    q = payload.q
    session_id = payload.session_id or DEFAULT_SESSION_ID
    deepseek_api_key = payload.deepseek_api_key
    api_keys = {"deepseek": payload.deepseek_api_key,
                "openai": payload.openai_api_key,
                "anthropic": payload.anthropic_api_key}
    start_total = time.time()

    history = get_history(session_id)
    resolved_query = condense_query(_normalize_query(q), history)

    if (history and has_reference_markers(q)
            and resolved_query.strip().lower() == _normalize_query(q).strip().lower()):
        last_q = history[-1].get("q", "")
        if last_q:
            resolved_query = f"{last_q} — {_normalize_query(q)}"
            logger.info(f"Follow-up fallback combined query: {resolved_query[:80]}")

    t1 = time.time()
    results = retrieve_from_db(resolved_query, top_k=10, source_filter=payload.source_filter)
    if EXCLUDED_SOURCES and not payload.source_filter:
        results = [
            r for r in results
            if not any(ex in (r.get("source") or "").lower() for ex in EXCLUDED_SOURCES)
        ]
    results = rerank(resolved_query, results, top_k=5)
    retrieval_time = time.time() - t1

    top_score = results[0].get("rerank_score", 0.0) if results else 0.0
    confidence = retrieval_confidence_band(results, RETRIEVAL_GATE_THRESHOLD, AMBIGUOUS_CEILING)

    if confidence == "none":
        total_time = time.time() - start_total

        is_followup = is_followup_turn(q, history, resolved_query)
        is_vague_in_domain = (not is_followup) and has_domain_vocabulary(q)

        if is_followup:
            last_topic = history[-1]["q"]
            answer = (
                f"I don't have more detail beyond what we already covered for "
                f'"{last_topic}" — could you tell me more concretely what you\'d '
                f"like me to check or expand on?"
            )
            role_out = "clarify"
            reason = "low_retrieval_confidence_followup"
            needs_clarification = True
            clarification_options = build_clarification_options("followup", history, results)
        elif is_vague_in_domain:
            candidate_sources = sorted({r.get("source") for r in results[:4] if r.get("source")})
            hint = f" The closest matches I found were in: {', '.join(candidate_sources)}." if candidate_sources else ""
            answer = (
                "I'm not sure which specific device or product area you mean here "
                "— could you say which one you're asking about?" + hint
            )
            role_out = "clarify"
            reason = "ambiguous_in_domain_query"
            needs_clarification = True
            clarification_options = build_clarification_options("ambiguous_in_domain", history, results)
        else:
            answer = NOT_FOUND_ANSWER
            role_out = "rejected"
            reason = "low_retrieval_confidence"
            needs_clarification = False
            clarification_options = []

        log_interaction(q, answer, role_out, "none", [], grounding_score=None, flagged=False)
        return {
            "answer": answer,
            "response_time_ms": round(total_time * 1000),
            "model": "none",
            "role": role_out,
            "needs_clarification": needs_clarification,
            "clarification_options": clarification_options,
            "reason": reason,
            "retrieval_score": round(top_score, 4),
            "resolved_query": resolved_query if resolved_query != q else None,
            "sources": [],
            "response_time": round(total_time, 3),
        }

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
            "response_time_ms": round(total_time * 1000),
            "model": "none",
            "role": "clarify",
            "needs_clarification": True,
            "candidate_sources": candidate_sources,
            "resolved_query": resolved_query if resolved_query != q else None,
            "sources": _build_sources(results),
            "retrieval_score": round(top_score, 4),
            "response_time": round(total_time, 3),
        }

    if payload.force_provider and payload.force_model:
        role = "rethink"
    else:
        role, (provider, model) = route_model(resolved_query)

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
                "response_time_ms": round(total_time * 1000),
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

    _top = results[0].get("rerank_score", 0.0) if results else 0.0
    top_chunks = [
        _strip_breadcrumb(r) for r in results[:3]
        if r.get("rerank_score", 0.0) >= _top * CONTEXT_FLOOR_RATIO
    ]
    context = "\n\n".join(r["text"][:1200] for r in top_chunks)

    prompt = f"""<context>
{context}
</context>

Using ONLY the information inside <context> above, answer the question below.
Answer directly - do NOT begin with phrases like "Based on the context" or "Based solely on the provided context"; state the answer itself.
If the context contains multiple similar-looking facts serving different purposes (e.g. different credential sets for different actions), give ONLY the one matching the question's subject and briefly note what the other is for.
If the context does not contain enough information, respond with exactly:
"{NOT_FOUND_ANSWER}"
Do not use any knowledge from outside the context.

Question: {resolved_query}
Answer:"""

    t3 = time.time()
    if role == "rethink":
        output = generate(payload.force_provider, prompt, payload.force_model, deepseek_api_key, api_keys=api_keys)
        if not output:
            output = {"text": "", "model": payload.force_model, "provider": payload.force_provider}
        output["fallback_used"] = False
    else:
        output = generate_with_fallback(role, prompt, deepseek_api_key=deepseek_api_key, api_keys=api_keys)
    llm_time = time.time() - t3

    raw_text = output.get("text", "").strip()
    generation_failed = (output.get("model") == "none") or not raw_text
    answer = _strip_preamble(raw_text) if raw_text else "I could not generate a response."

    refusal = is_refusal(answer)
    template_leak = is_template_leak(answer)

    if generation_failed:
        is_grounded, grounding_score = False, None
        flagged = True
        refusal = False
    elif template_leak:
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
        if not is_grounded and _lexically_supported(answer, top_chunks):
            is_grounded = True
            logger.info("Grounding rescued by lexical containment "
                        f"(nli={grounding_score}) for: {resolved_query[:60]}")
        flagged = not is_grounded

    escalated = False
    if flagged and role != "rethink" and output.get("provider") in ("local", "none"):
        logger.warning(
            f"Flagged local answer (grounding={grounding_score}, "
            f"template_leak={template_leak}, failed={generation_failed}) — "
            f"escalating to DeepSeek for: {resolved_query[:60]}"
        )
        deepseek_result = generate("deepseek", prompt, "deepseek-chat", deepseek_api_key=deepseek_api_key)
        if deepseek_result and deepseek_result.get("text"):
            output = deepseek_result
            answer = _strip_preamble(output["text"].strip())
            escalated = True
            template_leak = is_template_leak(answer)
            if template_leak:
                is_grounded, grounding_score, flagged = False, 0.0, True
            else:
                is_grounded, grounding_score = check_grounding(
                    answer, top_chunks, threshold=GROUNDING_THRESHOLD
                )
                if not is_grounded and _lexically_supported(answer, top_chunks):
                    is_grounded = True
                    logger.info("Escalated answer rescued by lexical "
                                f"containment (nli={grounding_score})")
                flagged = not is_grounded

    if template_leak or generation_failed or flagged:
        answer = NOT_FOUND_ANSWER
        if template_leak or generation_failed:
            grounding_score = None
        flagged = True

    add_to_memory(session_id, q, answer)
    sources = _build_sources(results)
    log_interaction(q, answer, role, output.get("model"),
                    [s["source"] for s in sources],
                    grounding_score=grounding_score, flagged=flagged)

    total_time = time.time() - start_total

    return {
        "answer": answer,
        "response_time_ms": round(total_time * 1000),
        "role": role,
        "model": output.get("model"),
        "provider": output.get("provider"),
        "fallback_used": output.get("fallback_used", False),
        "escalated_to_deepseek": escalated,
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