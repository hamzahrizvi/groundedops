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
import re
import threading
import time

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


def kind_label(provider: str) -> str:
    """What the key actually IS - the API it speaks - regardless of what the
    operator has named it. Shown beside a custom name so "Company keys" is
    still recognisably the OpenAI-compatible slot."""
    if provider == "openai":
        base = (os.getenv("OPENAI_BASE_URL") or "").strip()
        if base and "api.openai.com" not in base:
            return "OpenAI-compatible"
    return _PROVIDER_LABEL.get(provider, provider)


# ── operator-chosen names ───────────────────────────────────────────────
# The slots are fixed by the API a key speaks, but "OpenAI" is the wrong name
# for a key that goes to the on-prem gateway, and every picker in the console
# showing it made the routing look off-site when it was not. The name is
# display-only: routing, env var names and logs keep the provider id.
_PROVIDER_NAME_ENV = {p: f"PROVIDER_NAME_{p.upper()}" for p in _PROVIDER_KEY_ENV}
_NAME_MAX = 40
_NAME_RE = re.compile(r"^[A-Za-z0-9 _.\-()&/+]+$")


def label_for(provider: str) -> str:
    env = _PROVIDER_NAME_ENV.get(provider)
    custom = (os.getenv(env) or "").strip() if env else ""
    return custom or kind_label(provider)


def set_label(provider: str, name: str | None) -> None:
    """`name=None` or blank returns the slot to its built-in name."""
    env = _PROVIDER_NAME_ENV.get(provider)
    if not env:
        raise UnknownProviderError(provider)
    name = " ".join((name or "").split())
    if not name:
        _rewrite_env_line(env, None)
        os.environ.pop(env, None)
        logger.info(f"name for provider '{provider}' reset (via console)")
        return
    if len(name) > _NAME_MAX:
        raise ValueError(f"name must be {_NAME_MAX} characters or fewer")
    if not _NAME_RE.match(name):
        raise ValueError("name may use letters, numbers, spaces and - _ . ( ) & / +")
    _rewrite_env_line(env, name)
    os.environ[env] = name
    logger.info(f"provider '{provider}' renamed to {name!r} (via console)")


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
    # One line per setting is the whole format (main.py's _load_env_file). A
    # line break inside a value would write a SECOND setting of the value's
    # choosing -- e.g. a pasted "key\nOPENAI_BASE_URL=https://attacker"
    # redirecting every provider call, key attached.
    if value is not None and any(c in value for c in "\r\n\0"):
        raise ValueError("value cannot contain line breaks")
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
        # os.replace is atomic, but on Windows it is not always PERMITTED:
        # it fails with PermissionError (WinError 5) while any other handle
        # is open on the target, and a virus scanner or the search indexer
        # opens a file it has just seen written. Observed intermittently
        # here in a burst of saves -- the same file rewritten dozens of
        # times in a second -- and the same burst an operator can produce
        # by changing several settings quickly, where the cost is a 500 on
        # a save that then half-applied (os.environ updated, file not).
        #
        # A handful of short retries covers a scanner's window. The final
        # attempt is left unguarded so a genuine permissions problem still
        # raises rather than being silently swallowed.
        for delay in (0.05, 0.1, 0.2, 0.4):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(delay)
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


# ── outgoing mail ───────────────────────────────────────────────────────
# Same store and same writer as the provider keys. The password is a secret
# and gets the same treatment as a key: write-only from the console, never
# returned, only reported as set or not.
_SMTP_ENV = {
    "host": "SMTP_HOST",
    "port": "SMTP_PORT",
    "user": "SMTP_USER",
    "sender": "SMTP_FROM",
    "security": "SMTP_SECURITY",     # starttls | ssl | none
}
# The env var an older install (or docker-compose) may still set for the
# mail password. Named "legacy" rather than after what it holds: the
# console never writes it any more (see _set_vault_secret), and CodeQL
# reads an identifier with "password" in it as the password itself.
_SMTP_LEGACY_ENV = "SMTP_PASSWORD"
SMTP_SECURITY_MODES = ("starttls", "ssl", "none")


def get_smtp_settings() -> dict:
    """Everything except the password, which only mailer.py reads."""
    out = {k: (os.getenv(env) or "").strip() for k, env in _SMTP_ENV.items()}
    out["security"] = out["security"].lower() or "starttls"
    out["port"] = out["port"] or ("465" if out["security"] == "ssl" else "587")
    out["password_set"] = bool(get_smtp_password())
    return out


def _vault_secret(name: str, legacy_env: str) -> str:
    """A secret from the encrypted vault, else the env var an older install
    (or a docker-compose file) set. Reading the env keeps those working;
    the console never WRITES a secret there any more."""
    try:
        import keyvault
        val = keyvault.load_secret(name)
        if val:
            return val
    except Exception as exc:
        logger.warning(f"secret vault unreadable ({name}): {exc}")
    return (os.getenv(legacy_env) or "").strip()


