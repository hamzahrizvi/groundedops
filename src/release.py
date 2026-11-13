#!/usr/bin/env python3
"""release.py — bump VERSION and draft the release notes from git.

WHY THIS EXISTS

Every release is a git tag (v16.0, v17.0, ...). Until November 2026 this script also copied each
release's changed files into src/legacy/; that folder is gone (D-20261112-20), because git history and
the tags already hold every release. What is left is the mechanical half: the version bump, the
changed-file table and the commit list are derived from git so they cannot drift from what actually
changed. You still write the prose.

VERSION lives at the repo root, as its own file. Deliberately NOT reusing
INGEST_VERSION in main.py: that value tells the app whether the index needs
rebuilding, so bumping it every release would nag users into pointless
re-ingests.

USAGE

  python release.py --minor              # 15.0 -> 15.1
  python release.py --major              # 15.1 -> 16.0
  python release.py --minor --dry-run    # show what would happen
  python release.py --minor --base main  # scope changes against another base

It bumps VERSION, stages it, and prints the release notes to paste
into the tag message:

  python release.py --minor
  git commit -m "release: v15.1 — <what changed>"
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VERSION_FILE = os.path.join(ROOT, "VERSION")

# Never list these: runtime state, secrets, or the documents.
SKIP_PREFIXES = ("documents/", "corpus/")
SKIP_NAMES = {"logs.jsonl", "handover.txt", "conversations.db", "quota.db",
              "VERSION",
              # Deliberately untracked: it enumerates this system's weaknesses
              # (default admin password, unfixed history) and the repo is public.
              "HANDOFF.md"}


def sh(*args: str) -> str:
    # encoding="utf-8" explicitly - text=True alone decodes with the
    # platform's default locale encoding, which is cp1252 on Windows.
    # git log's output is UTF-8 (commit messages use real em dashes), so
    # that mismatch corrupted every em dash into mojibake in the generated
    # changelog - silently, since nothing here raises on it.
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def read_version() -> tuple[int, int]:
    try:
        raw = open(VERSION_FILE, encoding="utf-8").read().strip().lstrip("v")
        major, minor = raw.split(".")[:2]
        return int(major), int(minor)
    except (OSError, ValueError) as e:
        raise SystemExit(f"Cannot read {VERSION_FILE} ({e}): write the current "
                         "version, e.g. 17.3, into it.")


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
        f"| `{f}` | replaces `{f}` | |" for f in files)
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

    old_major, old_minor = read_version()
    if a.major:
        major, minor = old_major + 1, 0
    else:
        major, minor = old_major, old_minor + 1
    version = f"{major}.{minor}"

    files = changed_files(a.base)
    if not files:
        print(f"No changed files against {a.base} — nothing to release.")
        return 1

    print(f"version    : {old_major}.{old_minor} -> {version}")
    print(f"files      : {len(files)}")
    for f in files:
        print(f"    {f}")

    if sh("git", "rev-parse", "--verify", "--quiet", f"refs/tags/v{version}") and not a.dry_run:
        print(f"\nREFUSING: tag v{version} already exists.")
        return 1

    if a.dry_run:
        print("\n[dry run] nothing written")
        return 0

    with open(VERSION_FILE, "w", encoding="utf-8") as fh:
        fh.write(version + "\n")
    subprocess.run(["git", "add", "VERSION"], cwd=ROOT)

    print("\n" + changelog(version, files, a.base))
    print(f"VERSION is now {version} and staged. Put the prose above in the tag message.")
    print(f"Next:  git commit -m \"release: v{version} - <what changed>\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
