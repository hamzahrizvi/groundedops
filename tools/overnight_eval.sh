#!/usr/bin/env bash
# overnight_eval.sh -- PENDING.md session 4: the eval runs as one shell job.
#
# Steps 11 (M4 runs), the runs for 13 (M5 blind 3x3) and 14 (M7/M13
# re-measure) are shell work, not model work. This runs them unattended and
# writes eval_runs/<stamp>/summary.json; session 5 reads only that file.
#
#   nohup bash tools/overnight_eval.sh > /dev/null 2>&1 &
#   STEPS="m4 summary" bash tools/overnight_eval.sh     # a subset
#
# What it does, in order, one backend at a time (two on one box skew each
# other's latency):
#   1. starts its OWN backend on HEAD at :$A_PORT (nohup -X faulthandler,
#      readiness from the log -- the recipe that survives unattended runs;
#      preview_start is refused there) and proves it is HEAD by the M2 trace
#      fields on a probe answer. It never touches a backend it did not start.
#   2. m4:   eval.py --repeats 5 on the tuned suite, 3 on blind1, blind2 and
#            retrieval, each to its own --results file. Nothing is locked in:
#            no --update-baseline anywhere in this script.
#   3. live: run_live.py --path both (M12's /query vs /widget/ask reading).
#   4. stops A; m5: checks out $B_REF (v16.3) in a worktree, starts it on
#      :$B_PORT against a COPY of the index and stores taken at batch start
#      (a bare launch would share src/chroma_db and the JSON stores), runs
#      blind2 --repeats 3 with HEAD's eval.py, stops it, removes the worktree.
#   5. m13:  sweep_grounding.py --cases eval_cases_retrieval.json, in-process.
#   6. summary: tools/eval_batch_summary.py over the results and the M2-
#      labelled log window.
#
# Needs: DEEPSEEK_API_KEY (env, or src/.env, or the keyvault eval.py falls
# back to) for the grader; WIDGET_TOKEN_SECRET (env or src/.env) for the
# widget path. Secrets are exported, never printed.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO/src"
PY="$REPO/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$REPO/.venv/bin/python"
STAMP="$(date +%Y%m%d_%H%M)"
OUT="${OUT:-$REPO/eval_runs/$STAMP}"
A_PORT="${A_PORT:-8010}"
B_PORT="${B_PORT:-8004}"
B_REF="${B_REF:-v16.3}"
STEPS="${STEPS:-m4 live m5 m13 summary}"
READY_TIMEOUT="${READY_TIMEOUT:-900}"
START_UTC="$("$PY" -c 'import datetime;print(datetime.datetime.utcnow().isoformat())')"

mkdir -p "$OUT"
LOG="$OUT/batch.log"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
want() { case " $STEPS " in *" $1 "*) return 0 ;; esac; return 1; }

# ── secrets from src/.env, only when not already exported ──────────────
env_value() {  # env_value NAME -> value from src/.env, quotes and CR stripped
  [ -f "$SRC/.env" ] || return 0
  grep -m1 "^$1=" "$SRC/.env" | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}
[ -n "${DEEPSEEK_API_KEY:-}" ] || { v="$(env_value DEEPSEEK_API_KEY)"; [ -n "$v" ] && export DEEPSEEK_API_KEY="$v"; }
[ -n "${WIDGET_TOKEN_SECRET:-}" ] || { v="$(env_value WIDGET_TOKEN_SECRET)"; [ -n "$v" ] && export WIDGET_TOKEN_SECRET="$v"; }
unset v
export EVAL_GRADER_PROVIDER="${EVAL_GRADER_PROVIDER:-deepseek}"
_model="$(env_value ONLINE_DEEPSEEK_MODEL)"
export EVAL_GRADER_MODEL="${EVAL_GRADER_MODEL:-${_model:-deepseek-v4-flash}}"
export PYTHONIOENCODING=utf-8

cat > "$OUT/batch_meta.json" <<META
{"started_utc": "$START_UTC", "head": "$(git -C "$REPO" rev-parse --short HEAD)",
 "branch": "$(git -C "$REPO" rev-parse --abbrev-ref HEAD)",
 "dirty": $( [ -z "$(git -C "$REPO" status --porcelain)" ] && echo false || echo true ),
 "a_port": $A_PORT, "a_index": "src/chroma_db", "b_ref": "$B_REF", "b_port": $B_PORT,
 "b_index": "copy of src/chroma_db at batch start ($OUT/b_state/chroma_db)",
 "grader": "$EVAL_GRADER_PROVIDER/$EVAL_GRADER_MODEL", "steps": "$STEPS"}
META
log "batch $STAMP  head=$(git -C "$REPO" rev-parse --short HEAD)  steps: $STEPS  out: $OUT"

