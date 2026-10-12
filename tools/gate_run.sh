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
# A branch whose own commits touch nothing under src/ gets an office_only PASS and the bot is
# not asked (D-20261010-07). When the run's stored answers came from another bot model than the
# lab's today, the before is re-asked on the branch's base code into <out_dir>/before (D-20261010-09).
# The after pass gets the run's manuals/, the cited pages the run's grader had (D-20261010-23).
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-$REPO/.venv/Scripts/python.exe}"; [ -x "$PY" ] || PY="$REPO/.venv/bin/python"
DEV="$(cd "$1" && pwd)" || exit 1
RUN="$(cd "$2" && pwd)" || exit 1
OUT="$3"; shift 3
mkdir -p "$OUT/after" && OUT="$(cd "$OUT" && pwd)"
rm -f "$OUT/FAILED"   # an earlier attempt's (a cap stop is retried once the cap rises)
export PYTHONIOENCODING=utf-8 HF_HUB_OFFLINE=1 GO_CODE="$DEV/src" GO_DATA="${GO_DATA:-$REPO/src}"
# D-20261010-20: this gate's hidden siblings must not be readable in the live log Dev works beside
if [ $# -gt 0 ] && ! hits="$("$PY" "$REPO/tools/retrofit_gate.py" --leakcheck "$GO_DATA"/logs*.jsonl "$GO_DATA/faq_gaps.json" --cases "$@")"; then
  echo "hidden questions in the live log: $hits" > "$OUT/FAILED"; exit 1
fi
BE="${BE:-$REPO/tools/blind_eval.py}"
# exit 3 from blind_eval.py: the CEO's lab cap is reached (D-20261010-31); the gate fails closed and says so
log() { local f="$1"; shift; "$@" >> "$f" 2>&1 && return
  if [ $? -eq 3 ]; then echo "lab stopped at the cap" > "$OUT/FAILED"; else echo "failed: $*" > "$OUT/FAILED"; fi; exit 1; }
# D-20261010-07: the branch's own commits (in neither the office nor the product checkout) touch nothing
# under src/, so the bot cannot answer differently: PASS without asking it. Any git error runs the full gate.
not=(); for c in "$REPO" "$GO_DATA"; do h="$(git -C "$c" rev-parse HEAD 2>/dev/null)" && git -C "$DEV" cat-file -e "$h^{commit}" 2>/dev/null && not+=("$h"); done
if own="$(git -C "$DEV" log -m --format= --name-only HEAD --not "${not[@]}" -- src)" && [ -z "$own" ]; then
  echo '{"verdict": "PASS", "reasons": ["no product code changed"], "fixed": [], "became_wrong": [], "cases": 0, "siblings": 0, "office_only": true}' > "$OUT/gate.json"
  cat "$OUT/gate.json"; exit 0
fi
# a run graded by an older grader is re-marked first, so before and after share one marker
log "$OUT/gate.log" "$PY" "$BE" "$RUN" remark
# D-20261010-09: stored answers from another bot model are re-asked on the base code with today's model
"$PY" "$BE" "$RUN" models > "$OUT/models.json" 2>> "$OUT/gate.log" || { echo "failed: models" > "$OUT/FAILED"; exit 1; }
BEFORE="$RUN/cases_state.json"
if ! grep -q '"same": true' "$OUT/models.json"; then
  mkdir -p "$OUT/before"; cp "$RUN/cases.json" "$RUN/cases_state.json" "$OUT/before/"
  log "$OUT/gate.log" "$PY" "$BE" "$OUT/before" reset
  log "$OUT/gate.log" "$PY" "$REPO/tools/gate_rerun.py" --base-code "$DEV" "$OUT/base_code"
  log "$OUT/gate.log" env GO_CODE="$OUT/base_code/src" "$PY" -X faulthandler "$BE" "$OUT/before" system normal
  rm -rf "$OUT/base_code"
  BEFORE="$OUT/before/cases_state.json"   # not graded here: the after pass marks it as sys_before
fi

cp "$RUN/cases.json" "$RUN/cases_state.json" "$OUT/after/"
if [ -d "$RUN/manuals" ]; then mkdir -p "$OUT/after/manuals" && cp "$RUN/manuals/"* "$OUT/after/manuals/"; fi   # D-20261010-23
log "$OUT/gate.log" "$PY" "$BE" "$OUT/after" reset
# the before answers (stored, or re-asked above) ride along as sys_before, graded with after in one pass (D-20261010-05)
log "$OUT/gate.log" "$PY" -c 'import json,sys; s=json.load(open(sys.argv[2],encoding="utf-8")); s["sys_before"]=json.load(open(sys.argv[1],encoding="utf-8"))["sys_normal"]; json.dump(s,open(sys.argv[2],"w",encoding="utf-8"),indent=1,ensure_ascii=False)' "$BEFORE" "$OUT/after/cases_state.json"
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
  --after "$OUT/after/cases_state.json" --regraded sys_before --models "$OUT/models.json" --manuals "$OUT/after/manuals" --siblings "${SIBS[@]}" > "$OUT/gate.json"
cat "$OUT/gate.json"
"$PY" "$REPO/tools/gate_rerun.py" "$DEV" "$RUN" "$OUT"   # 1-2 case "became wrong": re-ask 6x branch vs base
