"""Console sign-in and accounts: bootstrap, password and emailed-link
sign-in, SSO, sessions, and user administration.

Moved out of main.py verbatim (2026-09-25). Registered on the app through
app.include_router; paths, parameters and responses are unchanged.
"""

import logging
import os
import socket
import threading
import time

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

import accounts
import keystore
from guards import _PROXY_HEADERS, _external_ip, _require_root, _require_session

logger = logging.getLogger(__name__)
router = APIRouter()


def _public_self(user_rec: dict) -> dict:
    """The signed-in account as the console needs it: level drives which
    nav entries render."""
    return {"id": user_rec["id"], "email": user_rec["email"],
            "name": user_rec.get("name", ""), "level": user_rec["level"],
            "must_change_password": bool(user_rec.get("must_change_password"))}


class LoginReq(BaseModel):
    email: str
    password: str


class BootstrapReq(BaseModel):
    email: str
    password: str
    name: str | None = None


class NewUserReq(BaseModel):
    email: str
    password: str
    level: str
    name: str | None = None


class LevelReq(BaseModel):
    level: str


class DisabledReq(BaseModel):
    disabled: bool


class PasswordReq(BaseModel):
    password: str


class SelfPasswordReq(BaseModel):
    current_password: str
    new_password: str


@router.get("/admin/auth/state")
def admin_auth_state():
    """Unauthenticated on purpose: the sign-in page has to know whether to
    show a sign-in form or a first-run "create the root account" form, and
    it cannot know that without asking. Leaks only whether this install has
    been set up, which an installer already knows."""
    import mailer
    import sso
    providers = sso.enabled()
    return {"initialised": not accounts.is_uninitialised(),
            "sso": bool(providers),
            # Which "Sign in with ..." buttons to draw: only fully configured
            # providers, so a button never leads to an error page.
            "sso_providers": providers,
            # Whether "forgot password" and "email me a link" can work, so the
            # sign-in page only offers them when they will.
            "email_links": mailer.is_configured(),
            "email_domain": accounts.ALLOWED_EMAIL_DOMAIN,
            # Whether first-run setup will be accepted from where the caller
            # is, so the gate can say "set this up on the server" instead of
            # offering a form that is going to 403.
            "bootstrap_token_required": bool(
                (os.getenv("BOOTSTRAP_TOKEN") or "").strip())}


@router.post("/admin/auth/bootstrap")
def admin_auth_bootstrap(payload: BootstrapReq, request: Request,
                         x_bootstrap_token: str | None = Header(default=None)):
    """First-run only: creates the single root account. `bootstrap_root`
    refuses once any account exists, so this cannot be used later to add a
    second way in.

    THE LAND-GRAB: this endpoint has to be unauthenticated — there is no
    account to authenticate against yet. While the admin surface was
    LAN-only that was fine. The moment ADMIN_ALLOWED_IPS opens it up, an
    unauthenticated "create the root account" endpoint on a reachable URL
    means whoever reaches it first owns the install. On a staging box that
    is a bot, not a colleague.

    So an EXTERNAL caller must additionally present BOOTSTRAP_TOKEN. A
    request that did not arrive through a proxy (a real LAN or console-on-
    the-box request) is unaffected, which keeps first-run setup simple in
    the normal case. Once the root account exists this whole path is closed
    by bootstrap_root regardless, so the token is only needed once.
    """
    external = any(h in request.headers for h in _PROXY_HEADERS)
    if external:
        expected = (os.getenv("BOOTSTRAP_TOKEN") or "").strip()
        if not expected:
            raise HTTPException(
                status_code=403,
                detail="First-run setup is not available over the network. "
                       "Create the first account on the server itself "
                       "(python manage_accounts.py create you@example.com "
                       "--level root), or set BOOTSTRAP_TOKEN.")
        import hmac as _hmac
        if not _hmac.compare_digest((x_bootstrap_token or "").strip(), expected):
            logger.warning("bootstrap refused: bad or missing BOOTSTRAP_TOKEN "
                           f"from {_external_ip(request)}")
            raise HTTPException(status_code=403,
                               detail="A valid setup token is required.")
    try:
        user = accounts.bootstrap_root(payload.email, payload.password,
                                       payload.name or "")
    except accounts.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))
    rec = accounts.find_by_email(user["email"])
    return {"user": user, "token": accounts.issue_session(rec)}


