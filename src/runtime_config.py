"""Runtime-mutable settings (v8.6).

GENERATION_MODE was an env var read at import — fine for deployment
config, but the UI needs a live toggle (online/DeepSeek vs free/local)
without a backend restart. This module is the single source of truth;
router.py and llm.py read it per-call, and main.py exposes GET/POST
/settings to drive it from the frontend.

Thread-safe via a lock; initial value still honors the env var so
deployment configs keep working.
"""
import os
import threading

_lock = threading.Lock()
_state = {
    # v9.1.2: default is now ONLINE (api). Set GENERATION_MODE=local for
    # offline-first deployments and for eval runs against the local baseline.
    "generation_mode": os.getenv("GENERATION_MODE", "api").strip().lower(),
    "local_models_loaded": False,   # set by /models/warmup, cleared by /models/unload
    # v9.1.1: which API answers in Online mode — deepseek | openai | anthropic
    "online_provider": os.getenv("ONLINE_PROVIDER", "deepseek").strip().lower(),
}


def get_generation_mode() -> str:
    with _lock:
        return _state["generation_mode"]


def set_generation_mode(mode: str) -> str:
    mode = (mode or "").strip().lower()
    if mode not in ("local", "api"):
        raise ValueError(f"invalid mode: {mode!r} (expected 'local' or 'api')")
    with _lock:
        _state["generation_mode"] = mode
    return mode


def get_settings() -> dict:
    with _lock:
        return dict(_state)


def set_local_models_loaded(loaded: bool) -> None:
    with _lock:
        _state["local_models_loaded"] = bool(loaded)


def get_online_provider() -> str:
    with _lock:
        return _state["online_provider"]


def set_online_provider(provider: str) -> str:
    provider = (provider or "").strip().lower()
    if provider not in ("deepseek", "openai", "anthropic"):
        raise ValueError(f"invalid provider: {provider!r}")
    with _lock:
        _state["online_provider"] = provider
    return provider
