from fastapi import FastAPI, UploadFile, File
from parsing import extract_text
from storage import save_file

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

    return {"filename": file.filename, "chars": len(text)}