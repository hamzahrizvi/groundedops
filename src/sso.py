"""Sign in with a work account: Microsoft (Entra ID) or Google Workspace.

This is the SSO seam accounts.py's docstring describes: the provider proves
who the person is, the email claim is resolved to an EXISTING account with
find_by_email, and issue_session does the rest. Nobody gets an account by
signing in -- accounts are still created or approved by root; SSO is only
another way to prove you are the person an account belongs to.

FLOW (OpenID Connect authorization code + PKCE)

  /admin/auth/sso/<p>/start     state, nonce and a PKCE verifier are minted,
                                the state is bound to this browser by a
                                short HttpOnly cookie, and the browser is
                                sent to the provider.
  /admin/auth/sso/<p>/callback  state and cookie must match; the code is
                                exchanged for an ID token; the account is
                                resolved; the browser returns to
                                /admin#sso=<handoff> -- a 2-minute single-use
                                code the console trades for a session, so no
                                session token ever appears in a URL.

ID TOKEN VALIDATION

The ID token comes straight from the provider's token endpoint over TLS, in
exchange for a code only we could redeem (client secret + PKCE verifier).
OpenID Connect Core 3.1.3.7 allows TLS server validation to stand in for
checking the token's signature in exactly that case, which keeps this free
of a JWT/crypto dependency. The claims are still checked: issuer, audience,
expiry and nonce.

WHY MICROSOFT MUST BE SINGLE-TENANT

With a multi-tenant app ("common"/"organizations"), any Entra tenant can
sign in -- and a tenant admin can put ANY address in a user's email claim
(the "nOAuth" class of bug). Matching that to an account here would let a
stranger's tenant sign in as your root admin. So the tenant must be your
own directory's ID or domain, and the token's `tid` must equal it.
Google equivalently requires email_verified, and SSO_GOOGLE_DOMAIN (hd)
restricts it to your Workspace.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time

import requests

import keystore

logger = logging.getLogger(__name__)

STATE_TTL = 10 * 60
STATE_COOKIE = "go_sso_state"
_MULTI_TENANT = {"common", "organizations", "consumers"}

_states: dict[str, dict] = {}
_states_lock = threading.Lock()
_discovery_cache: dict[str, tuple[float, dict]] = {}


class SsoError(Exception):
    """`code` is a short machine-readable reason the console turns into a
    sentence; the message is for the log."""
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


PROVIDERS = {
    "microsoft": {"label": "Microsoft"},
    "google": {"label": "Google"},
}


# ── configuration ───────────────────────────────────────────────────────

def config(provider: str) -> dict:
    c = keystore.get_sso_settings(provider)
    c["secret"] = keystore.get_sso_secret(provider)
    return c


def config_problem(provider: str) -> str | None:
    """Why this provider cannot be offered, or None when it can."""
    c = config(provider)
    if not c["client_id"] or not c["secret"]:
        return "client ID and secret are not set"
    if provider == "microsoft":
        t = (c.get("tenant") or "").strip().lower()
        if not t:
            return "tenant is not set"
        if t in _MULTI_TENANT:
            return (f"tenant '{t}' would let any organisation sign in; use your "
                    "directory (tenant) ID or its domain")
    return None


def enabled() -> list[dict]:
    return [{"key": p, "label": v["label"]} for p, v in PROVIDERS.items()
            if config_problem(p) is None]


# ── discovery ───────────────────────────────────────────────────────────

def _discovery_url(provider: str) -> str:
    if provider == "microsoft":
        tenant = config("microsoft")["tenant"].strip()
        return f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"
    return "https://accounts.google.com/.well-known/openid-configuration"


def _discovery(provider: str) -> dict:
    url = _discovery_url(provider)
    hit = _discovery_cache.get(url)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    res = requests.get(url, timeout=10)
    res.raise_for_status()
    doc = res.json()
    for field in ("authorization_endpoint", "token_endpoint", "issuer"):
        if not str(doc.get(field, "")).startswith("https://"):
            raise SsoError("provider_error", f"discovery document missing {field}")
    _discovery_cache[url] = (time.time(), doc)
    return doc


# ── the flow ────────────────────────────────────────────────────────────

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def redirect_uri(base: str, provider: str) -> str:
    return f"{base}/admin/auth/sso/{provider}/callback"


def start(provider: str, base: str) -> tuple[str, str]:
    """(URL to send the browser to, value for the state cookie)."""
    if provider not in PROVIDERS:
        raise SsoError("unknown_provider")
    problem = config_problem(provider)
    if problem:
        raise SsoError("not_configured", problem)
    c = config(provider)
    doc = _discovery(provider)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    binding = secrets.token_urlsafe(24)
    now = time.time()
    with _states_lock:
        for k in [k for k, v in _states.items() if v["exp"] < now]:
            _states.pop(k, None)
        _states[state] = {"provider": provider, "nonce": nonce, "verifier": verifier,
                          "binding": hashlib.sha256(binding.encode()).hexdigest(),
                          "base": base, "exp": now + STATE_TTL}
    params = {
        "client_id": c["client_id"],
        "response_type": "code",
        "redirect_uri": redirect_uri(base, provider),
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    if provider == "google" and c.get("domain"):
        params["hd"] = c["domain"]
    from urllib.parse import urlencode
    return doc["authorization_endpoint"] + "?" + urlencode(params), binding


def _decode_payload(id_token: str) -> dict:
    try:
        part = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        raise SsoError("bad_token", "ID token could not be decoded")


def finish(provider: str, code: str, state: str, binding_cookie: str | None) -> str:
    """Validate the provider's answer. Returns the verified email address."""
    with _states_lock:
        st = _states.pop(state or "", None)
    if not st or st["exp"] < time.time() or st["provider"] != provider:
        raise SsoError("expired", "unknown or expired state")
    if not binding_cookie or not secrets.compare_digest(
            hashlib.sha256(binding_cookie.encode()).hexdigest(), st["binding"]):
        # Sign-in was started in a different browser: refusing stops someone
        # completing their own sign-in in YOUR browser (login CSRF).
        raise SsoError("expired", "state cookie missing or mismatched")
    if not code:
        raise SsoError("cancelled")
    c = config(provider)
    doc = _discovery(provider)
    res = requests.post(doc["token_endpoint"], timeout=15, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(st["base"], provider),
        "client_id": c["client_id"],
        "client_secret": c["secret"],
        "code_verifier": st["verifier"],
    }, headers={"Accept": "application/json"})
    if res.status_code != 200:
        # The body names the problem (bad secret, redirect URI mismatch) and
        # carries nothing secret; log it for whoever is setting this up.
        logger.warning(f"SSO {provider} token exchange HTTP {res.status_code}: "
                       f"{res.text[:300]}")
        raise SsoError("provider_error", "token exchange failed")
    id_token = (res.json() or {}).get("id_token")
    if not id_token:
        raise SsoError("provider_error", "no id_token in token response")
    claims = _decode_payload(id_token)

    aud = claims.get("aud")
    if c["client_id"] not in (aud if isinstance(aud, list) else [aud]):
        raise SsoError("bad_token", "audience mismatch")
    if float(claims.get("exp", 0)) < time.time():
        raise SsoError("bad_token", "ID token expired")
    if not secrets.compare_digest(str(claims.get("nonce", "")), st["nonce"]):
        raise SsoError("bad_token", "nonce mismatch")

    if provider == "microsoft":
        # A single-tenant discovery document names its tenant in the issuer
        # (a domain resolves to the tenant's GUID). A "{tenantid}" template
        # means multi-tenant, which config_problem already refuses; this is
        # the backstop. An exact issuer match then pins the token's tenant.
        if "{tenantid}" in doc["issuer"]:
            raise SsoError("not_configured", "multi-tenant Microsoft sign-in is refused")
        if claims.get("iss") != doc["issuer"]:
            raise SsoError("wrong_tenant", "token issued by another tenant")
        email = claims.get("email") or claims.get("preferred_username") or ""
    else:
        if claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
            raise SsoError("bad_token", "issuer mismatch")
        if claims.get("email_verified") is not True:
            raise SsoError("unverified", "Google says the email is not verified")
        want = (c.get("domain") or "").strip().lower()
        if want and str(claims.get("hd", "")).lower() != want:
            raise SsoError("wrong_tenant", "not a Workspace account in the allowed domain")
        email = claims.get("email") or ""
    email = email.strip().lower()
    if "@" not in email:
        raise SsoError("no_email", "no email in ID token")
    return email
