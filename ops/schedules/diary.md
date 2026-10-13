Diary run for the GroundedOps agent office: the product owner writes a few plain lines per day on what was done, what went wrong and how it was sorted. The dispatcher starts it when tools/office_next.py says a day is due. Nobody is present: never ask questions.

OFFICE = C:\Users\hrizvi\Downloads\Git\groundedops\.claude\worktrees\agent-org
REPO   = C:\Users\hrizvi\Downloads\Git\groundedops
OPS    = C:\Users\hrizvi\groundedops-ops

0. Move the session to OFFICE with the change_directory tool.
1. Run `"<REPO>/.venv/Scripts/python.exe" "<OFFICE>/tools/office_diary.py" --due`, adding `--today` when the dispatcher's why is "you asked for it now" (the CEO's Do it now button: today's entry so far; it is written again once the day ends). NONE: stop with the line "Diary up to date."
2. For each date it prints, oldest first:
   run `"<REPO>/.venv/Scripts/python.exe" "<OFFICE>/tools/office_diary.py" --facts <date>` and keep its output;
   MODEL PICK (agent product-owner, job diary, inputs: the facts, already in the prompt), then spawn the product owner with it: Agent tool with subagent_type "product-owner"; if not listed, general-purpose told to read OFFICE/.claude/agents/product-owner.md and follow it exactly.
   Prompt: "Diary. Date = <date>. OPS = <OPS>. Facts:\n<the facts output>"
3. Finish with one line per date: "<date>: <its reply>".

CONSTRAINTS: write only OPS/diary/; never edit, stage or commit files in REPO or OFFICE.