@router.post("/admin/login")
def admin_login(payload: LoginReq):
    """Returns a session token plus the account, so the console knows which
    level it is rendering for. A failure is always the same 401 with the
    same wording — which of email or password was wrong is not the caller's
    business."""
    user = accounts.verify_password(payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=401,
                            detail="That email and password were not accepted")
    try:
        token = accounts.issue_session(user)
    except accounts.AccountError as e:
        # SESSION_SECRET missing. A 503 rather than a 401: the credentials
        # may well have been right, the server is not able to issue a
        # session at all, and saying "not accepted" would send someone off
        # to reset a password that was never the problem.
        raise HTTPException(status_code=503, detail=str(e))
    accounts.record_login(user["id"])
    return {"token": token, "user": _public_self(user)}


@router.post("/admin/auth/request")
def admin_auth_request(payload: BootstrapReq):
    """Submit an account request for root approval.

    The password is scrypt-hashed before the request is persisted. The reply
    is intentionally identical for an existing account and an already-pending
    request, so this unauthenticated route cannot enumerate staff accounts.
    """
    if accounts.is_uninitialised():
        raise HTTPException(status_code=409,
                            detail="Create the first root account before "
                                   "requesting additional access")
    try:
        accounts.submit_access_request(payload.email, payload.password,
                                       payload.name or "")
    except accounts.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"submitted": True,
            "message": "Your request was sent to a root account for approval."}


# ── emailed links: "forgot password" and "email me a sign-in link" ───────
# Both are unauthenticated by nature, so three rules hold throughout:
#
#   * The reply never says whether the address has an account. It is the
#     same wording, and the mail is sent on a thread so the response time
#     does not say it either.
#   * The link is built from a CONFIGURED console address when there is
#     one, never from the request's Host header on an exposed install --
#     otherwise anyone could ask for a reset of a colleague's account with
#     `Host: attacker.example` and the real email would carry the token to
#     the attacker's server the moment it was clicked.
#   * The token rides in the URL fragment (#...), which browsers never send
#     to a server, and is only spent by a deliberate click on the page.
#     Mail scanners that pre-open links therefore cannot use it up.

class EmailLinkReq(BaseModel):
    email: str
    purpose: str   # "reset" | "login"


class EmailTokenReq(BaseModel):
    token: str


class ResetReq(BaseModel):
    token: str
    password: str


_LINK_WINDOW = 15 * 60


_LINK_PER_EMAIL = 3


_LINK_PER_IP = 10


_link_hits: dict[str, list[float]] = {}


_link_lock = threading.Lock()


def _link_rate_ok(*keys: str) -> bool:
    now = time.time()
    limits = {"e": _LINK_PER_EMAIL, "i": _LINK_PER_IP}
    with _link_lock:
        if len(_link_hits) > 5000:   # junk addresses must not grow this forever
            for k in [k for k, v in _link_hits.items()
                      if not v or now - v[-1] >= _LINK_WINDOW]:
                _link_hits.pop(k, None)
        for k in keys:
            hits = [t for t in _link_hits.get(k, []) if now - t < _LINK_WINDOW]
            _link_hits[k] = hits
            if len(hits) >= limits[k[0]]:
                return False
        for k in keys:
            _link_hits[k].append(now)
    return True


def _local_hostnames() -> set[str]:
    names = {"localhost", "127.0.0.1", "::1"}
    try:
        host = socket.gethostname()
        names.update({host.lower(), socket.getfqdn().lower()})
        for info in socket.getaddrinfo(host, None):
            names.add(info[4][0].lower())
    except OSError:
        pass
    return names


