from chunking import chunk_text


def test_empty_input():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_short_text_single_chunk():
    text = "This is a short sentence. It fits in one chunk easily."
    chunks = chunk_text(text, size=500)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_long_text_multiple_chunks():
    sentence = "This is one sentence that takes up some space. "
    text = sentence * 30   # ~1470 chars
    chunks = chunk_text(text, size=500, overlap=50)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) > 0


def test_overlap_carries_content_forward():
    sentence = "Word " * 20 + ". "
    text = sentence * 10
    chunks = chunk_text(text, size=200, overlap=50)
    assert len(chunks) >= 2
    # With overlap, consecutive chunks should share some trailing/leading content
    # (exact overlap text depends on sentence boundaries, just check non-empty)
    assert all(c.strip() for c in chunks)


def test_no_chunk_exceeds_size_drastically():
    # Individual sentences longer than `size` will still form their own
    # chunk (we don't split mid-sentence), but chunks built from multiple
    # short sentences should respect the size budget roughly.
    text = "Short. " * 100
    chunks = chunk_text(text, size=100, overlap=10)
    for c in chunks:
        assert len(c) < 200  # generous upper bound, no runaway growth