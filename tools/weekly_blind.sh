#!/usr/bin/env bash
# weekly_blind.sh -- the Customer department's blind run as a shell job (no model tokens).
#
#   bash tools/weekly_blind.sh <run_dir>
#
# <run_dir> holds cases.json (written by the customer agent) and manuals/
# (tools/export_manuals.py). Runs the bot (normal mode), the whole-manual
# baseline and the grade, each resumable, then writes <run_dir>/DONE,
# or FAILED naming the phase. Log: <run_dir>/run.log.
# REPEATS=N (default 3 since 2026-10-19, D-20261018-02): the bot answers and is graded N times (cases_state.r<k>.json);
# the baseline answers BASE_REPEATS times (default 1) and each later repeat is graded beside repeat 1's.
set -u
REPEATS="${REPEATS:-3}"; BASE_REPEATS="${BASE_REPEATS:-1}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-$REPO/.venv/Scripts/python.exe}"; [ -x "$PY" ] || PY="$REPO/.venv/bin/python"
RUN="$(cd "$1" && pwd)" || exit 1
export PYTHONIOENCODING=utf-8 HF_HUB_OFFLINE=1
rm -f "$RUN/DONE" "$RUN/FAILED"
for p in "system normal" "baseline" "grade"; do
  n=$REPEATS; [ "$p" = baseline ] && n=$BASE_REPEATS
  [ "$n" -gt 1 ] && p="$p --repeats $n"
  echo "== $p $(date +%T)" >> "$RUN/run.log"
  # shellcheck disable=SC2086
  "$PY" -X faulthandler "$REPO/tools/blind_eval.py" "$RUN" $p >> "$RUN/run.log" 2>&1 \
    || { [ $? -eq 3 ] && p="$p: lab stopped at the cap"; echo "$p" > "$RUN/FAILED"; exit 1; }   # exit 3: OPS/lab_cap.json
done
touch "$RUN/DONE"
