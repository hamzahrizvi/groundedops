from pypdf import PdfReader
from docx import Document

def extract_text(path: str) -> str:
    if path.endswith(".txt"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    elif path.endswith(".pdf"):
        reader = PdfReader(path)
        return "".join([p.extract_text() or "" for p in reader.pages])

    elif path.endswith(".docx"):
        doc = Document(path)
        return "\n".join([p.text for p in doc.paragraphs])

    return ""