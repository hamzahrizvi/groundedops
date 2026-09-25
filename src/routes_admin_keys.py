"""Provider API keys, per-job model roles, and the credit watch.

Moved out of main.py verbatim (2026-09-25). Registered on the app through
app.include_router; paths, parameters and responses are unchanged.
"""

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import keystore
from guards import _require_root
from runtime_config import get_settings, set_online_provider

logger = logging.getLogger(__name__)
router = APIRouter()


# ── provider API keys (root only) ───────────────────────────────────────
# Everyone at `support` sees WHICH providers are available (admin_providers
# above); only `root` sees or changes the keys themselves. Keys are read
# through and written through keystore.py — this file never touches .env
# directly, matching the rule the rest of the codebase already follows for
# provider keys.

class ApiKeyReq(BaseModel):
    value: str


class KeyRoleReq(BaseModel):
    # null clears the assignment — "no preference", which is not the same
    # as a blank string and must survive JSON round-tripping as such.
    provider: str | None = None


class ModelReq(BaseModel):
    # null clears, with the same meaning as above: for a provider baseline
    # it means "fall back to the built-in default", and for a role it means
    # "inherit the baseline and keep following it".
    model: str | None = None


def _key_roles() -> list[dict]:
    """Each job, what is assigned to it, and whether that assignment is live.
    `assigned` is what was written; `effective` is what generation will
    actually use — they differ when the assigned provider's key was removed,
    and showing only one of them is how a dangling assignment stays
    invisible."""
    return [
        {"role": r, "label": keystore.role_label(r), "hint": keystore.role_hint(r),
         "assigned": keystore.get_role_assignment(r),
         "effective": keystore.get_role(r),
         # The MODEL half, same assigned/effective distinction and for the
         # same reason. `model_override` is null when the role follows the
         # provider's baseline; `model_effective` is what will actually be
         # sent. Showing only the override would hide the commonest case
         # -- a role inheriting a baseline nobody remembers setting.
         "model_override": keystore.get_role_model(r),
         "model_effective": _effective_role_model(r)}
        for r in keystore.roles()
    ]


def _effective_role_model(role: str) -> str | None:
    """What this role will send as the model name, or None when no
    provider resolves for it at all (nothing assigned, no key)."""
    try:
        provider = keystore.get_role(role) or (
            keystore.get_role("default") if role != "default" else None)
        if not provider:
            provider = get_settings().get("online_provider")
        if not provider:
            return None
        return keystore.model_for_role(role, provider)
    except Exception as exc:
        logger.debug(f"effective model for {role} unavailable: {exc}")
        return None


@router.get("/admin/keys")
def admin_keys_list(x_admin_password: str | None = Header(default=None)):
    _require_root(x_admin_password)
    return {
        "providers": [
            {"key": p, "label": keystore.label_for(p),
             "kind": keystore.kind_label(p),
             "configured": keystore.has_key(p), "masked": keystore.masked_key(p)}
            for p in keystore.providers()
        ],
        "roles": _key_roles(),
        # What the Settings picker holds, so the page can say what an
        # unassigned default will fall back to rather than implying nothing
        # answers at all.
        "fallback_provider": get_settings().get("online_provider"),
    }


