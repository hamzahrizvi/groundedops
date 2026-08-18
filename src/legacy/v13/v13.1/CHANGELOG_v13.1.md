# GroundedOps v13.1 — v13 rewired onto the real v12 baseline

v13.0 was a raw file drop applied over a stale base, applied before this
release, and silently reverted committed v12 features instead of adding to
them (see `PROJECT_MAP.md`'s former BROKEN STATE section for the full
account). v13.1 is that drop corrected: the same v13.0 intent, actually
merged onto `legacy/v12/v12.3_redesign/main.py` (2077 ln) and HEAD's
`faq_store.py` (562 ln) instead of replacing them.

## What deploys

| File | Where it goes | Notes |
|---|---|---|
| `main.py` | replaces `main.py` | restored v12.3 base + v13 grafts, see below |
| `faq_store.py` | replaces `faq_store.py` | restored HEAD + the gap log folded in |
| `admin.html` | replaces `admin.html` | retargeted to the real v12.3 route shapes |
| `_harness.py` | replaces `_harness.py` | test-only; store-path isolation fixes |
| `test_new_routes.py` | replaces `tests/test_new_routes.py` | trimmed to what's actually wired (see Known gaps) |

Not shipped here because they didn't change: `gaps.py` (**deleted** — see
below), `widget_config.py` (byte-identical to v13.0's copy — only its
*wiring* changed, in `main.py`), `test_ui.py` (unchanged), `styles.css`
(unchanged content, just relocated — see below).

## What v13.0 actually got wrong

`DEPLOY_v13.md` claimed most of its console-facing routes were new
("dead buttons" with no backend). They weren't — `POST /faq`,
`PATCH /faq/{id}` (question+answer), `DELETE /faq`, `GET /faq/gaps`, and
`POST /admin/faq/autogenerate` all already existed in v12.3. Dropping
v13.0's files in wholesale over a base that predated all of it reverted:
`CORSMiddleware`, the `/api` prefix-strip middleware, `GET /source_file`,
the `_SPAStatic` mount of `frontend/dist`, and `faq_store`'s entire
v11.3.3 "ask, don't guess" candidate-ranking system
(`suggest_candidates`, `record_gap`, `list_gaps`, `get_by_id`,
`delete_all`, the semantic score cache).

## What's genuinely new in this release

- **Gap tracking, folded into one store.** `gaps.py`'s unique signal — a
  genuine retrieval/grounding miss on the main `/query` path, as opposed
  to a rejected FAQ suggestion — is now inside `faq_store.record_gap`
  (merge-on-write dedup by normalized question, a `reason` field,
  `resolve_gap_matching` for auto-resolve-on-answer, `dismiss_gap` for
  spam). `gaps.py` and `gaps_store.json` are gone; `GET /faq/gaps` is one
  store, one shape.
- **A working autogenerate parser.** v12.3's `admin_faq_autogenerate`
  expected the model to return bare `QUESTION :: ANSWER` lines with zero
  preamble — broke on any fencing or "Sure, here you go:" prefix. Replaced
  with v13.0's JSON-array prompt + `_parse_qa_json`, which locates the
  array inside prose/markdown fences instead of requiring the whole
  response to be exactly that array.
- **`original_question` dedup.** Editing a drafted FAQ's wording used to
  make it look brand-new to a later re-draft, adding a near-duplicate.
  `update_entry` now remembers the pre-edit wording once, and
  `merge_questions` checks both.
- **`widget_config.py` wired for real.** Branding + lead capture
  (`GET /widget/config`, `POST /widget/lead`, `GET/PUT
  /admin/widget/config`, `GET /admin/leads`) is imported and live in
  `main.py` — the module shipped with v13.0 but was never imported.
- **`admin.html` retargeted**, not replaced: `GET /faq/gaps`'s real shape
  (`{gaps: [...], stats: {...}}`, no separate `product`/`category`
  split — a single `scope` field, matching how `_faq_scope` already
  worked everywhere else in `main.py`), plus a `/nocturne/styles.css`
  mount (nothing served that path before — the console rendered
  unstyled).

## Known gaps in this release

- `test_new_routes.py` does not cover the widget-config/leads routes even
  though they're now wired — it was trimmed when they were still
  deliberately unwired, and the wiring landed in a later pass without the
  tests catching up. Worth a follow-up.
- `run_tests.py` silently excludes any `test_*.py` file that fails to
  *import* rather than counting it as a failure — unrelated to this
  release, but worth knowing before trusting a green run_tests.py exit
  code for anything beyond what's listed above.

## Verified

`python run_tests.py` — 20/24 passed (4 pre-existing failures in
`test_llm.py`, unmodified, unrelated). `tests/test_new_routes.py` — all
checks pass. `main.py` imports cleanly against the real (non-stubbed)
codebase.
