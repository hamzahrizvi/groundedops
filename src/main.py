from fastapi import FastAPI, UploadFile, File
from parsing import extract_text
from storage import save_file
from retrieval import search
from llm import ask_ollama
from logger import log
from chunking import chunk_text

app = FastAPI()

DOCUMENTS = []
CHUNKS =[]


@app.get("/health")
def health():
    return {"status": "ok"}

#doc uploader (knowledge base)    
@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    path = save_file(content, file.filename)

    text = extract_text(path)

    DOCUMENTS.append({
        "name": file.filename,
        "text": text
    })


#OLLAMA response gen
@app.post("/query")
def query(q: str):
    results = search(q, CHUNKS)

    context = "\n\n".join(results)

    prompt = f"""
Answer ONLY using the context.

Context:
{context}

Question: {q}
"""

    answer = ask_ollama(prompt)

    return {
        "answer": answer
    }

#logger function
@app.post("/query")

def query(q:str) :
    results = search (q, DOCUMENTS)
    context = "\n\n" .join(results)

    answer = ask_ollama(context)

    log (q, answer)
    return {"answer": answer}

#multiple models

@app.post ("/query")
def query (q: str, provider: str ="ollama"):
    results = search (q, DOCUMENTS)
    context = "\n\n".join (results)
    prompt = f"""
Context: {context}

Question: {q}
"""
    answer = generate(provider,prompt)
    return{"answer": answer}

#cheeky chunks
@app.post ("/upload")
async def upload (file:UploadFile = File (...)):
    content = await file.read()
    path = save_file(content, file.filename)

    text = extract_text(path)
    chunks = chunk_text (text)

    for c in chunks: 
        CHUNKS.append({
            "source": file.filename
            "text": c
        })
    return {"chunks added" : len (chunks)}
