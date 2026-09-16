# Security Policy

## Supported versions

This isn't a library with parallel maintained release branches — only
the latest commit on `main` receives fixes. `src/legacy/` is a historical
reference archive of past releases (v3 through v13.1) and is never
patched; do not run anything from it.

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub Security Advisories](https://github.com/hamzahrizvi/groundedops/security/advisories/new)
rather than a public issue. There's no formal SLA — this is a
single-maintainer project — but reports are read promptly and a fix or
response should follow within a few days for anything credible.

## Known, already-accepted limitations

These are documented design tradeoffs, not vulnerabilities — please don't
open a report for them (see `QUICKSTART.md`'s "Known limitations" for the
full list):

- **Admin authentication is a single shared password**
  (`X-Admin-Password` against `ADMIN_PASSWORD`), not per-user accounts.
  Change the default before any shared or network-reachable deployment —
  it ships set to the literal word `admin`.
- **Source document downloads are unauthenticated by design**
  (`GET /source_file/{filename}`), so the embeddable public widget can
  offer them. Anything ingested is retrievable by filename if guessed.
- **Single tenant.** One document collection, one FAQ, one configuration
  per running instance — no isolation between customers sharing a
  deployment.
- **The admin console and its API routes are meant to stay off the
  public internet** — see `_PUBLIC_PREFIXES` / the LAN-only gate in
  `src/main.py`. If you're exposing this beyond your own network (the
  Cloudflare tunnel `run.cmd` can open, for example), only the `/widget/*`
  and `/health` paths are intended to be reachable from outside; treat
  anything else becoming reachable there as the actual vulnerability.

## Scope

In scope: the FastAPI backend (`src/main.py` and friends), the React
chat app and admin console (`src/frontend/`, `src/admin.html`), and the
embeddable widget (`src/frontend/public/widget/groundedops-widget.js`).

Out of scope: `src/legacy/` (historical, unmaintained), and
vulnerabilities in third-party dependencies (chromadb, sentence-transformers,
FastAPI, React, etc.) — please report those upstream; `.github/dependabot.yml`
tracks version updates here.