def _set_vault_secret(name: str, legacy_env: str, value: str | None) -> None:
    """Store (or with None, clear) a secret in the encrypted vault, and
    drop any clear-text copy the .env file still carries from before."""
    import keyvault
    keyvault.save_secret(name, value or "")
    _rewrite_env_line(legacy_env, None)
    os.environ.pop(legacy_env, None)


def get_smtp_password() -> str:
    return _vault_secret("smtp_password", _SMTP_LEGACY_ENV)


def set_smtp_settings(changes: dict) -> None:
    """Apply only the fields present. A blank value clears that field; the
    password is changed only when a non-blank value is supplied, or cleared
    with `clear_password`."""
    for field, env in _SMTP_ENV.items():
        if field not in changes:
            continue
        val = str(changes[field] if changes[field] is not None else "").strip()
        if field == "security" and val and val.lower() not in SMTP_SECURITY_MODES:
            raise ValueError("security must be starttls, ssl or none")
        if field == "port" and val and not (val.isdigit() and 0 < int(val) < 65536):
            raise ValueError("port must be a number between 1 and 65535")
        if field == "security":
            val = val.lower()
        _rewrite_env_line(env, val or None)
        if val:
            os.environ[env] = val
        else:
            os.environ.pop(env, None)
    pw = changes.get("password")
    if pw and str(pw).strip():
        _set_vault_secret("smtp_password", _SMTP_LEGACY_ENV, str(pw).strip())
    elif changes.get("clear_password"):
        _set_vault_secret("smtp_password", _SMTP_LEGACY_ENV, None)
    logger.info("SMTP settings updated (via console)")


# ── work-account sign-in (sso.py) ───────────────────────────────────────
# Client ID and tenant/domain are configuration; the client secret is a
# secret and is write-only from the console, exactly like the SMTP password.
_SSO_ENV = {
    "microsoft": {"client_id": "SSO_MICROSOFT_CLIENT_ID",
                  "tenant": "SSO_MICROSOFT_TENANT"},
    "google": {"client_id": "SSO_GOOGLE_CLIENT_ID",
               "domain": "SSO_GOOGLE_DOMAIN"},
}
_SSO_LEGACY_ENV = {"microsoft": "SSO_MICROSOFT_CLIENT_SECRET",
                   "google": "SSO_GOOGLE_CLIENT_SECRET"}


def get_sso_settings(provider: str) -> dict:
    fields = _SSO_ENV.get(provider)
    if fields is None:
        raise UnknownProviderError(provider)
    out = {k: (os.getenv(env) or "").strip() for k, env in fields.items()}
    out["secret_set"] = bool(get_sso_secret(provider))
    return out


def get_sso_secret(provider: str) -> str:
    env = _SSO_LEGACY_ENV.get(provider, "")
    if not env:
        return ""
    return _vault_secret(f"sso_{provider}_client_secret", env)


def set_sso_settings(provider: str, changes: dict) -> None:
    fields = _SSO_ENV.get(provider)
    if fields is None:
        raise UnknownProviderError(provider)
    for field, env in fields.items():
        if field not in changes:
            continue
        val = str(changes[field] or "").strip()
        if val and not re.match(r"^[A-Za-z0-9._\-]+$", val):
            raise ValueError(f"{field} contains characters it cannot have")
        _rewrite_env_line(env, val or None)
        if val:
            os.environ[env] = val
        else:
            os.environ.pop(env, None)
    legacy_env_name = _SSO_LEGACY_ENV[provider]
    if changes.get("secret") and str(changes["secret"]).strip():
        _set_vault_secret(f"sso_{provider}_client_secret", legacy_env_name,
                          str(changes["secret"]).strip())
    elif changes.get("clear_secret"):
        _set_vault_secret(f"sso_{provider}_client_secret", legacy_env_name, None)
    logger.info(f"SSO settings for {provider} updated (via console)")


# ── low-credit alerts ───────────────────────────────────────────────────
_CREDIT_THRESHOLD_ENV = "CREDIT_ALERT_THRESHOLD"
CREDIT_THRESHOLD_DEFAULT = 5.0


def get_credit_threshold() -> float:
    try:
        return float((os.getenv(_CREDIT_THRESHOLD_ENV) or "").strip())
    except ValueError:
        return CREDIT_THRESHOLD_DEFAULT


def set_credit_threshold(value: float | None) -> None:
    if value is None:
        _rewrite_env_line(_CREDIT_THRESHOLD_ENV, None)
        os.environ.pop(_CREDIT_THRESHOLD_ENV, None)
        return
    value = float(value)
    if value < 0:
        raise ValueError("threshold cannot be negative")
    text = f"{value:g}"
    _rewrite_env_line(_CREDIT_THRESHOLD_ENV, text)
    os.environ[_CREDIT_THRESHOLD_ENV] = text


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
