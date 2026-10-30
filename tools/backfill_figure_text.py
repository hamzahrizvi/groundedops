"""Add (or remove) the manuals' picture-label chunks without a rebuild.

Labels inside drawings reach search only as figure-text chunks, and an
install ingested with FIGURE_TEXT_INDEX off has none. This runs
ingest.index_figure_text over every indexed PDF that has figure crops: it
OCRs each crop, adds one chunk per legible figure with the same scope tags as
the document's prose, and replaces its own earlier chunks.

Run with the backend STOPPED (as tools/sweep_internal_chunks.py): a second
process writing Chroma leaves the server's handle stale. Run it under the lab
lock (it loads the OCR and embedding models). New uploads get figure text
only with FIGURE_TEXT_INDEX=1 in GO_DATA's .env.

FIGURE_DESCRIBE=1 (env or GO_DATA .env) also describes each crop with the
vision model (FIGURE_DESCRIBE_MODEL; claude-* uses the Anthropic key); kept in the sidecar, so a
re-run pays only for new crops.

  GO_CODE  code folder (default <repo>/src), GO_DATA  working folder holding
  chroma_db and .env (default <repo>/src); SOURCE_FILE_DIR as the server has
  it (default <GO_DATA>/../documents).

  python tools/backfill_figure_text.py            # dry run: list documents
  python tools/backfill_figure_text.py --apply    # add the chunks
  python tools/backfill_figure_text.py --remove   # take every figure-text chunk out
  python tools/backfill_figure_text.py --selftest
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = os.environ.get("GO_CODE") or os.path.join(ROOT, "src")
DATA = os.path.abspath(os.environ.get("GO_DATA") or os.path.join(ROOT, "src"))
os.environ.setdefault("SOURCE_FILE_DIR", os.path.join(os.path.dirname(DATA), "documents"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")


def load_env(path):
    """GO_DATA's .env, same rules as main._load_env_file (never overrides)."""
    if not os.path.isfile(path):
        return
    for line in open(path, encoding="utf-8-sig"):
        key, eq, value = line.strip().partition("=")
        if eq and key.strip() and not key.startswith("#") and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def wanted(rows, has_crops, excluded, secondary) -> list:
    out = []
    for r in rows:
        s = r["source"]
        if (s.lower().endswith(".pdf") and s not in out
                and has_crops(s) and not excluded(s) and not secondary(s)):
            out.append(s)
    return out


def described(metas) -> tuple[int, int, int]:
    """(figures with desc_tokens, sum of input tokens, sum of output tokens)."""
    t = [m["desc_tokens"] for m in metas if m.get("desc_tokens")]
    return len(t), sum(a for a, _ in t), sum(b for _, b in t)


def selftest():
    assert described([{"desc_tokens": [900, 60]}, {"desc_tokens": [100, 40]}, {}]) == (2, 1000, 100)
    rows = [{"source": n} for n in ("a.pdf", "b.txt", "c.pdf", "d.pdf", "e.PDF", "a.pdf")]
    got = wanted(rows, lambda s: s != "c.pdf", lambda s: s == "d.pdf", lambda s: s == "e.PDF")
    assert got == ["a.pdf"], got
    print("selftest OK")


def main(argv) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if "--selftest" in argv:
        selftest()
        return 0
    os.chdir(DATA)
    sys.path.insert(0, CODE)
    load_env(os.path.join(DATA, ".env"))
    import docindex, figures, ingest, retrieval_db as rdb
    from db import get_collection, invalidate_retrieval_cache

    if "--remove" in argv:
        ids = get_collection().get(where={"figure_text": True}, include=[])["ids"]
        if ids:
            get_collection().delete(ids=ids)
        invalidate_retrieval_cache()
        print(f"DONE removed {len(ids)} chunks", flush=True)
        return 0

    docs = wanted(docindex.source_index(), lambda s: bool(figures.figures_for(s)),
                  rdb.is_excluded, rdb.is_secondary)
    if "--apply" not in argv:
        total = 0
        for s in docs:
            c = len(figures.figures_for(s))
            total += c
            print(f"{c:4d} crops  {len(ingest.figure_text_chunks_for(s)):4d} figure-text now  {s}", flush=True)
        print(f"{len(docs)} documents, {total} crops; --apply to add", flush=True)
        print("DESCRIBED %d figures, %d input + %d output tokens" % described(
            m for s in docs for m in figures.figures_for(s).values()), flush=True)
        return 0

    added = failed = 0
    t0 = time.time()
    try:
        for s in docs:
            t = time.time()
            try:
                n = ingest.index_figure_text(s)
            except Exception as e:
                failed += 1
                print(f"FAILED {s}: {type(e).__name__}: {e}", flush=True)
                continue
            added += n
            print(f"{n:4d} chunks  {len(figures.figures_for(s)):4d} crops  {time.time() - t:5.0f} s  {s}", flush=True)
    finally:
        print("DESCRIBED %d figures, %d input + %d output tokens" % described(
            m for s in docs for m in figures.figures_for(s).values()), flush=True)
        print(f"DONE added {added} chunks, {len(docs)} documents, {failed} failed, {time.time() - t0:.0f} s", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
