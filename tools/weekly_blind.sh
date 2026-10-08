#!/usr/bin/env bash
# weekly_blind.sh -- the Customer department's blind run as a shell job (no model tokens).
#
#   bash tools/weekly_blind.sh <run_dir>
#
# <run_dir> holds cases.json (written by the customer agent) and manuals/
# (tools/export_manuals.py). Runs the bot (normal mode), the whole-manual
# baseline and the gpt-5 grade, each resumable, then writes <run_dir>/DONE,
# or FAILED naming the phase. Log: <run_dir>/run.log.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-$REPO/.venv/Scripts/python.exe}"; [ -x "$PY" ] || PY="$REPO/.venv/bin/python"
RUN="$(cd "$1" && pwd)" || exit 1
export PYTHONIOENCODING=utf-8 HF_HUB_OFFLINE=1
rm -f "$RUN/DONE" "$RUN/FAILED"
for p in "system normal" "baseline" "grade"; do
  echo "== $p $(date +%T)" >> "$RUN/run.log"
  # shellcheck disable=SC2086
  "$PY" -X faulthandler "$REPO/tools/blind_eval.py" "$RUN" $p >> "$RUN/run.log" 2>&1 \
    || { echo "$p" > "$RUN/FAILED"; exit 1; }
done
touch "$RUN/DONE"
