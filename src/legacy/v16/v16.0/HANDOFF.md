# Handoff — GroundedOps, end of v15.2

Continuation notes for a fresh chat. Repo:
`C:\Users\hrizvi\Downloads\Git\groundedops`, branch `fix/wire-faq-choices`,
PR **#8** → `main` (https://github.com/hamzahrizvi/groundedops/pull/8).

`PENDING.md` is the durable open-items list and is more current than this
file for anything not about CI. Read that too.

---

## Where CI stands

| Check | State |
|---|---|
| `unit` | **pass** (91/91 on CI's Python 3.11) |
| `build` | **pass** (backend image only) |
| `eval-selfcheck` | **pass** |
| `Analyze (actions / javascript / python)` | **pass** |
| `CodeQL` | **fails** — see below |

**The CodeQL check is the only thing left red, and it is not a code problem.**
It reports "N new alerts in code changed by this pull request". Those alerts
are almost entirely inside `src/legacy/v15/v15.2/` — the release snapshot this
PR adds. Each release drop copies `main.py`, `admin.html` etc. into a new
folder, and CodeQL counts those copies as newly-introduced vulnerable code.

Confirmed by querying alerts three ways: **0 open alerts on the branch**, but
16 of 17 PR-scoped alerts are under `legacy/`. The one genuine finding
(`docstore.py` py/path-injection) was fixed in `9a37d9e`.

**This will recur on every single release**, because `release.py` always adds
a new snapshot directory. Two ways out, both needing a decision:

1. **Dismiss the legacy alerts** (`PATCH /repos/.../code-scanning/alerts/{n}`
   with `dismissed_reason: "won't fix"`). Fast, but it is dismissing security
   alerts, so it should be a deliberate choice, not something automated away.
2. **Exclude `src/legacy/**` via a CodeQL `paths-ignore` filter.** The correct
   permanent fix, but it requires **advanced setup** — default setup cannot
   read a config file. Someone must disable default setup in repo settings
   first, then a `.github/workflows/codeql.yml` with a config can take over.

A third option worth considering: stop committing release snapshots to the
repo at all (they duplicate what git history already holds) — but that is a
bigger change to how this project has always worked.

## What landed in v15.2

Commits `4986c2c` → `9a37d9e`. Highlights, with the reasoning that matters:

- **React SPA retired.** It was a third chat surface duplicating the admin
  console's test chat and the widget, so every fix had to be made three times
  and the widget was always last. The widget was served *through* it
  (`public/widget` → vite → `dist` → mounted at `/`), so it moved to
  `src/widget/` and is served by **exact-path** routes — not
  `/widget/{filename}`, which would shadow `/widget/config` and
  `/widget/lead`. Public URL unchanged.
- **No build step remains anywhere.** Node/npm not required. `run.ps1`,
  `install.ps1`, `docker-compose.yml` and `ci.yml` all updated;
  `Dockerfile.frontend` deleted; the compose `widget` service repointed (its
  `widget-nginx.conf` had been deleted with the frontend and was recovered
  from git history).
- **Widget renders markdown** (tables, lists, bold) — ES5, no dependency.
  `txt.textContent` was why a pinout arrived as one unreadable run.
- **Product disambiguation resolves from the question wording.** "what is the
  pinout for nv9 usb?" named the product and was still asked which product;
  the reply is chat text, not a scoped request, so it looped forever.
  Choices are clickable and show display names (they showed raw keys because
  the lookup called a non-existent `catalog.load_catalog()` and a bare
  `except` swallowed the `AttributeError`).
- **`offer_support` on refusals**, fixed in two places: `/widget/ask` builds
  its own response dict, and the early retrieval-gate return in `/query`
  never set it — the commonest refusal path.
- **Security:** dropped a SHA256 key fingerprint from logs, hardened
  `docstore.find` to enumerate rather than construct paths, `Math.random` →
  `crypto.getRandomValues`, and added `permissions: contents: read` to CI.

## Traps discovered the hard way — do not re-learn these

- **CI runs Python 3.11; the local venv is 3.14.** A `test_new_routes.py`
  failure ("No response returned.") reproduced *only* on 3.11 and *only* when
  another test file had run first. Two CI cycles were wasted guessing before
  reproducing it locally. There is a working 3.11 at
  `C:\Users\hrizvi\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe`
  — **use it to reproduce CI failures instead of pushing.**
- **Cause of that one:** `tests/_harness.py` rewrites `sys.modules`
  wholesale. A dict snapshot cannot undo that. Files importing `_harness` now
  run in their own subprocess (`run_tests.py`), with `PYTHONPATH` passed
  because a subprocess puts `sys.path[0]` at the *script's* directory.
- **The `unit` CI job installs an explicit package list**, not
  `-r requirements.txt`. Adding a dep to requirements alone will not fix CI.
- **Bash heredocs in this environment mangle backslashes.** Python patch
  scripts containing regex or `\n` inside strings get corrupted. Write the
  payload with the Write tool and splice it, or use the Edit tool.
- **`git rm -r src/frontend` needed `-f`** and left an empty locked dir; that
  is cosmetic.

## Immediate next steps

1. **Decide the CodeQL question above.** It is the only blocker to a green PR.
2. **Restart the backend** and verify the v15.2 behaviour end to end — the
   widget markdown, product buttons, and support offer have *not* been
   confirmed against a running server by anyone yet.
3. `PROJECT_MAP.md` and `README.md` still describe the retired frontend,
   including a `FRONTEND_DIST` env var the code no longer reads.
4. The two genuinely-blocking items in `PENDING.md` are unchanged: **no
   working provider key** in `src/.env` (401), and the **grounding threshold
   (0.55) has never been swept**, so the false-refusal rate is unknown.

## Local state at handoff

- Uncommitted: `src/logs.jsonl` (runtime churn — leave it).
- Untracked: `documents/` (the document store, and the only copy of most
  source PDFs — decide whether it belongs in git), `HANDOFF_console_ux.md`
  (deliberately untracked; it lists this system's weaknesses and the repo is
  public), and this file.
- `gh` CLI is installed at `C:\Program Files\GitHub CLI\gh.exe` and
  authenticated as `hamzahrizvi`. It is **not on the default PATH** — prefix
  with `export PATH="/c/Program Files/GitHub CLI:$PATH"`.
- A Monday 08:00 UTC status routine exists
  (`trig_01FDVe9tGVEGhLfdgk8uyVjd`) but has **no repo access** — the
  claude.ai GitHub connection kept returning 401, so it currently just
  reports that it is not wired up rather than inventing a status.