def _console_base_url(request: Request) -> str | None:
    """Where an emailed link should point, or None when it cannot be known
    safely. ADMIN_PUBLIC_URL (or ADMIN_NETWORK_URL, which already says where
    colleagues reach the console) wins. Without either, the request's own
    origin is used only when its host is this machine -- a direct LAN or
    localhost visit -- because only then is the Host header not the
    caller's to choose."""
    configured = ((os.getenv("ADMIN_PUBLIC_URL") or "").strip()
                  or (os.getenv("ADMIN_NETWORK_URL") or "").strip())
    if configured:
        if "://" not in configured:
            configured = "http://" + configured
        return configured.rstrip("/")
    if any(h in request.headers for h in _PROXY_HEADERS):
        return None
    host = (request.url.hostname or "").lower()
    if host in _local_hostnames():
        return str(request.base_url).rstrip("/")
    return None


def _send_link_email(purpose: str, token: str, user: dict, base: str) -> None:
    import mailer
    link = f"{base}/admin#{'reset' if purpose == 'reset' else 'login'}={token}"
    mins = accounts.EMAIL_TOKEN_TTL[purpose] // 60
    who = user.get("name") or user["email"]
    if purpose == "reset":
        subject = "[GroundedOps] Reset your password"
        body = (f"Hi {who},\n\nSomeone asked to reset the password for your "
                f"GroundedOps console account. To choose a new one, open:\n\n"
                f"{link}\n\nThe link works once and expires in {mins} minutes. "
                f"Using it signs out every other session on your account.\n\n"
                f"If this wasn't you, ignore this email; your password has not "
                f"changed.\n")
    else:
        subject = "[GroundedOps] Your sign-in link"
        body = (f"Hi {who},\n\nUse this link to sign in to the GroundedOps "
                f"console without a password:\n\n{link}\n\nIt works once and "
                f"expires in {mins} minutes.\n\nIf you didn't ask for it, ignore "
                f"this email; nobody can use it without access to your inbox.\n")
    try:
        mailer.send([user["email"]], subject, body)
    except Exception as e:
        logger.warning(f"{purpose} link for {user['email']} not sent: {e}")


@router.post("/admin/auth/email-link")
def admin_auth_email_link(payload: EmailLinkReq, request: Request):
    import mailer
    if payload.purpose not in accounts.EMAIL_LINK_PURPOSES:
        raise HTTPException(status_code=400, detail="unknown link type")
    if not mailer.is_configured():
        raise HTTPException(status_code=503,
                            detail="Email is not set up on this server. Ask a root "
                                   "admin to reset your password from Accounts.")
    base = _console_base_url(request)
    if not base:
        logger.warning("email link refused: set ADMIN_PUBLIC_URL so links can "
                       "be addressed safely from behind a proxy")
        raise HTTPException(status_code=503,
                            detail="Email links are not available from this address. "
                                   "Ask a root admin to set ADMIN_PUBLIC_URL.")
    email = accounts.normalise_email(payload.email or "")
    ip = _external_ip(request) or (request.client.host if request.client else "")
    generic = {"sent": True,
               "message": "If that address has an account, an email is on its "
                          "way. Check your inbox (and junk folder)."}
    if not email or not _link_rate_ok("e:" + email, "i:" + ip):
        return generic   # rate-limited requests look the same as any other
    issued = accounts.create_email_token(email, payload.purpose)
    if issued:
        token, user = issued
        threading.Thread(target=_send_link_email,
                         args=(payload.purpose, token, user, base),
                         daemon=True).start()
    return generic


def _session_reply(user: dict) -> dict:
    try:
        token = accounts.issue_session(user)
    except accounts.AccountError as e:
        raise HTTPException(status_code=503, detail=str(e))
    accounts.record_login(user["id"])
    return {"token": token, "user": _public_self(user)}


@router.post("/admin/auth/email-login")
def admin_auth_email_login(payload: EmailTokenReq):
    user = accounts.consume_email_token(payload.token, "login")
    if not user:
        raise HTTPException(status_code=400,
                            detail="This sign-in link has expired or has already "
                                   "been used. Request a new one.")
    logger.info(f"signed in by email link: {user['email']}")
    return _session_reply(user)


