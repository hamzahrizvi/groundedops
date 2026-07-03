import logging
from pypdf import PdfReader
from docx import Document

logger = logging.getLogger(__name__)


def extract_text(path: str) -> str:
    try:
        if path.endswith(".txt"):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()

        elif path.endswith(".pdf"):
            reader = PdfReader(path)
            return "\n\n".join(p.extract_text() or "" for p in reader.pages)

        elif path.endswith(".docx"):
            doc = Document(path)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

        else:
            logger.warning(f"Unsupported file type: {path}")
            return ""

    except Exception as exc:
        logger.error(f"Failed to extract text from '{path}': {exc}")
        return ""