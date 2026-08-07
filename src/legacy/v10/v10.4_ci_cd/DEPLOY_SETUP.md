# CI/CD setup

## What runs automatically (on push/PR to main) — .github/workflows/ci.yml
1. eval gate — `python eval.py --selfcheck` validates the suite + baseline
   parse (full eval needs the corpus, which runs locally). Extend to a
   full eval in CI only if you commit a small public test corpus.
2. build — both Docker images must build or the run fails.

## Local dev auto-update (no rebuild on every change)
`docker-compose.override.yml` bind-mounts ./src and runs uvicorn --reload
+ the Vite dev server. Code changes reflect live:
    make dev        # start hot-reload stack
    make logs       # watch backend (incl. 'doc2query provider = ...')
For a clean production run WITHOUT the override:
    make prod

## STAGE 2 — real deployment (decision needed)
The deploy job in ci.yml is COMMENTED OUT because it needs a target.
Pick one and I'll wire it:
  - VPS + docker compose (push image to GHCR, ssh pull+restart)
  - Fly.io / Railway (deploy hook)
  - Cloud registry only (you deploy manually)
Auto-deploy MUST keep the eval gate in front of it — do not deploy on a
red eval. And the auth seams (admin password, user X-User-Id) must be
replaced with real auth before any public deploy.
