"""Every change to the product and the office, for the product owner to tick off once tested and approved.

  python tools/change_list.py --ops <OPS> --repo <REPO> --office <OFFICE>     scan, then print the CHANGES line
  python tools/change_list.py --ops <OPS> tick <key> "<what tested it, and why it is approved>"
  python tools/change_list.py --selftest

A scan reads every commit on the branch checked out in REPO (the product line) and in OFFICE
since the last scan, first-parent only, plus every scheduled office task whose instructions
changed. A merge of a Dev branch (agents/dev-<id>) comes in ticked: the gate tested it and the
product owner marked it READY TO MERGE before office_merge.py merged it, Mo's sweeps included.
Everything else (a Claude session's commit or merge, a task prompt edited by hand) comes in
open. The product owner ticks it in the next triage; the office page shows what is still open,
so nobody pushes an unchecked change. The office's own PENDING.md commits are left out.

State: OPS/state/changes.json. The list the product owner reads: OPS/state/changes.md. A branch
or task seen for the first time is only recorded, so the list starts from that scan.

A session that tested its own change reports it in OPS/state/checks/<name>.md: a line
"Covers: <key> <key> ..." (the keys this list shows, or longer commit ids) and, below it, what
it ran and what came out. The list shows "checks reported: <name>.md" beside each change a report
covers, so the product owner weighs that evidence instead of ordering the same checks again.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

TASKS = r"C:/Users/hrizvi/.claude/scheduled-tasks"
TEST_RUN = re.compile(r"^\d{8}[a-z]?$")   # as ops_pivot.TEST_RUN: Customer's runs, never a gate copy
DEV_MERGE = re.compile(r"^Merge branch 'agents/dev-(D-\d{8}-\d+)")
OWN = "docs(pending): the product owner's plan"   # pending_plan.py's commit
RECENT = 14 * 86400   # ticked changes stay on the list this long


def git(path, *args):
    return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, encoding="utf-8").stdout.strip()


def load(ops):
    try:
        with open(os.path.join(ops, "state", "changes.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"heads": {}, "tasks": {}, "items": []}


def save(ops, st):
    os.makedirs(os.path.join(ops, "state"), exist_ok=True)
    p = os.path.join(ops, "state", "changes.json")
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    os.replace(p + ".tmp", p)


def commits(where, path, st):
    """Items for the first-parent commits since the last scan of this checkout's branch."""
    branch, head = git(path, "rev-parse", "--abbrev-ref", "HEAD"), git(path, "rev-parse", "HEAD")
    key, old = f"{where}:{branch}", st["heads"].get(f"{where}:{branch}")
    st["heads"][key] = head
    if not old or old == head:
        return []
    if subprocess.run(["git", "-C", path, "merge-base", "--is-ancestor", old, head]).returncode:
        old = git(path, "merge-base", old, head)   # history was rewritten: list from where the two meet
    out = []
    for line in git(path, "log", "--first-parent", "--reverse", "--format=%H%x1f%P%x1f%ct%x1f%s", f"{old}..{head}").splitlines():
        sha, parents, when, subject = line.split("\x1f", 3)
        if subject.startswith(OWN):
            continue
        parents = parents.split()
        base = parents[0] if parents else sha + "^"
        files = git(path, "diff", "--name-only", base, sha).splitlines()
        item = {"key": sha[:8], "where": where, "branch": branch, "what": subject, "when": int(when),
                "files": files[:6] + ([f"and {len(files) - 6} more"] if len(files) > 6 else []), "ticked": False}
        wo = DEV_MERGE.match(subject)
        if wo:
            item.update(ticked=True, ticked_at=int(time.time()),
                        note=f"work order {wo.group(1)}: gate passed and READY TO MERGE before office_merge.py merged it")
        elif len(parents) > 1:
            item["what"] += f" ({len(git(path, 'log', '--no-merges', '--format=%h', f'{base}..{sha}').split())} commits)"
        out.append(item)
    return out


def tasks(tasks_dir, st):
    """Items for the office's scheduled tasks whose instructions (the text after the front matter) changed."""
    out = []
    for d in sorted(glob.glob(os.path.join(tasks_dir, "groundedops-office-*"))):
        p = os.path.join(d, "SKILL.md")
        try:
            body = open(p, encoding="utf-8").read().split("\n---\n", 1)[-1]
        except OSError:
            continue
        tid, h = os.path.basename(d), hashlib.sha256(body.encode("utf-8")).hexdigest()[:10]
        old, st["tasks"][tid] = st["tasks"].get(tid), h
        if old and old != h:
            out.append({"key": f"task:{tid}:{h}", "where": "office", "branch": "scheduled task",
                        "what": f"Scheduled task {tid}: its instructions changed", "when": int(os.path.getmtime(p)),
                        "files": [p], "ticked": False})
    return out


