"""Who is asking: the admin credential guards and the proxy-aware client IP.

These used to live in main.py. Every router needs them, so they sit here,
where importing them pulls in nothing but the account store.
"""

import logging

from fastapi import HTTPException

import accounts
import keystore

logger = logging.getLogger(__name__)


# Headers that only a reverse proxy / tunnel sets. Their PRESENCE is the
# signal that a request did not originate on the LAN, which matters because
# cloudflared runs ON this machine and forwards to localhost - so every
# tunnelled request arrives from 127.0.0.1 and would otherwise look like
# trusted local traffic, exposing the admin surface through the tunnel.
_PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "x-real-ip")


def _external_ip(request) -> str | None:
    """Real client IP if the request came via a proxy/tunnel, else None.
    CF-Connecting-IP is preferred: Cloudflare sets it and a client cannot
    forge it, whereas X-Forwarded-For is client-appendable."""
    ip = request.headers.get("cf-connecting-ip")
    if ip:
        return ip.strip()
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.headers.get("x-real-ip")


# ── Admin panel — account-gated catalog + doc management ───────────────
# Real accounts with levels now (see accounts.py). The single shared
# ADMIN_PASSWORD is gone.
#
# The credential travels in the `x-admin-password` header, which now carries
# a SESSION TOKEN rather than a password. The header name is kept only
# because 31 endpoints declare it; renaming it buys nothing on the wire and
# would touch every one of them. Read it as "the admin credential".
#
# Three guards, by what they let through:
#
#   _require_session — any signed-in account, including `basic`. Use for
#                      things every signed-in person may do.
#   _require_admin   — `support` or `root`. This is what all the existing
#                      admin endpoints use, so a `basic` account gets a
#                      session and is then refused everywhere that matters,
#                      which is exactly the intent.
#   _require_root    — `root` only. Account management, and nothing else.

if not keystore.session_secret_is_set():
    logger.warning(
        "SESSION_SECRET is not set — admin sign-in will refuse every "
        "attempt. Generate one with: python -c \"import secrets; "
        "print(secrets.token_hex(32))\" and put it in src/.env."
    )


def _require_session(cred: str | None) -> dict:
    """Any signed-in account. Returns the account record."""
    user = accounts.verify_session(cred)
    if not user:
        raise HTTPException(status_code=401, detail="Sign-in required")
    return user


def _require_admin(x_admin_password: str | None) -> dict:
    """`support` or above. The guard every pre-existing admin endpoint uses.

    Returns the account so newer call sites can attribute an action to a
    person; the 31 existing ones ignore the return value, which is why this
    change did not have to touch them.
    """
    user = _require_session(x_admin_password)
    if not accounts.has_level(user, "support"):
        raise HTTPException(
            status_code=403,
            detail="Your account does not have access to the admin console")
    return user


def _require_root(x_admin_password: str | None) -> dict:
    """`root` only — managing accounts is itself a privilege."""
    user = _require_session(x_admin_password)
    if not accounts.has_level(user, "root"):
        raise HTTPException(status_code=403,
                            detail="Only a root account can manage accounts")
    return user
