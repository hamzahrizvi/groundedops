"""Who is asking: the admin credential guards and the proxy-aware client IP.

These used to live in main.py. Every router needs them, so they sit here,
where importing them pulls in nothing but the account store.
"""

import ipaddress
import logging
import os
from functools import lru_cache

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


# Same default as main.py's PRIVATE_NETWORKS (the LAN gate); read per call so
# tests and operators see one rule. ponytail: main.py keeps its own copy of
# this list until main.py is free to import it from here.
_DEFAULT_PRIVATE = ("127.,10.,192.168.,172.16.,172.17.,172.18.,172.19.,"
                    "172.20.,172.21.,172.22.,172.23.,172.24.,172.25.,172.26.,"
                    "172.27.,172.28.,172.29.,172.30.,172.31.,::1")


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


def is_private_host(host: str) -> bool:
    """This box or the LAN, by the PRIVATE_NETWORKS prefixes main.py uses."""
    nets = os.getenv("PRIVATE_NETWORKS", _DEFAULT_PRIVATE).split(",")
    return bool(host) and any(host.startswith(p.strip()) for p in nets if p.strip())


@lru_cache(maxsize=8)
def _trusted_networks(raw: str) -> tuple:
    nets = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning(f"TRUSTED_PROXIES: ignoring {entry!r}, not an address or range")
    return tuple(nets)


def _peer_is_trusted(request) -> bool:
    """The socket peer may speak for the real client: this box (cloudflared
    forwards to localhost), the LAN, or an address in TRUSTED_PROXIES
    (comma-separated addresses or ranges, e.g. Cloudflare's published ranges
    or the website server that relays the widget's calls)."""
    host = request.client.host if request.client else ""
    if is_private_host(host):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _trusted_networks(os.getenv("TRUSTED_PROXIES", "")))


def _external_ip(request) -> str | None:
    """The real client's IP from the proxy headers, or None when there is none
    to believe (callers then use the socket address).

    D-20261008-11: a header is believed only from a trusted peer (see
    _peer_is_trusted); from anyone else it is a value the caller chose, and
    trusting it handed out a fresh per-IP allowance per request and let a
    forged address pass ADMIN_ALLOWED_IPS. CF-Connecting-IP is enough from a
    trusted peer; X-Forwarded-For and X-Real-IP, which a client can prepend
    to, also need TRUST_PROXY."""
    if not _peer_is_trusted(request):
        return None
    ip = request.headers.get("cf-connecting-ip")
    if ip:
        return ip.strip()
    if not _truthy("TRUST_PROXY"):
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    return real.strip() if real else None


def client_ip(request) -> str:
    """Who is calling, for per-address limits. Every route asks this."""
    return _external_ip(request) or (request.client.host if request.client else "unknown")


def is_local_caller(request) -> bool:
    """The real caller is this box or the LAN: no proxy header at all and a
    private socket address. Through the tunnel every request arrives from
    loopback but carries CF-Connecting-IP, so it is not local."""
    if any(h in request.headers for h in _PROXY_HEADERS):
        return False
    return is_private_host(request.client.host if request.client else "")


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
