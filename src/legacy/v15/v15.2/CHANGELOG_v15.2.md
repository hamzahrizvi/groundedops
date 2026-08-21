# GroundedOps v15.2 — three surfaces become two

The recurring complaint behind this release was that the same bug had to be
fixed three times and the widget — the only surface customers see — was
consistently last to get it. So one of the three is gone.

## The React SPA is retired

It was a third chat surface duplicating the admin console's test chat and the
widget. Internal testing now happens in the admin console; the widget is the
customer surface.

**The widget was served *through* that app**, which is why this could not be a
one-step deletion: it lived in `frontend/public/widget/`, vite copied it into
`frontend/dist/`, and `dist` was mounted at `/`. A customer-facing asset
depended on an internal dev app being built. Deleting the app first would have
404'd every embedded `<script src=".../widget/groundedops-widget.js">` in the
wild.

So the widget moved to `src/widget/` and is served by **exact-path** routes —
deliberately not a `/widget/{filename}` parameter, which would have shadowed
the existing `/widget/config` and `/widget/lead` endpoints. The public URL is
unchanged, now with `Cache-Control: no-store` so a cached stale copy cannot
masquerade as a broken deploy.

`/` now 307-redirects to `/admin`. Node and npm are no longer needed anywhere:
there is no build step in the project at all.

**What was lost, deliberately:** the generation-mode / provider toggle had no
other UI. `POST /settings/mode` and `POST /settings/online_provider` still
work but are curl-only until the admin console gains equivalents. Saved chat
history (browser localStorage) is gone.

## Answers are readable in the widget

`txt.textContent = msg.text` was why a pinout arrived as one unreadable run of
`| 1 | Vend 1 | Output | ...`. The widget now renders markdown — pipe tables
with header rows, lists, bold, code — via an ES5, dependency-free renderer
matching the admin console's: escape everything first, then re-introduce only
those constructs. Models sometimes run a table onto its heading line
(`**Pulse:** | Pin | Name |`), so pipe runs are split before parsing.

User messages stay `textContent` and are never parsed as markup.

## Asking "which product?" when the visitor already said it

`"what is the pinout for nv9 usb?"` named the product and was still asked
which product was meant — the check only looked at `payload.product`, never at
the question wording. Worse, the reply ("nv9 usb") is chat text rather than a
scoped request, so the next turn asked again, forever.

The product is now resolved from the question text first, and retrieval re-runs
scoped. `"nv9 usb"`, `"nv9usb"` and `"NV9USB+"` all collapse to the same
product; a bare `"nv9"` stays ambiguous between NV9USB+ and NV9 Spectral and
is still asked about, correctly, with a longest-match rule so `nv9usb` is not
swallowed by `nv9`.

Product choices are also **clickable** and show display names. They previously
showed raw catalogue keys (`nv9_spectral`, `nv9usb`) because the lookup called
a non-existent `catalog.load_catalog()` and a bare `except` swallowed the
`AttributeError`.

## Refusals offer a person

`offer_support` was missing in two places, not one: `/widget/ask` builds its own
response dict and never passed it through, and — more importantly — the
**early retrieval-gate return** in `/query` never set it. That is the commonest
refusal path, rejecting before the model is called, so exactly the refusals
most needing a human offer had no way to signal it. Only set on a genuine
rejection; a clarify is a working conversation, not a dead end.

`product_options` is deliberately **not** passed to the widget: it requires a
product before it will ask anything, so the backend can never populate them
there.

## Security findings from CodeQL

- **Clear-text logging of a credential, twice, and SHA256 on a credential.**
  The key-origin diagnostic logged an 8-char SHA256 prefix of each API key to
  tell two apart. CodeQL was right on both counts. The diagnostic question is
  only "is this client using the configured key or its own?", which a boolean
  answers — the digest added nothing.
- **Uncontrolled data in a path expression** (`docstore.find`). `basename()`
  alone is platform-dependent: on POSIX a backslash is a legal filename
  character, so a backslash-separated traversal survives it. Separators are
  now normalised first and the resolved path is confirmed to sit inside a
  known store directory. Verified: POSIX and Windows-style traversals both
  return `None`; real lookups unaffected.
- **Insecure randomness.** `Math.random()` generated the chat session id and
  element ids in the admin console. Replaced with a shared `randId()` built on
  `crypto.getRandomValues` — costs nothing, and a scanner cannot distinguish a
  "harmless" id from a security token any more than the next person to reuse
  the helper can.

Findings in `src/legacy/**` are **not** fixed: those directories are archives
of what shipped in each release. Editing them would make them lie about
history, which is their only purpose. They need dismissing at the
code-scanning level, or excluding via a CodeQL path filter.

## CI was green for the wrong reason

`python-multipart` was never in `requirements.txt`. FastAPI needs it for the
`UploadFile` document-upload endpoint, and CI could not even import
`test_new_routes.py`. That went unnoticed because `run_tests.py` used to
swallow load failures without affecting its exit code — the v15.0 fix to that
is what surfaced this, which is the fix doing its job.

## Verification

126/126 unit tests, exit 0 (1 skipped: `test_ui` needs playwright). Widget and
admin console JS both syntax-check. `run.ps1` and `install.ps1` parse. All five
widget/root routes register and the widget directory resolves. Path-traversal
attempts against `docstore.find` verified blocked.

## Still open

- **The grounding threshold (0.55) has never been swept**; the false-refusal
  rate is unknown. Needs a working provider key.
- **~50s of a 113s answered query is unattributed** — needs per-stage timing.
- `PROJECT_MAP.md` and `README.md` still describe the retired frontend,
  including a `FRONTEND_DIST` env var the code no longer reads.
- `documents/` is untracked and is the only copy of most of the corpus.
