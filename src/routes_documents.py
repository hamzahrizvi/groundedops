"""Documents: upload and ingest, the source inventory, retagging, downloads.

Moved out of main.py verbatim (2026-09-25). Registered on the app through
app.include_router; paths, parameters and responses are unchanged.
"""

import glob
import hashlib
import logging
import os
import threading
import uuid

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import catalog as catalog_mod
from app_state import capability
from db import delete_source, get_chunks_by_ids
from guards import _require_admin
from ingest import ingest_file
from memory import clear_memory

logger = logging.getLogger(__name__)
router = APIRouter()


class DeleteSourceRequest(BaseModel):
    source: str


class SourceChunksRequest(BaseModel):
    chunk_ids: list[str]


@router.post("/delete_source")
def remove_source(payload: DeleteSourceRequest):
    removed = delete_source(payload.source)
    clear_memory()
    return {"removed_chunks": removed, "source": payload.source}


@router.post("/source_chunks")
def source_chunks(payload: SourceChunksRequest):
    """Fetch full chunk text for the given chunk ids — backs the
    clickable "view source" feature in the UI."""
    return {"chunks": get_chunks_by_ids(payload.chunk_ids)}


_INGEST_JOBS: dict = {}


_INGEST_LOCK = threading.Lock()


def _ingest_worker(job_id: str, content: bytes, filename: str, api_keys: dict,
                   category_key: str | None = None, product_key: str | None = None,
                   ingest_provider: str | None = None,
                   replace_source: str | None = None):
    def _progress(stage, done, total):
        with _INGEST_LOCK:
            _INGEST_JOBS[job_id].update(
                stage=stage, done=done, total=total,
                pct=round(done / total * 100, 1) if total else 0.0)
    # v10.6: per-upload provider override. Set the env var the ingest code
    # reads, scoped to this thread's run. (Simple + effective for the
    # single-worker setup; if you later parallelize ingest, pass it
    # through explicitly instead of via env.)
    if ingest_provider:
        os.environ["INGEST_PROVIDER"] = ingest_provider
    try:
        count = ingest_file(
            content, filename, api_keys=api_keys, progress=_progress,
            category_key=category_key, product_key=product_key,
            replace_existing=bool(replace_source and replace_source == filename))
        # A new version of a document already held. The old chunks go only
        # AFTER the new ones are in: if ingest fails we still have the
        # version we had, which is the whole point of replacing rather than
        # deleting first and uploading second.
        if replace_source and replace_source != filename:
            try:
                removed = delete_source(replace_source)
                logger.info("replaced %r with %r (%s old chunk(s) removed)",
                            replace_source, filename, removed)
            except Exception as exc:
                logger.error("could not remove the replaced source %r: %s",
                             replace_source, exc)
        # v10.3: admin uploaded into a specific product -> tag the source so
        # it's scoped to that product/category from now on.
        if category_key and product_key:
            try:
                catalog_mod.attach_source(category_key, product_key, filename)
            except Exception as e:
                logger.warning(f"attach_source after ingest failed: {e}")
        with _INGEST_LOCK:
            _INGEST_JOBS[job_id] = {
                "status": "done", "file": filename, "chunks_added": count,
                "warning": None if count else "File already exists or no usable text found",
                "done": True, "pct": 100.0}
    except Exception as e:
        logger.exception(f"Ingest job {job_id} failed")
        with _INGEST_LOCK:
            _INGEST_JOBS[job_id] = {"status": "error", "error": str(e),
                                    "file": filename, "done": True}


def _source_product_label(source: str) -> str:
    """Where a source is currently filed, for a message that tells the
    uploader what they already have rather than just refusing them."""
    try:
        import catalog as _cat
        for c in _cat.catalog().get("categories", []):
            for pr in c.get("products", []):
                if source in (_cat.sources_for(pr.get("key")) or []):
                    return f"{c.get('name')} › {pr.get('name')}"
    except Exception:
        pass
    return "no product"


def _doc_key(filename: str) -> str:
    """A filename reduced to what makes it the SAME DOCUMENT.

    Name only, deliberately: the two copies of the MyCheckr manual sitting in
    this corpus are 7,147,709 and 7,147,175 bytes with different hashes, so a
    content check would have called them distinct and let the duplicate in.
    What actually distinguishes them is " (1)" -- the suffix a browser adds
    when you download a file you already have.

    So: drop the extension, fold case and whitespace, and strip a trailing
    " (n)". Anything more aggressive starts merging real documents -- "v7"
    and "v8" of a manual differ by two characters and are not the same file.
    """
    import re as _re
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    stem = _re.sub(r"\s*\((\d+)\)\s*$", "", stem)
    return " ".join(stem.lower().split())


