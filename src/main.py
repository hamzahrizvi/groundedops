import logging
import time
import threading
import requests

from fastapi import FastAPI, UploadFile, File

from memory import add_to_memory, get_memory_context
from reranker import rerank
from structure import extract_structured_block
from llm import generate, generate_with_fallback
from router import route_model
from logger import log_interaction
from grounding import check_grounding
from ingest import ingest_file
from retrieval_db import retrieve_from_db
from db import reset_collection, get_stats

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

GROUNDING_THRESHOLD = 0.55
RERANK_SCORE_GATE   = 0.2      # minimum cross-encoder score to pass retrieval gate

def warmup_models():
    """Pre‑load models with different timeouts (phi fast, mistral slow)."""
    models = [("phi", 30), ("mistral", 120)]
    for model, timeout in models:
        try:
            print(f"[WARMUP] Loading {model}...")
            requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": model,
                    "prompt": "ping",
                    "stream": False,
                    "keep_alive": "10m"
                },
                timeout=timeout
            )
            print(f"[WARMUP] {model} ready")
        except Exception as e:
            print(f"[WARMUP] Failed to load {model}: {e}")


# ─────────────────────────────────────────────
# STARTUP EVENT – run warmup in background
# ─────────────────────────────────────────────
@app.on_event("startup")                     
def startup_event():
    thread = threading.Thread(target=warmup_models)
    thread.daemon = True
    thread.start()

# ─────────────────────────────────────────────
# UPLOAD
# ─────────────────────────────────────────────
@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    count   = ingest_file(content, file.filename)

    if count == 0:
        return {
            "chunks_added": 0,
            "warning": "File already exists or no usable text could be extracted.",
            "file": file.filename,
        }

    return {"chunks_added": count, "file": file.filename}


# ─────────────────────────────────────────────
# QUERY
# ─────────────────────────────────────────────
@app.post("/query")
def query(q: str):
    start_total = time.time()

    # ── Retrieval ────────────────────────────
    t1      = time.time()
    results = retrieve_from_db(q)
    results = rerank(q, results, top_k=5)
    retrieval_time = time.time() - t1

    # ── Retrieval quality gate ───────────────
    top_score = results[0].get("rerank_score", 0.0) if results else 0.0

    if not results or top_score < RERANK_SCORE_GATE:
        total_time = time.time() - start_total
        log_interaction(q, "I could not find that in the knowledge base.",
                        "none", "none", [], grounding_score=None, flagged=False)
        return {
            "answer":           "I could not find that in the knowledge base.",
            "model":            "none",
            "role":             "rejected",
            "reason":           "low_retrieval_confidence",
            "retrieval_score":  round(top_score, 4),
            "sources":          [],
            "response_time":    round(total_time, 3),
        }

    # ── Routing ──────────────────────────────
    role, (provider, model) = route_model(q)

    # ── Structured extraction shortcut ───────
    t2        = time.time()
    extracted = extract_structured_block(results[:3])
    extraction_time = time.time() - t2

    sources = list({r.get("source", "") for r in results})

    if extracted and role == "extract":
        total_time = time.time() - start_total
        log_interaction(q, extracted, "extract", "structured", sources,
                        grounding_score=None, flagged=False)
        return {
            "answer":        extracted,
            "mode":          "extracted",
            "model":         "structured",
            "role":          "extract",
            "provider":      "local",
            "fallback_used": False,
            "sources":       sources,
            "response_time": round(total_time, 3),
        }

    # ── Context + memory ─────────────────────
    top_chunks      = results[:3]
    context         = "\n\n".join(r["text"][:400] for r in top_chunks)
    memory_context  = get_memory_context()

    combined_context = (
        memory_context + "\n\n" + context
        if memory_context and len(q.split()) < 8
        else context
    )

    # ── Prompt ───────────────────────────────
    prompt = f"""<context>
{combined_context}
</context>

Using ONLY the information inside <context> above, answer the question below.
If the context does not contain enough information, respond with exactly:
"I could not find that in the knowledge base."
Do not use any knowledge from outside the context.

Question: {q}
Answer:"""

    # ── LLM with fallback ────────────────────
    t3     = time.time()
    output = generate_with_fallback(role, prompt)
    llm_time = time.time() - t3

    answer = output.get("text", "").strip() or "I could not generate a response."

    # ── Grounding check ──────────────────────
    # Skip NLI check if the model already refused — refusals always score low
    # against context and would be incorrectly flagged
    is_refusal = "could not find" in answer.lower() or "unable to generate" in answer.lower()

    if is_refusal:
        is_grounded, grounding_score = True, None
        flagged = False
    else:
        is_grounded, grounding_score = check_grounding(
            answer, top_chunks, threshold=GROUNDING_THRESHOLD
        )
        flagged = not is_grounded

    # ── DeepSeek escalation on grounding failure ──
    if flagged and output.get("provider") == "local":
        logger.warning(f"Grounding {grounding_score:.3f} → escalating to DeepSeek: {q[:60]}")
        deepseek_result = generate("deepseek", prompt, "deepseek-chat")
        if deepseek_result and "unable" not in deepseek_result["text"].lower():
            output = deepseek_result
            answer = output["text"].strip()
            is_grounded, grounding_score = check_grounding(
                answer, top_chunks, threshold=GROUNDING_THRESHOLD
            )
            flagged = not is_grounded

    # ── Memory + logging ─────────────────────
    add_to_memory(q, answer)
    log_interaction(q, answer, role, output.get("model"), sources,
                    grounding_score=grounding_score, flagged=flagged)

    total_time = time.time() - start_total

    return {
        "answer":          answer,
        "role":            role,
        "model":           output.get("model"),
        "provider":        output.get("provider"),
        "fallback_used":   output.get("fallback_used", False),
        "grounding_score": grounding_score,
        "flagged":         flagged,
        "sources":         sources,
        "timing": {
            "retrieval_time":  round(retrieval_time, 3),
            "extraction_time": round(extraction_time, 3),
            "llm_time":        round(llm_time, 3),
            "total_time":      round(total_time, 3),
        },
    }


# ─────────────────────────────────────────────
# SYSTEM
# ─────────────────────────────────────────────
@app.post("/reset")
def reset():
    reset_collection()
    return {"status": "reset", "message": "Knowledge base cleared."}


@app.get("/status")
def status():
    stats = get_stats()
    return {
        "status":       "ok",
        "total_chunks": stats["total_chunks"],
        "sources":      stats["sources"],
        "doc_count":    len(stats["sources"]),
    }