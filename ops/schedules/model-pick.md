MODEL PICK (before every department spawn; never fail a run over it)

1. Spawn the picker: Agent tool with subagent_type "model-picker" and model "haiku" (if not listed, general-purpose with model "haiku", told to read OFFICE/.claude/agents/model-picker.md and follow it exactly).
   Prompt: "Agent: <the agent you are about to spawn>. Job: <the job word and one line on it, e.g. 'triage: 3 new findings, gates run: D-20261012-03'>. Inputs: <the paths that agent will read>."
2. Run `"<REPO>/.venv/Scripts/python.exe" "<OFFICE>/tools/model_pick.py" <agent> <job word> "<the picker's reply line>"`.
   It prints "MODEL <model> EFFORT <effort>", kept inside the agent's own floor and ceiling, and logs the pick.
3. Spawn the department with the Agent tool's model and effort set to those two values. The picked model also replaces the model a "general-purpose with model ..." fallback line names. If the picker or the tool fails, spawn it as before, with neither set.
One pick per spawn: a job that spawns the same agent several times in one step (Customer's WRITE-PART per product) picks once and reuses it.