def _existing_source_like(filename: str) -> str | None:
    """The indexed source this upload would duplicate, or None."""
    key = _doc_key(filename)
    if not key:
        return None
    try:
        import docstore as _ds
        names = [e.get("source") or "" for e in _ds.inventory()]
    except Exception as exc:
        logger.warning(f"duplicate check skipped ({exc})")
        return None

    # An exact name wins over a normalised one. The store can hold BOTH
    # "X.pdf" and "X (1).pdf" -- a stray browser copy sitting beside the real
    # file -- and reporting the "(1)" as the thing you already have names the
    # wrong document back at the person uploading.
    for src in names:
        if src == filename:
            return src
    matches = [src for src in names if _doc_key(src) == key]
    if not matches:
        return None
    # Otherwise prefer the copy without the "(n)" suffix: the shorter name is
    # the original, the suffixed one is the accident.
    return sorted(matches, key=len)[0]


@router.post("/upload")
async def upload(request: Request,
                 file: UploadFile = File(...),
                 x_user_id: str | None = Header(default=None)):
    """v10.x: ASYNC ingest. Returns a job_id immediately; the document is
    indexed in a background thread. Poll /upload/status/{job_id}.

    v10.3: optional category/product scope headers (admin uploads) attach
    the ingested source to that product.

    v12.0: those scope headers are now read off the RAW request so both
    "category-key" and "category_key" work. They were Header() params,
    which FastAPI maps to the HYPHENATED name only — so a frontend sending
    the underscored form produced None, the attach_source call in
    _ingest_worker was skipped silently, and the document then appeared in
    the "needs assignment" list despite a category having been chosen.
    """
    # Indexing needs the database and the embedding model. It does NOT need
    # the reranker, the grounding model or the spec index, and waiting for
    # those put a two-minute 503 in front of the first upload after a
    # restart.
    if not capability("search"):
        raise HTTPException(status_code=503,
                            detail="The search model is still loading - try "
                                   "again in a few seconds")

    _h = request.headers
    category_key = _h.get("category-key") or _h.get("category_key")
    product_key = _h.get("product-key") or _h.get("product_key")
    ingest_provider = _h.get("ingest-provider") or _h.get("ingest_provider")

    if not (category_key and product_key):
        # Loud, with the header names actually received — one upload tells
        # you exactly what the frontend is sending.
        logger.warning(
            f"upload: missing scope headers (category={category_key!r}, "
            f"product={product_key!r}) — '{file.filename}' will need manual "
            f"assignment. Headers received: {list(_h.keys())}")

    # Refuse a document already in the index unless the caller says this is
    # a new version of it. Without this the same manual accumulates copies --
    # each one re-answering the same questions from slightly different text,
    # and each needing to be filed by hand.
    replace = (_h.get("replace-source") or _h.get("replace_source") or "").strip()
    clash = _existing_source_like(file.filename)
    if replace and not (category_key and product_key):
        # A replacement with no scope chosen keeps the filing of the version
        # it replaces. Losing it silently is exactly how two documents ended
        # up in "Unassigned" after a reindex, needing to be filed by hand.
        import catalog as _cat
        for _c in _cat.catalog().get("categories", []):
            for _p in _c.get("products", []):
                if replace in (_cat.sources_for(_p.get("key")) or []):
                    category_key = category_key or _c.get("key")
                    product_key = product_key or _p.get("key")
                    logger.info("replacement inherits scope %s/%s from %r",
                                category_key, product_key, replace)
                    break
    if clash and not replace:
        raise HTTPException(status_code=409, detail={
            "error": "duplicate_document",
            "existing": clash,
            "product": _source_product_label(clash),
            "message": f"“{clash}” is already indexed. Upload it as a "
                       f"new version to replace it, or rename the file if it "
                       f"is genuinely a different document.",
        })

    content = await file.read()
    filename = file.filename
    job_id = str(uuid.uuid4())
    with _INGEST_LOCK:
        _INGEST_JOBS[job_id] = {"status": "starting", "file": filename,
                                "pct": 0.0, "done": False}
    threading.Thread(target=_ingest_worker,
                     args=(job_id, content, filename, {}, category_key, product_key,
                           ingest_provider, replace or None),
                     daemon=True).start()
    return {"job_id": job_id, "file": filename, "status": "started"}


