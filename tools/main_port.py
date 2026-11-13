"""Weekly: offer the product line's fixes to main (the general base for every customer); port the ones the CEO approves.

  python tools/main_port.py --ops OPS --repo REPO offer    NOT DUE, NONE, or one line per new fix: <sha8> <subject> | <files>
  python tools/main_port.py --ops OPS --repo REPO apply    ports every CEO-approved port item not yet done: one line each
  python tools/main_port.py --selftest

The CEO, 2026-11-12: once a week the product owner asks which of the week's fixes on the product line
(experimental/v17.3, Innovative Technology's) should go to main as well. offer lists the first-parent commits
since the last offer that are not on main yet, and starts the next week's clock. The product owner files the
fixes that help any customer as one item (.claude/agents/product-owner.md "Port to main"):

  ## D-<day>-NN | Port this week's fixes to main
  - Assigned: port
  - Port: <sha> <sha> ...
  - Verdict: NEEDS CEO
  - [ ] CEO approved

Once the CEO ticks it, apply picks those commits onto GitHub's main (origin/main) in a scratch worktree (a Dev merge as its
first-parent diff, with -x so the next offer knows it is there), skips any that conflict, runs the full suite on
main's code with them, and pushes main only if it passes: a fix that does not fit main's code never reaches it.
One attempt per approval. It reports to OPS/inbox/port/<id>-done.md, which wakes the product owner to close it.
This is the only thing in the office that pushes.

State: OPS/state/main_port.json {"since": <sha>, "asked": <epoch>, "done": {<id>: <result>}}. No file: the port is
not set up for this office, and nothing is ever due.
"""
import argparse
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ops_ledger import decisions  # noqa: E402

LINE, MAIN = "experimental/v17.3", "main"
WEEK = 7 * 86400
OWN = re.compile(r"^docs\(pending\)")   # the product owner's plan commits: Innovative Technology's diary, never main's


def git(path, *a):
    return subprocess.run(["git", "-C", path, *a], capture_output=True, text=True, encoding="utf-8", errors="replace")


def _state(ops):
    return os.path.join(ops, "state", "main_port.json")


