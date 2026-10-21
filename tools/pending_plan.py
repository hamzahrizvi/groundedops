"""The product owner's plan in REPO/PENDING.md, written from the office's decisions after every PO run.

  python tools/pending_plan.py --ops C:/Users/hrizvi/groundedops-ops --repo <REPO>
  python tools/pending_plan.py --selftest

Rewrites one block between the office-plan markers near the top of PENDING.md: every open office
item with its stage and next step, and what finished or was turned down in the last 30 days. Each
"- For the rejected list: ..." line in a decision is added once to "Rejected - do not redo without
new evidence". Then commits PENDING.md alone. Items that came from Security, are assigned to it, or carry `- Sensitive: yes` show only as "Security item
<id>, details in the office": the repository is public.

PENDING.md is also edited by hand and by other sessions (its header, CLAUDE.md P1): when it has
changes nobody committed, or anything else is staged, this writes nothing, prints WAIT and leaves
the update to the next run.
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
from ops_ledger import SECURITY, git_sets, ledger  # noqa: E402

START, END = "<!-- office-plan:start -->", "<!-- office-plan:end -->"
ANCHOR = re.compile(r"(?m)^## ")   # the first time, the block goes above the first section, after the intro
REJECTED = "### Rejected - do not redo without new evidence\n"
PLAN = re.compile(r"(?m)^## .*7 -> 10 plan.*$")   # rows "| 12 | M3 | what | who |"
NEXT = {"waiting for CEO": "your yes", "approved, queued": "Dev builds it", "sent back, rebuilding": "Dev rebuilds it",
        "built, waiting for the check": "the pre-ship check", "check passed": "Security's review",
        "ready to merge": "merges once Security clears it", "check failed": "the product owner sends it back",
        "blocked": "the product owner decides", "proposed": "the product owner triages it", "deferred": "parked"}


def masked(c):
    """Security's items, and any marked `- Sensitive: yes`, by where they came from, never by name: the repository is public."""
    if not c.get("security"):
        return c
    dept, _, name = c["id"].partition("/")   # an inbox card's file name can name the hole: keep only its date
    cid = f"{dept}/{name[:8]}" if name else dept
    return {**c, "id": cid, "title": f"Security item {cid}, details in the office"}


def block(cards, today):
    cards = [masked(c) for c in cards]
    open_ = [c for c in cards if c["lane"] != "past"]
    since = (today - datetime.timedelta(days=30)).strftime("%Y%m%d")
    past = [c for c in cards if c["lane"] == "past" and c["decided"] >= since]
    rows = "\n".join(f"| {c['id']} | {c['title'].replace('|', '/')} | {c['stage']} | {NEXT.get(c['stage'], '-')} |"
                     for c in sorted(open_, key=lambda c: (c["stage"] == "deferred", c["id"])))
    done = "; ".join(f"{c['id']} {c['title']} ({c['stage']})" for c in sorted(past, key=lambda c: c["id"])) or "nothing yet"
    return (f"{START}\n## Office plan (the product owner's)\n\n"
            "Rewritten from the product owner's decisions after each of its runs (`tools/pending_plan.py`).\n"
            "To change an item, tell the product owner or answer it on the office page; edits here are overwritten.\n\n"
            "| Item | What | Stage | Next |\n|---|---|---|---|\n" + (rows or "| - | nothing open | - | - |") +
            f"\n\n**Finished or turned down in the last 30 days:** {done}.\n{END}")


def rejected_lines(ops):
    """[(id, line)] from every decision that carries "- For the rejected list: ...", latest wording per id."""
    out, sec = {}, set()
    for path in sorted(glob.glob(os.path.join(ops, "decisions", "*.md"))):
        for part in re.split(r"(?m)^## ", open(path, encoding="utf-8").read())[1:]:
            m = re.match(r"(D-\d{8}-\d+)", part)
            r = re.search(r"(?m)^- For the rejected list:\s*(.+?)\s*$", part)
            if m and SECURITY.search(part):   # any entry: a re-triage can cite Security later
                sec.add(m.group(1))
            if m and r:
                out[m.group(1)] = r.group(1)
    return [(did, line) for did, line in out.items() if did not in sec]


