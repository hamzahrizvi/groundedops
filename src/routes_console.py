"""The admin console page, its static assets, and the network report.

Moved out of main.py verbatim (2026-09-25). Registered on the app through
app.include_router; paths, parameters and responses are unchanged.
"""

import ipaddress
import logging
import os
import socket
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from guards import _require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/admin/network")
def admin_network(request: Request, x_admin_password: str | None = Header(default=None)):
    """Where this server can be reached from other machines.

    The console cannot work this out for itself: a browser knows the address
    IT used, which is "localhost" for whoever is sitting at the machine --
    the one address nobody else can use. So the answer has to come from the
    server, and the rail can then say where to point a colleague or a widget
    embed without anyone running ipconfig.
    """
    _require_admin(x_admin_password)
    scheme = request.url.scheme or "http"
    default_port = 443 if scheme == "https" else 80
    # `URL.port` is None on ordinary :80/:443 requests. Falling back to 8000
    # there advertised a URL different from the one that had just worked.
    port = request.url.port or default_port

    def origin(host: str, port_: int = port, scheme_: str = scheme) -> str:
        shown = f"[{host}]" if ":" in host and not host.startswith("[") else host
        suffix = "" if ((scheme_ == "http" and port_ == 80)
                        or (scheme_ == "https" and port_ == 443)) else f":{port_}"
        return f"{scheme_}://{shown}{suffix}"

    urls: list[str] = []
    addresses: list[str] = []

    def add(host: str, *, url: str | None = None) -> None:
        host = (host or "").strip(" []")
        if not host or host.startswith(("127.", "169.254.")) or host == "::1":
            return
        if host not in addresses:
            addresses.append(host)
        candidate = url or origin(host)
        if candidate not in urls:
            urls.append(candidate.rstrip("/"))

    # Explicit deployment configuration wins. It covers VPNs, reverse
    # proxies and hosts with several real adapters where no OS heuristic can
    # know which address colleagues are meant to use.
    configured = (os.getenv("ADMIN_NETWORK_URL") or "").strip()
    if configured:
        try:
            raw = configured if "://" in configured else "http://" + configured
            parsed = urlsplit(raw)
            if parsed.hostname:
                cfg_scheme = (parsed.scheme
                              if parsed.scheme in ("http", "https") else "http")
                cfg_port = parsed.port or (443 if cfg_scheme == "https" else 80)
                add(parsed.hostname,
                    url=origin(parsed.hostname, cfg_port, cfg_scheme))
        except ValueError as exc:
            logger.warning(f"Ignoring invalid ADMIN_NETWORK_URL: {exc}")

    # If this request already arrived using a non-loopback IP, that is the
    # strongest possible evidence: it is a working console address. Put it
    # ahead of guessed adapter addresses.
    request_host = request.url.hostname or ""
    try:
        request_ip = ipaddress.ip_address(request_host)
        if not request_ip.is_loopback and not request_ip.is_link_local:
            add(request_host)
    except ValueError:
        pass

    routed = ""
    try:
        s_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s_.settimeout(0.4)
        try:
            # Nothing is sent; this just makes the OS choose the interface it
            # would really route over, which is the one a colleague reaches.
            s_.connect(("8.8.8.8", 80))
            routed = s_.getsockname()[0]
            add(routed)
        finally:
            s_.close()
    except Exception:
        pass
    # Everything else the host answers to, MINUS the virtual switches. A
    # Hyper-V or WSL adapter has a real address that answers locally and is
    # reachable from nothing, so listing it sends a colleague to a dead end.
    # Without psutil there are no adapter names to filter by, so the filter
    # is by subnet: keep an address only if it shares a /16 with the one the
    # OS actually routes over, which is how a second real NIC on the same
    # site looks and how a host-only switch does not.
    site = ".".join(routed.split(".")[:2]) if routed else ""
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith(("127.", "169.254.")) or ip in addresses:
                continue
            if site and ".".join(ip.split(".")[:2]) != site:
                continue
            add(ip)
    except Exception:
        pass
    return {"port": port, "addresses": addresses, "urls": urls,
            "primary_url": urls[0] if urls else None,
            "configured": bool(configured)}


