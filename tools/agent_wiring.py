"""Did each office run really use the model and effort its agent file asks for?

  python tools/agent_wiring.py [--days 8] [--office .] [session.jsonl ...]
  python tools/agent_wiring.py --selftest

Reads the Claude Code transcripts of the scheduled office runs: each run is
~/.claude/projects/<groundedops...>/<session>.jsonl whose first line names the
groundedops-office-<task> scheduled task, and its subagents sit in
<session>/subagents/agent-*.jsonl (+ .meta.json). Every assistant line records
the model and the effort that actually ran, so this compares those with the
`model:` and `effort:` lines in OFFICE/.claude/agents/<agent>.md.

A department spawned as "general-purpose" is always a mismatch: its office
agent type was not loaded, so the file's effort and tool list were ignored and
the subagent inherited the orchestrator's effort instead.
"""
import argparse
import glob
import json
import os
import re
import sys
import tempfile
import time

TASK = re.compile(r'<scheduled-task name=\\?"groundedops-office-([a-z-]+)')
PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")


def wanted(office, agent):
    """(model, effort) from the agent file's front matter; (None, None) if there is no file."""
    try:
        head = open(os.path.join(office, ".claude", "agents", agent + ".md"), encoding="utf-8").read().split("---")[1]
    except (OSError, IndexError):
        return None, None
    get = lambda key: (re.search(rf"^{key}:\s*(\S+)", head, re.M) or [None, None])[1]
    return get("model"), get("effort")


def ran(path):
    """Sorted distinct models and efforts on the assistant lines of one transcript."""
    models, efforts = set(), set()
    for line in open(path, encoding="utf-8"):
        if '"type":"assistant"' in line:
            d = json.loads(line)
            models.add((d.get("message") or {}).get("model") or "?")
            efforts.add(d.get("effort") or "?")
    return sorted(models), sorted(efforts)


def check(session, office):
    """One row per department subagent of one run: (task, agent type, wanted, got, ok)."""
    with open(session, encoding="utf-8") as f:
        m = TASK.search(f.readline())
    task = m.group(1) if m else None
    rows = []
    for meta_path in sorted(glob.glob(os.path.join(session[:-len(".jsonl")], "subagents", "*.meta.json"))):
        meta = json.load(open(meta_path, encoding="utf-8"))
        kind = meta.get("agentType")
        if meta.get("spawnDepth", 1) != 1 or (kind != "general-purpose" and not os.path.exists(
                os.path.join(office, ".claude", "agents", f"{kind}.md"))):
            continue                                    # the department's own helpers, not the department
        model, effort = wanted(office, kind if kind != "general-purpose" else (task or ""))
        models, efforts = ran(meta_path[:-len(".meta.json")] + ".jsonl")
        ok = (kind != "general-purpose" and model is not None
              and all(model in x for x in models) and efforts == [effort])
        rows.append((task or "?", kind, f"{model}/{effort}", f"{','.join(models)}/{','.join(efforts)}", ok))
    return rows


def office_runs(days):
    cutoff = time.time() - days * 86400
    for path in glob.glob(os.path.join(PROJECTS, "*groundedops*", "*.jsonl")):
        if os.path.getmtime(path) >= cutoff:
            with open(path, encoding="utf-8") as f:
                if TASK.search(f.readline()):
                    yield path


def selftest():
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, ".claude", "agents"))
        open(os.path.join(tmp, ".claude", "agents", "product-owner.md"), "w").write(
            "---\nname: product-owner\nmodel: fable\neffort: high\n---\nbody\n")
        run = os.path.join(tmp, "s.jsonl")
        open(run, "w").write(json.dumps({"content": '<scheduled-task name="groundedops-office-product-owner">'}) + "\n")
        sub = os.path.join(tmp, "s", "subagents")
        os.makedirs(sub)
        line = lambda model, effort: json.dumps({"type": "assistant", "effort": effort, "message": {"model": model}},
                                                separators=(",", ":")) + "\n"     # transcripts are compact
        for name, kind, effort in (("a", "product-owner", "high"), ("b", "general-purpose", "high"),
                                   ("c", "product-owner", "medium"), ("d", "Explore", "low")):
            json.dump({"agentType": kind, "spawnDepth": 1}, open(os.path.join(sub, name + ".meta.json"), "w"))
            open(os.path.join(sub, name + ".jsonl"), "w").write(line("claude-fable-5-1", effort))
        assert [r[4] for r in check(run, tmp)] == [True, False, False], check(run, tmp)
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--office", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--days", type=float, default=8)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("sessions", nargs="*")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    bad = 0
    for session in a.sessions or sorted(office_runs(a.days)):
        for task, kind, want, got, ok in check(session, a.office):
            bad += not ok
            print(f"{'OK      ' if ok else 'MISMATCH'} {task:15} {kind:16} wanted {want:14} ran {got}  {os.path.basename(session)}")
    print(f"{bad} mismatch(es)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
