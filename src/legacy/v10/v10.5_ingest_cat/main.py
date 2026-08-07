import logging
import os
import re
import threading
import time
import uuid

from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from pydantic import BaseModel

from db import get_collection, reset_collection, get_stats, delete_source, get_chunks_by_ids
from embeddings import _get_model as _get_embedding_model
from reranker import rerank, _get as _get_reranker_model
from structure import extract_structured_block
from logger import log_interaction
from router import route_model
from grounding import check_grounding, _get_nli_model
from llm import generate, generate_with_fallback, warmup_local_models, RETHINK_OPTIONS, condense_query
from runtime_config import get_settings, set_generation_mode, set_local_models_loaded, set_online_provider
import catalog as catalog_mod

import faq_store
import hashlib, glob
import conversations as convo_store
from memory import add_to_memory, clear_memory, get_history, get_last_query
from ingest import ingest_file
from retrieval_db import retrieve_from_db
from text_utils import (
    passes_retrieval_gate,
    retrieval_confidence_band,
    is_refusal,
    is_followup_turn,
    has_domain_vocabulary,
    has_reference_markers,
    is_template_leak,
    build_clarification_options,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
convo_store.init_db()

GROUNDING_THRESHOLD = 0.55

# Sigmoid-calibrated reranker score (0.5 = the model's own relevance
# boundary). Below this, the top chunk is judged irrelevant — refuse
# BEFORE generation rather than after, so out-of-domain queries don't
# trigger a 50s+ generation call that produces rambling output.
#
# TUNING (v7.1, round 1): lowered 0.5 -> 0.35. At 0.5, borderline-but-real
# queries (e.g. "why might MyCheckr registration fail…", reranker≈0.43)
# were shunted to clarify/reject and never got a chance to answer. Genuine
# out-of-domain queries ("capital of France") score ≈0.0, so 0.35 still
# rejects those cleanly; the grounding check (0.55) + suppression remain the
# backstop against hallucination on anything borderline that slips through.
RETRIEVAL_GATE_THRESHOLD = 0.35

# Above this, treat retrieval as unambiguous even with a borderline score,
# as long as results aren't scattered across many sources (see
# text_utils.retrieval_confidence_band).
AMBIGUOUS_CEILING = 0.65

# Context relative-score floor: a chunk enters the generation context only
# if its rerank score is at least this fraction of the TOP chunk's score.
# At 0.5: top=0.9996 admits chunks >= 0.4998 (drops boilerplate stragglers
# scoring ~0.2-0.4); top=0.74 admits >= 0.37 (borderline queries keep
# their supporting chunks). Relative, not absolute, so it adapts to query
# difficulty. Env-overridable for tuning without a code change.
CONTEXT_FLOOR_RATIO = float(os.getenv("CONTEXT_FLOOR_RATIO", "0.5"))

# Internal-only documents excluded from answering by default (comma-
# separated substrings matched case-insensitively against source names).
# Override with the EXCLUDED_SOURCES env var; set it to "" to expose the
# full corpus (internal deployments, eval_cases_api.json runs).
EXCLUDED_SOURCES = [
    s.strip().lower()
    for s in os.getenv("EXCLUDED_SOURCES", "icu_network_api").split(",")
    if s.strip()
]

SNIPPET_LEN = 160


# Matches the "[Doc — Section]\n" (or "[Doc]\n") breadcrumb prefix that
# ingest.py prepends to each stored chunk to aid retrieval. Anchored at the
# start and non-greedy so it only removes the single leading prefix line.
_BREADCRUMB_RE = re.compile(r"^\[[^\]\n]*\]\n")


_PREAMBLE_RE = re.compile(
    r"^(?:"
    r"based\s+(?:solely\s+)?(?:on|upon)\s+(?:the\s+)?(?:provided\s+|given\s+)?(?:context|information|documents?|knowledge\s+base)"
    r"|according\s+to\s+(?:the\s+)?(?:provided\s+|given\s+)?(?:context|information|documents?)"
    r"|from\s+(?:the\s+)?(?:provided\s+|given\s+)?context"
    r")\s*[,:.]?\s*",
    re.IGNORECASE,
)


def _strip_preamble(answer: str) -> str:
    """Remove 'Based solely on the provided context, ...' style preambles
    (v8.5). A prompt instruction forbidding them (v8.4.2) reduced but did
    not eliminate the tic — DeepSeek in particular keeps emitting them.
    Deterministic post-processing is the only reliable fix. Applied to
    every generated answer regardless of provider; re-capitalizes the
    first character of what remains."""
    stripped = _PREAMBLE_RE.sub("", answer.strip(), count=1)
    if stripped and stripped != answer.strip():
        return stripped[0].upper() + stripped[1:]
    return answer


def _lexically_supported(answer: str, chunks: list[dict]) -> bool:
    """Second chance for grounding failures on table-like text (v8.4.2).

    The NLI grounding model is unreliable at entailment against shredded
    table prose — observed: 'what is the weight of the MyCheckr Mini'
    generated the correct '152 g' from a retrieved technical-data chunk
    that literally contained 'MyCheckr Mini: 152 g', but NLI scored it
    0.028 and v8.4's enforcement suppressed a correct answer.

    If the answer's factual payload — its numbers (with units) — appears
    verbatim in the context, that is stronger evidence than an NLI score
    on garbled table text. Only answers that CONTAIN numeric claims can
    pass this way, and ALL their numbers must be present in the context;
    prose answers with no numbers still live or die by NLI alone, so this
    does not weaken the Cisco-class enforcement (that answer's numbers,
    e.g. '8 characters', came from the doc too, but it was prose-heavy
    and its policy claims failed NLI — see note below).

    Deliberately narrow: number-bearing answers where every number is
    context-supported AND the answer is short (<= 3 sentences, typical of
    factual lookups). Long synthesized answers must pass NLI proper.
    """
    numbers = re.findall(r"\d+(?:\.\d+)?", answer)
    if not numbers:
        return False
    if answer.count(".") > 3 or len(answer) > 400:
        return False
    context = " ".join(c.get("text", "") for c in chunks)
    return all(n in context for n in set(numbers))


def _normalize_query(q: str) -> str:
    """Light cleanup of user phrasing quirks that derail the small local
    generator without affecting retrieval: collapse repeated terminal
    punctuation ("???" -> "?") and de-shout queries that are (almost)
    entirely uppercase. Meaning-preserving; applied only to the working
    copy of the query — the raw input is still logged/stored as typed."""
    s = q.strip()
    s = re.sub(r"([?!.,])\1+", r"\1", s)
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        s = s.lower()
    return s


def _strip_breadcrumb(result: dict) -> dict:
    """Return a copy of a retrieval result with the ingest-time breadcrumb
    prefix removed from its text, so generation and grounding operate on the
    original source text. No-op for chunks stored before breadcrumbs existed
    (the regex simply doesn't match), so it's safe on a mixed collection."""
    text = result.get("text", "")
    stripped = _BREADCRUMB_RE.sub("", text, count=1)
    if stripped == text:
        return result
    out = dict(result)
    out["text"] = stripped
    return out

# session_id is required for correct multi-turn behaviour. Callers that
# omit it land in this shared bucket — fine for a one-off manual request,
# but it means unrelated callers can see each other's "previous query"
# during condensation. Every real client (app.py, test_queries.py)
# generates and sends its own id.
DEFAULT_SESSION_ID = "default"

APP_STATE = {
    "ready": False,
    "progress": 0,
    "message": "Starting",
    "error": None,
}
APP_STATE_LOCK = threading.Lock()


class QueryRequest(BaseModel):
    q: str
    session_id: str | None = None
    deepseek_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    # "Rethink with a different model": when set, skips routing/fallback
    # and calls this exact (provider, model) directly.
    force_provider: str | None = None
    force_model: str | None = None
    # Scope retrieval to one previously-seen source (from a clickable
    # source link) — "ask more about this document".
    source_filter: str | None = None
    product: str | None = None   # product OR category key (scope)
    category: str | None = None  # v10.3 category context (for product disambiguation)


class DeleteSourceRequest(BaseModel):
    source: str


class SourceChunksRequest(BaseModel):
    chunk_ids: list[str]


class ClearSessionRequest(BaseModel):
    session_id: str


def _set_app_state(*, ready=None, progress=None, message=None, error=None):
    with APP_STATE_LOCK:
        if ready is not None:
            APP_STATE["ready"] = ready
        if progress is not None:
            APP_STATE["progress"] = progress
        if message is not None:
            APP_STATE["message"] = message
        if error is not None:
            APP_STATE["error"] = error


def _warmup_stack():
    try:
        _set_app_state(progress=5, message="Initializing database")
        get_collection()

        _set_app_state(progress=20, message="Loading embeddings")
        _get_embedding_model()

        _set_app_state(progress=45, message="Loading reranker")
        _get_reranker_model()

        _set_app_state(progress=70, message="Loading grounding model")
        _get_nli_model()

        # v8.6: local LLMs are NO LONGER auto-warmed at startup. They cost
        # significant RAM and load time, and in api mode they're not used
        # at all. The UI's settings panel loads them on demand via
        # POST /models/warmup (and unloads via POST /models/unload when
        # switching to online mode). First local-mode query without a
        # manual warmup still works — it just pays cold-load on that call.
        _set_app_state(progress=85, message="Local LLMs available (load via settings)")

        _set_app_state(progress=100, message="Ready", ready=True, error=None)
        logger.info("System warmup complete")
    except Exception as e:
        logger.exception("Startup warmup failed")
        _set_app_state(ready=False, progress=100, message="Startup failed", error=str(e))


@app.on_event("startup")
def startup_event():
    thread = threading.Thread(target=_warmup_stack, daemon=True)
    thread.start()


@app.get("/status")
def status():
    with APP_STATE_LOCK:
        return dict(APP_STATE)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats")
def stats():
    return get_stats()


@app.get("/rethink_options")
def rethink_options():
    """Models available for the 'rethink with a different model' feature."""
    return {"options": [{"provider": p, "model": m} for p, m in RETHINK_OPTIONS]}


@app.post("/reset")
def reset():
    """Full reset: wipes the document collection AND every conversation
    session's memory."""
    reset_collection()
    clear_memory()
    return {"status": "reset"}


@app.post("/clear_session")
def clear_session(payload: ClearSessionRequest):
    """Clear one conversation's memory without touching the document
    collection — backs a "New conversation" button."""
    clear_memory(payload.session_id)
    return {"status": "cleared", "session_id": payload.session_id}


@app.post("/delete_source")
def remove_source(payload: DeleteSourceRequest):
    removed = delete_source(payload.source)
    clear_memory()
    return {"removed_chunks": removed, "source": payload.source}


@app.post("/source_chunks")
def source_chunks(payload: SourceChunksRequest):
    """Fetch full chunk text for the given chunk ids — backs the
    clickable "view source" feature in the UI."""
    return {"chunks": get_chunks_by_ids(payload.chunk_ids)}


_INGEST_JOBS: dict = {}
_INGEST_LOCK = threading.Lock()


def _ingest_worker(job_id: str, content: bytes, filename: str, api_keys: dict,
                   category_key: str | None = None, product_key: str | None = None):
    def _progress(stage, done, total):
        with _INGEST_LOCK:
            _INGEST_JOBS[job_id].update(
                stage=stage, done=done, total=total,
                pct=round(done / total * 100, 1) if total else 0.0)
    try:
        count = ingest_file(content, filename, api_keys=api_keys, progress=_progress,
                            category_key=category_key, product_key=product_key)
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


@app.post("/upload")
async def upload(file: UploadFile = File(...),
                 category_key: str | None = Header(default=None),
                 product_key: str | None = Header(default=None),
                 x_user_id: str | None = Header(default=None)):
    """v10.x: ASYNC ingest. Returns a job_id immediately; the document is
    indexed in a background thread (doc2query on a large PDF can take
    minutes). Poll /upload/status/{job_id} for progress.

    v10.3: optional category_key + product_key headers (admin uploads)
    attach the ingested source to that product for scoping."""
    if not APP_STATE["ready"]:
        raise HTTPException(status_code=503, detail="System is still loading")

    content = await file.read()
    filename = file.filename
    job_id = str(uuid.uuid4())
    with _INGEST_LOCK:
        _INGEST_JOBS[job_id] = {"status": "starting", "file": filename,
                                "pct": 0.0, "done": False}
    threading.Thread(target=_ingest_worker,
                     args=(job_id, content, filename, {}, category_key, product_key),
                     daemon=True).start()
    return {"job_id": job_id, "file": filename, "status": "started"}


@app.get("/upload/status/{job_id}")
def upload_status(job_id: str):
    with _INGEST_LOCK:
        job = _INGEST_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job id")
    return job


def _build_sources(results: list[dict]) -> list[dict]:
    """
    Build clickable source objects: one entry per unique source filename,
    with the chunk ids belonging to it (for /source_chunks lookup) and a
    short snippet from its best-scoring chunk.
    """
    by_source: dict[str, dict] = {}

    for r in results:
        src = r.get("source", "unknown")
        if src not in by_source:
            by_source[src] = {
                "source": src,
                "chunk_ids": [],
                "snippet": r["text"][:SNIPPET_LEN].strip() + ("…" if len(r["text"]) > SNIPPET_LEN else ""),
            }
        by_source[src]["chunk_ids"].append(r.get("id"))

    return list(by_source.values())


@app.get("/settings")
def settings():
    """Current runtime settings for the UI (mode toggle, model state)."""
    return get_settings()


class ModeRequest(BaseModel):
    mode: str  # "local" (free) | "api" (online/DeepSeek)


@app.post("/settings/mode")
def set_mode(payload: ModeRequest):
    """Live mode switch driven by the UI's online/free toggle (v8.6).
    No restart required; router and condensation read the mode per-call."""
    try:
        mode = set_generation_mode(payload.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Generation mode switched to: {mode}")
    return {"mode": mode, **get_settings()}


class ModelsRequest(BaseModel):
    models: list[str] | None = None  # subset of ["mistral", "phi"]; None = both


class ProviderRequest(BaseModel):
    provider: str  # deepseek | openai | anthropic


@app.post("/settings/online_provider")
def set_provider(payload: ProviderRequest):
    """v9.1.1: choose which API answers in Online mode."""
    try:
        p = set_online_provider(payload.provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Online provider set to: {p}")
    return get_settings()


# ── Local model install check + pull-with-progress (v9.1.3) ─────────────
# The startup gate must handle three states: model loaded, model installed
# but cold, and model NOT INSTALLED AT ALL. /models/status distinguishes
# them; /models/pull downloads missing models in a background thread while
# /models/pull_status feeds a progress bar in the UI.
_PULL_STATE: dict = {}
_PULL_LOCK = threading.Lock()


def _ollama_base() -> str:
    from llm import OLLAMA_URL
    return OLLAMA_URL.replace("/api/generate", "")


@app.get("/models/status")
def models_status():
    """Which local models are installed in Ollama (and is Ollama up)."""
    import requests as _requests
    try:
        r = _requests.get(f"{_ollama_base()}/api/tags", timeout=5)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        installed = {m: any(n.split(":")[0] == m for n in names)
                     for m in ("mistral", "phi")}
        return {"ollama_up": True, "installed": installed, **get_settings()}
    except Exception as e:
        return {"ollama_up": False, "installed": {"mistral": False, "phi": False},
                "error": str(e), **get_settings()}


def _pull_worker(model: str):
    import json as _json
    import requests as _requests
    try:
        with _requests.post(f"{_ollama_base()}/api/pull",
                            json={"name": model}, stream=True, timeout=3600) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    d = _json.loads(line)
                except Exception:
                    continue
                total, done = d.get("total"), d.get("completed")
                pct = round(done / total * 100, 1) if total and done else None
                with _PULL_LOCK:
                    st = _PULL_STATE.setdefault(model, {})
                    st["status"] = d.get("status", "downloading")
                    if pct is not None:
                        st["pct"] = pct
                if d.get("status") == "success":
                    break
        with _PULL_LOCK:
            _PULL_STATE[model] = {"status": "success", "pct": 100.0, "done": True}
    except Exception as e:
        logger.warning(f"Model pull failed for {model}: {e}")
        with _PULL_LOCK:
            _PULL_STATE[model] = {"status": "error", "error": str(e), "done": True}


@app.post("/models/pull")
def models_pull(payload: ModelsRequest = None):
    """Download missing local models via Ollama, in the background.
    Poll /models/pull_status for progress."""
    models = (payload.models if payload and payload.models else ["phi", "mistral"])
    models = [m for m in models if m in ("phi", "mistral")]
    with _PULL_LOCK:
        for m in models:
            _PULL_STATE[m] = {"status": "starting", "pct": 0.0, "done": False}
    for m in models:
        threading.Thread(target=_pull_worker, args=(m,), daemon=True).start()
    return {"pulling": models}


@app.get("/models/pull_status")
def models_pull_status():
    with _PULL_LOCK:
        return dict(_PULL_STATE)


@app.post("/models/warmup")
def models_warmup(payload: ModelsRequest = None):
    """Manually load selected local models into Ollama (v8.6.1: the UI's
    mode dialog lets the user choose which to load). Startup no longer
    warms anything automatically."""
    models = (payload.models if payload and payload.models else ["phi", "mistral"])
    models = [m for m in models if m in ("phi", "mistral")]
    results = warmup_local_models(models)
    ok = all(results.values()) and bool(results)
    set_local_models_loaded(ok)
    return {"loaded": ok, "models": results}


@app.post("/models/unload")
def models_unload(payload: ModelsRequest = None):
    """Unload selected local models from Ollama memory (keep_alive=0).
    v8.6.1: the UI's mode dialog lets the user choose which to unload."""
    import requests as _requests
    from llm import OLLAMA_URL
    models = (payload.models if payload and payload.models else ["phi", "mistral"])
    models = [m for m in models if m in ("phi", "mistral")]
    results = {}
    for model in models:
        try:
            _requests.post(OLLAMA_URL,
                           json={"model": model, "prompt": "", "keep_alive": 0},
                           timeout=15)
            results[model] = True
        except Exception as e:
            logger.warning(f"Unload failed for {model}: {e}")
            results[model] = False
    if set(models) >= {"phi", "mistral"} and all(results.values()):
        set_local_models_loaded(False)
    return {"unloaded": all(results.values()), "models": results}


# ── Conversation history (v2.1) — registered users only ──────────────
# ⚠ resolve_user_id currently TRUSTS the X-User-Id header (insecure
# placeholder). Replace with signed-token verification before production
# — see conversations.py docstring. Anonymous users (no id) get None and
# never hit this store; their history stays browser-local.

@app.get("/conversations")
def conversations_list(x_user_id: str | None = Header(default=None)):
    uid = convo_store.resolve_user_id(x_user_id)
    if not uid:
        return {"conversations": [], "authenticated": False}
    return {"conversations": convo_store.list_conversations(uid), "authenticated": True}


@app.get("/conversations/{convo_id}")
def conversations_get(convo_id: str, x_user_id: str | None = Header(default=None)):
    uid = convo_store.resolve_user_id(x_user_id)
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    convo = convo_store.get_conversation(uid, convo_id)
    if not convo:
        raise HTTPException(status_code=404, detail="Not found")
    return convo


@app.delete("/conversations/{convo_id}")
def conversations_delete(convo_id: str, x_user_id: str | None = Header(default=None)):
    uid = convo_store.resolve_user_id(x_user_id)
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    ok = convo_store.delete_conversation(uid, convo_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True}


# ── Ingest version fingerprint (v10.4) ───────────────────────────────
# When ingestion LOGIC changes (chunking, breadcrumbs, doc2query), old
# vectors are stale. We fingerprint the ingest-affecting code; the UI
# compares it to what the current DB was built with and prompts a
# re-ingest if they differ. Bump INGEST_VERSION when you change ingest.
INGEST_VERSION = "10.4"


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


@app.get("/ingest/version")
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


def _stamp_ingest():
    try:
        with open(_INGEST_STAMP_PATH, "w") as f:
            f.write(_ingest_fingerprint())
    except Exception as e:
        logger.warning(f"ingest stamp write failed: {e}")


# ── Docs folder reload (v10.4) ───────────────────────────────────────
# Keep source docs in ./corpus (mounted in Docker). This ingests any that
# aren't already in the DB — handy after an ingest-logic change: wipe the
# DB, hit reload, everything re-ingests from the folder.
CORPUS_DIR = os.getenv("CORPUS_DIR", "corpus")


@app.post("/ingest/reload_folder")
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


# ── FAQ (v10.4) — doc2query questions per product, admin-editable ─────

@app.get("/faq")
def faq_list(product: str | None = None):
    """Generated questions for a product (or all). Public read."""
    return {"faq": faq_store.list_for_product(product)}


class FaqEdit(BaseModel):
    answer: str


@app.patch("/faq/{faq_id}")
def faq_edit(faq_id: str, payload: FaqEdit, x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    updated = faq_store.update_answer(faq_id, payload.answer)
    if not updated:
        raise HTTPException(status_code=404, detail="FAQ entry not found")
    return updated


@app.delete("/faq/{faq_id}")
def faq_delete(faq_id: str, x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    if not faq_store.delete_entry(faq_id):
        raise HTTPException(status_code=404, detail="FAQ entry not found")
    return {"deleted": True}


class ReassignReq(BaseModel):
    source: str
    category_key: str
    product_key: str


@app.post("/admin/reassign_source")
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
    metas = got["metadatas"]
    for m in metas:
        m["category"] = payload.category_key
        m["product"] = payload.product_key
    col.update(ids=ids, metadatas=metas)
    # bust the BM25 cache so the new tags take effect
    try:
        from retrieval_db import _invalidate_bm25_cache
        _invalidate_bm25_cache()
    except Exception:
        pass
    return {"reassigned": len(ids), "source": payload.source,
            "category": payload.category_key, "product": payload.product_key}


@app.get("/admin/sources")
def admin_sources(x_admin_password: str | None = Header(default=None)):
    """List ingested sources with their current tags (for the re-assign UI)."""
    _require_admin(x_admin_password)
    from db import get_collection
    col = get_collection()
    got = col.get(include=["metadatas"])
    seen = {}
    for m in got.get("metadatas", []):
        src = m.get("source", "unknown")
        if src not in seen:
            seen[src] = {"source": src, "category": m.get("category", ""),
                         "product": m.get("product", "")}
    return {"sources": list(seen.values())}


@app.get("/catalog")
def get_catalog():
    """Category -> product tree for the pre-chat picker (v10.3)."""
    return catalog_mod.catalog()


# ── Admin panel (v10.3) — password-gated catalog + doc management ──────
# ⚠ TEMPORARY AUTH: a single shared password ("admin" by default, override
# with ADMIN_PASSWORD). This is a placeholder so you can manage the
# catalog now; it is NOT a real auth system. Before public exposure,
# replace _require_admin with the website's admin identity check (same
# effort as the user-token seam in conversations.py).

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")


def _require_admin(x_admin_password: str | None):
    if (x_admin_password or "") != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Admin password required")


class CategoryReq(BaseModel):
    key: str
    name: str


class ProductReq(BaseModel):
    category_key: str
    key: str
    name: str
    sources: list[str] | None = None


class AttachReq(BaseModel):
    category_key: str
    product_key: str
    source: str


@app.post("/admin/login")
def admin_login(x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    return {"ok": True}


@app.post("/admin/category")
def admin_add_category(payload: CategoryReq, x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    try:
        return catalog_mod.add_category(payload.key, payload.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/admin/category")
def admin_rename_category(payload: CategoryReq, x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    try:
        return catalog_mod.rename_category(payload.key, payload.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/admin/category/{key}")
def admin_delete_category(key: str, x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    return catalog_mod.delete_category(key)


@app.post("/admin/product")
def admin_add_product(payload: ProductReq, x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    try:
        return catalog_mod.add_product(payload.category_key, payload.key,
                                       payload.name, payload.sources)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/admin/product/{category_key}/{product_key}")
def admin_delete_product(category_key: str, product_key: str,
                         x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    return catalog_mod.delete_product(category_key, product_key)


@app.post("/admin/attach_source")
def admin_attach_source(payload: AttachReq, x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    try:
        return catalog_mod.attach_source(payload.category_key, payload.product_key, payload.source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/query")
def query(payload: QueryRequest, x_user_id: str | None = Header(default=None)):
    if not APP_STATE["ready"]:
        raise HTTPException(status_code=503, detail="System is still loading")

    q = payload.q
    session_id = payload.session_id or DEFAULT_SESSION_ID
    deepseek_api_key = payload.deepseek_api_key
    api_keys = {"deepseek": payload.deepseek_api_key,
                "openai": payload.openai_api_key,
                "anthropic": payload.anthropic_api_key}
    start_total = time.time()

    # ── Conversational query resolution (Rewrite-Retrieve-Read) ──
    # Resolves pronoun/ellipsis-dependent follow-ups ("give me that from
    # step 1") into a standalone query using THIS session's own history,
    # via a fast local LLM call rather than a surface-level heuristic.
    # See llm.condense_query / text_utils' "conversational query
    # condensation" section for the full rationale and references.
    #
    # `resolved_query` is used for everything downstream (retrieval,
    # routing, extraction, the final generation prompt) — by the time
    # we've worked out what the user actually means, that's the query
    # that matters everywhere. The RAW `q` is what gets stored back into
    # memory and logged, so future condensation prompts see the
    # conversation as the user actually typed it.
    history = get_history(session_id)
    # Normalize BEFORE condensation/retrieval: all-caps, repeated
    # punctuation ("DEFAULT LOGIN???") retrieves fine (embeddings are
    # case-tolerant) but reliably derails the small local generator,
    # which bails to the not-found template on malformed-looking input.
    # Raw `q` is still what gets stored in memory/logs.
    resolved_query = condense_query(_normalize_query(q), history)

    # CONVERSATIONAL FALLBACK (v8.4.3). Follow-ups were systematically
    # dying: condense_query runs phi on a 20s timeout (half its normal
    # budget) and silently returns the raw fragment on failure; phi also
    # mis-resolves pronouns even when it responds. A fragment like "how
    # is it powered" then retrieves at ~0.005 and gets rejected — the
    # user experiences a bot that can't hold a conversation. Net: if the
    # query references prior context but condensation left it (near)
    # unchanged, retrieve on LAST QUESTION + FRAGMENT combined. "does
    # MyCheckr Mini have a screen — how is it powered" hits Mini power
    # chunks trivially. Deterministic, zero-latency, no LLM dependency.
    # Only fires when markers AND history exist, so standalone queries
    # are never touched.
    if (history and has_reference_markers(q)
            and resolved_query.strip().lower() == _normalize_query(q).strip().lower()):
        last_q = history[-1].get("q", "")
        if last_q:
            frag = _normalize_query(q)
            # v10.2.1: FRAGMENT FIRST. The old "last_q — fragment" order let
            # the PREVIOUS entity dominate ranking — observed: "what about
            # the MyCheckr?" after Mini questions retrieved Mini chunks and
            # answered about the Mini. The current turn's words must lead.
            # Additionally, if the fragment itself names a domain entity
            # (has_domain_vocabulary), it likely carries its own subject —
            # an entity SWITCH — so weight history less by appending only
            # the last question's tail context.
            if has_domain_vocabulary(frag):
                resolved_query = f"{frag} ({last_q})"
            else:
                resolved_query = f"{frag} — {last_q}"
            logger.info(f"Follow-up fallback combined query: {resolved_query[:80]}")

    # ── Retrieval ────────────────────────────
    t1 = time.time()
    # v10.5: scope by EXPLICIT tag — product if chosen, else category.
    # Chunks carry these tags from upload; no filename matching.
    _scope = None
    if payload.product:
        _scope = {"product": payload.product}
    elif payload.category:
        _scope = {"category": payload.category}
    results = retrieve_from_db(resolved_query, top_k=10,
                               source_filter=payload.source_filter,
                               scope=_scope)
    # CORPUS SCOPING (v8.4): internal-only documents are excluded from
    # answering unless the caller explicitly filters to a source. Adding
    # the ICU Network API doc to the corpus polluted public answers
    # (compliance boilerplate bleeding into product definitions) and gave
    # off-domain queries material to latch onto (its password-policy
    # section powered a confident answer to "reset my Cisco router
    # password"). Configure via EXCLUDED_SOURCES (comma-separated
    # substrings, matched case-insensitively against chunk source names).
    # Set EXCLUDED_SOURCES="" to disable, e.g. for internal deployments
    # or when running eval_cases_api.json.
    if EXCLUDED_SOURCES and not payload.source_filter:
        results = [
            r for r in results
            if not any(ex in (r.get("source") or "").lower() for ex in EXCLUDED_SOURCES)
        ]
    results = rerank(resolved_query, results, top_k=5)
    retrieval_time = time.time() - t1

    top_score = results[0].get("rerank_score", 0.0) if results else 0.0
    confidence = retrieval_confidence_band(results, RETRIEVAL_GATE_THRESHOLD, AMBIGUOUS_CEILING)

    # ── No relevant content at all ───────────
    if confidence == "none":
        total_time = time.time() - start_total

        # A query that referenced prior context (or got rewritten by the
        # condensation step) is a follow-up, not a fresh out-of-domain
        # question. Retrieval failing on it doesn't mean the topic is
        # outside the knowledge base — it usually means the rewrite/
        # retrieval pairing didn't land. Treating it the same as a
        # genuinely standalone miss (e.g. "capital of France") produces
        # the flat, conversation-breaking "I could not find that"
        # response the user is reporting. Ask for clarification instead;
        # standalone misses (no reference markers, no history) are
        # completely unaffected and still get the blunt rejection.
        is_followup = is_followup_turn(q, history, resolved_query)

        # A STANDALONE query (no history dependency) can still be too
        # vague to retrieve well while clearly being about something in
        # our domain — "explain why device registration might fail"
        # never says WHICH device, "post installation verification
        # installer sign off" never says which product's installation.
        # These aren't out-of-domain misses like "capital of France"
        # (zero domain vocabulary); they're underspecified ones. Ask
        # which device/product instead of flatly rejecting, and surface
        # whatever low-scoring candidate sources retrieval DID turn up
        # as a concrete hint rather than a generic "which one?".
        is_vague_in_domain = (not is_followup) and has_domain_vocabulary(q)

        if is_followup:
            # is_followup_turn requires non-empty history, so this is
            # always available here — referencing the actual prior topic
            # instead of a generic line is what makes this feel like a
            # continued conversation rather than a second flat rejection.
            last_topic = history[-1]["q"]
            answer = (
                f"I don't have more detail beyond what we already covered for "
                f'"{last_topic}" — could you tell me more concretely what you\'d '
                f"like me to check or expand on?"
            )
            role_out = "clarify"
            reason = "low_retrieval_confidence_followup"
            needs_clarification = True
            clarification_options = build_clarification_options("followup", history, results)
        elif is_vague_in_domain:
            candidate_sources = sorted({r.get("source") for r in results[:4] if r.get("source")})
            hint = f" The closest matches I found were in: {', '.join(candidate_sources)}." if candidate_sources else ""
            answer = (
                "I'm not sure which specific device or product area you mean here "
                "— could you say which one you're asking about?" + hint
            )
            role_out = "clarify"
            reason = "ambiguous_in_domain_query"
            needs_clarification = True
            clarification_options = build_clarification_options("ambiguous_in_domain", history, results)
        else:
            answer = "I could not find that in the knowledge base."
            role_out = "rejected"
            reason = "low_retrieval_confidence"
            needs_clarification = False
            clarification_options = []

        log_interaction(q, answer, role_out, "none", [], grounding_score=None, flagged=False)
        return {
            "answer": answer,
        "response_time_ms": round(total_time * 1000),
            "model": "none",
            "role": role_out,
            "needs_clarification": needs_clarification,
            "clarification_options": clarification_options,
            "reason": reason,
            "retrieval_score": round(top_score, 4),
            "resolved_query": resolved_query if resolved_query != q else None,
            "sources": [],
            "response_time": round(total_time, 3),
        }

    # ── Ambiguous: ask a clarifying question instead of guessing ──
    if confidence == "ambiguous":
        candidate_sources = sorted({r.get("source") for r in results[:4] if r.get("source")})
        total_time = time.time() - start_total
        clarifying = (
            "I found a few different sections that could be relevant — "
            "could you clarify which part you mean? "
            f"Possible areas: {', '.join(candidate_sources)}."
        )
        log_interaction(q, clarifying, "clarify", "none", candidate_sources,
                        grounding_score=None, flagged=False)
        return {
            "answer": clarifying,
        "response_time_ms": round(total_time * 1000),
            "model": "none",
            "role": "clarify",
            "needs_clarification": True,
            "candidate_sources": candidate_sources,
            "resolved_query": resolved_query if resolved_query != q else None,
            "sources": _build_sources(results),
            "retrieval_score": round(top_score, 4),
            "response_time": round(total_time, 3),
        }

    # ── Routing ──────────────────────────────
    if payload.force_provider and payload.force_model:
        role = "rethink"
    else:
        role, (provider, model) = route_model(resolved_query)

    # ── Structured extraction shortcut ───────
    if role != "rethink":
        t2 = time.time()
        extracted = extract_structured_block(results[:5], query=resolved_query)
        extraction_time = time.time() - t2

        if extracted and role == "extract":
            total_time = time.time() - start_total
            sources = _build_sources(results)
            log_interaction(q, extracted, "extract", "structured",
                            [s["source"] for s in sources],
                            grounding_score=None, flagged=False)
            add_to_memory(session_id, q, extracted)
            return {
                "answer": extracted,
            "response_time_ms": round(total_time * 1000),
                "mode": "extracted",
                "model": "structured",
                "role": "extract",
                "provider": "local",
                "fallback_used": False,
                "resolved_query": resolved_query if resolved_query != q else None,
                "response_time": round(total_time, 3),
                "sources": sources,
            }
    else:
        extraction_time = 0.0

    # ── Context ──────────────────────────────
    # No separate memory-context injection here: resolved_query already
    # carries whatever context was needed from prior turns (that's the
    # whole point of the condensation step above), so the document
    # context retrieved against IT is what the model should answer from.
    # Strip the ingest-time breadcrumb prefix ("[Doc — Section]\n...") that
    # was prepended to help RETRIEVAL disambiguate near-identical sections.
    # It has already done its job by this point (retrieve_from_db + rerank
    # ran on the stored, prefixed text). Generation and the grounding NLI
    # check must see the ORIGINAL chunk text only — the prefix isn't part of
    # the source document and would otherwise pollute grounding scores and
    # leak "[Doc — Section]" fragments into answers.
    # CONTEXT SELECTION (v8.4.1): relative-score floor. Taking a flat
    # results[:3] lets weak stragglers ride into the prompt behind a
    # strong top hit — observed: "what is MyCheckr" retrieved the General
    # Description chunk at ~0.9996, but a WEEE/waste-disposal compliance
    # chunk from the SAME manual limped into the top-3 and the model
    # stitched both into one answer. Grounding can't catch it (the text
    # IS from the docs — irrelevant but grounded). Keep a chunk only if
    # it scores at least CONTEXT_FLOOR_RATIO of the top chunk's score;
    # a strong hit no longer drags noise in with it, while genuinely
    # close-scoring chunks (multi-chunk answers) all still qualify.
    _top = results[0].get("rerank_score", 0.0) if results else 0.0
    top_chunks = [
        _strip_breadcrumb(r) for r in results[:3]
        if r.get("rerank_score", 0.0) >= _top * CONTEXT_FLOOR_RATIO
    ]
    context = "\n\n".join(r["text"][:1200] for r in top_chunks)

    prompt = f"""<context>
{context}
</context>

Using ONLY the information inside <context> above, answer the question below.
Answer directly - do NOT begin with phrases like "Based on the context" or "Based solely on the provided context"; state the answer itself.
If the context contains multiple similar-looking facts serving different purposes (e.g. different credential sets for different actions), give ONLY the one matching the question's subject and briefly note what the other is for.
If the context does not contain enough information, respond with exactly:
"I could not find that in the knowledge base."
Do not use any knowledge from outside the context.

Question: {resolved_query}
Answer:"""

    # ── LLM ──────────────────────────────────
    t3 = time.time()
    if role == "rethink":
        output = generate(payload.force_provider, prompt, payload.force_model, deepseek_api_key, api_keys=api_keys)
        if not output:
            output = {"text": "", "model": payload.force_model, "provider": payload.force_provider}
        output["fallback_used"] = False
    else:
        output = generate_with_fallback(role, prompt, deepseek_api_key=deepseek_api_key, api_keys=api_keys)
    llm_time = time.time() - t3

    raw_text = output.get("text", "").strip()
    generation_failed = (output.get("model") == "none") or not raw_text
    answer = _strip_preamble(raw_text) if raw_text else "I could not generate a response."

    # ── Grounding check ──────────────────────
    refusal = is_refusal(answer)
    template_leak = is_template_leak(answer)

    # A total generation failure (every provider in the fallback chain
    # failed — output model=="none", e.g. mistral timed out AND no
    # DeepSeek key) must NOT be run through the grounding check. The
    # sentinel string "I was unable to generate a response." is generic
    # enough that NLI entailment scored it 0.993 "grounded" in
    # production — a meaningless score for a non-answer. Treat it as an
    # ungrounded non-answer up front.
    if generation_failed:
        is_grounded, grounding_score = False, None
        flagged = True
        refusal = False
    # Checked BEFORE the NLI grounding call: a known chat-template leak
    # (e.g. "...a chat between a curious user and an artificial
    # intelligence assistant...") makes no concrete factual claim, so
    # NLI entailment has nothing to contradict and can score it as
    # "grounded" — reproduced in production at 0.934, well above
    # threshold. is_template_leak catches this deterministically; the
    # semantic check is skipped entirely when it fires, same as for a
    # clean refusal.
    elif template_leak:
        is_grounded, grounding_score = False, 0.0
        flagged = True
        refusal = False
    elif refusal:
        is_grounded, grounding_score = True, None
        flagged = False
    else:
        is_grounded, grounding_score = check_grounding(
            answer, top_chunks, threshold=GROUNDING_THRESHOLD
        )
        # NLI fails on table-shredded text; give short numeric answers a
        # lexical second chance (see _lexically_supported docstring).
        if not is_grounded and _lexically_supported(answer, top_chunks):
            is_grounded = True
            logger.info("Grounding rescued by lexical containment "
                        f"(nli={grounding_score}) for: {resolved_query[:60]}")
        flagged = not is_grounded

    # ── DeepSeek escalation on grounding failure ──
    # Auto-retry on DeepSeek whenever the local answer is flagged (bad
    # grounding, a template leak, or a total local failure) AND a key is
    # available. This is the "auto-retry on DeepSeek if key present"
    # behaviour.
    escalated = False
    if flagged and role != "rethink" and output.get("provider") in ("local", "none"):
        logger.warning(
            f"Flagged local answer (grounding={grounding_score}, "
            f"template_leak={template_leak}, failed={generation_failed}) — "
            f"escalating to DeepSeek for: {resolved_query[:60]}"
        )
        deepseek_result = generate("deepseek", prompt, "deepseek-chat", deepseek_api_key=deepseek_api_key)
        if deepseek_result and deepseek_result.get("text"):
            output = deepseek_result
            answer = _strip_preamble(output["text"].strip())
            escalated = True
            template_leak = is_template_leak(answer)
            if template_leak:
                is_grounded, grounding_score, flagged = False, 0.0, True
            else:
                is_grounded, grounding_score = check_grounding(
                    answer, top_chunks, threshold=GROUNDING_THRESHOLD
                )
                if not is_grounded and _lexically_supported(answer, top_chunks):
                    is_grounded = True
                    logger.info("Escalated answer rescued by lexical "
                                f"containment (nli={grounding_score})")
                flagged = not is_grounded

    # ── Suppress an answer we could not verify ──
    # PRECISION-FIRST ENFORCEMENT (v8.4): suppress on ANY unresolved flag,
    # not just template leaks / total failures. Previously an answer whose
    # NLI grounding score FAILED the threshold was flagged and logged but
    # still SERVED to the user — observed in production: an off-domain
    # "reset my Cisco router password" query retrieved a stray password-
    # policy chunk, generated a confident walkthrough, scored 0.055
    # grounding, and was displayed anyway. The grounding check is the last
    # line of defense; a verdict it isn't allowed to enforce is theater.
    # After the escalation attempt above, if the best answer we have is
    # still ungrounded, refuse rather than serve it.
    if template_leak or generation_failed or flagged:
        answer = "I could not find that in the knowledge base."
        if template_leak or generation_failed:
            grounding_score = None
        flagged = True

    # ── Memory + logging ──────────────────────
    add_to_memory(session_id, q, answer)
    sources = _build_sources(results)
    log_interaction(q, answer, role, output.get("model"),
                    [s["source"] for s in sources],
                    grounding_score=grounding_score, flagged=flagged)

    total_time = time.time() - start_total

    # v2.1: persist for registered users (anonymous -> uid None -> skip;
    # their history stays browser-local). convo_id echoed back so the
    # client can continue the same server-side conversation.
    _uid = convo_store.resolve_user_id(x_user_id)
    _convo_id = None
    if _uid:
        try:
            _convo_id = convo_store.save_turn(
                _uid, payload.session_id, payload.product, q, answer, sources)
        except Exception as e:
            logger.warning(f"Conversation save failed (non-fatal): {e}")

    return {
        "answer": answer,
        "response_time_ms": round(total_time * 1000),
        "conversation_id": _convo_id,
        "role": role,
        "model": output.get("model"),
        "provider": output.get("provider"),
        "fallback_used": output.get("fallback_used", False),
        "escalated_to_deepseek": escalated,
        "grounding_score": grounding_score,
        "flagged": flagged,
        "retrieval_score": round(top_score, 4),
        "resolved_query": resolved_query if resolved_query != q else None,
        "timing": {
            "retrieval_time": round(retrieval_time, 3),
            "extraction_time": round(extraction_time, 3),
            "llm_time": round(llm_time, 3),
            "total_time": round(total_time, 3),
        },
        "sources": sources,
    }