def ticks_of(ops):
    """{id: [plan item codes]} from "- Ticks off: M3, FB1" lines in the decisions."""
    out = {}
    for path in sorted(glob.glob(os.path.join(ops, "decisions", "*.md"))):
        for part in re.split(r"(?m)^## ", open(path, encoding="utf-8").read())[1:]:
            m, t = re.match(r"(D-\d{8}-\d+)", part), re.search(r"(?m)^- Ticks off:\s*(.+?)\s*$", part)
            if m and t:
                out.setdefault(m.group(1), []).extend(x.strip() for x in t.group(1).split(",") if x.strip())
    return out


def tick(text, cards, ticks, today):
    """Marks each 7 -> 10 plan row that a merged or done item ticks off as done, once."""
    stage, m = {c["id"]: c["stage"] for c in cards}, PLAN.search(text)
    if not m:
        return text
    end = text.find("\n## ", m.end())
    end = len(text) if end < 0 else end
    sec = text[m.end():end]
    for did, codes in ticks.items():
        if stage.get(did) not in ("merged", "done"):
            continue
        for c in map(re.escape, codes):
            # by code only: the section's other tables reuse row numbers
            sec = re.sub(rf"(?m)^(\|\s*\d+\s*\|\s*{c}\s*\|\s*)(?!\s*\*\*Done)",
                         lambda x: f"{x.group(1)}**Done {today:%Y-%m-%d}** ({did}): ", sec)
    return text[:m.end()] + sec + text[end:]


def update(text, cards, rejects, today, ticks=None):
    text = tick(text, cards, ticks or {}, today)
    new = block(cards, today)
    if START in text:
        text = re.sub(re.escape(START) + ".*?" + re.escape(END), lambda _: new, text, count=1, flags=re.S)
    else:
        m = ANCHOR.search(text)
        text = text[:m.start()] + new + "\n\n" + text[m.start():] if m else text + "\n\n" + new + "\n"
    if REJECTED in text:
        head = text.index(REJECTED) + len(REJECTED)
        nxt = text.find("\n### ", head)
        section = text[head:nxt if nxt >= 0 else len(text)]
        add = "".join(f"- {line} ({did}, product owner)\n" for did, line in rejects if did not in section)
        if add:
            at = head + len(section.rstrip("\n")) + 1
            text = text[:at] + add + text[at:]
    return text


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, encoding="utf-8")


