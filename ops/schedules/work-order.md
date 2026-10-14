WORK ORDER (when the dispatcher's why is "work order <id>": do this instead of the numbered steps above, after step 0)

1. Status <dept> working "<the work order's title, in plain words>". Event po -> <dept> "Work order".
2. MODEL PICK (agent <dept>, job "work order: <title>", inputs the order's sections in OPS/decisions/*.md), then spawn the department with it.
   Prompt: "Job WORK ORDER <id>. REPO = <REPO>. OFFICE = <OFFICE>. OPS = <OPS>. Today = <YYYYMMDD>. Read every '## <id>' section in OPS/decisions/*.md: the first says what to do and why, later ones may change it. Do only that, within your own tools and rules. Then write OPS/inbox/<dept>/<id>-done.md: first line 'Status: DONE' or 'Status: BLOCKED - <why, in plain words>', then what you did, the files you wrote, and anything left for someone else. If the order needs a tool or access you do not have, write BLOCKED and say which; never work around it."
3. Status <dept> idle with last = its first line. Event <dept> -> po "Work order done" or "Work order blocked". The product owner closes it at its next triage.
4. Finish with the department's final message.