@router.get("/upload/status/{job_id}")
def upload_status(job_id: str):
    with _INGEST_LOCK:
        job = _INGEST_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job id")
    return job


# ── Ingest version fingerprint (v10.4) ───────────────────────────────
# When ingestion LOGIC changes (chunking, breadcrumbs, doc2query), old
# vectors are stale. We fingerprint the ingest-affecting code; the UI
# compares it to what the current DB was built with and prompts a
# re-ingest if they differ. Bump INGEST_VERSION when you change ingest.
INGEST_VERSION = "12.0"


def _ingest_fingerprint() -> str:
    parts = [INGEST_VERSION]
    for f in ("ingest.py", "chunking.py"):
        try:
            with open(f, "rb") as fh:
                parts.append(hashlib.sha256(fh.read()).hexdigest()[:12])
        except Exception:
            pass
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


_INGEST_STAMP_PATH = os.getenv("INGEST_STAMP_PATH", "ingest_stamp.txt")


@router.get("/ingest/version")
def ingest_version():
    """Compare the fingerprint the DB was built with against the current
    code. UI shows a 're-ingest recommended' banner when they differ."""
    current = _ingest_fingerprint()
    built_with = None
    if os.path.exists(_INGEST_STAMP_PATH):
        try:
            built_with = open(_INGEST_STAMP_PATH).read().strip()
        except Exception:
            pass
    return {"current": current, "built_with": built_with,
            "reingest_recommended": built_with is not None and built_with != current,
            "ingest_version": INGEST_VERSION}


# ── Docs folder reload (v10.4) ───────────────────────────────────────
# Keep source docs in ./corpus (mounted in Docker). This ingests any that
# aren't already in the DB — handy after an ingest-logic change: wipe the
# DB, hit reload, everything re-ingests from the folder.
CORPUS_DIR = os.getenv("CORPUS_DIR", "corpus")


