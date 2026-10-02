#!/usr/bin/env bash
# docker_startup_check.sh -- PENDING.md 10.3: does the backend image actually start?
#
#   docker build -f docker/Dockerfile.backend -t groundedops-backend ./src
#   bash tools/docker_startup_check.sh [image]      # default groundedops-backend
#
# Runs the image with no index and no provider key (what a fresh deploy is),
# then asserts three things:
#   1. /health answers 200                 -- the process is up
#   2. /health?deep=1 warmup.ready turns true -- the baked-in models load and
#      start-up finishes (the part a plain /health cannot see)
#   3. /health?deep=1 is 503 with ready=false -- an empty index must NOT read
#      as ready (the 9.x bug where a fresh deploy reported healthy)
# Needs only docker, curl and python3 (JSON parsing). Cleans up the container.

set -u
IMAGE="${1:-groundedops-backend}"
PORT="${PORT:-18000}"
NAME="groundedops-startup-check-$$"
WAIT="${WAIT:-240}"   # seconds; the image bakes the models, so ~30-60s normally

cleanup() { docker rm -f "$NAME" > /dev/null 2>&1; }
trap cleanup EXIT

fail() { echo "FAIL: $*"; echo "--- container log (tail) ---"; docker logs --tail 40 "$NAME" 2>&1; exit 1; }

# SESSION_SECRET is a dummy: without it the console refuses sign-in, but the
# process still starts. No provider key, no volume -> empty index.
docker run -d --name "$NAME" -p "$PORT:8000" \
  -e SESSION_SECRET=startup-check-not-a-real-secret \
  -e GENERATION_MODE=api "$IMAGE" > /dev/null || fail "docker run"

URL="http://127.0.0.1:$PORT"
json() { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)"; }

# 1. liveness
for _ in $(seq "$WAIT"); do
  curl -fs "$URL/health" > /dev/null 2>&1 && break
  [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = true ] || fail "container exited during start-up"
  sleep 1
done
curl -fs "$URL/health" > /dev/null || fail "/health never answered 200 in ${WAIT}s"
echo "ok: /health 200"

# 2. warm-up finished (503 is expected here; read the body, not the status)
for _ in $(seq "$WAIT"); do
  [ "$(curl -s "$URL/health?deep=1" | json "d['warmup']['ready']" 2>/dev/null)" = True ] && break
  sleep 1
done
BODY="$(curl -s "$URL/health?deep=1")"
[ "$(echo "$BODY" | json "d['warmup']['ready']")" = True ] || fail "warm-up not ready in ${WAIT}s: $BODY"
echo "ok: warm-up ready"

# 3. an empty index is not ready
CODE="$(curl -s -o /dev/null -w '%{http_code}' "$URL/health?deep=1")"
[ "$CODE" = 503 ] || fail "/health?deep=1 returned $CODE on an empty index, expected 503"
[ "$(echo "$BODY" | json "d['ready']")" = False ] || fail "deep health says ready on an empty index: $BODY"
echo "ok: deep health is 503/not-ready on an empty index"
echo "PASS"
