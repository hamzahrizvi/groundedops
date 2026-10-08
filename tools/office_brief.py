"""The office page's brief: what is moving, what is stuck and why, what needs the CEO, the latest
report, and each item's decision text, for HQ's pop-ups and its product-owner chat.

  python tools/office_brief.py [--ops C:/Users/hrizvi/groundedops-ops] [--repo ...] > OPS/brief.json
  python tools/office_brief.py --selftest

The dispatcher uploads the output to the office page (collection "brief", doc "current") every
tick, so its "updated" time is also when the hourly runner last ran.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ops_ledger import decisions, git_sets, stage_of  # noqa: E402
from office_next import holds, latest_usage, next_reset, plan  # noqa: E402

DEPT = {"product-owner": "Product owner", "dev": "Dev", "customer": "Customer", "security": "Security",
        "audit": "Audit", "merge": "Merge"}


def blocks(ops):
    """{id: [each '## id' section's text, oldest first]}"""
    out = {}
    for path in sorted(glob.glob(os.path.join(ops, "decisions", "*.md"))):
        for block in re.split(r"(?m)^## ", open(path, encoding="utf-8").read())[1:]:
            m = re.match(r"(D-\d{8}-\d+)", block)
            if m:   # a section runs until the next "# " heading or "## " block
                out.setdefault(m.group(1), []).append(re.split(r"(?m)^# ", "## " + block)[0].strip())
    return out


def item_text(texts):
    """The first decision (why and work order) plus the latest one, trimmed for a chat prompt."""
    t = texts[0][:1800]
    return t if len(texts) == 1 else t + "\n\n[latest decision]\n" + texts[-1][:2200]


def explain(line, titles):
    """One plan line -> {dept, state, why} in plain words."""
    name = lambda m: f"\u201c{titles.get(m.group(0), m.group(0))}\u201d"
    words = line.split()
    if words[0] == "IDLE":
        return {"dept": "Office", "state": "idle", "why": "Nothing is waiting for any department."}
    if words[0] == "MERGE":
        return {"dept": "Merge", "state": "run", "why": re.sub(r"D-\d{8}-\d+", name, "Merging " + words[1] + ": passed every check")}
    dept = DEPT.get(words[1].replace("groundedops-office-", ""), words[1])
    why = re.sub(r"D-\d{8}-\d+", name, line.split(None, 2)[2] if len(words) > 2 else "")
    why = why.replace("held (OPS/state/hold)", "on hold").replace("new findings", "new reports to read")
    return {"dept": dept, "state": "run" if words[0] == "RUN" else "wait", "why": why.strip()}


def brief(ops, repo, now=None, usage=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    items, texts = decisions(ops), blocks(ops)
    merged, reverted = git_sets(repo)
    five, week, old = usage or latest_usage(fallback=os.path.join(ops, "state", "usage.json"))
    cards, todo = {}, []
    for i, it in items.items():
        st = stage_of(it, ops, merged, reverted)
        cards[i] = {"title": it["title"], "stage": st, "assigned": it.get("assigned", "dev"),
                    "from": it.get("from", ""), "text": item_text(texts.get(i, [""]))}
        if st == "waiting for CEO":
            todo.append({"id": i, "title": it["title"], "kind": "approve",
                         "why": "The product owner needs your yes before anyone builds it."})
        elif it.get("assigned") == "ceo" and st == "approved, queued":
            todo.append({"id": i, "title": it["title"], "kind": "action",
                         "why": "Only you can do this. Mark it done once you have."})
    titles = {i: c["title"] for i, c in cards.items()}
    stalled = ""
    if old > 60:
        moving = []
        stalled = (f"Paused: the office's usage figures are {old / 60:.0f} hours old, and it starts nothing on"
                   " old figures. The hourly runner now fetches fresh ones itself.") if old != float("inf") else \
                  "Paused: the office has no usage figures, so it starts nothing."
    else:
        moving = [explain(x, titles) for x in plan(ops, repo, five, week, next_reset(now), now)]
        if moving and all(m["state"] == "wait" for m in moving):
            stalled = "Everything with work is waiting: " + "; ".join(f"{m['dept']}: {m['why']}" for m in moving)
    held = sorted(holds(ops, now))
    if held:
        stalled = (stalled + " " if stalled else "") + "On hold by hand: " + ", ".join(held) + "."
    reports = sorted(glob.glob(os.path.join(ops, "reports", "*.md")))
    return {"updated": now.isoformat(timespec="seconds"), "usage": {"five": five, "week": week,
            "minutes_old": None if old == float("inf") else round(old)}, "moving": moving, "stalled": stalled,
            "todo": todo, "items": cards,
            "report": open(reports[-1], encoding="utf-8").read()[:12000] if reports else ""}


def selftest():
    now = datetime.datetime(2026, 10, 8, 7, 0, tzinfo=datetime.timezone.utc)
    with tempfile.TemporaryDirectory() as ops:
        for d in ("decisions", "inbox/dev", "inbox/audit", "runs/20261008", "state", "reports"):
            os.makedirs(os.path.join(ops, d))
        w = lambda p, s="": open(os.path.join(ops, p), "w", encoding="utf-8").write(s)
        w("decisions/20261008.md", "# Triage\n\n## D-20261008-01 | Two products\n- Assigned: dev\n- Verdict: NEEDS CEO\n- [ ] CEO approved\n\nWhy: comparisons fail.\n\n"
                                   "## D-20261008-07 | Manuals\n- Assigned: ceo\n- Verdict: NEEDS CEO\n- [x] CEO approved\n\nPass the list on.\n\n"
                                   "# Later triage\n\n## D-20261008-07 | Manuals\n- Verdict: NEEDS CEO\n- [x] CEO approved\n\nStill open.\n")
        w("reports/20261008.md", "# Report\nAll fine.\n")
        for p in ("runs/20261008/DONE", "state/security_last_run.txt", "inbox/audit/20261008-weekly.md"):
            w(p)
        b = brief(ops, ops, now, (11, 38, 5))
        assert [(t["id"], t["kind"]) for t in b["todo"]] == [("D-20261008-01", "approve"), ("D-20261008-07", "action")], b["todo"]
        assert "Why: comparisons fail." in b["items"]["D-20261008-01"]["text"]
        assert "[latest decision]" in b["items"]["D-20261008-07"]["text"] and "Still open." in b["items"]["D-20261008-07"]["text"]
        assert "# Later triage" not in b["items"]["D-20261008-07"]["text"], "a block stops at the next heading"
        assert b["moving"] == [] or all(m["dept"] for m in b["moving"]), b["moving"]
        assert b["report"].startswith("# Report") and not b["stalled"], b["stalled"]
        assert brief(ops, ops, now, (11, 38, 300))["stalled"].startswith("Paused: the office's usage figures are 5 hours old")
        e = explain("WAIT groundedops-office-dev  build D-20261008-01; held (OPS/state/hold)", {"D-20261008-01": "Two products"})
        assert e == {"dept": "Dev", "state": "wait", "why": "build \u201cTwo products\u201d; on hold"}, e
    print("selftest ok")


def main():
    office = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--repo", default=os.path.normpath(os.path.join(office, "..", "..", "..")))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(brief(a.ops, a.repo), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