@router.get("/admin/keys/models/{provider}")
def admin_keys_list_models(provider: str,
                           x_admin_password: str | None = Header(default=None)):
    """The models this provider actually reports, for the console picker.

    A typed model name is a way to take answering down with a typo -- the
    same reasoning that made the reranker a PROFILE rather than a free-text
    box. Where the provider can tell us (any OpenAI-compatible endpoint,
    which includes the on-prem LiteLLM gateway), the console offers the
    real list; `itl-gpt-pro` and `itl-gpt-flash` are what ours returns.

    Degrades to an empty list rather than an error, and the page falls back
    to a typed box: a provider with no /v1/models endpoint, or one that is
    briefly unreachable, must not make the model unchangeable. `source`
    tells the page which of the two it is looking at.
    """
    _require_root(x_admin_password)
    provider = (provider or "").strip().lower()
    if provider not in keystore.providers():
        raise HTTPException(status_code=404, detail="unknown provider")
    current = keystore.get_model(provider)
    if provider != "openai":
        # Only the OpenAI-compatible shape is discoverable here. The others
        # publish their catalogues out of band, so the page types the name.
        return {"provider": provider, "models": [], "current": current,
                "source": "typed"}
    try:
        import requests as _rq
        from llm import OPENAI_BASE
        res = _rq.get(f"{OPENAI_BASE}/models", timeout=8,
                      headers={"Authorization":
                               f"Bearer {keystore.get_key('openai') or ''}"})
        res.raise_for_status()
        names = sorted(m.get("id") for m in res.json().get("data", [])
                       if m.get("id"))
        return {"provider": provider, "models": names, "current": current,
                "source": "listed", "endpoint": OPENAI_BASE}
    except Exception as exc:
        # The TYPE, not the message. This endpoint is root-only, so the
        # first version returned `str(exc)[:200]` on the grounds that an
        # operator debugging a gateway needs to see what went wrong --
        # but a requests exception stringifies to the full URL, and for
        # an auth failure that URL can carry a query parameter nobody
        # meant to put on screen. The class name already separates the
        # cases an operator acts on differently (ConnectionError = wrong
        # host or DNS, Timeout = reachable but slow, HTTPError = reached
        # and refused), and the full text is one line above in the log.
        logger.info(f"model list unavailable for {provider}: {exc}",
                    exc_info=True)
        return {"provider": provider, "models": [], "current": current,
                "source": "typed", "error": type(exc).__name__}


@router.post("/admin/keys/models/{provider}")
def admin_keys_set_model(provider: str, payload: ModelReq,
                         x_admin_password: str | None = Header(default=None)):
    """Set a provider's BASELINE model — the 'configure all the routes'
    half. Every role using this provider that carries no override of its
    own follows immediately, with no restart."""
    _require_root(x_admin_password)
    try:
        keystore.set_model(provider, payload.model)
    except keystore.UnknownProviderError:
        raise HTTPException(status_code=404, detail="unknown provider")
    return {"ok": True, "roles": _key_roles()}


@router.post("/admin/keys/roles/{role}/model")
def admin_keys_set_role_model(role: str, payload: ModelReq,
                              x_admin_password: str | None = Header(default=None)):
    """Override ONE role's model, or clear the override to inherit again.

    Clearing is not the same as setting the override to the baseline's
    current value: an inheriting role keeps following later changes to the
    baseline, which is what makes 'pick a model once' keep working."""
    _require_root(x_admin_password)
    try:
        keystore.set_role_model(role, payload.model)
    except keystore.UnknownRoleError:
        raise HTTPException(status_code=404, detail="unknown role")
    return {"ok": True, "roles": _key_roles()}


@router.post("/admin/keys/roles/{role}")
def admin_keys_set_role(role: str, payload: KeyRoleReq,
                        x_admin_password: str | None = Header(default=None)):
    """Assign a provider to a job, or clear it. Takes effect on the next
    request — llm.py reads the assignment per call, not at import.

    Assigning the DEFAULT also moves the Settings provider picker, because
    two controls that both decide "which API answers" and disagree is the
    kind of split that makes a saved setting look ignored."""
    me = _require_root(x_admin_password)
    provider = (payload.provider or "").strip().lower() or None
    if provider and not keystore.has_key(provider):
        raise HTTPException(
            status_code=400,
            detail=f"no key is configured for {keystore.label_for(provider)} — "
                   "save its key before giving it a job")
    try:
        keystore.set_role(role, provider)
    except keystore.UnknownRoleError:
        raise HTTPException(status_code=404, detail=f"unknown role '{role}'")
    except keystore.UnknownProviderError:
        raise HTTPException(status_code=404, detail=f"unknown provider '{provider}'")
    if role == "default" and provider:
        try:
            set_online_provider(provider)
        except ValueError:
            pass
    logger.info(f"key role '{role}' set to {provider or 'none'} by {me['email']}")
    return {"ok": True, "roles": _key_roles()}


class KeyNameReq(BaseModel):
    name: str | None = None


