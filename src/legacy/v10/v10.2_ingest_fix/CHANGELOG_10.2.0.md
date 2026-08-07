# GroundedOps — internal 10.2.0 (async ingest + ingestion provider control)

Release line: part of GO_v2.0. Base folder: groundedops/ — copy over your
repo root (merges into src/ and src/frontend/).

Files:
  src/main.py                              async upload + status endpoints
  src/ingest.py                            INGEST_PROVIDER + api_keys + progress
  src/frontend/src/api.js                  upload() now starts job + polls
  src/frontend/src/components/Dialogs.jsx  ingest progress bar
  src/frontend/nginx.conf                  generous proxy timeouts

## The 504
nginx returned 504 because ingest was a BLOCKING request and doc2query
(3-5 LLM calls per chunk) on a large PDF ran past the proxy timeout —
worst case on local CPU models. Fixed two ways:

## 1. Async ingest (the real fix)
POST /upload now returns a job_id immediately and indexes in a background
thread. Poll GET /upload/status/{job_id} for {stage, pct, done}. The UI
uploads, then polls, showing a progress bar per file. No long-held
request = no 504, regardless of document size or provider speed.

## 2. Ingestion provider control (your API-key-for-ingest request)
New env var INGEST_PROVIDER:
  deepseek | openai | anthropic   -> use that API for doc2query (fast;
                                     recommended for big docs — minutes
                                     not hours)
  local                           -> local mistral (slow on CPU)
  auto (default)                  -> API if a key is set, else local
API keys are read from the backend's ENV (DEEPSEEK_API_KEY etc.), so
batch/headless ingest needs no key in the request. In Docker, set it in
the backend service environment or .env.

## 3. Local fallback is also less fragile
If you stay on local ingestion (INGEST_PROVIDER=local), the async design
means it can take as long as it needs — the nginx timeout is no longer
the limit. The bumped nginx timeouts (600s) only matter for the short
poll requests now.

## Apply (no security-seam change here; identity work unchanged)
1. Copy groundedops/ over your repo root.
2. Rebuild: docker compose up -d --build   (frontend picks up nginx.conf)
3. To ingest via API: set INGEST_PROVIDER=deepseek (or openai/anthropic)
   and the matching key in the backend env, then upload.
4. Watch the progress bar; large docs now finish without 504.

## Note
Async ingest changes the /upload contract (was: returns chunks_added;
now: returns job_id + poll). If you have any script hitting /upload
directly, update it to poll /upload/status/{job_id}.
