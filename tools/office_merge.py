"""Merge a Dev branch that passed its checks - the CEO's standing instruction (2026-10-08).

  python tools/office_merge.py <id> [--ops ...] [--repo ...]
  python tools/office_merge.py --selftest

Merges agents/dev-<id> only when all of these hold:
  - OPS/inbox/dev/<id>.gate/gate.json says PASS,
  - the product owner's latest verdict for <id> is READY TO MERGE,
  - OPS/inbox/security/review-<id>.md says "Verdict: CLEAR",
  - no uncommitted change in the target checkout touches a file the branch changes.
The target is the checkout the branch is fewest commits ahead of: the main checkout's
branch for product fixes, experimental/agent-org-phase1 (this checkout) for office
fixes. Merges with --no-ff so the merge commit names the branch, py_compiles the merged
.py files and reverts the merge if one fails. Prints MERGED / WAIT <why> / FAILED <why>.
Never pushes.
"""
import argparse
import glob
import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ops_ledger import decisions  # noqa: E402

OFFICE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout.rstrip(), r.stderr.strip()   # rstrip: porcelain lines start with a space


def in_the_way(branch, target):
    """Uncommitted files in the target checkout that the branch also changes: the merge never goes over them."""
    changed = set(git(target, "diff", "--name-only", f"HEAD...{branch}")[1].splitlines())
    dirty = {line[3:].split(" -> ")[-1] for line in git(target, "status", "--porcelain", "--untracked-files=no")[1].splitlines()}
    return sorted(changed & dirty)


def merge(did, ops, repo, office=OFFICE):
    """The product branch, then any office-tooling companion Dev made for the same work order."""
    first = merge_branch(did, f"agents/dev-{did}", ops, repo, office)
    companion = f"agents/office-{did}"
    if not first.startswith("MERGED") or git(repo, "rev-parse", "--verify", "-q", companion)[0]:
        return first
    return first + "\n" + merge_branch(did, companion, ops, repo, office)


def merge_branch(did, branch, ops, repo, office=OFFICE):
    gate = os.path.join(ops, "inbox", "dev", did + ".gate", "gate.json")
    if not os.path.exists(gate) or json.load(open(gate, encoding="utf-8")).get("verdict") != "PASS":
        return "WAIT no gate PASS"
    history = decisions(ops).get(did, {}).get("history") or [""]
    if history[-1] != "READY TO MERGE":
        return f"WAIT the product owner's latest verdict is {history[-1] or 'missing'}, not READY TO MERGE"
    review = os.path.join(ops, "inbox", "security", f"review-{did}.md")
    m = re.search(r"(?mi)^Verdict:\s*(CLEAR|BLOCK)", open(review, encoding="utf-8").read()) if os.path.exists(review) else None
    if not m or m.group(1).upper() != "CLEAR":
        return "WAIT Security " + ("blocked it" if m else "has not reviewed it")
    ahead = {cwd: git(cwd, "rev-list", "--count", f"HEAD..{branch}") for cwd in dict.fromkeys((repo, office))}
    target = min((c for c, r in ahead.items() if r[0] == 0), key=lambda c: int(ahead[c][1]), default=None)
    if target is None:
        return f"FAILED no branch {branch}"
    if ahead[target][1] == "0":
        return f"MERGED {branch} already"
    changed, busy = set(git(target, "diff", "--name-only", f"HEAD...{branch}")[1].splitlines()), in_the_way(branch, target)
    if busy:
        return f"WAIT uncommitted changes in {target} touch {busy}"
    into = git(target, "rev-parse", "--abbrev-ref", "HEAD")[1]
    title = decisions(ops).get(did, {}).get("title", "")
    rc, out, err = git(target, "merge", "--no-ff", branch, "-m",
                       f"Merge branch '{branch}' ({did}: {title})\n\nMerged by the office after a gate PASS and a Security CLEAR "
                       f"(the CEO's standing instruction, 2026-10-08).")
    if rc:
        git(target, "merge", "--abort")
        files = sorted(set(re.findall(r"(?:Merge conflict in|CONFLICT \([^)]*\):.*? in) (\S+)", out + "\n" + err)))
        send_back(did, ops, into, branch, files)
        return f"FAILED merge conflict into {into} in {files or 'files git did not name'}; sent back to Dev for a rebase"
    bad = []
    for f in sorted(p for p in changed if p.endswith(".py") and os.path.exists(os.path.join(target, p))):
        try:
            py_compile.compile(os.path.join(target, f), doraise=True)
        except py_compile.PyCompileError:
            bad.append(f)
    if bad:
        git(target, "revert", "-m", "1", "--no-edit", "HEAD")
        return f"FAILED {bad} do not compile after the merge; the merge was reverted"
    return f"MERGED {branch} into {into} ({target})"