@router.post("/admin/keys/{provider}/name")
def admin_keys_set_name(provider: str, payload: KeyNameReq,
                        x_admin_password: str | None = Header(default=None)):
    """Rename a key slot. Display only: routing, env var names and logs keep
    the provider id, so a rename can never change which API is called.
    Blank returns it to the built-in name."""
    me = _require_root(x_admin_password)
    try:
        keystore.set_label(provider, payload.name)
    except keystore.UnknownProviderError:
        raise HTTPException(status_code=404, detail=f"unknown provider '{provider}'")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"key slot '{provider}' renamed by {me['email']}")
    return {"ok": True, "label": keystore.label_for(provider)}


# ── low-credit alerts ───────────────────────────────────────────────────
# Root only, like the keys themselves: the balance of the company's
# provider accounts and the mail server's settings are both root's business.

class SmtpReq(BaseModel):
    host: str | None = None
    port: str | None = None
    user: str | None = None
    sender: str | None = None
    security: str | None = None
    password: str | None = None
    clear_password: bool = False


class ThresholdReq(BaseModel):
    value: float | None = None


def _credits_status() -> dict:
    import credit_watch
    import mailer
    results, checked = credit_watch.last_results()
    return {"results": results, "checked_at": checked,
            "threshold": keystore.get_credit_threshold(),
            "recipients": credit_watch.recipients(),
            "smtp": keystore.get_smtp_settings(),
            "smtp_configured": mailer.is_configured()}


@router.get("/admin/credits")
def admin_credits(x_admin_password: str | None = Header(default=None)):
    _require_root(x_admin_password)
    return _credits_status()


@router.post("/admin/credits/check")
def admin_credits_check(x_admin_password: str | None = Header(default=None)):
    """Check every balance now, and email if anything is low -- the same
    thing the periodic check does, on demand."""
    _require_root(x_admin_password)
    import credit_watch
    alerted = credit_watch.maybe_alert(credit_watch.check_all())
    return dict(_credits_status(), alerted=alerted)


@router.post("/admin/credits/threshold")
def admin_credits_threshold(payload: ThresholdReq,
                            x_admin_password: str | None = Header(default=None)):
    _require_root(x_admin_password)
    try:
        keystore.set_credit_threshold(payload.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _credits_status()


@router.post("/admin/credits/smtp")
def admin_credits_smtp(payload: SmtpReq,
                       x_admin_password: str | None = Header(default=None)):
    """Save mail settings. The password is write-only: it is never returned,
    only reported as set or not."""
    me = _require_root(x_admin_password)
    try:
        keystore.set_smtp_settings(payload.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"SMTP settings saved by {me['email']}")
    return _credits_status()


@router.post("/admin/credits/test-email")
def admin_credits_test_email(x_admin_password: str | None = Header(default=None)):
    me = _require_root(x_admin_password)
    import credit_watch
    import mailer
    to = credit_watch.recipients()
    try:
        mailer.send(to, "[GroundedOps] Test email",
                    "This is a test from the GroundedOps console, sent by "
                    f"{me['email']}.\n\nLow-credit alerts will arrive at "
                    "this address.\n")
    except mailer.MailError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "sent_to": to}


@router.post("/admin/keys/{provider}")
def admin_keys_set(provider: str, payload: ApiKeyReq,
                   x_admin_password: str | None = Header(default=None)):
    """Takes effect immediately — keystore.set_key updates os.environ as
    well as the file — so a key pasted in here works on the very next
    request, no restart. Written to disk too, so it survives one."""
    me = _require_root(x_admin_password)
    try:
        keystore.set_key(provider, payload.value)
    except keystore.UnknownProviderError:
        raise HTTPException(status_code=404, detail=f"unknown provider '{provider}'")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"provider key for '{provider}' saved by {me['email']}")
    return {"ok": True, "masked": keystore.masked_key(provider)}


@router.delete("/admin/keys/{provider}")
def admin_keys_clear(provider: str,
                     x_admin_password: str | None = Header(default=None)):
    me = _require_root(x_admin_password)
    try:
        keystore.clear_key(provider)
    except keystore.UnknownProviderError:
        raise HTTPException(status_code=404, detail=f"unknown provider '{provider}'")
    logger.info(f"provider key for '{provider}' cleared by {me['email']}")
    return {"ok": True}
