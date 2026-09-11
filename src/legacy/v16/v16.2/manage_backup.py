#!/usr/bin/env python3
"""Back up and restore from the command line.

The console can do both, except on the day it will not start — a bad
restore, a corrupt store, a half-finished upgrade. That is exactly when a
backup matters, so it must not be the console's exclusive property.

    python manage_backup.py export                     # prompts for a passphrase
    python manage_backup.py export --out /mnt/nas/go.zip
    python manage_backup.py export --no-documents      # settings + index only
    python manage_backup.py inspect go.gobk            # what is in it
    python manage_backup.py restore go.gobk            # accounts left alone
    python manage_backup.py restore go.gobk --accounts # and accounts too

Archives are encrypted. BACKUP_PASSPHRASE is honoured for unattended runs
(cron); otherwise the passphrase is prompted for, never taken as an argument
— an argument lands in shell history and in the process list.

A CLI export is always a FULL one (accounts included), because whoever can
run this already has the files on disk — the level split in the console
exists to stop `support` pulling password hashes through the web, and it
would be theatre here.

Run from the `src` directory, or with the same store paths in the
environment, so it reads the same install the server does.
"""
import argparse
import getpass
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# backup.py resolves store paths from the environment at call time, so .env
# has to be loaded first — same ordering constraint main.py documents.
_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env):
    for _line in open(_env, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

import backup  # noqa: E402


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def _ask_passphrase(confirm: bool) -> str:
    """Prompted, never a command-line argument: an argument lands in shell
    history and in the process list. BACKUP_PASSPHRASE is honoured for
    unattended runs (cron), which is the one case where prompting cannot
    work."""
    env = (os.getenv("BACKUP_PASSPHRASE") or "").strip()
    if env:
        return env
    first = getpass.getpass("Backup passphrase: ")
    if confirm and getpass.getpass("Passphrase again: ") != first:
        sys.exit("Those did not match.")
    try:
        backup.check_passphrase(first)
    except backup.BackupError as e:
        sys.exit(str(e))
    return first


def cmd_export(args):
    passphrase = None
    if args.plain:
        print("WARNING: --plain writes an UNENCRYPTED archive containing "
              "password hashes\n         and customer contact details.")
    else:
        passphrase = _ask_passphrase(confirm=True)
    data, manifest = backup.create_archive(
        created_by=f"cli ({os.getenv('USER') or os.getenv('USERNAME') or 'unknown'})",
        level="root",
        include_documents=not args.no_documents,
        include_index=not args.no_index,
        passphrase=passphrase)
    out = args.out or backup.suggested_filename(manifest)
    with open(out, "wb") as fh:
        fh.write(data)
    print(f"Wrote {out} ({_human(len(data))})")
    for k, v in manifest["counts"].items():
        print(f"  {k}: {v}")
    if manifest.get("encrypted"):
        print("\nEncrypted. Without the passphrase this file is unreadable, "
              "including\nby this tool — there is no recovery path. Store the "
              "passphrase with your\nother secrets, not beside the backup.")
    else:
        print("\nNOT encrypted. Contains password hashes and customer contact "
              "details.\nStore it somewhere access-controlled.")


def cmd_inspect(args):
    with open(args.archive, "rb") as fh:
        data = fh.read()
    try:
        if backup.is_encrypted(data):
            # The header alone identifies a file, which is what you want when
            # working out which of six backups to restore.
            print(json.dumps(backup.read_envelope(data), indent=2))
            if not args.full:
                print("\n(header only — pass --full and the passphrase to read "
                      "the manifest inside)")
                return
            m = backup.read_manifest(data, _ask_passphrase(confirm=False))
        else:
            m = backup.read_manifest(data)
    except backup.BackupError as e:
        sys.exit(str(e))
    print(json.dumps(m, indent=2))


def cmd_restore(args):
    with open(args.archive, "rb") as fh:
        data = fh.read()
    passphrase = _ask_passphrase(confirm=False) if backup.is_encrypted(data) else None
    try:
        m = backup.read_manifest(data, passphrase)
    except backup.BackupError as e:
        sys.exit(str(e))

    print(f"Archive made {m.get('created_at')} by {m.get('created_by') or 'unknown'}")
    print(f"  {json.dumps(m.get('counts', {}))}")
    if args.accounts and not m.get("includes", {}).get("accounts"):
        sys.exit("--accounts was given but this archive has no accounts in it.")
    if args.accounts:
        print("\n  --accounts: every account will be REPLACED by the ones in this")
        print("  archive. If yours is not among them you will not be able to sign")
        print("  in; recover with manage_accounts.py.")
    if not args.yes:
        if input("\nOverwrite this install from that archive? [y/N] ").strip().lower() != "y":
            sys.exit("Left alone.")

    result = backup.restore_archive(
        data,
        restore_accounts=args.accounts,
        restore_documents=not args.no_documents,
        restore_index=not args.no_index,
        restore_conversations=not args.no_conversations,
        actor="cli",
        passphrase=passphrase)

    snap = args.snapshot_out or (
        "before-restore.gobk" if passphrase else "before-restore.zip")
    with open(snap, "wb") as fh:
        fh.write(result["safety_snapshot"])
    print(f"\nPrevious state saved to {snap} — restore that to undo this.")
    print(f"Restored: {json.dumps(result['restored'])}")
    if result["skipped"]:
        print(f"Skipped: {', '.join(result['skipped'])}")
    if result["restart_required"]:
        print("\nRestart the server: the search index was replaced on disk and "
              "the running process still holds the old one open.")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("export", help="write an archive")
    p.add_argument("--out", help="destination path")
    p.add_argument("--no-documents", action="store_true",
                   help="settings and index only, no source files")
    p.add_argument("--no-index", action="store_true",
                   help="skip the search index (a restore then needs reindex.py)")
    p.add_argument("--plain", action="store_true",
                   help="write an UNENCRYPTED archive (you already have the "
                        "files on disk; this is for piping into your own "
                        "encryption, not for casual use)")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("inspect", help="print an archive's manifest")
    p.add_argument("archive")
    p.add_argument("--full", action="store_true",
                   help="decrypt and print the inner manifest too")
    p.set_defaults(fn=cmd_inspect)

    p = sub.add_parser("restore", help="overwrite this install from an archive")
    p.add_argument("archive")
    p.add_argument("--accounts", action="store_true",
                   help="also replace accounts and limits")
    p.add_argument("--no-documents", action="store_true")
    p.add_argument("--no-index", action="store_true")
    p.add_argument("--no-conversations", action="store_true")
    p.add_argument("--snapshot-out", help="where to write the pre-restore snapshot")
    p.add_argument("-y", "--yes", action="store_true", help="skip the prompt")
    p.set_defaults(fn=cmd_restore)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
