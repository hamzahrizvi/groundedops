import logging
from pypdf import PdfReader
from docx import Document

logger = logging.getLogger(__name__)


def extract_pages(path: str) -> list[tuple[int, str]]:
    """Extract text as [(page_number, text), ...], 1-indexed.

    v3.3.0. extract_text() below joined every page into one string, which
    destroyed page boundaries before chunking ever ran — so a chunk could
    never say which page it came from, and citations couldn't reference
    one. This preserves the boundary so ingest can tag each chunk with its
    page span.

    Non-paginated formats report a single page 1: .docx has no fixed
    pagination without rendering (page breaks depend on the renderer), and
    .txt has none at all. Treating them as one page is honest — better
    than inventing page numbers that wouldn't match what the reader sees.
    """
    try:
        if path.endswith(".txt"):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return [(1, f.read())]

        elif path.endswith(".pdf"):
            reader = PdfReader(path)
            out = []
            for i, page in enumerate(reader.pages, start=1):
                txt = page.extract_text() or ""
                if txt.strip():
                    out.append((i, txt))
            return out

        elif path.endswith(".docx"):
            doc = Document(path)
            txt = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            return [(1, txt)] if txt.strip() else []

        else:
            logger.warning(f"Unsupported file type: {path}")
            return []

    except Exception as exc:
        logger.error(f"Failed to extract pages from '{path}': {exc}")
        return []


def extract_text(path: str) -> str:
    """Whole-document text. Retained for callers that don't need pages
    (eval, diagnostics); ingest now uses extract_pages()."""
    return "\n\n".join(t for _, t in extract_pages(path))
