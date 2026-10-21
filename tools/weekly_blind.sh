#!/usr/bin/env bash
# weekly_blind.sh -- the Customer department's blind run as a shell job (no model tokens).
#
#   bash tools/weekly_blind.sh <run_dir>
#
# <run_dir> holds cases.json (written by the customer agent) and manuals/
# (tools/export_manuals.py). Runs the bot (normal mode), the whole-manual
# baseline and the grade, each resumable, then writes <run_dir>/DONE,
# or FAILED naming the phase. Log: <run_dir>/run.log.
# REPEATS=N (default 3 since 2026-10-19, D-20261018-02): up to N answers per question (cases_state.r<k>.json).
# ADAPTIVE (the CEO, 2026-10-21; blind_eval.py ADAPTIVE): repeat 1 is graded first; repeats 2..N ask only the questions
# it did not get right, and the whole-manual baseline answers once, only the questions no repeat got right.
set -u
REPEATS="${REPEATS:-3}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-$REPO/.venv/Scripts/python.exe}"; [ -x "$PY" ] || PY="$REPO/.venv/bin/python"
RUN="$(cd "$1" && pwd)" || exit 1
export PYTHONIOENCODING=utf-8 HF_HUB_OFFLINE=1 ADAPTIVE=1
rm -f "$RUN/DONE" "$RUN/FAILED"
REP=""; [ "$REPEATS" -gt 1 ] && REP=" --repeats $REPEATS"
STEPS=("sys_normal|system normal" "sys_normal|grade")
[ -n "$REP" ] && STEPS+=("sys_normal|system normal$REP" "sys_normal|grade$REP")
STEPS+=("sys_normal,baseline|baseline" "sys_normal,baseline|grade")
for step in "${STEPS[@]}"; do
  systems="${step%%|*}"; p="${step#*|}"
  echo "== $p ($systems) $(date +%T)" >> "$RUN/run.log"
  # shellcheck disable=SC2086
  SYSTEMS="$systems" "$PY" -X faulthandler "$REPO/tools/blind_eval.py" "$RUN" $p >> "$RUN/run.log" 2>&1 \
    || { [ $? -eq 3 ] && p="$p: lab stopped at the cap"; echo "$p" > "$RUN/FAILED"; exit 1; }   # exit 3: OPS/lab_cap.json
done
touch "$RUN/DONE"