def send_back(did, ops, into, branch, files):
    """Another fix merged first and now conflicts (parallel Dev lanes make this common): hand the
    item back the way the product owner does, renaming the hand-back, gate and Security review to
    the next free number so Dev rebuilds it and the gate and Security run again on the result."""
    base = os.path.join(ops, "inbox", "dev", did)
    review = os.path.join(ops, "inbox", "security", f"review-{did}.md")
    n = 1 + max([int(m.group(1)) for p in glob.glob(base + ".*") + glob.glob(review + ".*")
                 for m in [re.search(r"\.(\d+)$", p)] if m] or [0])
    for src in (base + ".md", base + ".gate", review):
        if os.path.exists(src):
            os.rename(src, f"{src}.{n}")
    title = decisions(ops).get(did, {}).get("title", "")
    with open(os.path.join(ops, "decisions", time.strftime("%Y%m%d") + ".md"), "a", encoding="utf-8") as f:
        f.write(f"\n\n## {did} | {title}\n- Verdict: SENT BACK\n\nSent back by the office merge: another fix merged into "
                f"{into} first and {branch} now conflicts with it in {', '.join(files) or 'files git did not name'}. "
                f"Rebase {branch} onto {into}, resolve the conflicts keeping both fixes, run the full suite, then CHECK "
                f"again. Nothing else changes; the gate and Security run again on the rebased branch.\n")


def selftest():
    os.environ.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    with tempfile.TemporaryDirectory() as tmp:
        repo, ops = os.path.join(tmp, "repo"), os.path.join(tmp, "ops")
        w = lambda p, s: (os.makedirs(os.path.dirname(p), exist_ok=True), open(p, "w", encoding="utf-8").write(s))
        run = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, check=True)
        os.makedirs(repo)
        run("init", "-q", "-b", "main")
        w(os.path.join(repo, "a.py"), "x = 1\n")
        run("add", "."), run("commit", "-qm", "base")
        for did, f, body in (("D-20261008-91", "b.py", "y = 2\n"), ("D-20261008-92", "a.py", "x = 3\n"), ("D-20261008-93", "c.py", "def (\n"),
                             ("D-20261008-94", "b.py", "y = 5\n")):   # 94 built in parallel with 91, same file
            run("checkout", "-qb", f"agents/dev-{did}", "main")
            w(os.path.join(repo, f), body)
            run("add", "."), run("commit", "-qm", did)
            w(os.path.join(ops, "inbox", "dev", did + ".gate", "gate.json"), '{"verdict": "PASS"}')
            w(os.path.join(ops, "inbox", "security", f"review-{did}.md"), "# Review\nVerdict: CLEAR\n")
        run("checkout", "-qb", "agents/office-D-20261008-91", "main")                  # its tooling companion
        w(os.path.join(repo, "tools", "t.py"), "z = 4\n")
        run("add", "."), run("commit", "-qm", "companion")
        run("checkout", "-q", "main")
        w(os.path.join(ops, "decisions", "20261008.md"), "".join(
            f"## {d} | t\n- Verdict: NEEDS CEO\n- [x] CEO approved\n\n" for d in ("D-20261008-91", "D-20261008-92", "D-20261008-93", "D-20261008-94")))
        assert merge("D-20261008-91", ops, repo, repo).startswith("WAIT the product owner"), "needs READY TO MERGE"
        w(os.path.join(ops, "decisions", "20261009.md"), "".join(
            f"## {d} | t\n- Verdict: READY TO MERGE\n\n" for d in ("D-20261008-91", "D-20261008-92", "D-20261008-93", "D-20261008-94")))
        w(os.path.join(ops, "inbox", "dev", "D-20261008-94.md"), "Status: READY\n")
        w(os.path.join(repo, "a.py"), "x = 1  # someone's work in progress\n")         # dirty, not in D-1
        r = merge("D-20261008-91", ops, repo, repo)
        assert r.count("MERGED") == 2 and os.path.exists(os.path.join(repo, "tools", "t.py")), r   # companion too
        assert merge("D-20261008-91", ops, repo, repo) == "MERGED agents/dev-D-20261008-91 already\nMERGED agents/office-D-20261008-91 already"
        assert "work in progress" in open(os.path.join(repo, "a.py")).read(), "their edit is untouched"
        r = merge("D-20261008-94", ops, repo, repo)
        assert r.startswith("FAILED merge conflict") and "b.py" in r, r
        assert open(os.path.join(repo, "b.py")).read() == "y = 2\n", "the conflicting merge was aborted"
        dev = os.path.join(ops, "inbox", "dev")
        assert sorted(os.listdir(dev)) == ["D-20261008-91.gate", "D-20261008-92.gate", "D-20261008-93.gate", "D-20261008-94.gate.1", "D-20261008-94.md.1"], os.listdir(dev)
        assert os.path.exists(os.path.join(ops, "inbox", "security", "review-D-20261008-94.md.1")), "Security reviews the rebase again"
        assert "Verdict: SENT BACK" in open(os.path.join(ops, "decisions", time.strftime("%Y%m%d") + ".md")).read()
        assert merge("D-20261008-92", ops, repo, repo).startswith("WAIT uncommitted"), "never merges over their edit"
        assert merge("D-20261008-93", ops, repo, repo).startswith("FAILED"), "broken code is reverted"
        assert not os.path.exists(os.path.join(repo, "c.py")), "the revert removed it"
        w(os.path.join(ops, "inbox", "security", "review-D-20261008-92.md"), "Verdict: BLOCK\n")
        assert merge("D-20261008-92", ops, repo, repo) == "WAIT Security blocked it"
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("id", nargs="?")
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--repo", default=os.path.normpath(os.path.join(OFFICE, "..", "..", "..")))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest or not a.id:
        return selftest() if a.selftest else ap.error("give a work order id")
    print(merge(a.id, a.ops, a.repo))


if __name__ == "__main__":
    main()