@router.post("/admin/auth/reset")
def admin_auth_reset(payload: ResetReq):
    """Choose a new password from a reset link, and be signed in with it."""
    try:
        user = accounts.reset_password_with_token(payload.token, payload.password)
    except accounts.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _session_reply(user)


# ── work-account sign-in: Microsoft / Google (sso.py) ─────────────────────

@router.get("/admin/auth/sso/{provider}/start", include_in_schema=False)
def admin_sso_start(provider: str, request: Request):
    import sso
    base = _console_base_url(request)
    if not base:
        return RedirectResponse("/admin#sso_error=no_address", status_code=302)
    try:
        url, binding = sso.start(provider, base)
    except sso.SsoError as e:
        logger.warning(f"SSO start refused for {provider}: {e}")
        return RedirectResponse(f"/admin#sso_error={e.code}", status_code=302)
    except Exception as e:
        logger.warning(f"SSO start failed for {provider}: {e}")
        return RedirectResponse("/admin#sso_error=provider_error", status_code=302)
    res = RedirectResponse(url, status_code=302)
    # Lax, not Strict: the provider sends the browser back with a top-level
    # GET, which Lax allows and Strict would drop.
    res.set_cookie(sso.STATE_COOKIE, binding, max_age=sso.STATE_TTL, httponly=True,
                   samesite="lax", secure=base.startswith("https://"),
                   path="/admin/auth/sso")
    return res


@router.get("/admin/auth/sso/{provider}/callback", include_in_schema=False)
def admin_sso_callback(provider: str, request: Request, code: str = "",
                       state: str = "", error: str = ""):
    import sso
    def back(fragment: str):
        res = RedirectResponse(f"/admin#{fragment}", status_code=302)
        res.delete_cookie(sso.STATE_COOKIE, path="/admin/auth/sso")
        return res
    if error:
        # access_denied is the person pressing Cancel -- not worth a warning.
        logger.info(f"SSO {provider} returned error={error!r}")
        return back("sso_error=cancelled")
    try:
        email = sso.finish(provider, code, state,
                           request.cookies.get(sso.STATE_COOKIE))
    except sso.SsoError as e:
        logger.warning(f"SSO {provider} sign-in refused: {e}")
        return back(f"sso_error={e.code}")
    except Exception as e:
        logger.warning(f"SSO {provider} sign-in failed: {e}")
        return back("sso_error=provider_error")
    handoff = accounts.create_sso_handoff(email)
    if not handoff:
        # The provider vouched for them, but nobody here has given them an
        # account. Said plainly: they are who they say, and the fix is a
        # root admin, not trying again.
        logger.info(f"SSO {provider} sign-in with no account: {email}")
        return back("sso_error=no_account")
    logger.info(f"SSO {provider} sign-in verified for {email}")
    return back(f"sso={handoff}")


@router.post("/admin/auth/sso/finish")
def admin_sso_finish(payload: EmailTokenReq):
    user = accounts.consume_email_token(payload.token, "sso")
    if not user:
        raise HTTPException(status_code=400,
                            detail="That sign-in has expired. Please try again.")
    return _session_reply(user)


class SsoSettingsReq(BaseModel):
    client_id: str | None = None
    tenant: str | None = None
    domain: str | None = None
    secret: str | None = None
    clear_secret: bool = False


def _sso_status(request: Request) -> dict:
    import sso
    base = _console_base_url(request)
    out = {"console_url": base, "providers": {}}
    for p, meta in sso.PROVIDERS.items():
        out["providers"][p] = dict(
            keystore.get_sso_settings(p), label=meta["label"],
            problem=sso.config_problem(p),
            redirect_uri=sso.redirect_uri(base, p) if base else None)
    return out


@router.get("/admin/sso")
def admin_sso_settings(request: Request,
                       x_admin_password: str | None = Header(default=None)):
    """Root only. Includes the exact redirect URI to register with each
    provider, since a mismatch there is the usual first-time failure."""
    _require_root(x_admin_password)
    return _sso_status(request)


