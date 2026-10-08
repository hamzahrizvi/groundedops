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
from collections import defaultdict

DEPTS = ["customer", "audit", "security", "rnd", "marketing", "po", "dev", "lab"]
CAPACITY = {"dev": 3, "po": 8}          # ponytail: everyone else 2; tune when a board shows real overload
NOTES = re.compile(r"(-weekly|-market)\.md$|^review-")
PAST, FUTURE = {"merged", "rejected", "reverted"}, {"proposed", "waiting for CEO", "deferred"}


def _read(p):
    return open(p, encoding="utf-8").read()


def decisions(ops):
    items = {}
    for path in sorted(glob.glob(os.path.join(ops, "decisions", "*.md"))):
        for block in re.split(r"(?m)^## ", _read(path))[1:]:
            head, _, body = block.partition("\n")
            m = re.match(r"(D-\d{8}-\d+)\s*\|\s*(.+)", head.strip())
            if not m:
                continue
            did, title = m.groups()
            it = items.setdefault(did, {"id": did, "title": title.strip(), "history": [], "decided": did[2:10]})
            for k, v in re.findall(r"(?m)^- (Source|From|Assigned|Verdict|Run|Siblings):\s*(.+?)\s*$", body):
                if k == "Verdict":
                    it["history"].append(v.strip().upper())
                else:
                    it.setdefault(k.lower(), v.strip())
            if re.search(r"(?mi)^- \[x\] CEO approved", body):
                it["ceo_approved"] = True
    return items


def git_sets(repo):
    def run(*a):
        try:
            return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, timeout=30).stdout
        except Exception:
            return ""
    merged = {b.strip().lstrip("*+ ").replace("agents/dev-", "") for into in ("HEAD", "experimental/agent-org-phase1")
              for b in run("branch", "--merged", into, "--list", "agents/dev-*").splitlines() if b.strip()}  # product or office fix
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
    if it["id"] in merged:
        return "merged"
    if latest == "REJECT":
        return "rejected"
    if latest == "DEFER":
        return "deferred"
    if "NEEDS CEO" in it["history"] and not it.get("ceo_approved"):
        return "waiting for CEO"
    if os.path.exists(dev + ".md"):
        if re.search(r"(?m)^Status:\s*BLOCKED", _read(dev + ".md")):
            return "blocked"
        if gate:
            if gate.get("verdict") == "PASS":
                return "ready to merge" if latest == "READY TO MERGE" else "check passed"
            return "check failed"
        return "built, waiting for the check"
    return "sent back, rebuilding" if attempts else "approved, queued"


def ledger(ops, merged=frozenset(), reverted=frozenset(), today=None):
    today = today or datetime.date.today()
    items = decisions(ops)
    cited = {it.get("source", "").replace("\\", "/") for it in items.values()}
    cards = []
    for it in items.values():
        st = stage_of(it, ops, merged, reverted)
        cards.append({"id": it["id"], "title": it["title"], "from": it.get("from", ""), "assigned": it.get("assigned", "dev"),
                      "stage": st, "lane": "past" if st in PAST else "future" if st in FUTURE else "ongoing",
                      "decided": it["decided"]})
    for path in sorted(glob.glob(os.path.join(ops, "inbox", "*", "*.md"))):
        dept, name = os.path.basename(os.path.dirname(path)), os.path.basename(path)
        if dept == "dev" or NOTES.search(name) or f"inbox/{dept}/{name}" in cited:
            continue
        m = re.search(r"(?m)^#\s+(.+)$", _read(path))
        cards.append({"id": f"{dept}/{name[:-3]}", "title": (m.group(1) if m else name[:-3]).strip(), "from": dept,
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
    w("decisions/20261016.md", "## D-20261013-01 | Fix comparisons\n- Verdict: READY TO MERGE\n\n"
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
    r = ledger(ops, merged={"D-20261013-01"}, today=day)
    pts = {t["dept"]: t["points"] for t in r["rewards"]["table"]}
    assert (pts["customer"], pts["dev"], pts["po"]) == (4 + 5 + 3, 5 + 3, 1), pts
    assert r["rewards"]["leader"] == "customer" and r["cards"][0]["lane"] in ("past", "future", "ongoing")
    r = ledger(ops, merged={"D-20261013-01"}, reverted={"D-20261013-01"}, today=day)
    pts = {t["dept"]: t["points"] for t in r["rewards"]["table"]}
    assert pts["po"] == -3 and pts["dev"] == -3, pts                 # a revert costs, and earns no merge points
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
