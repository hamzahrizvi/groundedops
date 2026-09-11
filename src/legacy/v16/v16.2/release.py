#!/usr/bin/env python3
"""release.py — bump the version and snapshot the release into src/legacy/.

WHY THIS EXISTS

The legacy drops (v13.0, v13.1, v14.0, v15.0) were assembled by hand: work out
which files changed, copy them flat into a new folder, write a CHANGELOG with a
"what deploys" table, zip them. Done manually it gets skipped under pressure,
and a drop that misses a file is worse than no drop — it looks complete.

This does the mechanical half. You still write the prose; the file list, the
table, the version bump and the zip are derived from git so they cannot drift
from what actually changed.

VERSION lives at the repo root, as its own file. Deliberately NOT reusing
INGEST_VERSION in main.py: that value tells the app whether the index needs
rebuilding, so bumping it every release would nag users into pointless
re-ingests.

USAGE

  python release.py --minor              # 15.0 -> 15.1
  python release.py --major              # 15.1 -> 16.0
  python release.py --minor --dry-run    # show what would happen
  python release.py --minor --base main  # scope changes against another base

It stages everything it writes, so the release is part of your next commit:

  python release.py --minor
  git commit -m "release: v15.1 — <what changed>"
"""
import argparse
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VERSION_FILE = os.path.join(ROOT, "VERSION")
LEGACY = os.path.join(HERE, "legacy")

# Never snapshot these: runtime state, secrets, or the drops themselves.
SKIP_PREFIXES = ("src/legacy/", "documents/", "corpus/")
SKIP_NAMES = {"logs.jsonl", "handover.txt", "conversations.db", "quota.db",
              "VERSION", "before.json", "after.json", "after_bge.json",
              "minilm18.json",
              # Deliberately untracked: it enumerates this system's weaknesses
              # (default admin password, unfixed history) and the repo is public.
              "HANDOFF_console_ux.md"}


def sh(*args: str) -> str:
    # encoding="utf-8" explicitly - text=True alone decodes with the
    # platform's default locale encoding, which is cp1252 on Windows.
    # git log's output is UTF-8 (commit messages use real em dashes), so
    # that mismatch corrupted every em dash into mojibake in the generated
    # changelog - silently, since nothing here raises on it.
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def read_version() -> tuple[int, int]:
    if os.path.isfile(VERSION_FILE):
        raw = open(VERSION_FILE, encoding="utf-8").read().strip().lstrip("v")
        try:
            major, minor = raw.split(".")[:2]
            return int(major), int(minor)
        except Exception:
            pass
    # No VERSION file yet: infer from the highest legacy drop so the first run
    # continues the existing sequence instead of restarting at 1.0.
    best = (0, 0)
    if os.path.isdir(LEGACY):
        for name in os.listdir(LEGACY):
            if not name.startswith("v"):
                continue
            for sub in os.listdir(os.path.join(LEGACY, name)):
                try:
                    mj, mn = sub.lstrip("v").split(".")[:2]
                    best = max(best, (int(mj), int(mn)))
                except Exception:
                    continue
    return best


def changed_files(base: str) -> list[str]:
    """Files in this release: committed since `base`, plus anything pending.

    Union of the two, because release.py is meant to run just before the
    commit that finishes the release — so the newest work is still unstaged.
    """
    out: set[str] = set()
    if sh("git", "rev-parse", "--verify", "--quiet", base):
        for line in sh("git", "diff", "--name-only", f"{base}...HEAD").splitlines():
            out.add(line.strip())
    for line in sh("git", "status", "--porcelain").splitlines():
        path = line[3:].strip()
        if path:
            out.add(path)

    keep = []
    for f in sorted(out):
        if not f or f.endswith("/"):
            continue
        if any(f.startswith(p) for p in SKIP_PREFIXES):
            continue
        if os.path.basename(f) in SKIP_NAMES:
            continue
        if not os.path.isfile(os.path.join(ROOT, f)):
            continue          # deleted; nothing to snapshot
        keep.append(f)
    return keep


def changelog(version: str, files: list[str], base: str) -> str:
    rows = "\n".join(
        f"| `{os.path.basename(f)}` | replaces `{f}` | |" for f in files)
    log = sh("git", "log", "--oneline", f"{base}..HEAD") or "(nothing committed yet)"
    return f"""# GroundedOps v{version}

<!-- Write the prose here: what changed and WHY. The table below is generated
     from git, so it lists what actually changed rather than what you meant to
     change. Fill in the Notes column. -->

## What deploys

| File | Where it goes | Notes |
|---|---|---|
{rows}

## Commits in this release

```
{log}
```

## Verification

<!-- e.g. 126/126 unit tests, exit 0. eval_retrieval.py deltas. -->
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--major", action="store_true")
    g.add_argument("--minor", action="store_true")
    ap.add_argument("--base", default="origin/main",
                    help="what to diff against (default origin/main)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    major, minor = read_version()
    if a.major:
        major, minor = major + 1, 0
    else:
        minor += 1
    version = f"{major}.{minor}"

    files = changed_files(a.base)
    if not files:
        print(f"No changed files against {a.base} — nothing to release.")
        return 1

    dest = os.path.join(LEGACY, f"v{major}", f"v{version}")
    print(f"version    : {read_version()[0]}.{read_version()[1]} -> {version}")
    print(f"snapshot   : {os.path.relpath(dest, ROOT)}")
    print(f"files      : {len(files)}")
    for f in files:
        print(f"    {f}")

    # Flat layout matches the existing drops, so two different paths sharing a
    # basename would silently overwrite one another.
    seen: dict[str, str] = {}
    clashes = []
    for f in files:
        b = os.path.basename(f)
        if b in seen:
            clashes.append((seen[b], f))
        seen[b] = f
    if clashes:
        print("\nREFUSING: two files share a basename and the drop is flat:")
        for x, y in clashes:
            print(f"    {x}  <->  {y}")
        print("Rename one, or snapshot them into subfolders by hand.")
        return 1

    if os.path.isdir(dest) and not a.dry_run:
        print(f"\nREFUSING: {os.path.relpath(dest, ROOT)} already exists.")
        return 1

    if a.dry_run:
        print("\n[dry run] nothing written")
        return 0

    os.makedirs(dest, exist_ok=True)
    for f in files:
        shutil.copy2(os.path.join(ROOT, f), os.path.join(dest, os.path.basename(f)))

    cl = os.path.join(dest, f"CHANGELOG_v{version}.md")
    with open(cl, "w", encoding="utf-8") as fh:
        fh.write(changelog(version, files, a.base))

    zpath = os.path.join(dest, "files.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(dest)):
            if name != "files.zip":
                z.write(os.path.join(dest, name), name)

    with open(VERSION_FILE, "w", encoding="utf-8") as fh:
        fh.write(version + "\n")

    subprocess.run(["git", "add", "VERSION",
                    os.path.relpath(dest, ROOT).replace(os.sep, "/")], cwd=ROOT)

    print(f"\nwrote {len(files)} file(s) + CHANGELOG + files.zip, and staged them.")
    print(f"Next:  write the prose in {os.path.relpath(cl, ROOT)}")
    print(f"       git commit -m \"release: v{version} - <what changed>\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
