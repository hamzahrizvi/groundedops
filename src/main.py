import time 
from fastapi import FastAPI, UploadFile, File
from parsing import extract_text
from storage import save_file
from chunking import chunk_text
from retrieval import search
from structure import extract_structured_block
from llm import generate
from router import route_model
from logger import log_interaction

app = FastAPI()

CHUNKS = []


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    path = save_file(content, file.filename)

    text = extract_text(path)
    chunks = chunk_text(text)

    for c in chunks:
        CHUNKS.append({
            "source": file.filename,
            "text": c
        })

    return {"chunks_added": len(chunks)}


@app.post("/query")
def query(q: str):
    t1 = time.time()
    results = search(q, CHUNKS)
    print("search time:", time.time()-t1)
    if not results:
        log_interaction(
            query=q,
            answer="I could not find that in the knowledge base",
            role="none",
            model="none",
            sources=[]
        )
        return {"answer": "I could not find that in the knowledge base"}

    role, (provider, model) = route_model(q)

    t0 = time.time()
    extracted = extract_structured_block(results[:3])
    print ("chunking time :", time.time() - t0)
    if extracted and role == "extract":
        log_interaction(
            query=q,
            answer=extracted,
            role="extract",
            model="structured",
            sources=[r.get("source") for r in results]
        )

        return {
            "answer": extracted,
            "mode": "extracted"
        }

    context = "\n\n".join(r["text"] for r in results)

    prompt = f"""
Use ONLY the context below.

Context:
{context}

Question:
{q}

Answer:
"""

    try:
        output = generate(provider, prompt, model)
    except:
        output = generate("local", prompt, "mistral")

    answer = output["text"]

    log_interaction(
        query=q,
        answer=answer,
        role=role,
        model=output.get("model"),
        sources=[r.get("source") for r in results]
    )

    return {
        "answer": answer,
        "role": role,
        "model": output["model"]
    }