@router.get("/admin", include_in_schema=False)
def admin_console():
    """Serve the admin console (v12.0).

    A single self-contained file next to main.py, so it needs no build step
    and updates by replacing one file. It is NOT in _PUBLIC_PREFIXES, so the
    surface guard blocks it from the internet exactly like every other admin
    route - the console is for the support department's LAN only.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin.html")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404,
                            detail="admin.html not found next to main.py")
    # no-store, because this file IS the deployment: it is replaced in place
    # and the next reload is meant to be the new console. Without it a browser
    # serves its cached copy from the last visit and the fix you just shipped
    # is invisible until someone thinks to hard-reload -- which looks exactly
    # like the change not working.
    return FileResponse(path, media_type="text/html",
                        headers={"Cache-Control": "no-store, must-revalidate"})


# admin.html's first <link> is `nocturne/styles.css`, resolved by the browser
# against /admin — i.e. /nocturne/styles.css. Nothing served that path, so the
# console rendered completely unstyled: the stylesheet request fell through to
# the SPA mount at the bottom of this file, which re-raises for anything with a
# dot in it, giving a 404. Mounted here rather than added to the SPA's directory
# because the console is not part of the React build.
#
# Registered BEFORE the "/" mount (Starlette matches in registration order) and
# deliberately NOT in _PUBLIC_PREFIXES, so the surface guard keeps it on the LAN
# with the console it belongs to.
_NOCTURNE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nocturne")


# The brand mark and icon set. Sat unserved next to the console until now,
# which is why /admin had no logo and a 404 favicon.
_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    """The console asked for this on every load and got a 404 every time."""
    from fastapi.responses import FileResponse, Response
    for name in ("groundedops-logo-animated.svg", "logo.svg"):
        path = os.path.join(_ASSETS_DIR, name)
        if os.path.exists(path):
            return FileResponse(path, media_type="image/svg+xml")
    return Response(status_code=204)


# ── Serve the built frontend (v12.0) ────────────────────────────────
# Native installs previously needed Node running a second dev server on
# :5173 alongside the API on :8000 — two terminal windows and two URLs,
# which is a lot to ask of a non-technical tester. When a production build
# exists we serve it from here instead, so the whole app is ONE process on
# ONE port and the installer never needs Node.
#
# ── root route (v15.2) ────────────────────────────────────────────────────
# The React SPA that used to be mounted here has been retired. It was a third
# chat surface duplicating the admin console's test chat and the widget, so
# every fix had to be made three times and the widget -- the only one
# customers actually see -- was consistently last to get it. Internal testing
# now happens in the admin console; the widget is the customer surface.
#
# Anything the SPA uniquely offered is noted in PENDING.md: the generation
# mode / provider toggle has no UI replacement yet and is currently only
# reachable via POST /settings/mode and /settings/online_provider.
@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/admin", status_code=307)


@router.get("/stream-test", include_in_schema=False)
def stream_test_page():
    """Hand-testing page for /query/stream. Local/LAN only -- it inherits the
    same surface guard as the rest of the non-widget app, so a proxied request
    404s here exactly as it does for /admin. Not linked from anywhere."""
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "stream_test.html"),
                        media_type="text/html")


# Both mounts ride along with the router: FastAPI copies a Mount into the
# app on include_router, so they are registered exactly as app.mount would.
if os.path.isdir(_NOCTURNE_DIR):
    router.mount("/nocturne", StaticFiles(directory=_NOCTURNE_DIR), name="nocturne")
else:
    logger.warning(
        f"No {_NOCTURNE_DIR} — the admin console at /admin will render unstyled.")

if os.path.isdir(_ASSETS_DIR):
    router.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")
