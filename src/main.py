from fastapi import FastAPI, UploadFile, File
from parsing import extract_text
from storage import save_file
from retrieval import search

app = FastAPI()

DOCUMENTS = []

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    path = save_file(content, file.filename)

    text = extract_text(path)

    DOCUMENTS.append({
        "name": file.filename,
        "text": text
    })
@app.post("/query")
def query(q: str):
    results = search(q, DOCUMENTS)

    return {
        "results": results
    }
    return {"filename": file.filename, "chars": len(text)}