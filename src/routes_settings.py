"""Runtime settings: generation mode and provider, local models, sessions
and saved conversations, and the odd status endpoint.

Moved out of main.py verbatim (2026-09-25). Registered on the app through
app.include_router; paths, parameters and responses are unchanged.
"""

import logging
import threading

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import conversations as convo_store
from db import get_stats, reset_collection
from guards import _require_admin
from llm import warmup_local_models
from memory import clear_memory
from providers import _available_providers
from runtime_config import (get_settings, set_generation_mode,
                            set_local_models_loaded, set_online_provider)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/stats")
def stats():
    return get_stats()


@router.get("/rethink_options")
def rethink_options():
    """Models available for the 'rethink with a different model' feature.

    Filtered by what is actually reachable. Unfiltered, this offered two
    Ollama models with no Ollama running and a retired DeepSeek alias -- three
    options, none of which could answer, presented as the remedy for an answer
    the user was already unhappy with.
    """
    from llm import _rethink_options
    reachable = {p["key"] for p in _available_providers()}
    opts = [{"provider": p, "model": m} for p, m in _rethink_options()
            if p in reachable]
    return {"options": opts}


@router.post("/reset")
def reset():
    """Full reset: wipes the document collection AND every conversation
    session's memory."""
    reset_collection()
    clear_memory()
    return {"status": "reset"}


class ClearSessionRequest(BaseModel):
    session_id: str


@router.post("/clear_session")
def clear_session(payload: ClearSessionRequest):
    """Clear one conversation's memory without touching the document
    collection — backs a "New conversation" button."""
    clear_memory(payload.session_id)
    return {"status": "cleared", "session_id": payload.session_id}


@router.get("/settings")
def settings():
    """Current runtime settings for the UI (mode toggle, model state)."""
    return get_settings()


class ModeRequest(BaseModel):
    mode: str  # "local" (free) | "api" (online/DeepSeek)


@router.post("/settings/mode")
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


@router.post("/settings/online_provider")
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


@router.get("/models/status")
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
        # The exception text stays in the server log. Returned to the caller
        # it exposed internals -- the Ollama URL, and whatever requests put in
        # the message (CodeQL py/stack-trace-exposure); no UI reads it.
        logger.warning("Ollama status check failed: %s", e)
        return {"ollama_up": False, "installed": {"mistral": False, "phi": False},
                "error": "Ollama is not reachable", **get_settings()}


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


@router.post("/models/pull")
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


@router.get("/models/pull_status")
def models_pull_status():
    with _PULL_LOCK:
        return dict(_PULL_STATE)


@router.post("/models/warmup")
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


@router.post("/models/unload")
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

@router.get("/conversations")
def conversations_list(x_user_id: str | None = Header(default=None)):
    uid = convo_store.resolve_user_id(x_user_id)
    if not uid:
        return {"conversations": [], "authenticated": False}
    return {"conversations": convo_store.list_conversations(uid), "authenticated": True}


@router.get("/conversations/{convo_id}")
def conversations_get(convo_id: str, x_user_id: str | None = Header(default=None)):
    uid = convo_store.resolve_user_id(x_user_id)
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    convo = convo_store.get_conversation(uid, convo_id)
    if not convo:
        raise HTTPException(status_code=404, detail="Not found")
    return convo


@router.delete("/conversations/{convo_id}")
def conversations_delete(convo_id: str, x_user_id: str | None = Header(default=None)):
    uid = convo_store.resolve_user_id(x_user_id)
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    ok = convo_store.delete_conversation(uid, convo_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True}


@router.get("/admin/providers")
def admin_providers(x_admin_password: str | None = Header(default=None)):
    """Providers the console may offer: key present, or local warmed.

    An empty list is meaningful, not an error — it means nothing is
    configured and the caller should fall back to the server's own default
    chain rather than naming a provider.
    """
    _require_admin(x_admin_password)
    return {"providers": _available_providers()}
