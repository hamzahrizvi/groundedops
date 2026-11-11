# Working rules for Claude sessions

## One session edits code at a time (P1)

Only one Claude session changes files in this working tree at a time. Any
other session that needs to change code works in its own git worktree or
branch (`.claude/worktrees/...`) and merges back when done.

Two sessions sharing this tree broke the repository twice: one staged or
committed the other's half-finished edits. Before starting work, check that
no other session is running here. If `git status` shows changes you did not
make, stop and ask instead of staging them.

Other traps when committing in this repo:
- Stage whole files or real hunks; never `git apply --unidiff-zero` or `-U0`
  patches.
- `py_compile` every staged `.py` file before committing.
- Don't use `git commit -- <path>`: it re-adds files removed with
  `git rm --cached`.

## Where the plan lives

`PENDING.md` is the single source of what to do next. On `main` it is the
general product's plan; the Innovative Technology product line's plan is on
`experimental/v17.3`.

`PENDING.md` is also the product owner's diary: the PO edits it by hand and
works through it too. Uncommitted changes to it may be the PO's, so keep
them: edit around them, never revert them, and ask before committing them.
