"""Where the original documents live, and what was done to them.

WHY THIS EXISTS

Until v15 the retained originals were written to `os.getenv("SOURCE_FILE_DIR",
"/data/source_files")`. That default is a Docker volume path, and on Windows it
silently resolves to `C:\\data\\source_files` — outside the repo, outside version
control, outside any backup, and in a place nobody thinks to look. The files were
there the whole time; a search of the project and the user's folders concluded
they were lost.

That is the failure this module fixes. The index is a DERIVED artefact: it must
always be rebuildable from the document store, and the store must be somewhere
obvious and backed up. If the store is the thing that goes missing, nothing
else in the pipeline can be re-tuned, because re-chunking means re-ingesting.

RESOLUTION ORDER

  1. SOURCE_FILE_DIR, if set — an explicit choice always wins.
  2. <repo>/documents — the default, beside corpus/ where a human looks.
  3. /data/source_files — the legacy location, READ-ONLY, so an existing
     install keeps working and can be migrated deliberately.

A manifest sits beside the files recording, per document, the settings it was
ingested with: chunk geometry, parser, embedder. Without it there is no way to
tell whether a chunk in the index was built at 500 chars or 1200, which makes
"did the chunking change help?" unanswerable.
"""
import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
LEGACY_DIR = "/data/source_files"
MANIFEST_NAME = "manifest.json"


def store_dir() -> str:
    """The directory documents are written to. Created on demand."""
    explicit = (os.getenv("SOURCE_FILE_DIR") or "").strip()
    if explicit:
        return explicit
    return os.path.join(os.path.dirname(_HERE), "documents")


def read_dirs() -> list[str]:
    """Every directory an original might be found in, best first.

    The legacy path is included for reading so an install that predates v15
    still resolves its own documents before anyone has migrated.
    """
    dirs = [store_dir()]
    legacy = os.path.abspath(LEGACY_DIR)
    if legacy not in (os.path.abspath(d) for d in dirs) and os.path.isdir(legacy):
        dirs.append(legacy)
    return dirs


def find(filename: str) -> str | None:
    """Absolute path of a retained original, or None if it is genuinely gone."""
    base = os.path.basename(filename)
    for d in read_dirs():
        p = os.path.join(d, base)
        if os.path.isfile(p):
            return p
    return None


def save(filename: str, content: bytes) -> str:
    """Retain an original. Returns the path written."""
    d = store_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, os.path.basename(filename))
    with open(path, "wb") as fh:
        fh.write(content)
    return path


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# ── manifest ──────────────────────────────────────────────────────────────

def _manifest_path() -> str:
    return os.path.join(store_dir(), MANIFEST_NAME)


def load_manifest() -> dict:
    p = _manifest_path()
    if not os.path.isfile(p):
        return {"version": 1, "documents": {}}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("documents", {})
        return data
    except Exception as exc:
        logger.warning(f"Unreadable manifest at {p} ({exc}); starting a new one")
        return {"version": 1, "documents": {}}


def record(filename: str, *, content: bytes | None = None,
           chunks: int | None = None, pages: int | None = None,
           settings: dict | None = None) -> None:
    """Note how one document was ingested.

    Written after a successful ingest so the manifest describes what is
    actually in the index, not what was attempted.
    """
    data = load_manifest()
    entry = data["documents"].get(os.path.basename(filename), {})
    entry["ingested_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if content is not None:
        entry["sha256"] = sha256(content)
        entry["bytes"] = len(content)
    if chunks is not None:
        entry["chunks"] = chunks
    if pages is not None:
        entry["pages"] = pages
    if settings:
        entry["settings"] = settings
    data["documents"][os.path.basename(filename)] = entry

    try:
        os.makedirs(store_dir(), exist_ok=True)
        tmp = _manifest_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, _manifest_path())
    except Exception as exc:
        # Never fail an ingest because bookkeeping failed.
        logger.warning(f"Could not write manifest ({exc})")


def current_settings() -> dict:
    """The geometry a document ingested right now would get.

    Read lazily from ingest to avoid a circular import at module load.
    """
    try:
        import ingest
        chunk_size, overlap = ingest.CHUNK_SIZE, ingest.CHUNK_OVERLAP
    except Exception:
        chunk_size, overlap = None, None
    try:
        import parsing
        parser = "pdfplumber" if parsing.pdfplumber is not None else "pypdf"
    except Exception:
        parser = None
    return {
        "chunk_size": chunk_size,
        "chunk_overlap": overlap,
        "parser": parser,
        "embedder": "all-MiniLM-L6-v2",
    }


def stale_documents() -> list[dict]:
    """Documents whose recorded ingest settings differ from current settings.

    This is the "is my index actually built the way I think it is?" check.
    A chunk-size change only applies to documents ingested afterwards, so
    without this the index quietly holds a mix of geometries.
    """
    now = current_settings()
    out = []
    for name, entry in sorted(load_manifest()["documents"].items()):
        was = entry.get("settings") or {}
        diff = {k: (was.get(k), now.get(k)) for k in now
                if was.get(k) is not None and was.get(k) != now.get(k)}
        if diff or not was:
            out.append({"source": name, "recorded": was, "current": now,
                        "differs": diff, "unknown": not was})
    return out


def inventory() -> list[dict]:
    """Every retained original, with where it is and what is known about it."""
    manifest = load_manifest()["documents"]
    seen: dict[str, dict] = {}
    for d in read_dirs():
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name == MANIFEST_NAME or name in seen:
                continue
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            seen[name] = {
                "source": name,
                "path": p,
                "dir": d,
                "legacy_location": os.path.abspath(d) == os.path.abspath(LEGACY_DIR),
                "bytes": os.path.getsize(p),
                "manifest": manifest.get(name),
            }
    return list(seen.values())


def migrate_legacy(dry_run: bool = False) -> list[tuple[str, str]]:
    """Copy documents out of the legacy Docker path into the real store.

    Copy, never move: the legacy directory may be a live Docker volume, and
    losing the only copy while "tidying up" is exactly the failure mode this
    module exists to prevent.
    """
    legacy = os.path.abspath(LEGACY_DIR)
    dest = store_dir()
    if not os.path.isdir(legacy) or os.path.abspath(dest) == legacy:
        return []

    moved = []
    for name in sorted(os.listdir(legacy)):
        src = os.path.join(legacy, name)
        if not os.path.isfile(src) or name == MANIFEST_NAME:
            continue
        dst = os.path.join(dest, name)
        if os.path.isfile(dst):
            continue
        moved.append((src, dst))
        if not dry_run:
            os.makedirs(dest, exist_ok=True)
            shutil.copy2(src, dst)
    return moved
