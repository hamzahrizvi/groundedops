"""Takes Security's item titles out of PENDING.md in every commit not pushed yet. Run right before the first push.

  python tools/scrub_pending_history.py [--ops C:/Users/hrizvi/groundedops-ops] [--repo <REPO>]   # dry run
  python tools/scrub_pending_history.py --apply
  python tools/scrub_pending_history.py --selftest

Until D-20261010-25, tools/pending_plan.py wrote Security's items into PENDING.md by name, and the
repository is public. This rewrites every local commit that is on no remote branch: PENDING.md is
scrubbed with pending_plan.masked() (rows and the "Finished or turned down" line), nothing else in
the tree changes, and author, dates and messages stay byte-identical (signatures are dropped).
Every refs/heads/* branch that contained such a commit is moved, after checks: no leak left, the
tip differs only in PENDING.md, and each branch still merges into the checked-out one as before.

--apply moves the branches in one compare-and-swap transaction (anything that moved since the
check fails it whole; run again), keeps the old tips under refs/pre-scrub/<stamp>/, and refreshes
PENDING.md in every worktree on a moved branch that had no change of its own to it.
Also prints REVIEW lines: tools/ lines on unpushed branches, not yet on any remote, that share half (at least two) of
the key words of an open Security item's title (D-20261021-03).
"""
import argparse
import datetime
import glob
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ops_ledger import NOTES, _read, git_sets, ledger  # noqa: E402
from pending_plan import masked  # noqa: E402

PATH = "PENDING.md"


def security_cards(ops):
    """Security's decisions, plus every Security finding: one cited by a decision today was its own row once."""
    cards = [c for c in ledger(ops)["cards"] if c["security"]]
    for path in sorted(glob.glob(os.path.join(ops, "inbox", "security", "*.md"))):
        name = os.path.basename(path)[:-3]
        if not NOTES.search(os.path.basename(path)):
            m = re.search(r"(?m)^#\s+(.+)$", _read(path))
            cards.append({"id": f"security/{name}", "title": (m.group(1) if m else name).strip(), "security": True})
    return cards


def scrub(text, cards):
    for c in cards:
        m = masked(c)
        text = re.sub(rf"(?m)^\| {re.escape(c['id'])} \| [^|\n]* \|", lambda _: f"| {m['id']} | {m['title']} |", text)
        text = text.replace(f"{c['id']} {c['title']} (", f"{m['id']} {m['title']} (")
    return text


def leaks(text, cards):
    out = set()
    for c in cards:
        out |= {t for t in (c["title"], c["title"].replace("|", "/")) if t in text}
        if c["id"] != masked(c)["id"] and c["id"] in text:
            out.add(c["id"])
    return sorted(out)


COMMON = {"about", "after", "before", "every", "other", "their", "there", "these", "which", "while", "without", "still"}


def title_hits(ops, repo):
    """Pre-push grep (D-20261021-03): lines under tools/ on an unpushed branch tip that share half (at least two) of the key
    words (5+ letters) of an open Security item's title, read from OPS so the words are never written into tools/ themselves.
    A line is reported once however many branches carry it, and not at all when a remote branch already has it."""
    refs = lambda kind: gs(repo, "for-each-ref", "--format=%(refname:short)", f"refs/{kind}/").split()
    tips = [t for t in refs("heads") if gs(repo, "rev-list", "-1", t, "--not", "--remotes")]
    remotes = refs("remotes")
    hits = set()
    for c in ledger(ops, *git_sets(repo))["cards"]:
        words = set(re.findall(r"[a-z][a-z-]{4,}", c["title"].lower())) - COMMON
        if not (tips and c["security"] and c["lane"] != "past" and len(words) >= 2):
            continue
        need = max(2, (len(words) + 1) // 2)

        def grep(on):   # (path, text) -> ["branch:path:line", ...]
            # ponytail: one git grep per open item over every tip; fine for a once-before-push check
            out = subprocess.run(["git", "-C", repo, "grep", "-n", "-i", "-I", *[a for w in sorted(words) for a in ("-e", w)], *on, "--", "tools"],
                                 capture_output=True).stdout.decode("utf-8", "replace") if on else ""
            found = {}
            for line in out.splitlines():
                parts = line.split(":", 3)   # branch:path:line:text
                if len(parts) == 4 and sum(w in parts[3].lower() for w in words) >= need:
                    found.setdefault((parts[1], parts[3].strip()), []).append(":".join(parts[:3]))
            return found
        pushed = grep(remotes)
        for key, where in grep(tips).items():
            if key not in pushed:
                more = f" (+{len(where) - 1} more branches)" if len(where) > 1 else ""
                hits.add(f"{min(where)}{more} shares words with {masked(c)['id']}")
    return sorted(hits)


def g(repo, *a, inp=None, check=True):
    r = subprocess.run(["git", "-C", repo, *a], input=inp, capture_output=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a)}: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout


