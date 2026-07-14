# Running GroundedOps with Docker

One command brings up the whole stack — backend, frontend, and (for
offline mode) Ollama. No Python/Node/venv setup on the host.

## Prerequisites
- Docker Desktop (Windows/macOS) or Docker Engine + Compose (Linux).
- ~3 GB disk for images; +6 GB if you use Free/offline mode (local models).

## Layout expected
Place these files at the repo root, with your code in ./src and the
frontend in ./src/frontend:
    docker-compose.yml
    Dockerfile.backend
    Dockerfile.frontend
    .dockerignore
    src/                 (all .py, requirements.txt)
    src/frontend/        (React app; PLACE nginx.conf HERE — from src_frontend_dropin/)
NOTE: the compose file builds the backend from ./src and the frontend
image copies ./src/frontend. Adjust the two `context:` paths if your
tree differs.

## Start
```bash
docker compose up -d --build
# open http://localhost:8080
```
First build pre-downloads the three embedding/reranker/NLI models into
the backend image (a few minutes, once).

## Modes
- **Online (default)**: enter an API key in the app's startup popup
  (DeepSeek / OpenAI / Claude). Ollama isn't used; you can stop it:
  `docker compose stop ollama`.
- **Free/offline**: switch in the app. Pull models once:
  `docker compose exec ollama ollama pull mistral`
  `docker compose exec ollama ollama pull phi`
  (or use the app's Free-mode dialog, which pulls with a progress bar).
  Free mode is CPU-slow in Docker; uncomment the GPU block in
  docker-compose.yml if you have an NVIDIA GPU + container toolkit.

## Ingesting documents
- Drop PDFs into ./corpus (mounted read-only at /app/corpus), then use
  the app's Documents dialog to upload/ingest, OR
- Upload directly through the Documents dialog in the UI.
Ingested vectors persist in the `grounded-data` volume across restarts.

## Data & persistence
- `grounded-data` volume: chroma_db (your ingested corpus).
- `ollama-models` volume: downloaded local models.
- Wipe everything: `docker compose down -v` (removes both volumes).

## Production notes (before exposing to a network)
- Put TLS + auth in front (the frontend nginx is the place, or a
  reverse proxy). Admin endpoints (/upload, /reset, /delete_source) are
  NOT yet auth-gated — do not expose publicly without adding auth.
- Set a fixed CORS origin and rate limiting (planned for v2).
- Provide keys via a .env file (compose reads it) rather than the UI if
  running headless.
