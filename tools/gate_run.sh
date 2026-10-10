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
# A sibling whose text is already in the live question log or FAQ-gap list fails the gate before the bot is asked.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-$REPO/.venv/Scripts/python.exe}"; [ -x "$PY" ] || PY="$REPO/.venv/bin/python"
DEV="$(cd "$1" && pwd)" || exit 1
RUN="$(cd "$2" && pwd)" || exit 1
OUT="$3"; shift 3
mkdir -p "$OUT/after" && OUT="$(cd "$OUT" && pwd)"
export PYTHONIOENCODING=utf-8 HF_HUB_OFFLINE=1 GO_CODE="$DEV/src" GO_DATA="${GO_DATA:-$REPO/src}"
# D-20261010-20: this gate's hidden siblings must not be readable in the live log Dev works beside
if [ $# -gt 0 ] && ! hits="$("$PY" "$REPO/tools/retrofit_gate.py" --leakcheck "$GO_DATA"/logs*.jsonl "$GO_DATA/faq_gaps.json" --cases "$@")"; then
  echo "hidden questions in the live log: $hits" > "$OUT/FAILED"; exit 1
fi
BE="${BE:-$REPO/tools/blind_eval.py}"
log() { local f="$1"; shift; "$@" >> "$f" 2>&1 || { echo "failed: $*" > "$OUT/FAILED"; exit 1; }; }
# a run graded by an older grader is re-marked first, so before and after share one marker
log "$OUT/gate.log" "$PY" "$BE" "$RUN" remark

cp "$RUN/cases.json" "$RUN/cases_state.json" "$OUT/after/"
log "$OUT/gate.log" "$PY" "$BE" "$OUT/after" reset
# the old code's answers ride along as sys_before so before and after are graded in one pass (D-20261010-05)
log "$OUT/gate.log" "$PY" -c 'import json,sys; s=json.load(open(sys.argv[2],encoding="utf-8")); s["sys_before"]=json.load(open(sys.argv[1],encoding="utf-8"))["sys_normal"]; json.dump(s,open(sys.argv[2],"w",encoding="utf-8"),indent=1,ensure_ascii=False)' "$RUN/cases_state.json" "$OUT/after/cases_state.json"
log "$OUT/gate.log" "$PY" -X faulthandler "$BE" "$OUT/after" system normal
log "$OUT/gate.log" env SYSTEMS=sys_normal,baseline,sys_before "$PY" "$BE" "$OUT/after" grade

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
  --after "$OUT/after/cases_state.json" --regraded sys_before --siblings "${SIBS[@]}" > "$OUT/gate.json"
cat "$OUT/gate.json"
"$PY" "$REPO/tools/gate_rerun.py" "$DEV" "$RUN" "$OUT"   # 1-2 case "became wrong": re-ask 6x branch vs base
