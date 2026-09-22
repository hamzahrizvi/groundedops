"""Single point of access for provider API keys and other secrets.

Backing store is still `.env` → process environment (nothing else in this
deployment needs a real secrets manager yet — see PENDING.md). The point of
this module is that every other file asks *here*, not `os.getenv` ad hoc, so
the backing store can change in one place later (Docker secret, Vault, cloud
KMS) without touching call sites.

`set_key`/`clear_key` make this the one place that WRITES a key too — the
root-only console page (main.py's /admin/keys routes) calls these rather
than touching .env itself. A key saved this way takes effect immediately
(os.environ is updated in the same call) with no restart, and survives one
because it is written to the file, not just the process.
"""
import logging
import os
import threading

logger = logging.getLogger(__name__)

# provider key → env var name. Add new providers here only.
_PROVIDER_KEY_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

_PROVIDER_LABEL = {
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "anthropic": "Claude",
}

# Overridable so tests can point this at a scratch file instead of the real
# .env — see _harness.py. Resolved lazily (a function, not a constant) so
# a test can set the env var before the first call.
def _env_path() -> str:
    return os.getenv(
        "ENV_FILE_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


_write_lock = threading.Lock()


class MissingKeyError(RuntimeError):
    def __init__(self, provider: str):
        super().__init__(f"no API key configured for provider '{provider}'")
        self.provider = provider


class UnknownProviderError(ValueError):
    def __init__(self, provider: str):
        super().__init__(f"unknown provider '{provider}'")


def providers() -> list[str]:
    return list(_PROVIDER_KEY_ENV)


def label_for(provider: str) -> str:
    return _PROVIDER_LABEL.get(provider, provider)


def has_key(provider: str) -> bool:
    env = _PROVIDER_KEY_ENV.get(provider)
    return bool(env and (os.getenv(env) or "").strip())


def get_key(provider: str) -> str:
    """Raises MissingKeyError if the provider has no key set. Callers that
    already branch on has_key()/_available_providers() won't hit this; it's
    a backstop for anything that calls a provider directly."""
    env = _PROVIDER_KEY_ENV.get(provider)
    val = (os.getenv(env) or "").strip() if env else ""
    if not val:
        raise MissingKeyError(provider)
    return val


def masked_key(provider: str) -> str | None:
    """Enough to recognise a key without showing it: 4 chars, a run of
    dots, then the last 4. None if nothing is set. Never returns anything
    long or specific enough to be useful if it leaked into a log."""
    env = _PROVIDER_KEY_ENV.get(provider)
    val = (os.getenv(env) or "").strip() if env else ""
    if not val:
        return None
    if len(val) <= 8:
        return "•" * len(val)
    return f"{val[:4]}{'•' * 6}{val[-4:]}"


def _rewrite_env_line(key: str, value: str | None) -> None:
    """Update or remove one KEY=value line in the .env file, atomically,
    leaving every other line (including comments) untouched. `value=None`
    removes the line entirely rather than writing KEY=.

    Locked because two admins saving different keys at once must not race
    on read-modify-write of the same file.
    """
    path = _env_path()
    with _write_lock:
        lines = []
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8-sig") as f:
                lines = f.readlines()

        found = False
        out = []
        for raw in lines:
            stripped = raw.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                line_key = stripped.split("=", 1)[0].strip()
                if line_key == key:
                    found = True
                    if value is not None:
                        out.append(f"{key}={value}\n")
                    continue  # drop the old line either way
            out.append(raw)

        if value is not None and not found:
            if out and not out[-1].endswith("\n"):
                out[-1] += "\n"
            out.append(f"{key}={value}\n")

        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(out)
        os.replace(tmp, path)


def set_key(provider: str, value: str) -> None:
    env = _PROVIDER_KEY_ENV.get(provider)
    if not env:
        raise UnknownProviderError(provider)
    value = (value or "").strip()
    if not value:
        raise ValueError("key cannot be blank — use clear_key to remove one")
    _rewrite_env_line(env, value)
    os.environ[env] = value
    logger.info(f"API key saved for provider '{provider}' (via console)")


def clear_key(provider: str) -> None:
    env = _PROVIDER_KEY_ENV.get(provider)
    if not env:
        raise UnknownProviderError(provider)
    _rewrite_env_line(env, None)
    os.environ.pop(env, None)
    logger.info(f"API key cleared for provider '{provider}' (via console)")


# ── which key does which job ────────────────────────────────────────────
# A provider key and the job it does are different questions. The console
# could say WHICH providers had a key but not which one answers a normal
# question, which one answers a "deep" one, and which one picks up when the
# first fails — so a second key was configured and then did nothing.
#
# Three jobs, one provider each, and the SAME provider may hold more than
# one: a single-key install assigns it to all three and nothing changes.
# Stored in .env through the same writer as the keys, so an assignment
# survives a restart and takes effect without one.
_ROLE_ENV = {
    "default": "PROVIDER_ROLE_DEFAULT",
    "advanced": "PROVIDER_ROLE_ADVANCED",
    "backup": "PROVIDER_ROLE_BACKUP",
}

_ROLE_LABEL = {
    "default": "Default",
    "advanced": "Advanced",
    "backup": "Backup",
}

_ROLE_HINT = {
    "default": "Answers a normal question, and condenses follow-ups.",
    "advanced": "Answers a 'deep' question, where reasoning is worth the cost.",
    "backup": "Tried only when the provider above fails on a request.",
}


class UnknownRoleError(ValueError):
    def __init__(self, role: str):
        super().__init__(f"unknown role '{role}'")


def roles() -> list[str]:
    return list(_ROLE_ENV)


def role_label(role: str) -> str:
    return _ROLE_LABEL.get(role, role)


def role_hint(role: str) -> str:
    return _ROLE_HINT.get(role, "")


def get_role_assignment(role: str) -> str | None:
    """What is WRITTEN for this job, whether or not that provider still has
    a key. The console shows this so an assignment left dangling by a
    removed key is visible rather than silently absent."""
    env = _ROLE_ENV.get(role)
    if not env:
        raise UnknownRoleError(role)
    val = (os.getenv(env) or "").strip().lower()
    return val if val in _PROVIDER_KEY_ENV else None


def get_role(role: str) -> str | None:
    """What this job will ACTUALLY use: the assignment, masked to None when
    that provider has no key. Removing a key must not leave generation
    routed at a provider that is certain to 401 — callers fall back to their
    own default when this is None."""
    provider = get_role_assignment(role)
    if provider and not has_key(provider):
        return None
    return provider


def set_role(role: str, provider: str | None) -> None:
    """`provider=None` clears the assignment, which is not the same as a
    blank key — it means "no preference", and the caller falls back."""
    env = _ROLE_ENV.get(role)
    if not env:
        raise UnknownRoleError(role)
    if provider is None or not str(provider).strip():
        _rewrite_env_line(env, None)
        os.environ.pop(env, None)
        logger.info(f"provider for role '{role}' cleared (via console)")
        return
    provider = str(provider).strip().lower()
    if provider not in _PROVIDER_KEY_ENV:
        raise UnknownProviderError(provider)
    _rewrite_env_line(env, provider)
    os.environ[env] = provider
    logger.info(f"role '{role}' now served by provider '{provider}' (via console)")


# ADMIN_PASSWORD is gone: admin access is real accounts now (accounts.py).
# The session-signing secret lives here because it is a secret, even though
# it is not a provider key.

def get_session_secret() -> str:
    return (os.getenv("SESSION_SECRET") or "").strip()


def session_secret_is_set() -> bool:
    return bool(get_session_secret())


# ── Model selection ──────────────────────────────────────────────────────
#
# Providers were console-settable per role; MODELS were not settable at all.
# `ONLINE_OPENAI_MODEL` and friends were read straight from the environment
# at call time, so choosing which model answers meant hand-editing .env and
# restarting -- and nothing in the console showed what was in force.
#
# That gap had a visible cost. On 2026-09-22, with the on-prem gateway back
# on the network, the ADVANCED role was pointed at it (itl-gpt-flash) while
# the DEFAULT role was still routed at DeepSeek, so nearly every customer
# question went off-site to a metered API while the local gateway sat idle.
# Nothing was wrong; nobody had edited the other half of the config.
#
# TWO LEVELS, and the cascade is the point:
#
#   baseline   one model per provider (ONLINE_<PROVIDER>_MODEL). Picking a
#              model in the console writes this, and EVERY role using that
#              provider picks it up -- which is what "configure all the
#              routes" means.
#   override   an optional per-role model (MODEL_ROLE_<ROLE>). Empty means
#              inherit the baseline. This is what keeps the deliberate
#              split available: extraction on a fast model while the
#              inference contract runs on a reasoning one.
#
# Same storage as the role assignments (.env via _rewrite_env_line, plus
# os.environ in the same call), so a change applies to the next question
# without a restart and survives one. Deliberately NOT policy.json: this is
# LLM routing, it belongs beside the provider assignment it qualifies, and
# splitting the two across separate stores is how they drift apart.

_PROVIDER_MODEL_ENV = {
    "deepseek": "ONLINE_DEEPSEEK_MODEL",
    "openai": "ONLINE_OPENAI_MODEL",
    "anthropic": "ONLINE_ANTHROPIC_MODEL",
}

_ROLE_MODEL_ENV = {
    "default": "MODEL_ROLE_DEFAULT",
    "advanced": "MODEL_ROLE_ADVANCED",
    "backup": "MODEL_ROLE_BACKUP",
}

# Used only when a provider has never been configured at all. The console
# shows the live list where the provider can report one, so these are a
# floor, not a recommendation.
_PROVIDER_MODEL_FALLBACK = {
    "deepseek": "deepseek-v4-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-6",
}


def providers() -> list[str]:
    return list(_PROVIDER_KEY_ENV)


def get_model(provider: str) -> str:
    """The baseline model for one provider."""
    provider = (provider or "").strip().lower()
    env = _PROVIDER_MODEL_ENV.get(provider)
    if not env:
        raise UnknownProviderError(provider)
    return ((os.getenv(env) or "").strip()
            or _PROVIDER_MODEL_FALLBACK.get(provider, ""))


def set_model(provider: str, model: str | None) -> None:
    """Set the baseline model for a provider. Every role using that
    provider and carrying no override of its own follows immediately --
    this is the 'configures all the routes' half."""
    provider = (provider or "").strip().lower()
    env = _PROVIDER_MODEL_ENV.get(provider)
    if not env:
        raise UnknownProviderError(provider)
    if model is None or not str(model).strip():
        _rewrite_env_line(env, None)
        os.environ.pop(env, None)
        logger.info(f"model for provider '{provider}' cleared (via console)")
        return
    model = str(model).strip()
    _rewrite_env_line(env, model)
    os.environ[env] = model
    logger.info(f"model for provider '{provider}' set to {model!r} "
                f"(via console)")


def get_role_model(role: str) -> str | None:
    """A role's model OVERRIDE, or None when it inherits the baseline."""
    env = _ROLE_MODEL_ENV.get(role)
    if not env:
        raise UnknownRoleError(role)
    return (os.getenv(env) or "").strip() or None


def set_role_model(role: str, model: str | None) -> None:
    """`model=None` clears the override, returning the role to the
    provider's baseline -- which is not the same as setting it to the
    baseline's current value, because it keeps following later changes."""
    env = _ROLE_MODEL_ENV.get(role)
    if not env:
        raise UnknownRoleError(role)
    if model is None or not str(model).strip():
        _rewrite_env_line(env, None)
        os.environ.pop(env, None)
        logger.info(f"model override for role '{role}' cleared (via console)")
        return
    model = str(model).strip()
    _rewrite_env_line(env, model)
    os.environ[env] = model
    logger.info(f"model override for role '{role}' set to {model!r} "
                f"(via console)")


def model_for_role(role: str, provider: str) -> str:
    """What this role will ACTUALLY send as the model name: its override
    when it has one, otherwise the provider's baseline. The single answer
    llm.py and the console must agree on."""
    try:
        override = get_role_model(role)
    except UnknownRoleError:
        override = None
    return override or get_model(provider)
