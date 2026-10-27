"""Plan board, department boards and improvement points, computed from the ops folders.

  python tools/ops_ledger.py [--ops C:/Users/hrizvi/groundedops-ops] [--repo .] > ledger.json
  python tools/ops_ledger.py --selftest

Nothing is judged or self-reported. Cards and points come only from files the
departments already write (findings, decisions, Dev hand-backs, gate.json) and
from git (which agents/dev-<id> branches are merged into HEAD, or reverted).

Points, per month of the decision:
  finder (From):     +2 approved; +5 merged with a gate PASS; +1 per wrong answer the gate saw fixed
  builder (Assigned): +5 merged after a first-try PASS (+3 after a send-back); +1 per wrong answer fixed
  product owner:     +1 per merged item; -3 for a reverted one (builder -3 too)
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict

DEPTS = ["customer", "audit", "security", "rnd", "marketing", "po", "dev", "lab"]
CAPACITY = {"dev": 3, "po": 8}          # ponytail: everyone else 2; tune when a board shows real overload
NOTES = re.compile(r"(-weekly|-market|-done)\.md$|^review-")   # -done: a department's reply on its work order, not a new finding
SECURITY = re.compile(r"(?mi)^- (From:\s*security\b|Assigned:\s*security\b|Source:\s*inbox[/\\]security[/\\]|Sensitive:\s*yes\b)")   # Security's, or marked sensitive by the product owner (D-20261020-07)
PAST, FUTURE = {"merged", "rejected", "reverted", "done"}, {"proposed", "waiting for CEO", "deferred"}


def _read(p):
    return open(p, encoding="utf-8").read()


def new_decisions_file(ops, now=None):
    """A new decisions file that sorts after every one written so far. decisions() reads files in name order, so an
    append to an older file is read before newer passes: the CEO's 20:19 tick, appended to 20261016.md, lost to
    unticked boxes in 20261016h/i.md written at 17:33 and 17:49 (2026-10-16), and Dev never got the item.
    The product owner names its passes <day>.md, <day>b.md, <day>c.md ...; the office adds <latest>_<HHMMSS>.md."""
    now = now or datetime.datetime.now()
    day = f"{now:%Y%m%d}"
    names = sorted(os.path.basename(p)[:-3] for p in glob.glob(os.path.join(ops, "decisions", day + "*.md")))
    return os.path.join(ops, "decisions", f"{names[-1].split('_')[0]}_{now:%H%M%S}.md" if names else day + ".md")


def decisions(ops):
    items, seq = {}, 0
    for path in sorted(glob.glob(os.path.join(ops, "decisions", "*.md"))):
        for block in re.split(r"(?m)^## ", _read(path))[1:]:
            seq += 1
            head, _, body = block.partition("\n")
            m = re.match(r"(D-\d{8}-\d+)\s*\|\s*(.+)", head.strip())
            if not m:
                continue
            did, title = m.groups()
            it = items.setdefault(did, {"id": did, "title": title.strip(), "history": [], "decided": did[2:10]})
            for k, v in re.findall(r"(?m)^- (Source|From|Assigned|Reassigned|Verdict|Run|Siblings|Touches|Priority|Still broken):\s*(.+?)\s*$", body):
                if k in ("Assigned", "Reassigned"):   # the CEO's move (tools/ceo_inbox.py reassign) or a later triage's: the latest wins
                    it["assigned"] = v.strip()
                    continue
                if k == "Touches":   # files or folders the work order changes (office_next.py keeps overlapping ones apart): the latest wins
                    # notes in brackets dropped first: "src/main.py (the band label)" is src/main.py (2026-10-24)
                    it["touches"] = [p.split()[0].lower().replace("\\", "/").removeprefix("./").rstrip("/")
                                     for p in re.sub(r"\([^)]*\)", "", v).split(",") if p.strip()]
                    continue
                if k == "Priority":   # the product owner's rank of open work, 1 = first (office_next.py builds in this order)
                    it["priority"] = int(re.match(r"\d+", v.strip()).group()) if re.match(r"\d+", v.strip()) else 99
                    continue
                if k == "Still broken":   # the product owner re-checked it is not already fixed (the CEO, 2026-10-15)
                    it["checked"] = seq
                    continue
                if k == "Source":   # a later triage may cite a newer report for the same item: every one counts as decided
                    it["last_source"] = v.strip()
                    it.setdefault("sources", []).append(v.strip().replace("\\", "/"))
                    it.setdefault("cited", {})[v.strip().replace("\\", "/")] = path   # for answered()
                if k == "Verdict":   # "READY TO MERGE (unchanged)" is READY TO MERGE: a note in brackets is not a verdict
                    v = re.sub(r"\s*\([^()]*\)\s*$", "", v)   # (2026-10-16: the suffix dropped a cleared merge off the plan)
                    if "DEFER" in it["history"] and v.strip().upper() in ("NEEDS CEO", "AUTO-APPROVED"):
                        it["reopened"] = os.path.basename(path)[:8]   # back from the parked list: an older attempt is void
                    it["history"].append(v.strip().upper())
                    if v.strip().upper() == "AUTO-APPROVED":
                        it["approved"], it["approved_in"] = seq, path
                else:
                    it.setdefault(k.lower(), v.strip())
            if SECURITY.search(body):
                it["security"] = True
            if re.search(r"(?mi)^- \[x\] CEO approved", body):
                it["ceo_approved"], it["approved"] = True, seq + 0.5   # a tick needs a "Still broken" check in a later section
                it["approved_in"] = path
            elif re.search(r"(?m)^- Verdict:\s*NEEDS CEO", body) and re.search(r"(?mi)^- \[ \] CEO approved", body):
                it["ceo_approved"] = False   # decided again with an unticked box: it needs a fresh yes, an old tick does not carry
    return items


def answered(it, ops, path):
    """A hand-back or <id>-done.md that a decision cites as its Source, in a decisions file written after it:
    the product owner has answered it, so it no longer says the item is stuck."""
    # ponytail: by file mtime, and decisions files are appended all day, so a hand-back rewritten after the citing
    # triage but before a later append to the same file reads as answered; date each section if that bites
    f = it.get("cited", {}).get(os.path.relpath(path, ops).replace("\\", "/"))
    if bool(f) and os.path.exists(path) and os.path.getmtime(path) <= os.path.getmtime(f):
        return True
    # approved again after the note (a CEO tick, or the product owner's AUTO-APPROVED): that is the answer, and the
    # department runs the order again (2026-10-19: R&D's BLOCKED note on the CashGenic check stayed "stuck" after the
    # CEO did the upload and ticked it, and the check was never re-run)
    a = it.get("approved_in")
    return bool(a) and os.path.exists(path) and os.path.exists(a) and os.path.getmtime(path) < os.path.getmtime(a)


def git_sets(repo):
    def run(*a):
        try:
            return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, timeout=30).stdout
        except Exception:
            return ""
    merged = {b.strip().lstrip("*+ ").replace("agents/dev-", "") for into in ("HEAD", "experimental/agent-org-phase1")
              for b in run("branch", "--merged", into, "--list", "agents/dev-*").splitlines() if b.strip()}  # product or office fix
    # a branch Dev created but never committed to sits on main's history, so --merged lists it; it is not merged
    # (D-20261012-05 showed as merged for a day and was never rescheduled, 2026-10-13)
    empty = lambda log: len(log) == 1 and log[0].startswith("branch: Created")
    merged = {i for i in merged if not empty(run("reflog", "show", "--format=%gs", f"agents/dev-{i}").split("\n")[:-1])}
    reverts = run("log", "--oneline", "-i", "--grep=revert")
    reverted = set(re.findall(r"D-\d{8}-\d+", reverts))
    return merged, reverted


def stage_of(it, ops, merged, reverted):
    latest = it["history"][-1] if it["history"] else ""   # a later verdict overrides: DEFER then CEO-approved, or SENT BACK then DEFER
    dev = os.path.join(ops, "inbox", "dev", it["id"])
    attempts = len(glob.glob(dev + ".gate.*"))
    gate = json.load(open(dev + ".gate/gate.json", encoding="utf-8")) if os.path.exists(dev + ".gate/gate.json") else None
    it["gate"], it["first_try"] = gate, attempts == 0
    if it["id"] in reverted:
        return "reverted"
    rode = re.search(r"(?m)^Branch:\s*agents/dev-(D-\d{8}-\d+)", _read(dev + ".md")) if os.path.exists(dev + ".md") else None
    # the latest verdict judged another order's gate: this one rides on that branch (D-20261010-09/-11/-23 on -07)
    host = re.search(r"inbox/dev/(D-\d{8}-\d+)\.gate", it.get("last_source", ""))
    host = host.group(1) if host and host.group(1) != it["id"] and latest == "READY TO MERGE" else None
    if it["id"] in merged or (rode and rode.group(1) in merged) or host in merged:   # built on another order's branch (D-13 rode on D-08)
        return "merged"
    if host:
        return f"rides on {host}"   # not rebuilt, checked or merged on its own
    if latest == "DONE":   # a CEO action ticked off on the office page (tools/ceo_inbox.py)
        return "done"
    if latest == "REJECT":
        return "rejected"
    if latest == "DEFER":
        return "deferred"
    if "NEEDS CEO" in it["history"] and not it.get("ceo_approved"):
        return "waiting for CEO"
    stamp = lambda p: time.strftime("%Y%m%d", time.localtime(os.path.getmtime(p)))
    if os.path.exists(dev + ".md") and it.get("reopened") and stamp(dev + ".md") < it["reopened"]:
        return "sent back, rebuilding"   # handed back before it was parked and reopened: Dev builds it again
    if gate and os.path.exists(dev + ".md") and os.path.getmtime(dev + ".gate/gate.json") < os.path.getmtime(dev + ".md"):
        gate = None   # a check of an earlier hand-back, not of this one
    if os.path.exists(dev + ".md") and re.search(r"(?m)^Status:\s*BLOCKED", _read(dev + ".md")):
        # stuck only while it is Dev's and no triage has answered it (D-20261009-02 looped on "Dev is stuck", 2026-10-14)
        if it.get("assigned", "dev") == "dev" and not answered(it, ops, dev + ".md"):
            return "blocked"
    elif os.path.exists(dev + ".md"):
        if gate:
            if gate.get("verdict") == "PASS":
                return "ready to merge" if latest == "READY TO MERGE" else "check passed"
            return "check failed"
        return "built, waiting for the check"
    return "sent back, rebuilding" if attempts else "approved, queued"


def ledger(ops, merged=frozenset(), reverted=frozenset(), today=None):
    today = today or datetime.date.today()
    items = decisions(ops)
    cited = {s for it in items.values() for s in it.get("sources", [])}
    cards = []
    for it in items.values():
        st = stage_of(it, ops, merged, reverted)
        cards.append({"id": it["id"], "title": it["title"], "from": it.get("from", ""), "security": it.get("security", False),
                      "assigned": it.get("assigned", "dev"),
                      "stage": st, "lane": "past" if st in PAST else "future" if st in FUTURE else "ongoing",
                      "decided": it["decided"], "priority": None if st in PAST else it.get("priority")})
    for path in sorted(glob.glob(os.path.join(ops, "inbox", "*", "*.md"))):
        dept, name = os.path.basename(os.path.dirname(path)), os.path.basename(path)
        if dept in ("dev", "ceo") or NOTES.search(name) or f"inbox/{dept}/{name}" in cited:   # ceo: notes, answered in the report
            continue
        text = _read(path)
        m = re.search(r"(?m)^#\s+(.+)$", text)
        cards.append({"id": f"{dept}/{name[:-3]}", "title": (m.group(1) if m else name[:-3]).strip(), "from": dept, "security": dept == "security" or bool(SECURITY.search(text)),
                      "assigned": "po", "stage": "proposed", "lane": "future", "decided": name[:8]})

    boards = {}
    for d in DEPTS:
        doing = [c["title"] for c in cards if c["lane"] == "ongoing" and c["assigned"] == d and c["stage"] != "approved, queued"]
        queue = [c["title"] for c in cards if c["stage"] == "proposed"] if d == "po" else \
                [c["title"] for c in cards if c["stage"] == "approved, queued" and c["assigned"] == d]
        cap = CAPACITY.get(d, 2)
        boards[d] = {"capacity": cap, "doing": doing, "queue": queue,
                     "done": sum(1 for c in cards if c["stage"] == "merged" and d in (c["assigned"], c["from"])),
                     "load": round((len(queue) if d == "po" else len(doing) + len(queue)) / cap, 2)}

    month = today.strftime("%Y%m")
    pts = {"month": defaultdict(int), "all": defaultdict(int)}
    stats = defaultdict(lambda: {"approved": 0, "merged": 0, "fixed": 0})
    for it in items.values():
        h = it["history"]
        frm, asg = it.get("from", ""), it.get("assigned", "dev")
        award = []
        if "AUTO-APPROVED" in h or ("NEEDS CEO" in h and it.get("ceo_approved")):
            award.append((frm, 2)); stats[frm]["approved"] += 1
        gate = it.get("gate") or {}
        if it["id"] in merged and it["id"] not in reverted and gate.get("verdict") == "PASS":
            fixed = len(gate.get("fixed") or [])
            award += [(frm, 5 + fixed), (asg, (5 if it["first_try"] else 3) + fixed), ("po", 1)]
            stats[frm]["merged"] += 1; stats[frm]["fixed"] += fixed
            if asg != frm:
                stats[asg]["merged"] += 1; stats[asg]["fixed"] += fixed
        if it["id"] in reverted:
            award += [("po", -3), (asg, -3)]
        for who, p in award:
            if not who:
                continue
            pts["all"][who] += p
            if it["decided"].startswith(month):
                pts["month"][who] += p
    table = sorted(({"dept": d, "points": pts["month"][d], "all_time": pts["all"][d], **stats[d]} for d in DEPTS),
                   key=lambda r: (-r["points"], -r["all_time"], r["dept"]))
    leader = table[0]["dept"] if table[0]["points"] > 0 else None
    return {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "cards": sorted(cards, key=lambda c: c["decided"], reverse=True), "boards": boards,
            "rewards": {"month": today.strftime("%Y-%m"), "leader": leader, "table": table}}


def _selftest():
    import tempfile
    od = tempfile.mkdtemp()
    os.makedirs(os.path.join(od, "decisions"))
    t = datetime.datetime(2026, 10, 16, 20, 19, 5)
    assert os.path.basename(new_decisions_file(od, t)) == "20261016.md", "first file of the day"
    for n in ("20261016.md", "20261016h.md"):
        open(os.path.join(od, "decisions", n), "w").write("## D-20261016-13 | Local\n- Assigned: dev\n- Verdict: NEEDS CEO\n- [ ] CEO approved\n")
    p = new_decisions_file(od, t)
    assert os.path.basename(p) == "20261016h_201905.md" and "20261016h.md" < os.path.basename(p) < "20261016i.md", p
    open(p, "w").write("## D-20261016-13 | Local\n- Verdict: NEEDS CEO\n- [x] CEO approved\n")
    assert os.path.basename(new_decisions_file(od, t.replace(minute=30))) == "20261016h_203005.md"
    assert decisions(od)["D-20261016-13"]["ceo_approved"], "the newest tick wins"
    os.makedirs(os.path.join(od, "inbox", "rnd"))
    note = os.path.join(od, "inbox", "rnd", "D-20261016-13-done.md")
    open(note, "w").write("Status: BLOCKED - needs an upload\n")
    t0 = time.time() - 600
    os.utime(note, (t0, t0))
    assert answered(decisions(od)["D-20261016-13"], od, note), "approved again after a blocked note: answered, runs again"
    os.utime(note, (time.time() + 60, time.time() + 60))
    assert not answered(decisions(od)["D-20261016-13"], od, note), "a note newer than the approval is still open"
    ops = tempfile.mkdtemp()
    for d in ("decisions", "inbox/customer", "inbox/dev", "inbox/audit"):
        os.makedirs(os.path.join(ops, d))
    w = lambda p, s: open(os.path.join(ops, p), "w", encoding="utf-8").write(s)
    w("inbox/customer/20261012-comparisons.md", "# Comparisons use the wrong manual\n")
    w("inbox/customer/20261012-procedures.md", "# Procedures get setup steps\n")
    w("inbox/audit/20261014-weekly.md", "# weekly note\n")
    w("inbox/audit/20261014-cache.md", "# Cache repeated questions\n")
    w("decisions/20261013.md", "## D-20261013-01 | Fix comparisons\n- Source: inbox/customer/20261012-comparisons.md\n"
      "- From: customer\n- Assigned: dev\n- Verdict: NEEDS CEO\n- [x] CEO approved\n\n"
      "## D-20261013-02 | Procedures\n- Source: inbox/customer/20261012-procedures.md\n- From: customer\n- Verdict: NEEDS CEO\n- [ ] CEO approved\n\n"
      "## D-20261013-03 | Vague\n- From: customer\n- Assigned: dev\n- Verdict: DEFER\n")
    w("decisions/20261016.md", "## D-20261013-01 | Fix comparisons\n- Verdict: READY TO MERGE (unchanged)\n\n"
      "## D-20261013-03 | Vague\n- Verdict: NEEDS CEO\n- [x] CEO approved\n")   # a deferred item the CEO approves later
    w("inbox/dev/D-20261013-01.md", "Status: READY\nBranch: agents/dev-D-20261013-01\n")
    os.makedirs(os.path.join(ops, "inbox/dev/D-20261013-01.gate"))
    w("inbox/dev/D-20261013-01.gate/gate.json", json.dumps({"verdict": "PASS", "fixed": ["C-03", "C-14", "C-22"]}))
    day = datetime.date(2026, 10, 20)
    r = ledger(ops, today=day)
    st = {c["id"]: c["stage"] for c in r["cards"]}
    assert st == {"D-20261013-01": "ready to merge", "D-20261013-02": "waiting for CEO", "D-20261013-03": "approved, queued",
                  "audit/20261014-cache": "proposed"}, st
    assert r["boards"]["dev"]["doing"] == ["Fix comparisons"] and r["boards"]["po"]["queue"] == ["Cache repeated questions"]
    pts = {t["dept"]: t["points"] for t in r["rewards"]["table"]}
    assert pts["customer"] == 4 and pts["dev"] == 0, pts            # two approved, none merged yet
    w("decisions/20261013d.md", "## D-20261013-05 | Security fixture title\n- Source: inbox/dev/D-20261013-01.md\n- From: dev\n- Assigned: security\n- Verdict: DEFER\n")
    assert {c["id"]: c["security"] for c in ledger(ops, today=day)["cards"]}["D-20261013-05"], "assigned to Security = Security's, whoever raised it"
    os.remove(os.path.join(ops, "decisions/20261013d.md"))
    w("decisions/20261013b.md", "## D-20261013-04 | Audit\n- From: security\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n")
    w("inbox/dev/D-20261013-04.md", "Status: READY\nBranch: agents/dev-D-20261013-01\n")   # rode on 01's branch
    assert {c["id"]: c["stage"] for c in ledger(ops, merged={"D-20261013-01"}, today=day)["cards"]}["D-20261013-04"] == "merged"
    os.remove(os.path.join(ops, "inbox/dev/D-20261013-04.md"))   # hand-back set aside; the verdict judged 01's gate
    w("decisions/20261013c.md", "## D-20261013-04 | Audit\n- Source: inbox/dev/D-20261013-01.gate/gate.json\n- Verdict: READY TO MERGE\n")
    assert {c["id"]: c["stage"] for c in ledger(ops, today=day)["cards"]}["D-20261013-04"] == "rides on D-20261013-01"
    assert {c["id"]: c["stage"] for c in ledger(ops, merged={"D-20261013-01"}, today=day)["cards"]}["D-20261013-04"] == "merged"
    for p in ("decisions/20261013b.md", "decisions/20261013c.md"):
        os.remove(os.path.join(ops, p))
    r = ledger(ops, merged={"D-20261013-01"}, today=day)
    pts = {t["dept"]: t["points"] for t in r["rewards"]["table"]}
    assert (pts["customer"], pts["dev"], pts["po"]) == (4 + 5 + 3, 5 + 3, 1), pts
    assert r["rewards"]["leader"] == "customer" and r["cards"][0]["lane"] in ("past", "future", "ongoing")
    r = ledger(ops, merged={"D-20261013-01"}, reverted={"D-20261013-01"}, today=day)
    pts = {t["dept"]: t["points"] for t in r["rewards"]["table"]}
    assert pts["po"] == -3 and pts["dev"] == -3, pts                 # a revert costs, and earns no merge points
    # an office-only PASS (D-20261010-07) counts as a merge, not as a bot fix
    w("decisions/20261013c.md", "## D-20261013-05 | Office tool\n- From: audit\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n")
    w("inbox/dev/D-20261013-05.md", "Status: READY\nBranch: agents/dev-D-20261013-05\n")
    os.makedirs(os.path.join(ops, "inbox/dev/D-20261013-05.gate"))
    w("inbox/dev/D-20261013-05.gate/gate.json", json.dumps({"verdict": "PASS", "reasons": ["no product code changed"],
      "fixed": [], "became_wrong": [], "cases": 0, "siblings": 0, "office_only": True}))
    row = {t["dept"]: t for t in ledger(ops, merged={"D-20261013-01", "D-20261013-05"}, today=day)["rewards"]["table"]}
    assert (row["audit"]["merged"], row["audit"]["fixed"], row["audit"]["points"]) == (1, 0, 2 + 5), row["audit"]
    assert (row["dev"]["merged"], row["dev"]["fixed"]) == (2, 3), row["dev"]
    # parked and reopened (D-20261009-09, 2026-10-14): an unticked NEEDS CEO asks again; the old hand-back and its check are void
    w("decisions/20261013e.md", "## D-20261013-06 | Several asks\n- Assigned: dev\n- Verdict: NEEDS CEO\n- [x] CEO approved\n")
    w("inbox/dev/D-20261013-06.md", "Status: READY\n")
    os.makedirs(os.path.join(ops, "inbox/dev/D-20261013-06.gate"))
    w("inbox/dev/D-20261013-06.gate/gate.json", json.dumps({"verdict": "FAIL"}))
    old = datetime.datetime(2026, 10, 10, 12).timestamp()
    os.utime(os.path.join(ops, "inbox/dev/D-20261013-06.md"), (old, old))
    os.utime(os.path.join(ops, "inbox/dev/D-20261013-06.gate/gate.json"), (old + 60, old + 60))
    six = lambda: {c["id"]: c["stage"] for c in ledger(ops, today=day)["cards"]}["D-20261013-06"]
    assert six() == "check failed", six()
    w("decisions/20261014.md", "## D-20261013-06 | Several asks\n- Verdict: DEFER\n")
    w("decisions/20261015.md", "## D-20261013-06 | Several asks\n- Verdict: NEEDS CEO\n- [ ] CEO approved\n")
    assert six() == "waiting for CEO", six()
    w("decisions/20261015b.md", "## D-20261013-06 | Several asks\n- Verdict: NEEDS CEO\n- [x] CEO approved\n")
    assert six() == "sent back, rebuilding", six()
    # D-20261009-02 (2026-10-14): CEO moves it to Dev, Dev hands back BLOCKED, a later triage gives it back to Customer
    w("decisions/20261017.md", "## D-20261013-07 | Hidden questions\n- From: customer\n- Assigned: customer\n- Verdict: AUTO-APPROVED\n\n"
      "# CEO decisions\n\n## D-20261013-07 | Hidden questions\n- Reassigned: dev\n- Verdict: NEEDS CEO\n- [x] CEO approved\n")
    w("inbox/dev/D-20261013-07.md", "Status: BLOCKED\nDev must not write the hidden test for its own fix.\n")
    seven = lambda: next((c["assigned"], c["stage"]) for c in ledger(ops, today=day)["cards"] if c["id"] == "D-20261013-07")
    assert seven() == ("dev", "blocked"), seven()
    os.utime(os.path.join(ops, "inbox/dev/D-20261013-07.md"), (old, old))   # the triage below is written after the hand-back
    w("decisions/20261017b.md", "## D-20261013-07 | Hidden questions\n- Source: inbox/dev/D-20261013-07.md\n- Assigned: customer\n- Verdict: AUTO-APPROVED\n")
    assert seven() == ("customer", "approved, queued"), "a later Assigned wins, and an answered hand-back is not stuck"
    w("decisions/20261017b.md", "## D-20261013-07 | Hidden questions\n- Source: inbox/dev/D-20261013-07.md\n- Assigned: dev\n- Verdict: AUTO-APPROVED\n")
    assert seven() == ("dev", "approved, queued"), "resent to Dev: it builds again"
    w("inbox/dev/D-20261013-07.md", "Status: BLOCKED\nStill no.\n")   # a newer hand-back than the triage: stuck again
    t7 = time.time() + 5   # newer than the triage for sure: two writes in one instant can share a timestamp
    os.utime(os.path.join(ops, "inbox/dev/D-20261013-07.md"), (t7, t7))
    assert seven() == ("dev", "blocked"), seven()
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest(); sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    o = ap.parse_args()
    merged, reverted = git_sets(o.repo)
    json.dump(ledger(o.ops, merged, reverted), sys.stdout, indent=1, ensure_ascii=False)
