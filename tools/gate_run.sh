#!/usr/bin/env bash
# gate_run.sh -- run the retrofit gate on a Dev branch (shell job, no model tokens).
#
#   bash tools/gate_run.sh <dev_worktree> <run_dir> <out_dir> <siblings.json> [more siblings...]
#
# <run_dir> is the weekly blind run the finding came from (graded on the old
# code). Re-asks its questions with the Dev worktree's code, asks the hidden
# sibling questions, grades both, then tools/retrofit_gate.py writes
# <out_dir>/gate.json. Data (index, stores, .env) comes from this repo's src/.
#
# <out_dir> sits where Dev reads (inbox/dev/<id>.gate), so it gets the open set
# only. Everything the hidden pass writes (its log, sib_*/ states) goes to
# $GATE_HELD, default <heldout>/gates/<out_dir name>-<timestamp>, beside the
# sibling files. Before scoring, the open-side files are checked for sibling text.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-$REPO/.venv/Scripts/python.exe}"; [ -x "$PY" ] || PY="$REPO/.venv/bin/python"
DEV="$(cd "$1" && pwd)" || exit 1
RUN="$(cd "$2" && pwd)" || exit 1
OUT="$3"; shift 3
mkdir -p "$OUT/after" && OUT="$(cd "$OUT" && pwd)"
export PYTHONIOENCODING=utf-8 HF_HUB_OFFLINE=1 GO_CODE="$DEV/src" GO_DATA="${GO_DATA:-$REPO/src}"
BE="${BE:-$REPO/tools/blind_eval.py}"
log() { local f="$1"; shift; "$@" >> "$f" 2>&1 || { echo "failed: $*" > "$OUT/FAILED"; exit 1; }; }

cp "$RUN/cases.json" "$RUN/cases_state.json" "$OUT/after/"
log "$OUT/gate.log" "$PY" "$BE" "$OUT/after" reset
log "$OUT/gate.log" "$PY" -X faulthandler "$BE" "$OUT/after" system normal
log "$OUT/gate.log" "$PY" "$BE" "$OUT/after" grade

SIBS=()
if [ $# -gt 0 ]; then
  HELD="${GATE_HELD:-$(dirname "$(dirname "$1")")/gates/$(basename "$OUT")-$(date +%Y%m%d-%H%M%S)}"
  mkdir -p "$HELD" && HELD="$(cd "$HELD" && pwd)"
fi
for s in "$@"; do
  d="$HELD/sib_$(basename "$s" .json)"; mkdir -p "$d"; cp "$s" "$d/cases.json"
  log "$HELD/gate.log" env SYSTEMS=sys_normal "$PY" -X faulthandler "$BE" "$d" system normal
  log "$HELD/gate.log" env SYSTEMS=sys_normal "$PY" "$BE" "$d" grade
  SIBS+=("$d/cases_state.json")
done

if [ $# -gt 0 ] && ! hits="$("$PY" "$REPO/tools/retrofit_gate.py" --leakcheck "$OUT" --cases "$@")"; then
  echo "hidden text under inbox/dev: $hits" > "$OUT/FAILED"; exit 1
fi

"$PY" "$REPO/tools/retrofit_gate.py" --before "$RUN/cases_state.json" \
  --after "$OUT/after/cases_state.json" --siblings "${SIBS[@]}" > "$OUT/gate.json"
cat "$OUT/gate.json"
"$PY" "$REPO/tools/gate_rerun.py" "$DEV" "$RUN" "$OUT"   # 1-2 case "became wrong": re-ask 6x branch vs base
