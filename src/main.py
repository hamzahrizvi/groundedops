from fastapi import FastAPI, UploadFile, File
from parsing import extract_text
from storage import save_file
from chunking import chunk_text
from retrieval import search
from structure import extract_structured_block
from llm import ask_ollama

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
    # retrieval
    results = search(q, CHUNKS, k=10)

    # structured extraction
    extracted = extract_structured_block(results)

    if extracted:
        return {
            "answer": extracted,
            "mode": "structured"
        }

    # llm fallback
    context = "\n\n".join(c["text"] for c in results)

    prompt = f"""
You must answer using ONLY the context.

Context:
{context}

Question: {q}

If the answer is not clearly present, say:
"I could not find that in the knowledge base."
"""

    answer = ask_ollama(prompt)

    return {
        "answer": answer,
        "mode": "llm"
    }