def gs(repo, *a, **k):
    return g(repo, *a, **k).decode("utf-8").strip()


def blob_text(repo, sha):
    return g(repo, "cat-file", "blob", sha).decode("utf-8", "surrogateescape")


def rewrite(repo, cards):
    """{old commit: new commit} for every unpushed commit whose tree or parents change."""
    new, trees = {}, {}
    for line in gs(repo, "rev-list", "--topo-order", "--reverse", "--parents", "--branches", "--not", "--remotes").splitlines():
        c, *parents = line.split()
        raw = g(repo, "cat-file", "commit", c)
        head, _, body = raw.partition(b"\n\n")
        tree = head.split(b"\n")[0][len(b"tree "):].decode()
        if tree not in trees:
            trees[tree] = tree
            entries = g(repo, "ls-tree", "-z", tree).split(b"\0")[:-1]
            for i, e in enumerate(entries):
                meta, name = e.split(b"\t", 1)
                if name == PATH.encode():
                    mode, typ, sha = meta.decode().split(" ")
                    text = blob_text(repo, sha)
                    clean = scrub(text, cards)
                    if clean != text:
                        nb = gs(repo, "hash-object", "-w", "--no-filters", "--stdin", inp=clean.encode("utf-8", "surrogateescape"))
                        entries[i] = f"{mode} {typ} {nb}\t".encode() + name
                        trees[tree] = gs(repo, "mktree", "-z", inp=b"\0".join(entries) + b"\0")
        ntree, nparents = trees[tree], [new.get(p, p) for p in parents]
        if ntree == tree and nparents == parents:
            continue
        out, ps, skip = [], iter(nparents), False
        for h in head.split(b"\n"):
            if skip and h.startswith(b" "):
                continue
            skip = h.startswith((b"gpgsig ", b"gpgsig-sha256 "))
            if skip:
                continue
            if h.startswith(b"tree "):
                h = b"tree " + ntree.encode()
            elif h.startswith(b"parent "):
                h = b"parent " + next(ps).encode()
            out.append(h)
        new[c] = gs(repo, "hash-object", "-t", "commit", "-w", "--stdin", inp=b"\n".join(out) + b"\n\n" + body)
    return new


def leaky_commits(repo, tip, cards, memo):
    """How many unpushed commits reachable from tip carry a PENDING.md that names a Security item."""
    commits = gs(repo, "rev-list", tip, "--not", "--remotes").split()
    if not commits:
        return 0
    found = g(repo, "cat-file", "--batch-check", inp="".join(f"{c}:{PATH}\n" for c in commits).encode()).decode().splitlines()
    n = 0
    for line in found:
        sha = line.split()[0]
        if line.endswith(" missing"):
            continue
        if sha not in memo:
            memo[sha] = bool(leaks(blob_text(repo, sha), cards))
        n += memo[sha]
    return n


