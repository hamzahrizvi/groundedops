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
def remove_source(payload: DeleteSourceRequest,
                  x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    removed = _remove_source(payload.source)   # deletes the original too; an internal mark stays
    try:
        import confluence_sync
        confluence_sync.exclude_removed(payload.source)
    except Exception as exc:
        logger.warning("Confluence item for %r not excluded: %s", payload.source, exc)
    return {"removed_chunks": removed, "source": payload.source}


def remove_source_now(source: str) -> int:
    """What Remove does, for callers inside the server (the Confluence sync)
    that have no sign-in to show; the route above checks the caller's.

    Also drops an internal mark, without re-indexing: the sync un-marks every
    source it removes, and an un-mark that re-indexed from the original would
    bring the removed document straight back (D-20261024-06)."""
    removed = _remove_source(source)
    from retrieval_db import is_marked_internal, set_internal
    if is_marked_internal(source):
        set_internal([source], False, reindex=False)
    return removed


def _remove_source(source: str) -> int:
    """Remove a document for good: its retained original and manifest entry
    first, then its chunks, memory and figures. The original goes first so a
    failure stops here, before remove_source_now drops an internal mark: no
    moment where an unmarked original is downloadable. A rebuild cannot bring
    it back (D-20261024-18)."""
    import docstore
    docstore.delete(source)
    removed = delete_source(source)
    clear_memory()
    try:
        import figures as _figures
        _figures.remove_figures(source)
    except Exception as exc:
        logger.warning("figures for %r not removed: %s", source, exc)
    return removed


@router.post("/source_chunks")
def source_chunks(payload: SourceChunksRequest,
                  x_admin_password: str | None = Header(default=None)):
    """Fetch full chunk text for the given chunk ids — backs the
    clickable "view source" feature in the UI."""
    _require_admin(x_admin_password)
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
        report: dict = {}
        count = ingest_file(
            content, filename, api_keys=api_keys, progress=_progress,
            category_key=category_key, product_key=product_key,
            replace_existing=bool(replace_source and replace_source == filename),
            report=report)
        # A new version of a document already held. The old chunks go only
        # AFTER the new ones are in: if ingest fails we still have the
        # version we had, which is the whole point of replacing rather than
        # deleting first and uploading second.
        if replace_source and replace_source != filename:
            try:
                removed = _remove_source(replace_source)
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
        # Pages the parser could not read are OFFERED, not OCR'd. The console
        # shows the list with the warning OCR deserves and a button; nothing
        # is guessed from a picture until somebody chooses that.
        candidates = list(report.get("ocr_candidates") or [])
        ocr_ok, ocr_why = _ocr_availability() if candidates else (False, None)
        with _INGEST_LOCK:
            _INGEST_JOBS[job_id] = {
                "status": "done", "file": filename, "chunks_added": count,
                "chunks": count,
                "warning": None if count else (
                    "Kept as internal: stored, not used for answers, so nothing was indexed."
                    if report.get("internal") else
                    "File already exists or no usable text found"),
                "pages": report.get("pages"),
                "ocr_candidates": candidates,
                "ocr_available": ocr_ok,
                "ocr_unavailable_reason": ocr_why,
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
                 x_user_id: str | None = Header(default=None),
                 x_admin_password: str | None = Header(default=None)):
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
    _require_admin(x_admin_password)
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
    # D-20261025-05: a new version whose name differs only in letter case is
    # the same document: keep the name it is indexed under. On a
    # case-insensitive disk (Windows) both spellings are one file and one
    # figures folder, so indexing the new spelling and then removing the old
    # one deleted the new version's figures, and left its chunks, manifest
    # entry and internal mark split across two names.
    import docstore as _ds
    filename = file.filename
    _base = _ds._safe_basename(filename)
    if replace and _base and _ds._safe_basename(replace).casefold() == _base.casefold():
        filename = replace
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

    # D-20261022-03: Internal = never used to answer a customer, never
    # downloadable. Marked BEFORE indexing so the document is never public
    # for a moment. No header: a new version keeps the mark of the document
    # it replaces; anything else is public (the upload card says so).
    from retrieval_db import internal_sources, is_excluded, set_internal
    raw = (_h.get("internal") or "").strip().lower()
    if raw in ("1", "true", "yes"):
        internal = True
    elif raw in ("0", "false", "no"):
        internal = False
    elif raw:
        raise HTTPException(status_code=400, detail=(
            "The 'internal' header must be 1 (internal) or 0 (public)."))
    else:
        internal = bool(replace) and is_excluded(replace)
    # An internal upload always writes its mark: "already in the set" may be
    # the fail-closed or last-good set of an unreadable file, not the file.
    if internal or filename in internal_sources():
        try:
            # reindex=False: the worker below indexes this upload. Re-indexing
            # the old original first would make it skip the upload as a duplicate.
            set_internal([filename], internal, reindex=False)
        except Exception as exc:
            logger.error("upload refused, internal mark not saved for %r: %s",
                         filename, exc)
            raise HTTPException(status_code=500, detail=(
                "Could not record whether this document is internal, so it "
                "was not indexed. Try again."))

    content = await file.read()
    job_id = str(uuid.uuid4())
    with _INGEST_LOCK:
        _INGEST_JOBS[job_id] = {"status": "starting", "file": filename,
                                "pct": 0.0, "done": False}
    threading.Thread(target=_ingest_worker,
                     args=(job_id, content, filename, {}, category_key, product_key,
                           ingest_provider, replace or None),
                     daemon=True).start()
    return {"job_id": job_id, "file": filename, "status": "started", "internal": internal}


@router.get("/upload/status/{job_id}")
def upload_status(job_id: str, x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    with _INGEST_LOCK:
        job = _INGEST_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job id")
    return job


# ── OCR of unreadable pages (admin-approved) ─────────────────────────
# The parser reports pages with no usable text layer; this is the step the
# admin takes after reading the warning. It runs as a job on the same
# status endpoint as an upload, because on CPU it is slower than one.

def _ocr_availability() -> tuple[bool, str | None]:
    try:
        import ocr as _ocr
        return _ocr.available()
    except Exception as exc:
        return False, str(exc)


class OcrRequest(BaseModel):
    source: str
    # Defaults to every pending candidate for the source.
    pages: list[int] | None = None


def _ocr_worker(job_id: str, source: str, pages: list[int]):
    def _progress(stage, done, total):
        with _INGEST_LOCK:
            _INGEST_JOBS[job_id].update(
                stage=stage, done=done, total=total,
                pct=round(done / total * 100, 1) if total else 0.0)
    try:
        # Imported here, not at module top: the route tests stand in a stub
        # for `ingest` that knows only ingest_file.
        from ingest import ingest_ocr_pages
        count = ingest_ocr_pages(source, pages, progress=_progress)
        with _INGEST_LOCK:
            _INGEST_JOBS[job_id] = {
                "status": "done", "kind": "ocr", "file": source,
                "chunks_added": count, "chunks": count,
                "ocr_pages": pages,
                "warning": None if count else "OCR found no legible text on those pages",
                "done": True, "pct": 100.0}
    except Exception as e:
        logger.exception(f"OCR job {job_id} failed")
        with _INGEST_LOCK:
            # The detail is in the log (logger.exception above); exception
            # text is not sent to the browser (CodeQL: information exposure).
            _INGEST_JOBS[job_id] = {"status": "error", "kind": "ocr",
                                    "error": "OCR failed - the server log has the details.",
                                    "file": source, "done": True}


@router.get("/admin/ocr/status")
def ocr_status(x_admin_password: str | None = Header(default=None)):
    """Whether OCR can run on this install, so the console can say "not
    installed" instead of offering a button that fails."""
    _require_admin(x_admin_password)
    ok, why = _ocr_availability()
    if not ok:
        # why can carry an exception's text; log it, send a fixed message.
        logger.info("OCR unavailable: %s", why)
    return {"available": ok,
            "reason": None if ok else "OCR is not installed on this server "
                                      "(the server log says why)."}


@router.post("/admin/ocr")
def run_ocr(payload: OcrRequest,
            x_admin_password: str | None = Header(default=None)):
    """Start OCR on a document's unreadable pages. Returns a job id to poll
    on /upload/status/{job_id}.

    Pages default to the manifest's pending candidates. Explicit pages are
    accepted (an admin may want a page the heuristic did not flag) but must
    exist in the document, which ingest_ocr_pages checks.
    """
    _require_admin(x_admin_password)
    ok, why = _ocr_availability()
    if not ok:
        raise HTTPException(status_code=503, detail=f"OCR is not available: {why}")
    import docstore as _ds
    source = os.path.basename((payload.source or "").replace("\\", "/"))
    if not _ds.find(source):
        raise HTTPException(status_code=404,
                            detail=f"No retained original for {source!r}")
    pages = payload.pages
    if not pages:
        pages = _ds.ocr_state(source)["pending"]
    pages = sorted({int(p) for p in pages if int(p) >= 1})
    if not pages:
        raise HTTPException(status_code=400,
                            detail="No pages to OCR for that document")
    job_id = str(uuid.uuid4())
    with _INGEST_LOCK:
        _INGEST_JOBS[job_id] = {"status": "starting", "kind": "ocr",
                                "file": source, "pct": 0.0, "done": False}
    threading.Thread(target=_ocr_worker, args=(job_id, source, pages),
                     daemon=True).start()
    return {"job_id": job_id, "file": source, "pages": pages, "status": "started"}


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
def ingest_version(x_admin_password: str | None = Header(default=None)):
    """Compare the fingerprint the DB was built with against the current
    code. UI shows a 're-ingest recommended' banner when they differ."""
    _require_admin(x_admin_password)
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

    Gated in main.py (_TOKEN_GATED_PREFIXES): a caller needs a widget member token unless it sends no proxy header from a private address (D-20261010-32).
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
    from retrieval_db import is_excluded  # the corpus policy main.query applies
    if is_excluded(safe):
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


@router.get("/figure/{source}/{name}")
def figure(source: str, name: str):
    """Serve one crop cut from a document at ingest (figures.py), so an
    answer can show the diagram beside the text it cites.

    Same shape as /source_file: the source is reduced to a bare basename
    and resolved by the module that wrote it, and the crop name must be
    exactly the form figures.py writes (p<page>_<n>.png), so nothing off
    the URL ever becomes a path. Excluded sources are refused, as their
    originals are. Token-gated externally alongside /source_file.
    """
    import docstore
    import figures as _figures
    safe = docstore._safe_basename(source)
    if not safe:
        raise HTTPException(status_code=404, detail="Not found")
    from retrieval_db import is_excluded
    if is_excluded(safe):
        raise HTTPException(status_code=404, detail="Not found")
    path = _figures.figure_path(safe, docstore._safe_basename(name))
    if not path:
        raise HTTPException(status_code=404, detail="Not found")
    # figure_path realpaths the folder: a short name (INTERN~1.PDF) or alias
    # resolves to the long folder, so test the mark on the folder really opened.
    if is_excluded(os.path.basename(os.path.dirname(path))):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="image/png")


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
    n = retag_source(payload.source, payload.category_key, payload.product_key)
    from retrieval_db import is_marked_internal
    if not n and not is_marked_internal(payload.source):   # internal: re-filed, no chunks
        raise HTTPException(status_code=404, detail=f"No chunks for source '{payload.source}'")
    return {"reassigned": n, "source": payload.source,
            "category": payload.category_key, "product": payload.product_key}


def retag_source(source: str, category_key: str | None, product_key: str | None) -> int:
    """File an indexed source under `product_key` ("a,b" for several, "" for
    none) in the index and the catalogue. Returns the chunks retagged, 0 when
    the source is not indexed."""
    from db import get_collection
    col = get_collection()
    got = col.get(where={"source": source}, include=["metadatas"])
    ids = got.get("ids", [])
    # Several products, comma-joined: an installation guide covering the
    # MyCheckr and the MyCheckr Mini belongs to both, and filing it under one
    # meant the other product's visitors were never offered it.
    keys = list(dict.fromkeys(
        k.strip() for k in (product_key or "").split(",") if k.strip()))
    if not ids:
        # A document marked internal has no chunks, but its filing still
        # counts: un-marking indexes it again from the catalogue.
        from retrieval_db import is_marked_internal
        if is_marked_internal(source):
            catalog_mod.refile_source(source, keys)
        return 0
    # Flags from a previous assignment have to go, or a document moved off
    # a product stays findable under it -- the worst kind of stale tag,
    # because nothing on screen says it is still there. with_product_tags
    # writes them as None: Chroma keeps a key that is merely absent.
    from db import with_product_tags
    metas = []
    for m in got["metadatas"]:
        m = with_product_tags(m, keys)
        m["category"] = category_key
        metas.append(m)
    col.update(ids=ids, metadatas=metas)
    # The catalogue is what reindex.py rebuilds from. Until it was written
    # here too, a retag lived only in Chroma and vanished on the next rebuild.
    try:
        catalog_mod.refile_source(source, keys)
    except Exception as exc:
        logger.warning("reassign_source: catalogue not updated for %r: %s",
                       source, exc)
    # BM25 and the source inventory both carry the old tags until told.
    try:
        from db import invalidate_retrieval_cache
        invalidate_retrieval_cache()
    except Exception:
        pass
    return len(ids)


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
    from retrieval_db import internal_sources
    internal = internal_sources()
    out = [{"source": r["source"], "category": r["category"],
            "product": r["product"], "chunks": r["chunks"],
            "internal": r["source"] in internal} for r in rows]
    try:
        import docstore as _ds
        for item, r in zip(out, rows):
            item["freshness"] = _ds.freshness(r["source"], r["version"])
            # Pages the parser could not read, and which of them have been
            # OCR'd. Shown on the row so the offer is not lost with the
            # upload card.
            item["ocr"] = _ds.ocr_state(r["source"])
    except Exception as exc:
        logger.warning("source freshness enrichment failed (non-fatal): %s", exc)
    # Marked documents are kept out of the index (D-20261024-06), so list
    # them from the store, or the page could never make them public again.
    try:
        import docstore as _ds
        from reindex import catalog_assignment
        from retrieval_db import _name_key
        listed = {_name_key(r["source"]) for r in rows}
        assign = catalog_assignment()
        for name in sorted(internal):
            if _name_key(name) in listed or not _ds.find(name):
                continue
            listed.add(_name_key(name))
            cat, prod = assign.get(_ds._safe_basename(name), ("", ""))
            out.append({"source": name, "category": cat, "product": prod,
                        "chunks": 0, "internal": True})
    except Exception as exc:
        logger.warning("internal documents not listed (non-fatal): %s", exc)
    return {"sources": out}


class InternalReq(BaseModel):
    sources: list[str]
    internal: bool


@router.post("/admin/source_internal")
def admin_source_internal(payload: InternalReq,
                          x_admin_password: str | None = Header(default=None)):
    """Mark documents internal, or customer-facing again, from the
    Documents page (the Confluence page could already). A synced Confluence
    document changes its decision there too, or the next sync of a changed
    page would put the old mark back. FAQs are hand-curated and stay until
    removed, so the reply lists the ones to offer for removal."""
    _require_admin(x_admin_password)
    import confluence_sync
    sources = list(dict.fromkeys(s for s in payload.sources if s))
    if not sources:
        raise HTTPException(status_code=400, detail="No documents given")
    # By every name the manual is held under (its copy, the PDF kept over it,
    # uploads it replaces): set_items then flags every copy, and the
    # sync's _carry_marks does not put back a mark removed here.
    held = confluence_sync.items_holding(sources)      # item key -> every name it is held under
    names = list(dict.fromkeys([*sources, *(n for ns in held.values() for n in ns)]))
    # Before marking: marking takes the documents' chunks out of the index,
    # and related_faqs reads their text.
    faqs = related_faqs(names) if payload.internal else []
    if held:
        confluence_sync.set_items(list(held), decision="internal" if payload.internal else "approved")
    covered = {n for ns in held.values() for n in ns}
    rest = [s for s in sources if s not in covered]
    if rest:
        from retrieval_db import set_internal
        set_internal(rest, payload.internal)
    clear_memory()   # a follow-up could otherwise lean on an earlier answer
    return {"sources": sources, "internal": payload.internal, "faqs": faqs}


# Share of an answer's content words that must appear in ONE passage of the
# documents for the FAQ to be offered as "close wording". Offered unticked, so
# a loose match costs a glance, not a deletion.
_RELATED_OVERLAP = 0.7


def related_faqs(sources: list[str]) -> list[dict]:
    """FAQs to offer for removal when `sources` go internal: those made from
    them (and their translations), marked derived, then others whose answer
    reads like one of their passages."""
    import faq_store
    from db import get_collection
    items = faq_store._load()
    wanted = set(sources)
    derived = {it["id"] for it in items if it.get("source") in wanted}
    derived |= {it["id"] for it in items if it.get("translation_of") in derived}
    texts = get_collection().get(where={"source": {"$in": sources}},
                                 include=["documents"]).get("documents") or []
    passages = [faq_store._tokens(t) for t in texts]
    out = []
    for it in items:
        row = {"id": it["id"], "question": it.get("question", ""),
               "answer": it.get("answer", ""), "language": it.get("language", "")}
        if it["id"] in derived:
            src = it.get("source") if it.get("source") in wanted else "a document you chose"
            out.append(dict(row, derived=True, why="made from " + src))
            continue
        words = faq_store._tokens(it.get("answer"))
        if len(words) < 4 or not passages:
            continue
        best = max(len(words & p) for p in passages) / len(words)
        if best >= _RELATED_OVERLAP:
            out.append(dict(row, derived=False,
                            why=f"{round(best * 100)}% of its answer is in these documents"))
    return out
