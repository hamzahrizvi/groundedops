"""The console's Confluence page: connection settings (root only), the sync
mode, the documents found with their approval, and the run history.
The work itself is confluence_sync.py."""

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import confluence_sync
import keystore
from guards import _require_admin, _require_root

logger = logging.getLogger(__name__)
router = APIRouter()


class SettingsReq(BaseModel):
    folder_url: str | None = None
    email: str | None = None
    token: str | None = None
    clear_token: bool | None = None


class ModeReq(BaseModel):
    mode: str


class ItemsReq(BaseModel):
    keys: list[str]
    decision: str | None = None
    product: str | None = None      # "k1,k2"; "" files it under nothing
    keep_upload: bool | None = None # true: keep the uploaded copy alongside


class RunReq(BaseModel):
    apply: bool = True              # false: check what changed, index nothing
    check: bool | None = None       # None: read Confluence first only if the last check is old


def _status() -> dict:
    return {**confluence_sync.overview(), "settings": keystore.get_confluence_settings()}


@router.get("/admin/confluence")
def admin_confluence(x_admin_password: str | None = Header(default=None)):
    _require_admin(x_admin_password)
    return _status()


@router.post("/admin/confluence")
def admin_confluence_save(payload: SettingsReq,
                          x_admin_password: str | None = Header(default=None)):
    """The connection: root only, because it holds the API token, which is
    write-only like the mail password."""
    me = _require_root(x_admin_password)
    changes = payload.model_dump(exclude_unset=True)
    try:
        if changes.get("folder_url"):
            changes["folder_url"] = confluence_sync.normalise_folder_url(changes["folder_url"])
        keystore.set_confluence_settings(changes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Confluence settings saved by {me['email']}")
    return _status()


@router.post("/admin/confluence/mode")
def admin_confluence_mode(payload: ModeReq,
                          x_admin_password: str | None = Header(default=None)):
    me = _require_admin(x_admin_password)
    try:
        confluence_sync.set_mode(payload.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Confluence sync mode set to {payload.mode} by {me['email']}")
    return _status()


@router.post("/admin/confluence/items")
def admin_confluence_items(payload: ItemsReq,
                           x_admin_password: str | None = Header(default=None)):
    me = _require_admin(x_admin_password)
    try:
        confluence_sync.set_items(payload.keys, payload.decision, payload.product,
                                  payload.keep_upload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Confluence: {len(payload.keys)} item(s) set "
                f"decision={payload.decision} product={payload.product} by {me['email']}")
    return _status()


@router.post("/admin/confluence/run")
def admin_confluence_run(payload: RunReq,
                         x_admin_password: str | None = Header(default=None)):
    me = _require_admin(x_admin_password)
    from app_state import capability
    if not confluence_sync.configured():
        raise HTTPException(status_code=400,
                            detail="A root admin has to connect Confluence first")
    if payload.apply and not capability("search"):
        raise HTTPException(status_code=503,
                            detail="The search model is still loading - try again in a few seconds")
    if confluence_sync.running():
        raise HTTPException(status_code=409, detail="A check or sync is already running")
    confluence_sync.run_in_background(apply=payload.apply, check=payload.check)
    logger.info(f"Confluence {'sync' if payload.apply else 'check'} started by {me['email']}")
    return {**_status(), "running": {"phase": "starting"}}


@router.post("/admin/confluence/cancel")
def admin_confluence_cancel(x_admin_password: str | None = Header(default=None)):
    """Stop the run in progress at its next page or document."""
    me = _require_admin(x_admin_password)
    if not confluence_sync.cancel():
        raise HTTPException(status_code=409, detail="Nothing is running")
    logger.info(f"Confluence run stop requested by {me['email']}")
    return {"running": confluence_sync.running()}


class ScheduleReq(BaseModel):
    every: str
    hours: float = 24
    time: str = "02:00"
    day: int = 0


@router.post("/admin/confluence/schedule")
def admin_confluence_schedule(payload: ScheduleReq,
                              x_admin_password: str | None = Header(default=None)):
    me = _require_admin(x_admin_password)
    try:
        confluence_sync.set_schedule(payload.every, payload.hours, payload.time, payload.day)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Confluence schedule {payload.every} {payload.hours}h {payload.time} day {payload.day} set by {me['email']}")
    return _status()


@router.get("/admin/confluence/progress")
def admin_confluence_progress(since: int = 0,
                              x_admin_password: str | None = Header(default=None)):
    """What a run is doing, and the documents it has reached since entry
    `since`. Polled while a run goes; the full page is fetched once at the end."""
    _require_admin(x_admin_password)
    return {"running": confluence_sync.running(max(0, since))}


class BundleReq(BaseModel):
    path: list[str]                 # folder titles from the root down
    on: bool = True


@router.post("/admin/confluence/bundle")
def admin_confluence_bundle(payload: BundleReq,
                            x_admin_password: str | None = Header(default=None)):
    """Combine a folder into one document, or split it again. A check starts
    straight away (if none is running) so the list shows the result; nothing
    reaches the bot until the next sync."""
    me = _require_admin(x_admin_password)
    try:
        confluence_sync.set_bundle(payload.path, payload.on)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Confluence: {' / '.join(payload.path)} "
                f"{'combined' if payload.on else 'split'} by {me['email']}")
    started = confluence_sync.configured() and not confluence_sync.running()
    if started:
        confluence_sync.run_in_background(apply=False)
    return {**_status(), "running": {"phase": "starting"} if started else confluence_sync.running()}
