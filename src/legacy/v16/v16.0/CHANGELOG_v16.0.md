# GroundedOps v16.0

Major version, not minor, because `ADMIN_PASSWORD` is gone entirely — anyone
with it set in `.env` needs to create a real account instead — and several
other structural changes land alongside it: real accounts with levels,
encrypted backup/restore, a runtime-editable access policy, and the widget
and admin console's test chat becoming one implementation instead of two.

v15.1 and v15.2 shipped as `release.py` snapshots but were never tagged or
given a GitHub release — tagging had stalled at v11.3.3. This release
bundles everything since v15.2 so that gap doesn't widen further, and
resumes tagging from here.

## Accounts replace the shared admin password

`ADMIN_PASSWORD` — one shared password, no per-user identity — is gone.
Three levels now: `root` (everything, including account management),
`support` (everything except account management), `basic` (test chat
only). Sessions are HMAC tokens carrying a per-account `token_epoch`, so
disabling an account or changing its password kills its live sessions
immediately. Passwords use `hashlib.scrypt` (stdlib, no new dependency).
`manage_accounts.py` is the recovery path if the console is unreachable.

**Migration**: on first run with no `accounts.json`, the console offers to
create the first (root) account. Do this before the install is reachable by
anyone else — first-run bootstrap is necessarily unauthenticated.

## Backup and restore, encrypted

Answers the long-standing "`accounts.json` has no backup and is not
regenerable" item. An archive (`.gobk`) carries the stores, the FAQ, the
catalogue, the widget config, customer enquiries, `conversations.db`, and
the Chroma index — so a restore needs no re-ingesting. Sealed in an
AES-256-GCM envelope keyed from a passphrase (scrypt-derived); the header
stays readable without it so a file can be identified, and is authenticated
as GCM associated data so it cannot be edited undetected. There is no way
to make a file only this application can open — that would need a key
baked into a public repository — so the passphrase is the real protection,
and losing it loses the archive with no recovery path, by design.

Export is gated at `support`; import is `root` only, since it overwrites.
Archive contents scale with level: only a root export carries
`accounts.json` and `policy.json`. `manage_backup.py` does both from the
CLI for the day the console will not start.

## Access policy, runtime-editable

Daily allowances, guest limits and per-conversation caps moved from
import-time env constants (`quota.py`) to a persisted store read per
request (`policy.py`), editable from the console's "Access & limits" page
with no restart. Guest (signed-out) AI access is a switch and it is **off**
by default: `widget_api.py`'s invariant holds — no LLM call is reachable
without an account, which is what keeps public cost exposure and the public
prompt-injection surface at zero.

## One widget, not three

`groundedops-widget.js` now reads `/widget/config` — the Widget design
page previously saved settings the live widget never applied. The admin
console's Test chat now iframes the real widget on a stand-in page
(`/widget/preview`) rather than running a second chat implementation, which
is the exact problem that got the React SPA retired in v15.2. Contact forms
are built from console config; a refusal now offers the support form and a
phone number instead of ending on bare refusal text.

## Staging exposure, made deliberate

The admin console was unreachable behind any reverse proxy, with no way to
open it. `ADMIN_ALLOWED_IPS` now permits specific external IP prefixes,
sitting in front of authentication rather than instead of it.
`BOOTSTRAP_TOKEN` closes the matching land-grab: an allowlist usually
covers a whole office range, not one person, so first-run root creation
needs a second, explicit credential once opened to the network. `run.ps1`
now shows the LAN IP for the admin console (it was hardcoded to
`127.0.0.1`, which only works on the machine running the script) and warns
if no account exists yet before a link gets shared.

## What deploys

