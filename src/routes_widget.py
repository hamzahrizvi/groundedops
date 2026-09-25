"""The website widget: its script and preview page, its public config and
lead form, and the console pages that configure and export it.

Moved out of main.py verbatim (2026-09-25). Registered on the app through
app.include_router; paths, parameters and responses are unchanged.
"""

import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

import accounts
import widget_config
import widget_export
from guards import _require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Widget design + lead capture (v13.0) ─────────────────────────────
# widget_config.py shipped with the v13.0 drop but was never imported, so the
# console's "Widget design" page and both contact forms called routes that did
# not exist. The module's TWO AUDIENCES split is enforced here: the public
# widget reads a stripped config and can POST a lead; only the console reads
# the full config, writes it, or sees the leads it produced.

# ── widget asset serving (v15.2) ──────────────────────────────────────────
# The widget script used to be served incidentally: it lived in the React
# app's public/ folder, vite copied it into dist/, and dist was mounted at
# "/". That made a customer-facing asset depend on an internal dev app being
# built and mounted -- retiring the React app would have 404'd every embedded
# <script src=".../widget/groundedops-widget.js"> in the wild.
#
# It now lives in src/widget/ and is served explicitly. EXACT paths, not a
# /widget/{filename} parameter, because a param route would shadow the
# /widget/config and /widget/lead API endpoints below.
_WIDGET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "widget")


