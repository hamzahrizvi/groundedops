def chunk_text (text, size=500, overlap=50):
    chunks = []
    start = 0

    while start < len(text):
        end = start + size
        chunk = text [start:end]
        chunks.append(chunk)
        start += size - overlap

    return chunks
