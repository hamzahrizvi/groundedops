"""Backup export and restore, access policy, and quota resets.

Moved out of main.py verbatim (2026-09-25). Registered on the app through
app.include_router; paths, parameters and responses are unchanged.
"""

import json
import logging
import os
import uuid

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

import backup
import policy
from guards import _require_admin, _require_root

logger = logging.getLogger(__name__)
router = APIRouter()


# ── backup / restore ────────────────────────────────────────────────────
# Export is support-and-above; import is root only, because it overwrites.
#
# The archive's CONTENTS SCALE WITH LEVEL: a support export carries what
# support can already reach through the console (documents, index, FAQ,
# catalogue, widget config, enquiries). Only a root export additionally
# carries accounts.json and policy.json. Without that split, "anyone may
# export" would hand every staff password hash to a level that deliberately
# cannot manage accounts.

class ExportReq(BaseModel):
    documents: bool = True
    index: bool = True
    # Required unless BACKUP_ALLOW_PLAINTEXT is set. In the body, never a
    # query parameter: query strings end up in proxy and server access logs,
    # and a passphrase in a log is a passphrase that has leaked.
    passphrase: str | None = None


@router.post("/admin/backup/export")
def admin_backup_export(payload: ExportReq | None = None,
                        x_admin_password: str | None = Header(default=None)):
    """POST, not GET, so the passphrase travels in a body rather than a URL.

    The archive is encrypted unless BACKUP_ALLOW_PLAINTEXT is deliberately
    set — it carries password hashes and customer contact details, and a
    backup is precisely the file that ends up on a NAS or in an email.
    """
    me = _require_admin(x_admin_password)
    payload = payload or ExportReq()
    passphrase = (payload.passphrase or os.getenv("BACKUP_PASSPHRASE") or "").strip()
    allow_plain = os.getenv("BACKUP_ALLOW_PLAINTEXT", "").strip().lower() in (
        "1", "true", "yes")

    if not passphrase and not allow_plain:
        raise HTTPException(
            status_code=400,
            detail="A passphrase is required to export a backup. It cannot be "
                   "recovered if lost, so store it with your other secrets — "
                   "not alongside the backup.")
    try:
        if passphrase:
            backup.check_passphrase(passphrase)
        data, manifest = backup.create_archive(
            created_by=me["email"], level=me["level"],
            include_documents=payload.documents, include_index=payload.index,
            passphrase=passphrase or None)
    except backup.BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("backup export failed")
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    from fastapi.responses import Response
    return Response(
        content=data,
        media_type=("application/octet-stream" if manifest.get("encrypted")
                    else "application/zip"),
        headers={
            "Content-Disposition":
                f'attachment; filename="{backup.suggested_filename(manifest)}"',
            # So the console can show what it just downloaded without
            # reopening the zip in the browser.
            "X-Backup-Sensitive": "1" if manifest["sensitive"] else "0",
            "X-Backup-Encrypted": "1" if manifest.get("encrypted") else "0",
            "X-Backup-Counts": json.dumps(manifest["counts"]),
        })


@router.post("/admin/backup/inspect")
async def admin_backup_inspect(file: UploadFile = File(...),
                               passphrase: str = Form(default=""),
                               x_admin_password: str | None = Header(default=None)):
    """Read an archive's manifest without writing anything, so nobody has to
    agree to a restore before seeing what is in the file.

    An encrypted archive still identifies itself without its passphrase —
    when it was made, what it holds, whether it carries accounts — because
    otherwise picking the right file out of a folder of backups would mean
    typing a passphrase into each one in turn.
    """
    _require_root(x_admin_password)
    data = await file.read()
    try:
        if backup.is_encrypted(data) and not passphrase.strip():
            return {"manifest": backup.read_envelope(data),
                    "encrypted": True, "passphrase_required": True,
                    "size_bytes": len(data)}
        return {"manifest": backup.read_manifest(data, passphrase.strip() or None),
                "encrypted": backup.is_encrypted(data),
                "passphrase_required": False,
                "size_bytes": len(data)}
    except backup.BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/backup/import")