def run(ops, repo, today=None):
    today = today or datetime.date.today()
    path = os.path.join(repo, "PENDING.md")
    if git(repo, "status", "--porcelain", "--", "PENDING.md").stdout.strip() or git(repo, "diff", "--cached", "--name-only").stdout.strip():
        return "WAIT PENDING.md has changes nobody committed, or other files are staged; the plan waits for the next run"
    raw = open(path, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")
    new = update(text, ledger(ops, *git_sets(repo), today=today)["cards"], rejected_lines(ops), today, ticks_of(ops))
    if new == text:
        return "UNCHANGED"
    open(path, "w", encoding="utf-8", newline="").write(new.replace("\n", nl))
    git(repo, "add", "PENDING.md")
    r = git(repo, "commit", "-q", "-m", f"docs(pending): the product owner's plan, {today:%Y-%m-%d}")
    return "COMMITTED" if r.returncode == 0 else "FAILED " + (r.stderr or r.stdout).strip()[:200]


def selftest():
    with tempfile.TemporaryDirectory() as tmp:
        ops, repo = os.path.join(tmp, "ops"), os.path.join(tmp, "repo")
        for d in ("decisions", "inbox/dev", "inbox/audit", "inbox/security"):
            os.makedirs(os.path.join(ops, d))
        os.makedirs(repo)
        w = lambda p, s: open(p, "w", encoding="utf-8", newline="").write(s)
        w(os.path.join(ops, "decisions", "20261010.md"),
          "## D-20261010-01 | Sales brush-off\n- From: customer\n- Assigned: dev\n- Verdict: NEEDS CEO\n- [ ] CEO approved\n\n"
          "## D-20261010-02 | Bigger reranker\n- From: rnd\n- Verdict: REJECT\n- For the rejected list: A bigger reranker: no gain on 40 blind questions.\n"
          "\n## D-20261010-03 | Rate limits trust a forged header\n- Source: inbox/security/20261010-forge.md\n- From: security\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n\n"
          "## D-20261010-04 | Uploads stored before sign-in\n- Source: inbox/security/20261010-upload.md\n- From: audit\n- Verdict: REJECT\n- For the rejected list: Uploads stored before sign-in: accepted risk.\n"
          "\n## D-20261010-05 | Test lines in the live log\n- Source: inbox/dev/D-20261010-01.md\n- From: audit\n- Verdict: REJECT\n- For the rejected list: Test lines in the live log: later.\n"
          "\n## D-20261010-05 | Test lines in the live log\n- Source: inbox/security/20261010-log.md\n- From: audit\n- Verdict: REJECT\n"
          "\n## D-20261010-06 | Security fixture title\n- Source: inbox/dev/D-20261010-01.md\n- From: dev\n- Assigned: security\n- Verdict: DEFER\n")
        w(os.path.join(ops, "inbox", "audit", "20261010-cache.md"), "# Cache repeated answers\n")
        w(os.path.join(ops, "inbox", "security", "20261010-chunked-cap.md"), "# The size cap misses chunked uploads\n")
        w(os.path.join(repo, "PENDING.md"), "# Pending\r\n\r\nIntro.\r\n\r\n**Friday review.** Once a week.\r\n\r\n## The plan\r\n\r\n"
                                           "### Rejected - do not redo without new evidence\r\n\r\n- Old idea.\r\n\r\n### Waiting\r\n\r\nSMTP.\r\n")
        for a in (("init", "-q"), ("config", "user.email", "t@t"), ("config", "user.name", "t"), ("add", "PENDING.md"), ("commit", "-qm", "x")):
            git(repo, *a)
        today = datetime.date(2026, 10, 10)
        assert run(ops, repo, today) == "COMMITTED"
        t = open(os.path.join(repo, "PENDING.md"), encoding="utf-8", newline="").read()
        assert "\r\n" in t and t.index("**Friday review.**") < t.index(START) < t.index("## The plan"), t
        assert "| D-20261010-01 | Sales brush-off | waiting for CEO | your yes |" in t, t
        assert "| audit/20261010-cache | Cache repeated answers | proposed |" in t, t
        assert "D-20261010-02 Bigger reranker (rejected)" in t, t
        assert "| D-20261010-03 | Security item D-20261010-03, details in the office |" in t, t
        assert "| security/20261010 | Security item security/20261010, details in the office |" in t, t
        assert "D-20261010-04 Security item D-20261010-04, details in the office (rejected)" in t, t
        assert "D-20261010-05 Security item D-20261010-05, details in the office (rejected)" in t, t
        assert "| D-20261010-06 | Security item D-20261010-06, details in the office |" in t, t
        for leak in ("Security fixture", "forged", "chunked", "Uploads stored", "sign-in", "live log"):
            assert leak not in t, (leak, t)
        assert "- Old idea.\r\n- A bigger reranker: no gain on 40 blind questions. (D-20261010-02, product owner)\r\n\r\n### Waiting" in t, t
        assert run(ops, repo, today) == "UNCHANGED", "a second run changes nothing"
        assert t.count("D-20261010-02, product owner") == 1
        w(os.path.join(repo, "PENDING.md"), t + "a hand edit\r\n")
        assert run(ops, repo, today).startswith("WAIT") and open(os.path.join(repo, "PENDING.md"), encoding="utf-8",
                                                                 newline="").read().endswith("a hand edit\r\n"), "others' edits are left alone"
        assert git(repo, "log", "--oneline").stdout.count("\n") == 2
    plan = "## The 7 -> 10 plan\n\n| 3 | M1 | Fix the test tool. | Sonnet |\n| 4 | M3 | Compare tool. | Opus |\n| 5 | M2 | Label lines. | Opus |\n\n## Next\n"
    cards = [{"id": "D-1", "stage": "merged"}, {"id": "D-2", "stage": "approved, queued"}]
    t = tick(plan, cards, {"D-1": ["M1", "M2"], "D-2": ["M3"]}, datetime.date(2026, 10, 11))
    assert "| 3 | M1 | **Done 2026-10-11** (D-1): Fix the test tool. |" in t and "| 5 | M2 | **Done 2026-10-11** (D-1): Label" in t, t
    assert "| 4 | M3 | Compare tool. |" in t, "only merged or done items tick a row off"
    assert tick(t, cards, {"D-1": ["M1"]}, datetime.date(2026, 10, 12)) == t, "a row is ticked once"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--repo", default="C:/Users/hrizvi/Downloads/Git/groundedops")
    o = ap.parse_args()
    print(run(o.ops, o.repo))
