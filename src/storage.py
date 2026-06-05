import logging
import os

logger = logging.getLogger(__name__)

UPLOAD_DIR         = "uploads"
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".docx"}

os.makedirs(UPLOAD_DIR, exist_ok=True)


def save_file(content: bytes, filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}"
        )

    path = os.path.join(UPLOAD_DIR, filename)
    with open(path, "wb") as f:
        f.write(content)

    logger.info(f"Saved '{path}' ({len(content):,} bytes)")
    return path