Picture test for the GroundedOps agent office (D-20261022-14): questions whose answer is only in a manual's
picture, scored apart from the weekly 40. Nobody is present: never ask questions, never guess past a failure.

OFFICE = C:\Users\hrizvi\Downloads\Git\groundedops\.claude\worktrees\agent-org (branch experimental/agent-org-phase1)
REPO   = C:\Users\hrizvi\Downloads\Git\groundedops (the product under test: src/, .venv, the live index, documents/figures)
OPS    = C:\Users\hrizvi\groundedops-ops
PY     = <REPO>/.venv/Scripts/python.exe

PURGE: each phase runs in a fresh subagent from a cleared context. The questions are HIDDEN: never read,
print or quote RUN/cases.json, RUN/parts, the answers or the run log; read only the tools' last lines.

0. Move the session to OFFICE with the change_directory tool. Use the OFFICE STATUS section at the end for "status" and "event".
1. STAMP = today as YYYYMMDD (add b, c, ... if OPS/heldout/images/STAMP exists). RUN = OPS/heldout/images/STAMP. Create RUN/parts.
   Status customer working "Reading the manuals for the picture test".
   `cd "<REPO>/src" && HF_HUB_OFFLINE=1 "<PY>" "<OFFICE>/tools/export_manuals.py" "<RUN>/manuals"`
2. `"<PY>" "<OFFICE>/tools/image_set.py" plan "<RUN>" "<REPO>/documents/figures"`: one line per manuals file, "<stem> | <document>=<n> | ...".
   Status customer working "Writing picture questions". MODEL PICK once (agent customer, job write-images, inputs RUN/manuals),
   then spawn one customer subagent per line, all in one message: Agent tool, subagent_type "customer" (if not listed,
   general-purpose with model "sonnet" told to read OFFICE/.claude/agents/customer.md and follow it exactly). Prompt:
   "Job WRITE-IMAGES. RUN = <RUN>. OFFICE = <OFFICE>. FIGS = <REPO>/documents/figures. PRODUCT = <stem>. DOCS = <the line's document=n pairs, joined by '; '>. ID PREFIX = P<STAMP>-<stem>."
3. `"<PY>" "<OFFICE>/tools/image_set.py" merge "<RUN>" "<REPO>/documents/figures"`. Last line "OK <n>": go on. "FAIL <part>: ...":
   re-run that part's subagent once, then merge again; still FAIL: status customer blocked "Picture questions failed the merge", stop.
4. Status lab working "Asking the chatbot the picture questions". Run, with run_in_background and wait for the notice (no polling with sleep):
   `cd "<OFFICE>" && GO_CODE="<REPO>/src" GO_DATA="<REPO>/src" SYSTEMS=sys_normal "<PY>" tools/blind_eval.py "<RUN>" system normal > "<RUN>/run.log" 2>&1 && GO_CODE="<REPO>/src" GO_DATA="<REPO>/src" SYSTEMS=sys_normal "<PY>" tools/blind_eval.py "<RUN>" grade >> "<RUN>/run.log" 2>&1; echo exit $?`
   (one answer per question, no whole-manual baseline: the manual text does not hold these answers). Exit 3 = lab cap: status lab blocked "Lab stopped at the cap", stop. Other non-zero: status lab blocked "Picture run failed", stop.
5. `"<PY>" "<OFFICE>/tools/image_set.py" score "<RUN>" "<OPS>/runs/images-<STAMP>"` prints "figure found x of n, explanation right y of n" and writes OPS/runs/images-<STAMP>/score.json (counts per product and case ids, no text).
   This score is never added to the weekly score, the pivot or the trend.
6. Write OPS/inbox/customer/images-<STAMP>.md: the two counts, the per-product counts from score.json, the run folder path, and "questions hidden under OPS/heldout/images/<STAMP>". Status lab idle with last = the score line. Event lab -> po "Picture test: <score line>".

CONSTRAINTS: never edit, stage or commit files in REPO or OFFICE; never reindex, rebuild an .exe or push. OPS is the only place this run writes.