async def admin_backup_import(
        file: UploadFile = File(...),
        passphrase: str = Form(default=""),
        restore_accounts: bool = False,
        restore_documents: bool = True,
        restore_index: bool = True,
        restore_conversations: bool = True,
        x_admin_password: str | None = Header(default=None)):
    """Overwrite this install from an archive. Root only, and destructive.

    A safety snapshot of the current state is taken before anything is
    written, and handed back so it can be downloaded. `restore_accounts`
    defaults to FALSE: restoring an accounts file that does not contain your
    own account locks you out of the console you are standing in.
    """
    me = _require_root(x_admin_password)
    data = await file.read()
    try:
        result = backup.restore_archive(
            data, restore_accounts=restore_accounts,
            restore_documents=restore_documents,
            restore_index=restore_index,
            restore_conversations=restore_conversations,
            actor=me["email"],
            passphrase=passphrase.strip() or os.getenv("BACKUP_PASSPHRASE") or None)
    except backup.BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("backup restore failed")
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")

    # The pre-restore snapshot is kept on disk rather than returned inline:
    # a multi-hundred-MB base64 blob in a JSON response helps nobody.
    snap_dir = os.getenv("BACKUP_SNAPSHOT_DIR", "backup_snapshots")
    snap_name = None
    try:
        os.makedirs(snap_dir, exist_ok=True)
        snap_name = f"before-restore-{uuid.uuid4().hex[:8]}.zip"
        with open(os.path.join(snap_dir, snap_name), "wb") as fh:
            fh.write(result["safety_snapshot"])
    except Exception as e:
        logger.warning(f"could not write the pre-restore snapshot: {e}")

    return {
        "ok": True,
        "manifest": result["manifest"],
        "restored": result["restored"],
        "skipped": result["skipped"],
        "restart_required": result["restart_required"],
        "safety_snapshot": snap_name,
        "safety_snapshot_dir": snap_dir,
    }


# ── access policy (root only) ───────────────────────────────────────────
# Limits and who may use the assistant. Root rather than support: every
# setting here has a cost consequence, and opening AI to signed-out visitors
# in particular is a decision with a bill attached.

class PolicyReq(BaseModel):
    changes: dict


@router.get("/admin/policy")
def admin_policy_get(x_admin_password: str | None = Header(default=None)):
    _require_root(x_admin_password)
    return {"policy": policy.get(), "defaults": policy.defaults()}


@router.put("/admin/policy")
def admin_policy_update(payload: PolicyReq,
                        x_admin_password: str | None = Header(default=None)):
    me = _require_root(x_admin_password)
    try:
        return {"policy": policy.update(payload.changes, actor=me["email"])}
    except policy.PolicyError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/policy/reset")
def admin_policy_reset(x_admin_password: str | None = Header(default=None)):
    me = _require_root(x_admin_password)
    return {"policy": policy.reset(actor=me["email"])}


class QuotaResetReq(BaseModel):
    session_id: str | None = None   # clears one conversation's per-session cap
    uid: str | None = None          # clears a signed-in member/staff caller's daily credits
    visitor_id: str | None = None   # clears an anonymous visitor's daily credits/FAQ lookups
    client_ip: str | None = None    # paired with visitor_id - see quota.reset_visitor


@router.post("/admin/quota/reset")
def admin_quota_reset(payload: QuotaResetReq,
                      x_admin_password: str | None = Header(default=None)):
    """Support-desk relief valve for the widget's "That didn't go through"
    complaint: a visitor who actually hit a 429 quota limit (session, daily
    credits, or anonymous FAQ lookups) reads it as a broken connection, and
    the honest fix is a widget that tells them what happened - this
    endpoint is the "let them back in right now" companion to that, for
    when waiting for the window to roll over isn't good enough."""
    me = _require_admin(x_admin_password)
    import quota as _q
    reset = []
    if payload.session_id:
        _q.reset_session(payload.session_id)
        reset.append("session")
    if payload.uid:
        _q.reset_uid(payload.uid)
        reset.append("member")
    if payload.visitor_id:
        _q.reset_visitor(payload.visitor_id, payload.client_ip or "")
        reset.append("visitor")
    if not reset:
        raise HTTPException(status_code=400,
                            detail="Provide session_id, uid, and/or visitor_id")
    logger.info(f"/admin/quota/reset: {reset} reset by {me.get('email')}")
    return {"reset": reset}