def load(ops):
    try:
        with open(_state(ops), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(ops, st):
    with open(_state(ops) + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    os.replace(_state(ops) + ".tmp", _state(ops))


def due(ops, now=None):
    st = load(ops)
    return st is not None and (now or time.time()) - st.get("asked", 0) >= WEEK


def ports(ops):
    """{id: [sha, ...]} from the latest "- Port:" line of each item in the decisions files."""
    out = {}
    for p in sorted(glob.glob(os.path.join(ops, "decisions", "*.md"))):
        for block in re.split(r"(?m)^## ", open(p, encoding="utf-8", errors="replace").read())[1:]:
            m, line = re.match(r"(D-\d{8}-\d+)", block), re.search(r"(?m)^- Port:\s*(.+?)\s*$", block)
            if m and line:
                out[m.group(1)] = re.findall(r"\b[0-9a-f]{7,40}\b", line.group(1))
    return out


def approved(ops):
    """{id: [sha, ...]} for the port items the CEO ticked that apply has not run yet."""
    st = load(ops)
    if st is None:
        return {}
    items = decisions(ops)
    return {i: s for i, s in ports(ops).items() if i not in st.get("done", {}) and items.get(i, {}).get("ceo_approved")
            and (items[i]["history"] or [""])[-1] not in ("DONE", "REJECT", "DEFER")}


def offer(ops, repo, now=None):
    st = load(ops)
    if st is None:
        return ["NOT SET UP (no OPS/state/main_port.json)"]
    if not due(ops, now):
        return [f"NOT DUE (last offer {datetime.date.fromtimestamp(st['asked'])})"]
    head = git(repo, "rev-parse", LINE).stdout.strip()
    since = st.get("since") or git(repo, "merge-base", MAIN, LINE).stdout.strip()
    git(repo, "fetch", "-q", "origin", MAIN)
    there = set(re.findall(r"cherry picked from commit ([0-9a-f]{40})",
                           git(repo, "log", MAIN, "origin/" + MAIN, "--format=%b").stdout))
    out = []
    for line in git(repo, "log", "--first-parent", "--reverse", "--format=%H%x1f%P%x1f%s", f"{since}..{head}").stdout.splitlines():
        sha, parents, subject = line.split("\x1f", 2)
        parents = parents.split()
        # a merge whose branch tip was picked by hand (a session's fix) is on main already
        if OWN.match(subject) or sha in there or (len(parents) > 1 and parents[1] in there):
            continue
        files = [f for f in git(repo, "diff", "--name-only", parents[0], sha).stdout.split() if f != "PENDING.md"]
        if files:
            out.append(f"{sha[:8]} {subject} | {', '.join(files[:8])}{' ...' if len(files) > 8 else ''}")
    st.update(since=head, asked=int(now or time.time()))
    save(ops, st)
    return out or ["NONE"]


def _port(ops, repo, i, shas, test_cmd):
    """(result line, note body) for one approved item."""
    wt = os.path.join(ops, "scratch", f"port-{i}")
    git(repo, "worktree", "remove", "--force", wt)
    shutil.rmtree(wt, ignore_errors=True)
    # GitHub's main, never the local branch: a session's unpushed commits on local main are not ours to push
    # (2026-11-12: a cleanup session had 10 there)
    git(repo, "fetch", "-q", "origin", MAIN)
    base = git(repo, "rev-parse", "origin/" + MAIN).stdout.strip()
    r = git(repo, "worktree", "add", "--detach", wt, base)
    if r.returncode:
        return f"NOT PUSHED {i}: no scratch worktree ({r.stderr.strip()[:200]})", ""
    picked, skipped = [], []
    try:
        for s in shas:
            full = git(repo, "rev-parse", "--verify", "-q", s + "^{commit}").stdout.strip()
            if not full:
                skipped.append(f"{s}: no such commit")
                continue
            merge = len(git(repo, "rev-list", "--parents", "-n", "1", full).stdout.split()) > 2
            r = git(wt, "cherry-pick", "-x", *(["-m", "1"] if merge else []), full)
            if r.returncode:
                empty = "empty" in r.stdout + r.stderr
                git(wt, "cherry-pick", "--abort")
                skipped.append(f"{s}: " + ("already on main" if empty else "conflicts with main's code; port it by hand"))
            else:
                picked.append(s)
        if not picked:
            return f"NOT PUSHED {i}: nothing picked", "Pushed to main: no, nothing could be picked\n" + "".join(f"Skipped {x}\n" for x in skipped)
        src = os.path.join(wt, "src")
        t = subprocess.run(test_cmd or [os.path.join(repo, ".venv", "Scripts", "python.exe"), "run_tests.py"], cwd=src,
                           env=dict(os.environ, PYTHONPATH=src, HF_HUB_OFFLINE="1"),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        log = os.path.join(ops, "state", f"port-{i}-suite.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "w", encoding="utf-8") as f:
            f.write(t.stdout + "\n--- stderr ---\n" + t.stderr)
        out = t.stdout.strip().splitlines()
        summary = next((l.strip() for l in reversed(out) if re.search(r"\d+/\d+ passed", l)), f"exit code {t.returncode}")
        fails = [l.strip() for l in out if l.strip().startswith("FAIL")][:15]
        tail = (fails + [""] if fails else []) + (out[-30:] if out else t.stderr.strip().splitlines()[-30:])
        if t.returncode:
            pushed, why = False, f"the suite failed on main with them ({summary}" + (f"; first failure: {fails[0]})" if fails else ")")
        else:
            head = git(wt, "rev-parse", "HEAD").stdout.strip()
            p = git(wt, "push", "origin", "HEAD:refs/heads/main")
            pushed = p.returncode == 0
            why = f"{base[:8]}..{head[:8]}" if pushed else f"push refused ({(p.stderr.strip().splitlines() or ['?'])[-1]})"
            if pushed and git(repo, "merge-base", "--is-ancestor", MAIN, base).returncode == 0:
                git(repo, "update-ref", "refs/heads/" + MAIN, head)   # local main had nothing of its own: it follows
            elif pushed:
                why += "; local main has unpushed commits of its own, left as it was (merge origin/main into it)"
        body = (f"Pushed to main: {'yes, ' if pushed else 'no, '}{why}\n" + "".join(f"Picked {x}\n" for x in picked)
                + "".join(f"Skipped {x}\n" for x in skipped) + f"Tests on main with them: {summary}\n")
        if t.returncode:
            body += "\nThe suite's last lines:\n" + "\n".join("    " + l for l in tail) + f"\nFull suite output: {log}\n"
        line = f"{'PUSHED' if pushed else 'NOT PUSHED'} {i}: {len(picked)} picked, {len(skipped)} skipped; {why}"
        return line, body
    finally:
        git(repo, "worktree", "remove", "--force", wt)
        shutil.rmtree(wt, ignore_errors=True)


def apply(ops, repo, test_cmd=None):
    todo = approved(ops)
    out = []
    for i, shas in todo.items():
        line, body = _port(ops, repo, i, shas, test_cmd)
        os.makedirs(os.path.join(ops, "inbox", "port"), exist_ok=True)
        with open(os.path.join(ops, "inbox", "port", f"{i}-done.md"), "w", encoding="utf-8") as f:
            f.write(f"Status: DONE - {line}\n\n{body}")
        st = load(ops)   # one attempt per approval: a failed port is the product owner's to re-file, not retried every tick
        st.setdefault("done", {})[i] = line
        save(ops, st)
        out.append(line)
    return out or ["NONE"]


def selftest():
    d = tempfile.mkdtemp()
    run = lambda cwd, *a: subprocess.run(a, cwd=cwd, check=True, capture_output=True)
    origin, repo, ops = (os.path.join(d, n) for n in ("origin.git", "repo", "ops"))
    run(d, "git", "init", "-q", "--bare", "-b", "main", origin)
    run(d, "git", "clone", "-q", origin, repo)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        run(repo, "git", "config", k, v)
    w = lambda p, s: (os.makedirs(os.path.dirname(os.path.join(repo, p)), exist_ok=True),
                      open(os.path.join(repo, p), "w").write(s))
    def commit(msg, files):
        for p, s in files.items():
            w(p, s)
        run(repo, "git", "add", "-A")
        run(repo, "git", "commit", "-q", "-m", msg)
        return git(repo, "rev-parse", "HEAD").stdout.strip()
    commit("base", {"src/a.py": "a = 1\n", "src/b.py": "b = 1\n", "PENDING.md": "plan\n"})
    run(repo, "git", "push", "-q", "origin", "main")
    run(repo, "git", "checkout", "-q", "-b", LINE)
    commit("docs(pending): the product owner's plan", {"PENDING.md": "plan 2\n"})
    fix = commit("fix a", {"src/a.py": "a = 2\n"})
    run(repo, "git", "checkout", "-q", "-b", "agents/dev-D-1")
    commit("dev work", {"src/c.py": "c = 1\n"})
    run(repo, "git", "checkout", "-q", LINE)
    run(repo, "git", "merge", "-q", "--no-ff", "agents/dev-D-1", "-m", "Merge branch 'agents/dev-D-1'")
    dev = git(repo, "rev-parse", "HEAD").stdout.strip()
    itl = commit("ITL wording", {"src/b.py": "b = 'ITL'\n"})
    run(repo, "git", "checkout", "-q", MAIN)
    commit("main moved", {"src/b.py": "b = 'general'\n"})
    run(repo, "git", "push", "-q", "origin", "main")
    local = commit("a session's unpushed work", {"src/d.py": "d = 1\n"})
    os.makedirs(os.path.join(ops, "decisions"))
    os.makedirs(os.path.join(ops, "state"))
    os.makedirs(os.path.join(ops, "scratch"))

    assert offer(ops, repo)[0].startswith("NOT SET UP") and not due(ops) and approved(ops) == {}, "no state: off"
    save(ops, {})
    got = offer(ops, repo, now=1_000_000)
    assert [l[:8] for l in got] == [fix[:8], dev[:8], itl[:8]], got   # the plan commit is left out, the merge is one line
    assert "src/c.py" in got[1] and "PENDING" not in " ".join(got), got
    assert offer(ops, repo, now=1_000_000 + 3600)[0].startswith("NOT DUE"), "one offer a week"
    assert offer(ops, repo, now=1_000_000 + WEEK)[0] == "NONE", "the next week starts after the last offer"

    dp = os.path.join(ops, "decisions", "20261112.md")
    open(dp, "w").write(f"## D-20261112-01 | Port this week's fixes to main\n- Assigned: port\n"
                        f"- Port: {fix[:8]} {dev[:8]} {itl[:8]}\n- Verdict: NEEDS CEO\n- [ ] CEO approved\n")
    assert approved(ops) == {}, "not before the CEO's tick"
    open(dp, "a").write("\n## D-20261112-01 | Port this week's fixes to main\n- [x] CEO approved\n")
    assert list(approved(ops)) == ["D-20261112-01"]
    ok = [sys.executable, "-c", "print('  3/3 passed, 0 skipped, 0 failed')"]
    got = apply(ops, repo, test_cmd=ok)
    assert got[0].startswith("PUSHED D-20261112-01: 2 picked, 1 skipped"), got   # the ITL edit conflicts with main's
    note = open(os.path.join(ops, "inbox", "port", "D-20261112-01-done.md")).read()
    assert f"Skipped {itl[:8]}: conflicts" in note and "3/3 passed" in note, note
    shown = git(origin, "show", "main:src/c.py").stdout + git(origin, "show", "main:src/a.py").stdout
    assert shown == "c = 1\na = 2\n", shown
    assert git(origin, "cat-file", "-e", "main:src/d.py").returncode, "a session's unpushed commit on local main is not pushed"
    assert git(repo, "rev-parse", "main").stdout.strip() == local and "left as it was" in got[0], got
    assert approved(ops) == {} and apply(ops, repo, test_cmd=ok) == ["NONE"], "one attempt per approval"
    assert not os.listdir(os.path.join(ops, "scratch")), "the scratch worktree is gone"
    st = load(ops)
    st.update(asked=0, since=None)   # offer again from the split
    save(ops, st)
    got = offer(ops, repo, now=1_000_000)
    assert got == [itl[:8] + " ITL wording | src/b.py"], f"picked ones are not offered again: {got}"

    # a red suite pushes nothing
    run(repo, "git", "checkout", "-q", LINE)
    fix2 = commit("fix a again", {"src/a.py": "a = 3\n"})
    run(repo, "git", "checkout", "-q", MAIN)
    open(dp, "a").write(f"\n## D-20261112-02 | Port\n- Port: {fix2}\n- Verdict: NEEDS CEO\n- [x] CEO approved\n")
    before = git(origin, "rev-parse", "main").stdout
    bad = [sys.executable, "-c", "import sys; print('  FAIL  test_x::test_y'); print('  2/3 passed, 0 skipped, 1 failed'); sys.stderr.write('log line\\n' * 60); raise SystemExit(1)"]
    got = apply(ops, repo, test_cmd=bad)
    assert got[0].startswith("NOT PUSHED D-20261112-02") and "2/3 passed" in got[0], got
    assert git(origin, "rev-parse", "main").stdout == before, "nothing pushed on a red suite"
    assert "test_x::test_y" in got[0], got
    slog = os.path.join(ops, "state", "port-D-20261112-02-suite.log")
    note = open(os.path.join(ops, "inbox", "port", "D-20261112-02-done.md"), encoding="utf-8").read()
    assert "test_x::test_y" in note and slog in note, note
    assert open(slog, encoding="utf-8").read().count("log line") == 60, "full output kept in the log"

    # local main with nothing of its own follows the push
    run(repo, "git", "fetch", "-q", "origin")
    run(repo, "git", "reset", "-q", "--hard", "origin/main")
    open(dp, "a").write(f"\n## D-20261112-03 | Port\n- Port: {fix2}\n- Verdict: NEEDS CEO\n- [x] CEO approved\n")
    got = apply(ops, repo, test_cmd=ok)
    assert got[0].startswith("PUSHED D-20261112-03") and "left as it was" not in got[0], got
    assert git(origin, "rev-parse", "main").stdout == git(repo, "rev-parse", "main").stdout, "local main moved with it"
    shutil.rmtree(d, ignore_errors=True)
    print("selftest ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--repo", default="C:/Users/hrizvi/Downloads/Git/groundedops")
    ap.add_argument("job", nargs="?", choices=("offer", "apply"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.job:
        print("\n".join(offer(a.ops, a.repo) if a.job == "offer" else apply(a.ops, a.repo)))
    else:
        ap.error("offer, apply or --selftest")