# ── backends ────────────────────────────────────────────────────────────
up() { curl -s -m 5 "http://127.0.0.1:$1/health" > /dev/null 2>&1; }

start_backend() {  # start_backend PORT DIR LOGFILE [VAR=value ...]
  local port="$1" dir="$2" blog="$3"; shift 3
  if up "$port"; then
    log "port $port is already serving -- not ours, refusing to reuse it"
    return 1
  fi
  ( cd "$dir" && env "$@" nohup "$PY" -X faulthandler -m uvicorn main:app \
      --host 127.0.0.1 --port "$port" > "$blog" 2>&1 & echo $! > "$OUT/pid_$port" )
  local waited=0
  until grep -q "warmup complete" "$blog" 2>/dev/null; do
    sleep 5; waited=$((waited + 5))
    if [ "$waited" -ge "$READY_TIMEOUT" ]; then
      log "backend :$port not ready after ${READY_TIMEOUT}s (see $blog)"; return 1
    fi
  done
  sleep 15
  up "$port" || { log "backend :$port logged warmup but does not answer"; return 1; }
  log "backend :$port ready (${waited}s)"
}

listener_pid() {  # Windows PID listening on 127.0.0.1:PORT, if any
  netstat -ano 2>/dev/null | tr -d '\r' \
    | awk -v a="127.0.0.1:$1" '$2 == a && $4 == "LISTENING" {print $5; exit}'
}

stop_backend() {  # only a backend this script started (pid file present)
  local port="$1" pid
  pid="$(cat "$OUT/pid_$port" 2>/dev/null)" || return 0
  [ -n "$pid" ] || return 0
  # $! is the MSYS env/nohup wrapper, and the venv python.exe is a launcher
  # that spawns the real interpreter: killing $! (or its winpid) left both
  # 2026-09-27 backends serving all night. Kill the listener's whole tree;
  # start_backend refused an occupied port, so the listener is ours.
  local lpid; lpid="$(listener_pid "$port")"
  [ -n "$lpid" ] && taskkill //F //T //PID "$lpid" > /dev/null 2>&1
  local winpid; winpid="$(cat "/proc/$pid/winpid" 2>/dev/null)"
  [ -n "$winpid" ] && taskkill //F //T //PID "$winpid" > /dev/null 2>&1
  kill "$pid" 2>/dev/null
  sleep 5
  rm -f "$OUT/pid_$port"
  if up "$port"; then log "backend :$port STILL SERVING after stop"
  else log "backend :$port stopped"; fi
}
trap 'stop_backend "$A_PORT"; stop_backend "$B_PORT"' EXIT

is_head() {  # the M2/M12 trace fields exist only on this code
  curl -s -m 300 -H 'Content-Type: application/json' \
    -d '{"q":"what is MyCheckr","session_id":"preflight-batch-probe"}' \
    "http://127.0.0.1:$1/query" \
  | "$PY" -c 'import json,sys; m=(json.load(sys.stdin).get("pipeline") or {}).get("meta") or {}; sys.exit(0 if m.get("surface")=="query" and m.get("origin")=="preflight" else 1)'
}

run_eval() {  # run_eval PORT NAME CASES REPEATS BASELINE
  local port="$1" name="$2" cases="$3" reps="$4" base="$5"
  log "eval $name: $cases x$reps on :$port"
  ( cd "$SRC" && EVAL_URL="http://127.0.0.1:$port/query" "$PY" eval.py \
      --cases "$cases" --repeats "$reps" --results "$OUT/$name.json" \
      --baseline "$base" ) >> "$OUT/$name.log" 2>&1
  log "eval $name: exit $? ($(grep -E 'individual runs passed' "$OUT/$name.log" | tail -1 | sed 's/^ *//'))"
}
NO_BASELINE="$OUT/no_baseline.json"   # never created: the diff is skipped

# ── A: HEAD ─────────────────────────────────────────────────────────────
if want m4 || want live; then
  if start_backend "$A_PORT" "$SRC" "$OUT/backend_a.log" && is_head "$A_PORT"; then
    log "A is HEAD (trace carries surface/origin)"
    if want m4; then
      run_eval "$A_PORT" m4_tuned     eval_cases.json           5 eval_baseline.json
      run_eval "$A_PORT" m4_blind1    eval_cases_blind.json     3 "$NO_BASELINE"
      run_eval "$A_PORT" m4_blind2    eval_cases_blind2.json    3 "$NO_BASELINE"
      run_eval "$A_PORT" m4_retrieval eval_cases_retrieval.json 3 eval_baseline_retrieval.json
    fi
    if want live; then
      log "live: run_live --path both on :$A_PORT"
      ( cd "$SRC" && "$PY" tests/run_live.py --url "http://127.0.0.1:$A_PORT" \
          --path both --quiet --out "$OUT/live.md" --json "$OUT/live.json" ) \
        >> "$OUT/live.log" 2>&1
      log "live: exit $? ($(grep -E '^agreement' "$OUT/live.log" | tail -1))"
    fi
  else
    log "A failed to start or is not HEAD -- m4/live skipped"
  fi
  stop_backend "$A_PORT"
