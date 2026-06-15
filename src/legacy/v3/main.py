import logging
import threading
import time

from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel

from db import get_collection, reset_collection, get_stats
from embeddings import _get_model as _get_embedding_model
from reranker import rerank, _get as _get_reranker_model   # fixed import
from structure import extract_structured_block
from logger import log_interaction
from router import route_model
from grounding import check_grounding, _get_nli_model      # added check_grounding
from llm import generate, generate_with_fallback, warmup_local_models
from memory import add_to_memory, get_memory_context
from ingest import ingest_file
from retrieval_db import retrieve_from_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

GROUNDING_THRESHOLD = 0.55

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
        _get_reranker_model()   # now works

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

@app.post("/reset")
def reset():
    reset_collection()
    return {"status": "reset"}

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    if not APP_STATE["ready"]:
        raise HTTPException(status_code=503, detail="System is still loading")
    content = await file.read()
    count = ingest_file(content, file.filename)
    if count == 0:
        return {"chunks_added": 0, "warning": "File already exists or no usable text found"}
    return {"chunks_added": count, "file": file.filename}

@app.post("/query")
def query(payload: QueryRequest):
    if not APP_STATE["ready"]:
        raise HTTPException(status_code=503, detail="System is still loading")
    q = payload.q
    deepseek_api_key = payload.deepseek_api_key
    start_total = time.time()
    t1 = time.time()
    results = retrieve_from_db(q, top_k=10)
    results = rerank(q, results, top_k=5)   # rerank is imported
    retrieval_time = time.time() - t1
    if not results:
        total_time = time.time() - start_total
        log_interaction(q, "I could not find that in the knowledge base.", "none", "none", [], grounding_score=None, flagged=False)
        return {"answer": "I could not find that in the knowledge base.", "model": "none", "role": "rejected", "reason": "low_retrieval_confidence", "response_time": round(total_time, 3)}
    role, (provider, model) = route_model(q)
    t2 = time.time()
    extracted = extract_structured_block(results[:3])
    extraction_time = time.time() - t2
    if extracted and role == "extract":
        total_time = time.time() - start_total
        sources = list({r.get("source", "") for r in results})
        log_interaction(q, extracted, "extract", "structured", sources, grounding_score=None, flagged=False)
        return {"answer": extracted, "mode": "extracted", "model": "structured", "role": "extract", "provider": "local", "fallback_used": False, "response_time": round(total_time, 3), "sources": sources}
    top_chunks = results[:3]
    context = "\n\n".join(r["text"][:250] for r in top_chunks)
    memory_context = get_memory_context()
    combined_context = memory_context + "\n\n" + context if (memory_context and len(q.split()) < 8) else context
    prompt = f"""<context>
{combined_context}
</context>

Using ONLY the information inside <context> above, answer the question below.
If the context does not contain enough information, respond with exactly:
"I could not find that in the knowledge base."
Do not use any knowledge from outside the context.

Question: {q}
Answer:"""
    t3 = time.time()
    output = generate_with_fallback(role, prompt, deepseek_api_key=deepseek_api_key)
    llm_time = time.time() - t3
    answer = output.get("text", "").strip()
    if not answer:
        answer = "I could not generate a response."
    is_grounded, grounding_score = check_grounding(answer, top_chunks, threshold=GROUNDING_THRESHOLD)
    flagged = not is_grounded
    if flagged and "could not find" not in answer.lower() and output.get("provider") == "local":
        logger.warning(f"Grounding score {grounding_score:.3f} — escalating to DeepSeek for: {q[:60]}")
        deepseek_result = generate("deepseek", prompt, "deepseek-chat", deepseek_api_key=deepseek_api_key)
        if deepseek_result and deepseek_result.get("text"):
            output = deepseek_result
            answer = output["text"].strip()
            is_grounded, grounding_score = check_grounding(answer, top_chunks, threshold=GROUNDING_THRESHOLD)
            flagged = not is_grounded
    add_to_memory(q, answer)
    sources = list({r.get("source", "") for r in results})
    log_interaction(q, answer, role, output.get("model"), sources, grounding_score=grounding_score, flagged=flagged)
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