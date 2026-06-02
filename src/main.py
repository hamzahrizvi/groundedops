from fastapi import FastAPI, UploadFile, File
from parsing import extract_text
from storage import save_file
from retrieval import search
from llm import ask_ollama, generate
from logger import log
from chunking import chunk_text
from pydantic import BaseModel

class QueryRequest(BaseModel):
    q: str
    provider: str = "ollama"
app = FastAPI()

DOCUMENTS = []
CHUNKS = []

@app.get("/health")
def health():
    return {"status": "ok"}


#upload
@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    path = save_file(content, file.filename)

    text = extract_text(path)

    if not text or not text.strip():
        return {
            "error": "No text extracted",
            "filename": file.filename
        }

    chunks = chunk_text(text)

    for c in chunks:
        CHUNKS.append({
            "source": file.filename,
            "text": c
        })

    return {
        "filename": file.filename,
        "chunks_added": len(chunks)
    }


# Query
@app.post("/query")
def query(req: QueryRequest):
    try:
        if not CHUNKS:
            return {"error": "No documents uploaded"}

        results = search(req.q, CHUNKS)

        if not results:
            return {"error": "No relevant content found"}

        context = "\n\n".join(results[:5])

        prompt = f"""
Answer ONLY using the context.

Context:
{context}

Question: {req.q}
"""

        answer = generate(req.provider, prompt)

        log(req.q, answer)

        return {"answer": answer}

    except Exception as e:
        print("ERROR:", str(e))
        return {"error": str(e)}