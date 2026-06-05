import logging
import time

from fastapi import FastAPI, UploadFile, File

from memory import add_to_memory, get_memory_context
from embeddings import embed_texts
from reranker import rerank
from parsing import extract_text
from storage import save_file
from chunking import chunk_text
from retrieval import search, RETRIEVAL_THRESHOLD
from structure import extract_structured_block
from llm import generate, generate_with_fallback
from router import route_model
from logger import log_interaction
from grounding import check_grounding

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

CHUNKS: list[dict] = []

# Tunable thresholds
GROUNDING_THRESHOLD = 0.55      # Below this → flag (and optionally escalate)


# UPLOAD
@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    path = save_file(content, file.filename)

    text = extract_text(path)
    if not text or not text.strip():
        return {"chunks_added": 0, "warning": "No text could be extracted from this file."}

    chunks = chunk_text(text)
    texts = [c for c in chunks if isinstance(c, str) and c.strip()]
    if not texts:
        return {"chunks_added": 0, "warning": "File parsed but produced no usable chunks."}

    vectors = embed_texts(texts)

    for i, c in enumerate(texts):
        CHUNKS.append({
            "source": file.filename,
            "text": c,
            "embedding": vectors[i],
        })

    logger.info(f"Uploaded '{file.filename}': {len(texts)} chunks (total: {len(CHUNKS)})")
    return {"chunks_added": len(texts), "total_chunks": len(CHUNKS)}


# QUERY
@app.post("/query")
def query(q: str):
    start_total = time.time()

    #Retrieval
    t1 = time.time()
    results, top_score = search(q, CHUNKS)
    results = rerank(q, results, top_k=5)
    retrieval_time = time.time() - t1

    #Retrieval quality gate
    if not results or top_score < RETRIEVAL_THRESHOLD:
        total_time = time.time() - start_total
        log_interaction(q, "I could not find that in the knowledge base.",
                        "none", "none", [], grounding_score=None, flagged=False)
        return {
            "answer": "I could not find that in the knowledge base.",
            "model": "none",
            "role": "rejected",
            "reason": "low_retrieval_confidence",
            "retrieval_score": round(top_score, 6),
            "response_time": round(total_time, 3),
        }

    # Route
    role, (provider, model) = route_model(q)

    #extraction shortcut 
    t2 = time.time()
    extracted = extract_structured_block(results[:3])
    extraction_time = time.time() - t2

    if extracted and role == "extract":
        total_time = time.time() - start_total
        sources = list({r.get("source", "") for r in results})
        log_interaction(q, extracted, "extract", "structured", sources,
                        grounding_score=None, flagged=False)
        return {
            "answer": extracted,
            "mode": "extracted",
            "model": "structured",
            "role": "extract",
            "provider": "local",
            "fallback_used": False,
            "response_time": round(total_time, 3),
        }

    #Build context
    top_chunks = results[:3]
    context = "\n\n".join(r["text"][:400] for r in top_chunks)

    memory_context = get_memory_context()
    if memory_context and len(q.split()) < 8:
        combined_context = memory_context + "\n\n" + context
    else:
        combined_context = context

    #Prompt (hard-delimited, refusal instruction)
    prompt = f"""<context>
{combined_context}
</context>

Using ONLY the information inside <context> above, answer the question below.
If the context does not contain enough information, respond with exactly:
"I could not find that in the knowledge base."
Do not use any knowledge from outside the context.

Question: {q}
Answer:"""

    # LLM with fallback chain
    t3 = time.time()
    output = generate_with_fallback(role, prompt)
    llm_time = time.time() - t3

    answer = output.get("text", "").strip()
    if not answer:
        answer = "I could not generate a response."

    # Grounding check
    is_grounded, grounding_score = check_grounding(
        answer, top_chunks, threshold=GROUNDING_THRESHOLD
    )
    flagged = not is_grounded

    # DeepSeek escalation on grounding failure
    if (
        flagged
        and "could not find" not in answer.lower()
        and output.get("provider") == "local"
    ):
        logger.warning(
            f"Grounding score {grounding_score:.3f} — escalating to DeepSeek for: {q[:60]}"
        )
        deepseek_result = generate("deepseek", prompt, "deepseek-chat")
        if deepseek_result and "unable" not in deepseek_result["text"].lower():
            output = deepseek_result
            answer = output["text"].strip()
            is_grounded, grounding_score = check_grounding(
                answer, top_chunks, threshold=GROUNDING_THRESHOLD
            )
            flagged = not is_grounded

    # Memory update
    add_to_memory(q, answer)

    # Log        
    sources = list({r.get("source", "") for r in results})
    log_interaction(
        q, answer, role, output.get("model"), sources,
        grounding_score=grounding_score, flagged=flagged,
    )

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
    }