def check(repo, cards):
    """(moves [(ref, old, new)], report lines, ok)."""
    new = rewrite(repo, cards)
    heads = [l.split() for l in gs(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/").splitlines()]
    moves = [(ref, old, new[old]) for ref, old in heads if old in new]
    cur = gs(repo, "symbolic-ref", "-q", "HEAD", check=False)
    cur_old = dict(heads).get(cur)
    cur_new = new.get(cur_old, cur_old)
    memo, lines, ok = {}, [], True
    for ref, old, nw in moves:
        before, after = leaky_commits(repo, old, cards, memo), leaky_commits(repo, nw, cards, memo)
        changed = set(gs(repo, "diff", "--name-only", old, nw).split())
        note = []
        if after:
            note.append("still leaks")
        if changed - {PATH}:
            note.append("tip differs beyond PENDING.md: " + ", ".join(sorted(changed - {PATH})))
        if cur_old and ref != cur:
            a = subprocess.run(["git", "-C", repo, "merge-tree", "--write-tree", old, cur_old], capture_output=True).returncode
            b = subprocess.run(["git", "-C", repo, "merge-tree", "--write-tree", nw, cur_new], capture_output=True).returncode
            if a != b:
                note.append(f"merge into {cur} changes (exit {a} -> {b})")
        ok &= not note
        lines.append(f"{'FAIL' if note else 'MOVE'} {ref} {old[:7]} -> {nw[:7]}, leaky commits {before} -> {after}"
                     + (" | " + "; ".join(note) if note else ""))
    return moves, lines, ok


def worktrees(repo):
    """{branch ref: [worktree path]}."""
    out, path = {}, None
    for line in gs(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch "):
            out.setdefault(line[len("branch "):], []).append(path)
    return out


def apply(repo, moves):
    """Moves every branch at once or none; returns report lines."""
    wts, clean, lines = worktrees(repo), [], []
    for ref, old, _ in moves:
        for wt in wts.get(ref, []):
            same = all(subprocess.run(["git", "-C", wt, "diff", *x, "--quiet", old, "--", PATH]).returncode == 0
                       for x in ([], ["--cached"]))
            (clean if same else lines).append(wt if same else f"LEFT {wt}: PENDING.md has changes of its own; refresh it by hand")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    tx = "".join(f"create refs/pre-scrub/{stamp}/{ref[len('refs/heads/'):]} {old}\nupdate {ref} {nw} {old}\n" for ref, old, nw in moves)
    r = subprocess.run(["git", "-C", repo, "update-ref", "-m", "scrub_pending_history", "--stdin"], input=tx.encode(), capture_output=True)
    if r.returncode:
        return [f"FAILED nothing moved (a branch changed since the check? run again): {r.stderr.decode('utf-8', 'replace').strip()}"]
    for wt in clean:
        g(wt, "checkout", "HEAD", "--", PATH)
    return [f"MOVED {len(moves)} branches; old tips under refs/pre-scrub/{stamp}/"] + lines + [f"REFRESHED {wt}" for wt in clean]


def main(ops, repo, do_apply=False):
    cards = security_cards(ops)
    moves, lines, ok = check(repo, cards)
    print("\n".join(lines) or "NOTHING TO DO: no unpushed commit names a Security item in PENDING.md")
    hits = title_hits(ops, repo)
    if hits:
        print("REVIEW before any push: these tools/ lines share key words with an open Security item's title; make them neutral\n" + "\n".join(hits))
    if not ok:
        print("ABORT: a check failed, nothing moved")
        return 1
    if do_apply and moves:
        out = apply(repo, moves)
        print("\n".join(out))
        return 1 if out[0].startswith("FAILED") else 0
    if moves:
        print("DRY RUN: nothing moved; --apply moves them")
    return 0


def selftest():
    import contextlib
    import io
    with tempfile.TemporaryDirectory() as tmp:
        ops, repo, wt = (os.path.join(tmp, d) for d in ("ops", "repo", "wt"))
        for d in ("decisions", "inbox/security", "inbox/dev"):
            os.makedirs(os.path.join(ops, d))
        os.makedirs(repo)
        w = lambda p, s: open(p, "w", encoding="utf-8", newline="").write(s)
        w(os.path.join(ops, "decisions", "20261010.md"), "## D-20261010-03 | Security fixture A\n"
          "- Source: inbox/security/20261010-fixture-b.md\n- From: security\n- Verdict: AUTO-APPROVED\n\n"
          "## D-20261010-04 | Bigger reranker\n- From: rnd\n- Verdict: REJECT\n")
        w(os.path.join(ops, "inbox", "security", "20261010-fixture-b.md"), "# Security fixture B | with a pipe\n")
        w(os.path.join(ops, "inbox", "security", "review-D-1.md"), "# Review\n")
        run = lambda *a, at=repo: g(at, *a).decode()
        for a in (("init", "-q", "-b", "main"), ("config", "user.email", "t@t"), ("config", "user.name", "t"), ("config", "core.autocrlf", "false")):
            run(*a)

        def commit(path, text, msg, at=repo):
            w(os.path.join(at, path), text)
            run("add", path, at=at)
            run("commit", "-qm", msg, at=at)

        commit(PATH, "# Pending\r\n", "base")
        run("update-ref", "refs/remotes/origin/main", "HEAD")
        leaky = ("# Pending\r\n| D-20261010-03 | Security fixture A | approved, queued | Dev builds it |\r\n"
                 "| D-20261010-04 | Bigger reranker | rejected | - |\r\n"
                 "| security/20261010-fixture-b | Security fixture B / with a pipe | proposed | - |\r\n"
                 "**Finished:** D-20261010-03 Security fixture A (merged); D-20261010-04 Bigger reranker (rejected).\r\n")
        commit(PATH, leaky, "docs(pending): plan\n\nwith a body")
        run("branch", "side")
        run("worktree", "add", "-q", wt, "side")
        commit("b.txt", "side work\n", "side work", at=wt)
        cards = security_cards(ops)
        commit(PATH, scrub(leaky, cards), "docs(pending): masked plan")
        assert leaks(leaky, cards) == ["Security fixture A", "Security fixture B / with a pipe", "security/20261010-fixture-b"]
        assert leaks(scrub(leaky, cards), cards) == [] and "D-20261010-04 Bigger reranker (rejected)" in scrub(leaky, cards)
        log = lambda ref: run("log", "--format=%an %ae %ad %cn %ce %cd %B", ref)
        before = {r: (run("rev-parse", r).strip(), log(r)) for r in ("main", "side")}
        with contextlib.redirect_stdout(io.StringIO()) as out:
            assert main(ops, repo) == 0
        assert out.getvalue().count("MOVE refs/heads/") == 2 and "leaky commits 2 -> 0" in out.getvalue(), out.getvalue()
        assert all(run("rev-parse", r).strip() == before[r][0] for r in before), "a dry run moves nothing"
        moves, _, ok = check(repo, cards)
        commit("c.txt", "more\n", "more side work", at=wt)
        assert ok and apply(repo, moves)[0].startswith("FAILED"), "a stale check fails the transaction"
        assert run("rev-parse", "main").strip() == before["main"][0] and not run("for-each-ref", "refs/pre-scrub/")
        before["side"] = (run("rev-parse", "side").strip(), log("side"))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            assert main(ops, repo, do_apply=True) == 0, out.getvalue()
        assert "REFRESHED" in out.getvalue() and "LEFT" not in out.getvalue(), out.getvalue()
        for r, (old, msgs) in before.items():
            assert run("rev-parse", r).strip() != old and log(r) == msgs, r
            assert run("rev-parse", f"refs/pre-scrub/{run('for-each-ref', '--format=%(refname:lstrip=2)', 'refs/pre-scrub/').split('/')[0]}/{r}").strip() == old
            for c in run("rev-list", r).split():
                assert "Security fixture A" not in run("show", f"{c}:{PATH}") and "Security fixture B" not in run("show", f"{c}:{PATH}"), c
        assert run("show", "side:b.txt") == "side work\n" and run("show", "side:c.txt") == "more\n"
        assert run("rev-parse", "origin/main").strip() == run("rev-list", "--max-parents=0", "main").strip(), "pushed commits stay"
        assert not run("status", "--porcelain", at=wt) and not run("status", "--porcelain"), "worktrees stay clean"
        with contextlib.redirect_stdout(io.StringIO()) as out:
            assert main(ops, repo) == 0 and out.getvalue().startswith("NOTHING TO DO"), out.getvalue()
        os.makedirs(os.path.join(repo, "tools"))
        commit("tools/t.py", "a = 'security fixture'\nb = 'fixture only'\n", "tools")
        assert title_hits(ops, repo) == ["main:tools/t.py:1 shares words with D-20261010-03"], title_hits(ops, repo)
        run("branch", "copy")
        assert title_hits(ops, repo) == ["copy:tools/t.py:1 (+1 more branches) shares words with D-20261010-03"], title_hits(ops, repo)
        run("update-ref", "refs/remotes/origin/main", "HEAD")
        commit("x.txt", "after the push\n", "unpushed, tools/ unchanged")
        assert title_hits(ops, repo) == [], "a line a remote already has is not reported"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--repo", default="C:/Users/hrizvi/Downloads/Git/groundedops")
    ap.add_argument("--apply", action="store_true")
    o = ap.parse_args()
    sys.exit(main(o.ops, o.repo, o.apply))
