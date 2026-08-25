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


# ADMIN_PASSWORD is gone: admin access is real accounts now (accounts.py).
# The session-signing secret lives here because it is a secret, even though
# it is not a provider key.

def get_session_secret() -> str:
    return (os.getenv("SESSION_SECRET") or "").strip()


def session_secret_is_set() -> bool:
    return bool(get_session_secret())