| File | Where it goes | Notes |
|---|---|---|
| `codeql-config.yml` | replaces `.github/codeql/codeql-config.yml` | CI, unrelated to app runtime |
| `ci.yml` | replaces `.github/workflows/ci.yml` | added `cryptography` to the unit job's explicit package list |
| `codeql.yml` | replaces `.github/workflows/codeql.yml` | excludes `src/legacy/**` |
| `.gitignore` | replaces `.gitignore` | accounts.json, policy.json, backup archives/snapshots, quota.db-wal/-shm |
| `HANDOFF.md` | replaces `HANDOFF.md` | session continuation notes |
| `PENDING.md` | replaces `PENDING.md` | open-items log, kept current through this release |
| `PROJECT_MAP.md` | replaces `PROJECT_MAP.md` | architecture doc updated for every feature below |
| `QUICKSTART.md` | replaces `QUICKSTART.md` | first-run account creation replaces the old password step |
| `README.md` | replaces `README.md` | new env vars documented |
| `STAGING.md` | new | deployment runbook: exposure model, secrets, persistence, WordPress plugin |
| `.env.example` | replaces `docker/.env.example` | new; template for `docker/.env` |
| `docker-compose.override.yml` | replaces `docker/docker-compose.override.yml` | dev-only overlay, unchanged in substance |
| `docker-compose.yml` | replaces `docker/docker-compose.yml` | `SESSION_SECRET` required, no default; runtime state moved onto the persistent volume |
| `install.ps1` | replaces `install.ps1` | |
| `run.ps1` | replaces `run.ps1` | admin URL now uses the real LAN IP; account-bootstrap and tunnel/admin warnings |
| `_harness.py` | replaces `src/_harness.py` | test isolation for accounts, policy, keystore, quota.db, backup paths |
| `accounts.py` | new | account store: levels, sessions, scrypt hashing |
| `admin.html` | replaces `src/admin.html` | sign-in, Accounts, Access & limits, API keys, Backup pages; Test chat iframes the real widget |
| `backup.py` | new | export/restore, encrypted envelope |
| `docstore.py` | replaces `src/docstore.py` | |
| `embeddings.py` | replaces `src/embeddings.py` | |
| `eval.py` | replaces `src/eval.py` | |
| `eval_cases.json` | replaces `src/eval_cases.json` | |
| `eval_retrieval.py` | replaces `src/eval_retrieval.py` | |
| `faq_store.py` | replaces `src/faq_store.py` | |
| `ingest.py` | replaces `src/ingest.py` | |
| `keystore.py` | new | single point of access for provider API keys and secrets |
| `llm.py` | replaces `src/llm.py` | provider usage/token accounting captured (was discarded) |
| `main.py` | replaces `src/main.py` | account/policy/backup/keystore endpoints; ADMIN_ALLOWED_IPS + BOOTSTRAP_TOKEN gates |
| `manage_accounts.py` | new | CLI account recovery |
| `manage_backup.py` | new | CLI backup export/inspect/restore |
| `memory.py` | replaces `src/memory.py` | |
| `parsing.py` | replaces `src/parsing.py` | |
| `policy.py` | new | runtime-editable access limits |
| `quota.py` | replaces `src/quota.py` | reads limits through policy.py; per-session caps added |
| `reindex.py` | replaces `src/reindex.py` | |
| `release.py` | replaces `src/release.py` | fixed: `git log` output was decoded with the platform locale encoding instead of UTF-8, corrupting em dashes in every generated changelog on Windows |
| `requirements.txt` | replaces `src/requirements.txt` | |
| `retrieval_db.py` | replaces `src/retrieval_db.py` | |
| `router.py` | replaces `src/router.py` | |
| `run_tests.py` | replaces `src/run_tests.py` | |
| `test_queries.py` | replaces `src/test_queries.py` | |
| `test_accounts.py` | new | |
| `test_backup.py` | new | |
| `test_exposure.py` | new | |
| `test_keystore.py` | new | |
| `test_llm.py` | replaces `src/tests/test_llm.py` | token-usage coverage added |
| `test_new_routes.py` | replaces `src/tests/test_new_routes.py` | session tokens replace the shared password fixture |
| `test_parsing.py` | replaces `src/tests/test_parsing.py` | |
| `test_policy.py` | new | |
| `test_token_usage.py` | new | |
| `test_widget_forms.py` | new | |
| `groundedops-widget.js` | replaces `src/widget/groundedops-widget.js` | reads `/widget/config`; contact forms; contact-intent detection |
| `groundedops-widget.php` | replaces `src/widget/groundedops-widget.php` | no longer overrides console branding via hardcoded `data-*` |
| `preview.html` | new | stand-in customer page the console's Test chat iframes |
| `widget-nginx.conf` | replaces `src/widget/widget-nginx.conf` | |
| `widget_api.py` | replaces `src/widget_api.py` | guest-AI gate reads policy.py; per-session cap check; draft_enquiry endpoint |
| `widget_config.py` | replaces `src/widget_config.py` | enquiry write-up, phone, cc_visitor on lead forms |

## Commits in this release

```
d9bcaa1 fix(run.ps1): show the LAN IP for the admin console, not 127.0.0.1
1431560 feat(backup): encrypt archives in an authenticated envelope
0c5018f feat(backup): export and restore everything, so nothing is re-ingested
4cf38a6 feat(deploy): make staging exposure a deliberate, tested decision
e628de8 feat: accounts, access policy, and one widget across every surface
eab36b0 Merge origin/main: keep GitHub's CodeQL workflow, add the path filter
a11108e ci: own CodeQL workflow so src/legacy can be excluded
9a37d9e fix(security): resolve documents by enumeration, not path construction
4c9ffa2 fix(build): drop the frontend image, repoint the widget service
b47915a fix(security): close the remaining live CodeQL findings
c9c97d1 fix(ci): run harness-based tests in their own process
c2de389 fix(ci): install the deps test_new_routes.py actually needs
542b27e release: v15.2 — retire the React SPA, fix CI, address CodeQL findings
b8205f7 docs: add PENDING.md, the working list of open items
2b52980 release: v15.1 — three thresholds on uncalibrated scores
2ebbee9 docs: archive v14.0 and v15.0 drops
4986c2c feat: make the index disposable and retrieval measurable
```

## Verification

- `run_tests.py`: 139/139 passed, 1 skipped (`test_ui.py`, needs playwright —
  pre-existing, unrelated to this release).
- `test_accounts.py`, `test_backup.py`, `test_exposure.py`, `test_keystore.py`,
  `test_policy.py`, `test_token_usage.py`, `test_widget_forms.py` are new
  this release.
- `manage_backup.py export` → `inspect` → `restore` round-tripped by hand
  against a scratch install (encrypted).
- `run.ps1`'s account-detection regex checked by hand against a real
  `accounts.json` (true) and an empty-`users` file (false); the script
  parses cleanly under PowerShell's own parser.
- Fixed during this release, not before it shipped: `release.py`'s
  changelog generator corrupted em dashes via a locale-encoding mismatch —
  caught while writing this file, fixed, and the snapshot regenerated
  clean.

**Not verified**: no step in this release has been exercised against a
real deployed host. The console's Backup, Accounts, and Access & limits
pages, the sign-in/bootstrap flow, and the widget's contact-form flow have
not been through a browser. `docker compose up` has not been run since the
compose file changed. The WordPress plugin edit was not PHP-linted (no
`php` binary available in this environment).
