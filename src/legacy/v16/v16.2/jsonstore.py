"""Atomic, self-protecting reads and writes for the JSON state files.

THE FAILURE THIS EXISTS TO STOP

Every store here had the same shape, and together the two halves formed a
chain that destroys data:

    items = _load()      # read fails -> warning logged -> returns []
    items.append(new)    # the caller cannot tell "empty" from "unreadable"
    _save(items)         # writes [new]; the other 300 entries are GONE

and three of the five stores wrote by truncating the file in place, so an
interrupted write was enough to produce the unreadable file that starts it.
A crash mid-write, a full disk, or -- as actually happened on 2026-09-01 --
a file written as UTF-8 and read back as cp1252, and the next write
persists the default over the original.

The FAQ store survived that only because nothing wrote before it was found.

THE FIX, AND WHY IT IS IN save() RATHER THAN load()

The obvious repair is to make every read say whether it succeeded, but that
means touching ~40 call sites and trusting all of them forever. Instead the
guard sits at the one place the damage is actually done:

  * load() degrades as before -- a broken FAQ file must not take chat down,
    so serving paths still get a usable default.
  * save() REFUSES to overwrite a file it cannot itself parse. By the time
    a bad read has produced a default, the file on disk is unreadable, so
    this catch fires and the original is preserved.

The unreadable file is copied aside once, as <name>.corrupt, and left in
place. Leaving it means writes keep failing loudly until a human looks --
which is the point. Moving it would let the very next write lay down the
default and quietly complete the data loss.

Reads stay soft, writes go hard, and no call site had to change.
"""
from __future__ import annotations

import json
import logging
import os
import shutil

logger = logging.getLogger(__name__)

CORRUPT_SUFFIX = ".corrupt"


class StateUnreadable(Exception):
    """A state file exists but cannot be parsed, so it must not be written
    over. Raised by save(); surfaces as a 500 rather than silent loss."""


def _parse(path: str):
    """The file's contents, or raise. Encoding is pinned deliberately: the
    2026-09-01 incident was a UTF-8 file read back under the platform
    default, which on Windows is cp1252."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load(path: str, default, *, label: str = ""):
    """Contents of `path`, or `default`.

    An ABSENT file is a legitimate empty state and returns the default
    quietly. A PRESENT but unparseable one is an emergency: it is logged at
    error, and the default is returned so serving continues -- but the file
    is left untouched so save() will refuse to overwrite it.
    """
    if not os.path.exists(path):
        return default
    try:
        return _parse(path)
    except Exception as exc:
        logger.error(
            f"{label or path} exists but could not be read ({exc}). Serving "
            f"defaults; writes to it will be refused until it is repaired "
            f"or removed.")
        return default


def _quarantine(path: str) -> str | None:
    """Copy an unreadable file aside, once. Copy, not move: the original has
    to stay put so save() keeps refusing."""
    dest = path + CORRUPT_SUFFIX
    if os.path.exists(dest):
        return dest
    try:
        shutil.copy2(path, dest)
        logger.error(f"kept a copy of the unreadable file at {dest}")
        return dest
    except OSError as exc:
        logger.warning(f"could not quarantine {path}: {exc}")
        return None


def save(path: str, data, *, label: str = "", indent: int = 2,
         sort_keys: bool = False) -> None:
    """Write `data` atomically, refusing to clobber an unreadable file.

    Atomic because the alternative -- opening the real path for writing --
    truncates it before the new bytes land, so a crash there leaves exactly
    the corrupt file this module is trying to prevent.
    """
    if os.path.exists(path):
        try:
            _parse(path)
        except Exception as exc:
            _quarantine(path)
            raise StateUnreadable(
                f"refusing to overwrite {label or path}: the existing file "
                f"could not be parsed ({exc}). A copy is at "
                f"{path + CORRUPT_SUFFIX}. Repair or remove the original, "
                f"then retry.")

    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=indent, sort_keys=sort_keys)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