@router.post("/admin/sso/{provider}")
def admin_sso_save(provider: str, payload: SsoSettingsReq, request: Request,
                   x_admin_password: str | None = Header(default=None)):
    me = _require_root(x_admin_password)
    try:
        keystore.set_sso_settings(provider, payload.model_dump(exclude_unset=True))
    except keystore.UnknownProviderError:
        raise HTTPException(status_code=404, detail="unknown provider")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"SSO settings for {provider} saved by {me['email']}")
    return _sso_status(request)


@router.get("/admin/auth/me")
def admin_auth_me(x_admin_password: str | None = Header(default=None)):
    """Any level. The console calls this on load to re-establish who it is
    rendering for after a refresh."""
    return {"user": _require_session(x_admin_password)}


@router.post("/admin/auth/password")
def admin_auth_change_password(payload: SelfPasswordReq,
                               x_admin_password: str | None = Header(default=None)):
    """Self-service, any level. Requires the current password even though
    the session is already proven, so a walked-up-to unlocked browser
    cannot be used to lock the real owner out."""
    me = _require_session(x_admin_password)
    if not accounts.verify_password(me["email"], payload.current_password):
        raise HTTPException(status_code=401,
                            detail="Your current password was not accepted")
    try:
        accounts.set_password(me["id"], payload.new_password)
    except accounts.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # token_epoch moved, so the caller's own token is now dead too. Hand
    # back a fresh one rather than bouncing them to the sign-in page.
    rec = accounts.find_by_id(me["id"])
    return {"ok": True, "token": accounts.issue_session(rec)}


# ── account management (root only) ─────────────────────────────────────

@router.get("/admin/users")
def admin_users_list(x_admin_password: str | None = Header(default=None)):
    _require_root(x_admin_password)
    return {"users": accounts.list_users(),
            "requests": accounts.list_access_requests(),
            "levels": list(accounts.LEVELS)}


@router.post("/admin/users")
def admin_users_create(payload: NewUserReq,
                       x_admin_password: str | None = Header(default=None)):
    me = _require_root(x_admin_password)
    try:
        return {"user": accounts.create_user(
            payload.email, payload.password, payload.level,
            name=payload.name or "", created_by=me["email"])}
    except accounts.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/account-requests/{request_id}/approve")
def admin_access_request_approve(request_id: str, payload: LevelReq,
                                 x_admin_password: str | None = Header(default=None)):
    me = _require_root(x_admin_password)
    try:
        return {"user": accounts.approve_access_request(
            request_id, payload.level, me["email"])}
    except accounts.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/admin/account-requests/{request_id}")
def admin_access_request_reject(request_id: str,
                                x_admin_password: str | None = Header(default=None)):
    me = _require_root(x_admin_password)
    try:
        accounts.reject_access_request(request_id, me["email"])
    except accounts.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.post("/admin/users/{user_id}/level")
def admin_users_set_level(user_id: str, payload: LevelReq,
                          x_admin_password: str | None = Header(default=None)):
    me = _require_root(x_admin_password)
    try:
        return {"user": accounts.set_level(user_id, payload.level, me["email"])}
    except accounts.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/users/{user_id}/disabled")
def admin_users_set_disabled(user_id: str, payload: DisabledReq,
                             x_admin_password: str | None = Header(default=None)):
    me = _require_root(x_admin_password)
    try:
        return {"user": accounts.set_disabled(user_id, payload.disabled, me["id"])}
    except accounts.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/users/{user_id}/password")
def admin_users_set_password(user_id: str, payload: PasswordReq,
                             x_admin_password: str | None = Header(default=None)):
    """Root resetting someone else's password — for the "locked out, needs a
    way back in" case. Forces a change at their next sign-in so the reset
    value does not stay in use."""
    me = _require_root(x_admin_password)
    try:
        accounts.set_password(user_id, payload.password, me["email"],
                              must_change=True)
    except accounts.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/admin/users/{user_id}")
def admin_users_delete(user_id: str,
                       x_admin_password: str | None = Header(default=None)):
    me = _require_root(x_admin_password)
    try:
        accounts.delete_user(user_id, me["id"])
    except accounts.AccountError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}