fi

# ── B: the last release, on a copy of the same index ────────────────────
if want m5; then
  WT="$REPO/.claude/worktrees/eval-$B_REF"
  ST="$OUT/b_state"
  mkdir -p "$ST"
  log "m5: copying index and stores for $B_REF"
  cp -r "$SRC/chroma_db" "$ST/chroma_db"
  for f in catalog_config.json faq_store.json faq_gaps.json policy.json \
           widget_config.json widget_leads.json accounts.json \
           spec_index.json doc_vocab.json; do
    [ -f "$SRC/$f" ] && cp "$SRC/$f" "$ST/$f"
  done
  if [ ! -d "$WT" ]; then
    git -C "$REPO" worktree add --detach "$WT" "$B_REF" >> "$LOG" 2>&1
  fi
  [ -f "$SRC/.env" ] && cp "$SRC/.env" "$WT/src/.env"   # gitignored there too
  if start_backend "$B_PORT" "$WT/src" "$OUT/backend_b.log" \
       CHROMA_DIR="$ST/chroma_db" CATALOG_CONFIG="$ST/catalog_config.json" \
       FAQ_STORE_PATH="$ST/faq_store.json" FAQ_GAP_PATH="$ST/faq_gaps.json" \
       POLICY_PATH="$ST/policy.json" WIDGET_CONFIG_PATH="$ST/widget_config.json" \
       WIDGET_LEADS_PATH="$ST/widget_leads.json" ACCOUNTS_PATH="$ST/accounts.json" \
       SPEC_INDEX_CACHE="$ST/spec_index.json" DOC_VOCAB_CACHE="$ST/doc_vocab.json" \
       CONVO_DB_PATH="$ST/conversations.db" QUOTA_DB_PATH="$ST/quota.db" \
       AUTH_TOKENS_PATH="$ST/auth_tokens.json" SOURCE_FILE_DIR="$REPO/documents"; then
    run_eval "$B_PORT" "m5_blind2_$B_REF" eval_cases_blind2.json 3 "$NO_BASELINE"
  else
    log "m5: $B_REF backend did not start -- skipped"
  fi
  stop_backend "$B_PORT"
  git -C "$REPO" worktree remove --force "$WT" >> "$LOG" 2>&1 \
    && log "m5: worktree removed"
  rm -rf "$ST/chroma_db"   # 34MB copy; the stores stay for inspection
  if [ -f "$OUT/m5_blind2_$B_REF.json" ] && [ -f "$OUT/m4_blind2.json" ]; then
    ( cd "$SRC" && "$PY" eval.py --compare-results "$OUT/m5_blind2_$B_REF.json" \
        "$OUT/m4_blind2.json" ) > "$OUT/m5_compare.txt" 2>&1
    log "m5: comparison -> m5_compare.txt"
  fi
fi

# ── M13: the grounding sweep, in-process ────────────────────────────────
if want m13; then
  log "m13: sweep_grounding --cases eval_cases_retrieval.json"
  # HF_HUB_OFFLINE: headless, SentenceTransformer's online revalidation
  # segfaults inside huggingface_hub (2026-09-28, twice, main thread); the
  # models are cached by then, backend A loaded them this batch.
  ( cd "$SRC" && HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}" "$PY" -X faulthandler \
      sweep_grounding.py --cases eval_cases_retrieval.json ) \
    >> "$OUT/m13_sweep.log" 2>&1
  rc=$?
  log "m13: exit $rc"
  # Copy only this run's output: on 2026-09-27 the sweep segfaulted and a
  # month-old sweep_grounding.json was copied in as if it were tonight's.
  if [ "$rc" -eq 0 ] && [ "$SRC/sweep_grounding.json" -nt "$LOG" ]; then
    cp "$SRC/sweep_grounding.json" "$OUT/m13_sweep.json"
    ( cd "$SRC" && "$PY" sweep_grounding.py --report-only ) > "$OUT/m13_sweep_report.txt" 2>&1
  fi
fi

# ── summary ─────────────────────────────────────────────────────────────
if want summary; then
  "$PY" "$REPO/tools/eval_batch_summary.py" "$OUT" --since "$START_UTC" \
    > "$OUT/summary.stdout" 2>&1
  log "summary: exit $? -> $OUT/summary.json"
fi
log "batch done"
