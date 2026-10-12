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
from ops_pivot import agg, run_facts  # noqa: E402
from office_next import claims, holds, is_off, latest_usage, next_reset, plan  # noqa: E402

DEPT = {"product-owner": "Product owner", "dev": "Dev", "customer": "Customer", "security": "Security",
        "audit": "Audit", "rnd": "R&D", "marketing": "Marketing", "merge": "Merge"}


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


GOOD_GAP, GOOD_WRONG = 10, 1   # the CEO, 2026-10-12: when two blind runs in a row are this close, test on an unseen product


def ready_for_new_product(ops):
    """Why to feed a new product (overfitting check), or "". Good = the last two blind runs each within
    GOOD_GAP points of the whole-manual baseline and at most GOOD_WRONG wrong answers."""
    runs = sorted(d for d in glob.glob(os.path.join(ops, "runs", "*"))
                  if os.path.exists(os.path.join(d, "DONE")) and os.path.exists(os.path.join(d, "cases_state.json")))
    last = [agg(f) for f in (run_facts(d) for d in runs[-4:]) if f][-2:]
    if len(last) < 2 or not all(a["base_pct"] is not None and a["base_pct"] - a["bot_pct"] <= GOOD_GAP
                                and a["wrong"] <= GOOD_WRONG for a in last):
        return ""
    return (f"The last two blind runs scored {last[0]['bot_pct']} and {last[1]['bot_pct']}, within {GOOD_GAP} points of an AI"
            " given the whole manual. To check the fixes did not just fit these manuals, add a product the office has"
            " never tested and ask the product owner to point Customer's next run at it.")


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
    good = ready_for_new_product(ops)
    if good:
        todo.append({"id": "new-product", "title": "Give the office a new product's manual", "kind": "info", "why": good})
    titles = {i: c["title"] for i, c in cards.items()}
    stalled = ""
    if old > 60:
        moving = []
        stalled = (f"Paused: the office's usage figures are {old / 60:.0f} hours old, and it starts nothing on"
                   " old figures. The hourly runner now fetches fresh ones itself.") if old != float("inf") else \
                  "Paused: the office has no usage figures, so it starts nothing."
    else:   # work another lane is doing shows under "running", not as a wait
        moving = [m for m in (explain(x, titles) for x in plan(ops, repo, five, week, next_reset(now), now))
                  if "already running (lane" not in m["why"]]
        if moving and all(m["state"] == "wait" for m in moving):
            stalled = "Everything with work is waiting: " + "; ".join(f"{m['dept']}: {m['why']}" for m in moving)
    held = sorted(holds(ops, now))
    if is_off(ops):
        moving, held = [], []
        stalled = "Paused: you switched the office off. Work already started finishes; nothing new starts until you switch it on."
    if held:
        stalled = (stalled + " " if stalled else "") + "On hold by hand: " + ", ".join(held) + "."
    reports = sorted(glob.glob(os.path.join(ops, "reports", "*.md")))
    running = [{**explain(c.get("line", "IDLE"), titles), "state": "now", "lane": c.get("lane"), "since": c.get("at")}
               for c in claims(ops).values()]
    if running and stalled.startswith("Everything with work is waiting"):
        stalled = ""   # waiting behind work that is running is not a stall
    return {"updated": now.isoformat(timespec="seconds"), "usage": {"five": five, "week": week,
            "minutes_old": None if old == float("inf") else round(old)}, "running": running, "moving": moving, "stalled": stalled, "off": is_off(ops),
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
        for p in ("runs/20261008/DONE", "state/security_last_run.txt", "inbox/audit/20261008-weekly.md",
                  "state/rnd_last_run.txt", "state/marketing_last_run.txt"):
            w(p)
        b = brief(ops, ops, now, (11, 38, 5))
        assert [(t["id"], t["kind"]) for t in b["todo"]] == [("D-20261008-01", "approve"), ("D-20261008-07", "action")], b["todo"]
        assert "Why: comparisons fail." in b["items"]["D-20261008-01"]["text"]
        assert "[latest decision]" in b["items"]["D-20261008-07"]["text"] and "Still open." in b["items"]["D-20261008-07"]["text"]
        assert "# Later triage" not in b["items"]["D-20261008-07"]["text"], "a block stops at the next heading"
        assert b["moving"] == [] or all(m["dept"] for m in b["moving"]), b["moving"]
        assert b["report"].startswith("# Report") and not b["stalled"] and b["running"] == [], b
        os.makedirs(os.path.join(ops, "state", "claims"))
        w("state/claims/dev-D-20261008-01.json", json.dumps({"lane": 2, "at": "2026-10-08T06:30:00+00:00",
                                                              "line": "RUN  groundedops-office-dev  build D-20261008-01"}))
        r = brief(ops, ops, now, (11, 38, 5))["running"]
        assert r == [{"dept": "Dev", "state": "now", "why": "build “Two products”", "lane": 2, "since": "2026-10-08T06:30:00+00:00"}], r
        assert brief(ops, ops, now, (11, 38, 300))["stalled"].startswith("Paused: the office's usage figures are 5 hours old")
        e = explain("WAIT groundedops-office-dev  build D-20261008-01; held (OPS/state/hold)", {"D-20261008-01": "Two products"})
        assert e == {"dept": "Dev", "state": "wait", "why": "build \u201cTwo products\u201d; on hold"}, e
        def run(name, bot, base):   # 3 cases, one score each
            d = os.path.join(ops, "runs", name); os.makedirs(d, exist_ok=True)
            json.dump({"cases": [{"id": f"c{k}", "q": "q", "type": "lookup"} for k in range(3)]}, open(os.path.join(d, "cases.json"), "w"))
            json.dump({"grades": {f"c{k}": {"sys_normal": {"score": bot[k]}, "baseline": {"score": base[k]}} for k in range(3)}},
                      open(os.path.join(d, "cases_state.json"), "w"))
            open(os.path.join(d, "DONE"), "w").close()
        run("r1", [3, 3, 2], [3, 3, 2]); run("r2", [3, 2, 3], [3, 2, 3])
        got = ready_for_new_product(ops)
        assert got.startswith("The last two blind runs scored 88.9 and 88.9"), got
        run("r3", [3, 0, 2], [3, 3, 3])
        assert ready_for_new_product(ops) == "", "a bad run: keep fixing"
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