def _serve_widget_asset(name: str, media_type: str):
    path = os.path.join(_WIDGET_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Widget asset not found")
    # no-store: customers embed this by a stable URL, so a cached stale copy
    # is indistinguishable from a broken deploy.
    return FileResponse(path, media_type=media_type,
                        headers={"Cache-Control": "no-store"})


@router.get("/widget/groundedops-widget.js")
def widget_js():
    return _serve_widget_asset("groundedops-widget.js", "application/javascript")


@router.get("/widget/preview")
def widget_preview():
    """A stand-in customer page that embeds the real widget, shown in an
    iframe by the console's Test chat.

    Public, like the rest of /widget/*, and deliberately so: it exposes
    nothing the widget itself does not already expose on every page it is
    embedded on. It is also useful on its own as an embed smoke-test —
    if this page works and a customer's does not, the fault is their
    embed, not the widget.
    """
    return _serve_widget_asset("preview.html", "text/html")


@router.get("/widget/groundedops-widget.php")
def widget_php():
    # Served as text: it is a snippet for the website team to copy, not
    # something this process executes.
    return _serve_widget_asset("groundedops-widget.php", "text/plain")


@router.get("/widget/config")
def widget_get_config(request: Request, visitor_id: str | None = None):
    """PUBLIC — read by the embedded widget on load. public_config() drops the
    admin-only fields (notification addresses), so this is safe unauthenticated
    and stays on the _PUBLIC_PREFIXES allowlist with the rest of /widget/*.

    Carries the two fields widget_api.py's older version of this route supplied
    before v13.0 moved config into the console: `sign_in_url` (the "sign in for
    more questions" upsell target) and the caller's `quota`, so the widget can
    show its remaining allowance before spending anything. Quota is resolved
    defensively — a missing or broken quota module must degrade the upsell, not
    break the config fetch the whole widget waits on.
    """
    out = widget_config.public_config()
    out["sign_in_url"] = os.getenv("WIDGET_SIGN_IN_URL", "")
    try:
        import quota as _q

        auth = request.headers.get("authorization", "")
        _tok = auth[7:].strip() if auth.lower().startswith("bearer ") else None
        _fwd = request.headers.get("x-forwarded-for", "")
        _ip = (_fwd.split(",")[0].strip() if _fwd
               else (request.client.host if request.client else ""))
        out["quota"] = _q.status(_q.identify(_tok, visitor_id, _ip))
    except Exception as e:
        logger.warning(f"/widget/config: quota unavailable ({e})")
        out["quota"] = None
    return out


@router.get("/admin/widget/config")
def widget_admin_get_config(x_admin_password: str | None = Header(default=None)):
    """Full config, including the admin-only fields, for the console editor."""
    _require_admin(x_admin_password)
    return widget_config.get()


@router.put("/admin/widget/config")
def widget_save_config(payload: dict,
                       x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    try:
        return widget_config.save(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/widget/config/reset")
def widget_reset_config(x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    return widget_config.reset()


class PluginExportReq(BaseModel):
    api_url: str = ""
    include_secret: bool = False


@router.post("/admin/widget/export_plugin")
def widget_export_plugin(payload: PluginExportReq,
                         x_admin_password: str | None = Header(default=None)):
    """Build a WordPress plugin zip with this deployment's settings baked in.

    POST rather than GET for the same reason as the backup export: the
    response can carry a secret, and a GET is what ends up in browser history
    and proxy access logs.

    Embedding the secret needs `root`, not `support`. Without it the zip is
    ordinary code; with it the zip is a credential that mints signed-in
    visitors, so it sits at the same level as the other secret-handling
    endpoints rather than with the day-to-day console.
    """
    me = _require_admin(x_admin_password)
    if payload.include_secret and not accounts.has_level(me, "root"):
        raise HTTPException(
            status_code=403,
            detail="Only a root account can export a plugin with the signing "
                   "secret embedded. Export without it and add the secret to "
                   "wp-config.php instead.")
    try:
        data, manifest = widget_export.build_plugin_zip(
            payload.api_url, include_secret=payload.include_secret,
            exported_by=me.get("email", ""))
    except widget_export.ExportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("widget plugin export failed")
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    from fastapi.responses import Response
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition":
                f'attachment; filename="{manifest["filename"]}"',
            # So the console can warn about what it just handed over without
            # reopening the archive in the browser.
            "X-Plugin-Sensitive": "1" if manifest["sensitive"] else "0",
            "X-Plugin-Api-Url": manifest["api_url"],
        })


@router.get("/admin/widget/detect_url")
def widget_detect_url(x_admin_password: str | None = Header(default=None)):
    """Best-effort discovery of a local run.cmd/run.ps1 Cloudflare quick
    tunnel, so the plugin-export card can offer that address instead of
    whatever this console page happens to be viewed from -- the two are NOT
    the same address when the console itself is being reached over the LAN
    while the tunnel is what the WIDGET needs.

    Same access level as the rest of Widget design (support+): this reveals
    no secret, only a URL, so it does not need root the way embedding the
    signing secret does.

    Returns {"url": null, ...} rather than 404 when nothing is found, which
    is the ordinary case for a real deployment -- the console treats that as
    an expected, silent no-op, not an error.
    """
    _require_admin(x_admin_password)
    return widget_export.detect_tunnel_url()


class LeadReq(BaseModel):
    kind: str                      # "sales" | "support"
    values: dict = {}
    product: str | None = None
    transcript: list | None = None
    # The written-up enquiry (from /widget/draft_enquiry, or typed by the
    # visitor) and which of those it was.
    enquiry: str | None = None
    summary_source: str = "none"   # "chat" | "written" | "none"


@router.post("/widget/lead")
def widget_submit_lead(payload: LeadReq):
    """PUBLIC — a visitor submitting the sales or support form.

    Unauthenticated by necessity: the widget is embedded on a customer-facing
    page. Unknown keys in `values` are dropped against the configured fields and
    the transcript is kept only if that form allows it — see
    widget_config.add_lead.

    NOT RATE LIMITED HERE. This is a public write endpoint and bots will find
    it; MAX_LEADS caps the file size but does nothing about the noise. Put it
    behind the same rate limiting / captcha as any other public form at the edge
    (reverse proxy, WAF) before exposing it — which is why this is called out
    rather than half-implemented in application code.
    """
    try:
        lead = widget_config.add_lead(
            payload.kind, payload.values or {},
            product=payload.product, transcript=payload.transcript,
            enquiry=payload.enquiry or "",
            summary_source=payload.summary_source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Deliberately does not echo the stored lead back to a public caller.
    return {"received": True, "id": lead["id"]}


@router.get("/admin/leads")
def admin_list_leads(kind: str | None = None, unhandled_only: bool = False,
                     x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    return {"leads": widget_config.list_leads(kind, not unhandled_only),
            "stats": widget_config.lead_stats()}


@router.post("/admin/leads/{lead_id}/handled")
def admin_mark_lead(lead_id: str, handled: bool = True,
                    x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    if not widget_config.mark_lead(lead_id, handled):
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"ok": True, "handled": handled}


@router.delete("/admin/leads/{lead_id}")
def admin_delete_lead(lead_id: str,
                      x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    if not widget_config.delete_lead(lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"deleted": True}


@router.post("/admin/widget/preview_token")
def admin_widget_preview_token(x_admin_password: str | None = Header(default=None)):
    """Mint a short-lived widget token so the console can preview the widget
    as a SIGNED-IN visitor, not only as an anonymous one.

    Admin-gated and deliberately short-lived: this mints the same kind of
    token the company website issues, so it is a credential, not a preview
    flag. `member` rather than `staff` because member is what a real
    customer holds — previewing as staff would show allowances no customer
    has.
    """
    me = _require_admin(x_admin_password)
    try:
        import quota as _q
        token = _q.issue_token(f"preview-{me['id']}", "member", 1800)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not mint a preview token: {e}")
    return {"token": token, "expires_in": 1800, "tier": "member"}