@router.post("/ingest/reload_folder")
def reload_folder(x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    if not os.path.isdir(CORPUS_DIR):
        raise HTTPException(status_code=404, detail=f"No {CORPUS_DIR}/ folder found")
    files = [f for f in glob.glob(os.path.join(CORPUS_DIR, "*"))
             if f.lower().endswith((".pdf", ".txt", ".docx"))]
    jobs = []
    for path in files:
        with open(path, "rb") as fh:
            content = fh.read()
        fname = os.path.basename(path)
        job_id = str(uuid.uuid4())
        with _INGEST_LOCK:
            _INGEST_JOBS[job_id] = {"status": "starting", "file": fname, "pct": 0.0, "done": False}
        threading.Thread(target=_ingest_worker,
                         args=(job_id, content, fname, {}, None, None), daemon=True).start()
        jobs.append({"file": fname, "job_id": job_id})
    return {"reloading": jobs, "count": len(jobs)}


@router.get("/admin/source_sample")
def admin_source_sample(source: str, x_admin_password: str | None = Header(default=None)):
    """v10.13: return a text sample of an ingested source so the FRONTEND
    can generate FAQ questions from real content (browser calls the LLM,
    no backend key needed)."""
    _require_admin(x_admin_password)
    from docindex import source_text
    sample, chunks = source_text(source, max_chunks=8, cap=6000)
    return {"source": source, "sample": sample, "chunks": chunks}


@router.get("/source_file/{filename}")
def source_file(filename: str):
    """Serve the original ingested document so an answer can link back to it
    (v12.0). Requires a re-ingest: originals are retained at ingest time,
    and documents indexed earlier were never kept.

    SECURITY: the filename comes from the URL, so it is reduced to a bare
    basename — without that, "../../etc/passwd" escapes the directory.
    Excluded (internal) sources are refused so they can't be downloaded.

    NOTE: deliberately unauthenticated so the public widget can offer
    downloads, which means ANY retained document is publicly retrievable by
    name. Gate this per tenant/product before real customer traffic.
    """
    import docstore
    # docstore._safe_basename, not os.path.basename: basename() is
    # platform-dependent (a backslash is a legal POSIX filename character, so
    # a backslash-separated traversal survives it) and this name comes
    # straight off the URL. One sanitiser, shared with the store that
    # resolves it, rather than two that can drift apart.
    safe = docstore._safe_basename(filename)
    if not safe:
        raise HTTPException(status_code=404, detail="Not found")
    _lower = safe.lower()
    from main import EXCLUDED_SOURCES     # the corpus policy main.query applies
    for _ex in EXCLUDED_SOURCES:
        if _ex and _ex in _lower:
            raise HTTPException(status_code=404, detail="Not found")
    # v15: resolved via docstore, which checks <repo>/documents AND the legacy
    # /data/source_files. SOURCE_FILE_DIR alone 404'd any document retained in
    # the newer location -- the answer cited a page, then the link was dead.
    # Only what docstore resolves: it confirms the path stays inside a known
    # store directory. The previous `or os.path.join(SOURCE_FILE_DIR, safe)`
    # fallback re-introduced an unchecked join, defeating the point.
    path = docstore.find(safe)
    if not path or not os.path.isfile(path):
        raise HTTPException(
            status_code=404,
            detail="Source file not retained. Re-ingest to enable downloads.")
    return FileResponse(path, filename=safe)


class ReassignReq(BaseModel):
    source: str
    category_key: str
    product_key: str


@router.post("/admin/reassign_source")
def admin_reassign_source(payload: ReassignReq, x_admin_password: str | None = Header(default=None)):
    """v10.5: re-tag every chunk of an already-ingested SOURCE to a
    category/product — for docs ingested before direct tagging, or to move
    a doc. Updates Chroma metadata in place (no re-embed)."""
    _require_admin(x_admin_password)
    from db import get_collection
    col = get_collection()
    got = col.get(where={"source": payload.source}, include=["metadatas"])
    ids = got.get("ids", [])
    if not ids:
        raise HTTPException(status_code=404, detail=f"No chunks for source '{payload.source}'")
    # Several products, comma-joined: an installation guide covering the
    # MyCheckr and the MyCheckr Mini belongs to both, and filing it under one
    # meant the other product's visitors were never offered it.
    keys = list(dict.fromkeys(
        k.strip() for k in (payload.product_key or "").split(",") if k.strip()))
    metas = got["metadatas"]
    for m in metas:
        # Flags from a previous assignment have to go, or a document moved off
        # a product stays findable under it -- the worst kind of stale tag,
        # because nothing on screen says it is still there.
        for stale in [k for k in list(m.keys()) if k.startswith("prod_")]:
            m.pop(stale, None)
        m["category"] = payload.category_key
        m["product"] = ",".join(keys)
        m["products"] = ",".join(keys)
        for k in keys:
            m["prod_" + k] = True
    col.update(ids=ids, metadatas=metas)
    # BM25 and the source inventory both carry the old tags until told.
    try:
        from db import invalidate_retrieval_cache
        invalidate_retrieval_cache()
    except Exception:
        pass
    return {"reassigned": len(ids), "source": payload.source,
            "category": payload.category_key, "product": payload.product_key}


@router.get("/admin/crossrefs")
def admin_crossrefs(x_admin_password: str | None = Header(default=None)):
    """Documents the corpus refers to, and whether we hold them.

    The rows with `held: null` are a shopping list: every question those
    documents would have answered is a refusal today. "MyCheckr Range
    Technical Data" is cited by both MyCheckr manuals for the device
    dimensions, which is why "what is the screen size?" cannot be answered
    while "what is the weight?" can.

    `example` carries the sentence the reference was found in, because the
    scan is string matching and will occasionally lift an API section name
    ("Action Data") that is not a document at all. One glance at the
    sentence settles it, which is the difference between a report an
    operator uses and one they stop opening.
    """
    _require_admin(x_admin_password)
    import crossrefs
    rows = crossrefs.scan()
    return {"referenced": rows,
            "missing": sum(1 for r in rows if not r.get("held")),
            "held": sum(1 for r in rows if r.get("held"))}


@router.get("/admin/sources")
def admin_sources(x_admin_password: str | None = Header(default=None)):
    """List ingested sources with their current tags (for the re-assign UI).

    2026-08-29: `chunks` was missing entirely, so the console's "N sections"
    line always read 0 -- not because anything was empty, but because this
    endpoint never counted per-source chunks in the first place. Every
    metadata row is walked anyway to find sources, so counting them is free.
    """
    _require_admin(x_admin_password)
    from docindex import source_index
    rows = source_index()
    out = [{"source": r["source"], "category": r["category"],
            "product": r["product"], "chunks": r["chunks"]} for r in rows]
    try:
        import docstore as _ds
        for item, r in zip(out, rows):
            item["freshness"] = _ds.freshness(r["source"], r["version"])
    except Exception as exc:
        logger.warning("source freshness enrichment failed (non-fatal): %s", exc)
    return {"sources": out}
