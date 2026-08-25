"""Access policy: who may use the assistant, and how much.

The limits here were previously env vars read once at import in quota.py.
That is right for deployment config and wrong for anything an operator needs
to change: raising a member's daily allowance meant editing .env and
restarting the server, which is not something you do while someone is
waiting. This module holds the same settings, persisted, and readable
per-call so a change takes effect on the next request.

Env vars are still honoured as the INITIAL value, so existing deployment
configs keep working — the file only records what an operator has since
changed. quota.py reads through here rather than its own module constants.

Persisted to policy.json (gitignored like the other runtime stores). Losing
it falls back to the env/default values, so it is not catastrophic to lose,
unlike accounts.json.
"""
import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

_PATH = os.getenv("POLICY_PATH", "policy.json")
_lock = threading.Lock()

# 0 means "no limit" for the per-session caps. Daily credit allowances use 0
# to mean genuinely zero, which is why they are not interchangeable.
_DEFAULTS = {
    # ── Anonymous visitors ─────────────────────────────────────────────
    # OFF by default, and deliberately so. quota.py documents the invariant:
    # with this off, no LLM call is reachable without an account, which
    # removes the public cost exposure and the public prompt-injection
    # surface in one decision. Turning it on is a real choice with a real
    # bill attached, which is why it is a switch and not a default.
    "anon_llm_enabled": os.getenv("WIDGET_AI_DRAFT_ANONYMOUS", "").strip().lower()
                        in ("1", "true", "yes"),
    # What an anonymous visitor is told when the assistant is account-only.
    "anon_notice": (
        "Full AI support is for account holders. I can still answer from our "
        "reviewed FAQs, or put you in touch with our team."
    ),
    # Credits an anonymous visitor may spend on the generative pipeline once
    # anon_llm_enabled is on. Kept small: this is the public internet.
    "anon_llm_credits": int(os.getenv("QUOTA_ANON_LLM", "3")),

    # ── Daily allowances (per quota window) ────────────────────────────
    "member_daily_credits": int(os.getenv("QUOTA_MEMBER", "25")),
    "staff_daily_credits": int(os.getenv("QUOTA_STAFF", "500")),
    "anon_faq_daily": int(os.getenv("QUOTA_ANON_FAQ", "60")),
    "anon_ip_daily": int(os.getenv("QUOTA_ANON_IP", "200")),

    # ── Per-conversation caps ──────────────────────────────────────────
    # Distinct from the daily allowance: these bound a single sitting, so one
    # visitor cannot spend a whole day's credits in one runaway thread.
    # 0 = no per-session cap.
    "questions_per_session": int(os.getenv("WIDGET_QUESTIONS_PER_SESSION", "0")),
    "tokens_per_session": int(os.getenv("WIDGET_TOKENS_PER_SESSION", "0")),
}

_INT_FIELDS = ("anon_llm_credits", "member_daily_credits", "staff_daily_credits",
               "anon_faq_daily", "anon_ip_daily", "questions_per_session",
               "tokens_per_session")
_BOOL_FIELDS = ("anon_llm_enabled",)
_TEXT_FIELDS = ("anon_notice",)

MAX_NOTICE_CHARS = 400
# Ceilings on what an operator can set through the console. Not security --
# a root user could edit the file -- but a typed-in extra zero on a daily
# allowance is a cost incident, and a slip is far likelier than an attack.
_MAX = {
    "anon_llm_credits": 100,
    "member_daily_credits": 10000,
    "staff_daily_credits": 100000,
    "anon_faq_daily": 100000,
    "anon_ip_daily": 100000,
    "questions_per_session": 1000,
    "tokens_per_session": 2000000,
}


def _load() -> dict:
    out = dict(_DEFAULTS)
    if os.path.exists(_PATH):
        try:
            with open(_PATH, encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for k, v in saved.items():
                    if k in out:
                        out[k] = v
        except Exception as e:
            logger.warning(f"policy read failed, using defaults: {e}")
    return out


def get() -> dict:
    with _lock:
        return _load()


def value(key: str):
    """One setting. quota.py calls this per-request, so it stays cheap: the
    file is small and the OS caches it."""
    return _load().get(key, _DEFAULTS.get(key))


class PolicyError(ValueError):
    pass


def _coerce(key: str, raw):
    if key in _BOOL_FIELDS:
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if key in _INT_FIELDS:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise PolicyError(f"'{key}' must be a whole number")
        if n < 0:
            raise PolicyError(f"'{key}' cannot be negative")
        cap = _MAX.get(key)
        if cap is not None and n > cap:
            raise PolicyError(f"'{key}' is capped at {cap}")
        return n
    if key in _TEXT_FIELDS:
        return str(raw or "").strip()[:MAX_NOTICE_CHARS]
    raise PolicyError(f"unknown setting '{key}'")


def update(changes: dict, actor: str = "") -> dict:
    """Apply a partial update. Unknown keys are an error rather than being
    dropped silently — an operator who mistypes a setting name should be told,
    not left believing a limit was applied."""
    if not isinstance(changes, dict) or not changes:
        raise PolicyError("nothing to change")
    unknown = [k for k in changes if k not in _DEFAULTS]
    if unknown:
        raise PolicyError(f"unknown setting(s): {', '.join(sorted(unknown))}")

    clean = {k: _coerce(k, v) for k, v in changes.items()}
    with _lock:
        current = _load()
        current.update(clean)
        tmp = _PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
        os.replace(tmp, _PATH)
    for k, v in clean.items():
        logger.info(f"policy: {k} -> {v!r} by {actor or 'unknown'}")
    return current


def reset(actor: str = "") -> dict:
    with _lock:
        if os.path.exists(_PATH):
            os.remove(_PATH)
    logger.info(f"policy reset to defaults by {actor or 'unknown'}")
    return dict(_DEFAULTS)


def defaults() -> dict:
    return dict(_DEFAULTS)