def runs_after(ops, when):
    """The finished test runs (OPS/runs/<stamp>/DONE) that ended after this change."""
    done = [os.path.join(ops, "runs", d, "DONE") for d in os.listdir(os.path.join(ops, "runs"))
            if TEST_RUN.match(d)] if os.path.isdir(os.path.join(ops, "runs")) else []
    return sorted(os.path.basename(os.path.dirname(p)) for p in done if os.path.exists(p) and os.path.getmtime(p) > when)


def reported(ops):
    """{covered key or commit id: [report file names]} from OPS/state/checks/*.md "Covers:" lines."""
    out = {}
    for p in sorted(glob.glob(os.path.join(ops, "state", "checks", "*.md"))):
        try:
            text = open(p, encoding="utf-8").read()
        except OSError:
            continue
        for m in re.finditer(r"^Covers:(.*)$", text, re.M):
            for k in m.group(1).replace(",", " ").split():
                out.setdefault(k, []).append(os.path.basename(p))
    return out


def render(ops, st, now):
    day = lambda t: datetime.datetime.fromtimestamp(t).strftime("%d %b %H:%M")
    rep = reported(ops)
    # a report may name the list's 8-character key or a longer commit id
    checked = lambda key: sorted({n for k, ns in rep.items() if k.startswith(key) or key.startswith(k) for n in ns})
    lines = ["# Changes to tick", "",
             "Written by tools/change_list.py. Tick a change once it is tested and you approve it:",
             '`python tools/change_list.py --ops <OPS> tick <key> "<what tested it, and why it is approved>"`.', "",
             "## Open", ""]
    for i in (i for i in st["items"] if not i["ticked"]):
        r = runs_after(ops, i["when"])
        lines.append(f"- [ ] `{i['key']}` | {i['where']} ({i['branch']}) | {i['what']} | {day(i['when'])}"
                     f" | files: {', '.join(i['files']) or 'none'} | test runs since: {', '.join(r[:3]) or 'none yet'}"
                     f" | checks reported: {', '.join(checked(i['key'])) or 'none'}")
    lines += ["", "## Ticked in the last 14 days", ""]
    lines += [f"- [x] `{i['key']}` | {i['where']} | {i['what']} | {i.get('note', '')}" for i in st["items"]
              if i["ticked"] and now - i.get("ticked_at", i["when"]) < RECENT]
    with open(os.path.join(ops, "state", "changes.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def scan(ops, repos, tasks_dir=TASKS, now=None):
    st, now = load(ops), now or time.time()
    new = [i for where, path in repos for i in commits(where, path, st)] + tasks(tasks_dir, st)
    seen = {i["key"] for i in st["items"]}
    st["items"] += [i for i in new if i["key"] not in seen]
    st["items"] = [i for i in st["items"] if not i["ticked"] or now - i.get("ticked_at", i["when"]) < 4 * RECENT]
    save(ops, st)
    render(ops, st, now)
    return f"CHANGES {open_count(ops)} open ({sum(not i['ticked'] for i in new)} new); list: {os.path.join(ops, 'state', 'changes.md')}"


def open_count(ops):
    return sum(not i["ticked"] for i in load(ops)["items"])


def tick(ops, key, note):
    st = load(ops)
    for i in st["items"]:
        if i["key"] == key and not i["ticked"]:
            i.update(ticked=True, ticked_at=int(time.time()), note=note)
            save(ops, st)
            render(ops, st, time.time())
            return f"TICKED {key}"
    return f"NO OPEN CHANGE {key}"


def selftest():
    with tempfile.TemporaryDirectory() as tmp:
        ops, product, office, tdir = (os.path.join(tmp, d) for d in ("ops", "product", "office", "tasks"))
        g = lambda p, *a: subprocess.run(["git", "-C", p, "-c", "user.email=t@t", "-c", "user.name=t", *a],
                                         capture_output=True, check=True)
        w = lambda p, s: open(p, "w", encoding="utf-8").write(s)

        def commit(p, name, msg):
            w(os.path.join(p, name), msg)
            g(p, "add", name), g(p, "commit", "-qm", msg)
        for p in (product, office):
            os.makedirs(p), g(p, "init", "-q", "-b", "main"), commit(p, "a.txt", "start")
        os.makedirs(os.path.join(tdir, "groundedops-office-dev"))
        task = os.path.join(tdir, "groundedops-office-dev", "SKILL.md")
        w(task, "---\nname: x\n---\nDo the work.\n")
        repos = [("product", product), ("office", office)]
        assert scan(ops, repos, tdir).startswith("CHANGES 0 open (0 new)"), "the first scan only records where things stand"

        commit(product, "b.txt", "fix: a session's own commit")
        g(product, "checkout", "-qb", "agents/dev-D-20261101-01"), commit(product, "c.txt", "feat: Dev's fix")
        g(product, "checkout", "-q", "main"), g(product, "merge", "-q", "--no-ff", "agents/dev-D-20261101-01", "-m",
                                                 "Merge branch 'agents/dev-D-20261101-01' (D-20261101-01: a fix)")
        g(product, "checkout", "-qb", "claude/x"), commit(product, "d.txt", "one"), commit(product, "e.txt", "two")
        g(product, "checkout", "-q", "main"), g(product, "merge", "-q", "--no-ff", "claude/x", "-m", "Merge branch 'claude/x'")
        commit(product, "PENDING.md", OWN + ", 2026-11-01")
        commit(office, "t.py", "feat(office): a tool")
        w(task, "---\nname: x\n---\nDo the work twice.\n")
        os.makedirs(os.path.join(ops, "runs", "20261101")), os.makedirs(os.path.join(ops, "runs", "20261101-gate"))
        for d in ("20261101", "20261101-gate"):
            w(os.path.join(ops, "runs", d, "DONE"), "")
            os.utime(os.path.join(ops, "runs", d, "DONE"), (time.time() + 60,) * 2)

        assert scan(ops, repos, tdir).startswith("CHANGES 4 open (4 new)"), open(os.path.join(ops, "state", "changes.md")).read()
        st, md = load(ops), open(os.path.join(ops, "state", "changes.md"), encoding="utf-8").read()
        assert [i["what"] for i in st["items"] if i["ticked"]] == ["Merge branch 'agents/dev-D-20261101-01' (D-20261101-01: a fix)"]
        assert "Merge branch 'claude/x' (2 commits)" in md and "files: d.txt, e.txt" in md, md
        assert OWN not in md and "20261101-gate" not in md and "test runs since: 20261101" in md, md
        assert "Scheduled task groundedops-office-dev: its instructions changed" in md, md
        key = next(i["key"] for i in st["items"] if i["what"] == "fix: a session's own commit")
        sha = git(product, "log", "--format=%H", "-1", "--", "b.txt")
        os.makedirs(os.path.join(ops, "state", "checks"))
        w(os.path.join(ops, "state", "checks", "s1.md"), f"# Session checks\nCovers: {sha}\nFull suite: 400 passed.\n")
        scan(ops, repos, tdir)
        md = open(os.path.join(ops, "state", "changes.md"), encoding="utf-8").read()
        assert next(ln for ln in md.splitlines() if key in ln).endswith("checks reported: s1.md"), md
        assert md.count("checks reported: none") == 3, "a report covers only the commits it names"
        assert tick(ops, key, "run 20261101 shows no drop; belongs") == f"TICKED {key}" and open_count(ops) == 3
        assert tick(ops, key, "again") == f"NO OPEN CHANGE {key}", "a change is ticked once"
        assert scan(ops, repos, tdir).startswith("CHANGES 3 open (0 new)"), "a second scan adds nothing"
        assert f"- [x] `{key}`" in open(os.path.join(ops, "state", "changes.md"), encoding="utf-8").read()
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--repo", default="C:/Users/hrizvi/Downloads/Git/groundedops")
    ap.add_argument("--office", default="C:/Users/hrizvi/Downloads/Git/groundedops/.claude/worktrees/agent-org")
    ap.add_argument("cmd", nargs="*", help='tick <key> "<note>"; nothing = scan')
    o = ap.parse_args()
    if o.cmd[:1] == ["tick"] and len(o.cmd) == 3:
        print(tick(o.ops, o.cmd[1], o.cmd[2]))
    elif o.cmd:
        sys.exit('usage: change_list.py [--ops OPS] tick <key> "<note>"')
    else:
        print(scan(o.ops, [("product", o.repo), ("office", o.office)]))
