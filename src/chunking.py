import re

def chunk_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    if not text or not text.strip():
        return []

    sentences = re.split(r'(?<=[.!?])\s+', text)

    chunks = []
    current = ""

    for s in sentences:
        s = s.strip()
        if not s:
            continue

        if len(current) + len(s) > size:
            if current:
                chunks.append(current.strip())

            if overlap and chunks:
                prev = chunks[-1]
                # Try to cut at sentence or newline boundary
                boundary = max(prev.rfind(". "), prev.rfind("\n"))
                if boundary > 0:
                    overlap_text = prev[boundary+1:]
                else:
                    overlap_text = prev[-overlap:] if overlap else ""
                current = overlap_text + " " + s
            else:
                current = s
        else:
            if current:
                current += " " + s
            else:
                current = s

    if current:
        chunks.append(current.strip())

    return chunks