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
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ops_ledger import PAST, answered, decisions, git_sets, stage_of  # noqa: E402
from ops_costs import cap_status  # noqa: E402
from ops_pivot import TEST_RUN, agg, run_facts, test_runs  # noqa: E402
from office_next import (STUCK, blocking, claims, every, holds, is_off, lane_cron, latest_usage, next_reset, plan,  # noqa: E402
                         stuck, stuck_why, work_mode)

DEPT = {"product-owner": "Product owner", "dev": "Dev", "customer": "Customer", "security": "Security",
        "audit": "Audit", "rnd": "R&D", "marketing": "Marketing", "merge": "Merge", "diary": "Diary",
        "lab": "Test lab"}


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


DOC_CAP = 230_000   # the page's database refuses a document over 256 KiB; the brief stays well under it


def fit(cards):
    """Finished items keep a short summary; if the brief would still be too big, they lose it, then open
    items are cut too. Over the cap the lanes' uploads are refused and the page reads 'stopped' (2026-10-16:
    108 items with their whole decision text made it 254 KB)."""
    size = lambda: len(json.dumps(cards, ensure_ascii=False).encode())
    for c in cards.values():
        if c["stage"] in PAST:
            c["text"] = c["text"][:400]
    for keep, done_too in ((0, True), (1200, False), (400, False)):
        if size() <= DOC_CAP:
            break
        for c in cards.values():
            if done_too == (c["stage"] in PAST):
                c["text"] = c["text"][:keep]
    return cards


def explain(line, titles):
    """One plan line -> {dept, state, why} in plain words."""
    name = lambda m: f"\u201c{titles.get(m.group(0), m.group(0))}\u201d"
    words = line.split()
    if words[0] == "IDLE":
        return {"dept": "Office", "state": "idle", "why": "Nothing is waiting for any department."}
    if words[0] == "MERGE":
        return {"dept": "Merge", "state": "run", "why": re.sub(r"D-\d{8}-\d+", name, "Merging " + words[1] + ": passed every check")}
    key = words[1].replace("groundedops-office-", "")
    dept = DEPT.get(key, words[1])
    why = re.sub(r"D-\d{8}-\d+", name, line.split(None, 2)[2] if len(words) > 2 else "")
    why = why.replace("held (OPS/state/hold)", "on hold").replace("new findings", "new reports to read")
    # "Run now" can lift a wait on the budget, a hold, or (Customer) on fixes in flight; never the lab cap or the office switch
    can = words[0] == "WAIT" and "lab stopped" not in line and any(
        k in line for k in ("would pass 70%", "ahead of the week's pace", "held (OPS/state/hold)", "customer fixes in flight"))
    return {"dept": dept, "key": key, "can_run": can, "state": "run" if words[0] == "RUN" else "wait", "why": why.strip()}


GOOD_GAP, GOOD_WRONG = 10, 1   # the CEO, 2026-10-12: when two blind runs in a row are this close, test on an unseen product


def ready_for_new_product(ops):
    """Why to feed a new product (overfitting check), or "". Good = the last two blind runs each within
    GOOD_GAP points of the whole-manual baseline and at most GOOD_WRONG wrong answers."""
    runs = test_runs(ops)
    last = [agg(f) for f in (run_facts(d) for d in runs[-4:]) if f][-2:]
    if len(last) < 2 or not all(a["base_pct"] is not None and a["base_pct"] - a["bot_pct"] <= GOOD_GAP
                                and a["wrong"] <= GOOD_WRONG for a in last):
        return ""
    return (f"The last two blind runs scored {last[0]['bot_pct']} and {last[1]['bot_pct']}, within {GOOD_GAP} points of an AI"
            " given the whole manual. To check the fixes did not just fit these manuals, add a product the office has"
            " never tested and ask the product owner to point Customer's next run at it.")


def alerts(ops, cards, stalled, off, repo=None, now=None):
    """What is stopping office work right now, for the page's pop-up: [{id, title, why, suggest, actions}].
    The id is stable while the problem lasts, so the page pops each problem once. Each action is a button:
    {label, choice, id, note} sent to tools/ceo_inbox.py, or {label, tab} to open a tab. A failed gate is not
    here: it is the check doing its job, and the product owner decides what happens next."""
    po_now = {"label": "Ask the product owner to decide now", "choice": "run_now", "id": "product-owner"}
    park = lambda i: {"label": "Park it for later", "choice": "defer", "id": i}
    out = []
    if off:
        out.append({"id": "off", "title": "The office is switched off", "why": "Nothing new starts while it is off.",
                    "suggest": "Switch it on if you did not mean to leave it off.",
                    "actions": [{"label": "Switch the office on", "choice": "on", "id": ""}]})
    elif stalled.startswith("Paused"):
        out.append({"id": "paused", "title": "The office is paused", "why": stalled,
                    "suggest": "Nothing to do: the next hourly check fetches fresh usage figures and carries on.", "actions": []})
    # a lane changes its own schedule when the CEO changes "Next run"; an unattended run cannot approve the
    # schedule tool, so lanes 2 and 3 kept the old hourly times for days while the page showed the new setting (2026-10-19)
    try:
        asked = os.path.getmtime(os.path.join(ops, "state", "every"))
    except OSError:
        asked = 0
    behind = [n for n in (1, 2, 3) if lane_cron_now(ops, n) != lane_cron(n, every(ops))]
    if behind and time.time() - asked > 90 * 60:
        out.append({"id": "cron-" + "-".join(map(str, behind)) + f"-{every(ops)}",
                    "title": "Your \u201cNext run\u201d setting has not reached every lane",
                    "why": f"Lane {', '.join(map(str, behind))} still runs on its old schedule: changing its own schedule needs"
                           " a person's approval, and nobody is there during a scheduled run.",
                    "suggest": "Ask Claude in any session to set the lanes to the page's \u201cNext run\u201d setting, or give those"
                               " lanes the same permissions as lane 1 in the app's Routines settings.", "actions": []})
    # a lane past its department's limit is likely waiting on a permission prompt nobody sees (office_next.py STUCK);
    # the office never stops it or drops its claim, so a person must
    held = claims(ops)
    for k, since in stuck(held, now or datetime.datetime.now(datetime.timezone.utc)).items():
        dept = DEPT.get(k.split("-D-")[0], k)
        out.append({"id": f"stuck-{k}-{since:%Y%m%d%H%M}", "title": f"Lane {held[k].get('lane')} looks stuck on {dept}",
                    "why": f"It has held {dept}'s work for more than {STUCK.get(k.split('-D-')[0], 120)} minutes, far longer than"
                           f" its runs take ({stuck_why(since)}). A scheduled run that hits a permission prompt waits for a"
                           " person who is not there.",
                    "suggest": "Open that lane's latest run in the Claude app and answer the prompt or stop the run. The other"
                               " lanes keep taking other work meanwhile.", "actions": []})
    lab = cap_status(ops)
    if lab["stopped"]:
        out.append({"id": f"cap-{lab.get('since')}", "title": "The test lab stopped at its spending cap",
                    "why": "Test runs and fix checks wait until the cap is raised.",
                    "suggest": "Raise the cap on the Costs tab if the spend is expected.",
                    "actions": [{"label": "Open the Costs tab", "tab": "costs"}]})
    runs = sorted(d for d in glob.glob(os.path.join(ops, "runs", "*")) if TEST_RUN.match(os.path.basename(d)))
    if runs and os.path.exists(os.path.join(runs[-1], "FAILED")):
        step = open(os.path.join(runs[-1], "FAILED"), encoding="utf-8").read().strip()
        out.append({"id": "run-" + os.path.basename(runs[-1]), "title": "The latest test run failed",
                    "why": f"The 40-question test stopped at its \u201c{step}\u201d step, so the score is not updated.",
                    "suggest": "Run it again. If it fails at the same step twice, ask the product owner to look at the run log.",
                    "actions": [{"label": "Run the test again", "choice": "run_now", "id": "customer"}, po_now]})
    for g in glob.glob(os.path.join(ops, "inbox", "dev", "*.gate")):
        i = os.path.basename(g)[:-5]
        if os.path.exists(os.path.join(g, "FAILED")) and not os.path.exists(os.path.join(g, "gate.json")):
            out.append({"id": "gate-" + i, "title": f"The check of \u201c{cards.get(i, {}).get('title', i)}\u201d could not run",
                        "why": open(os.path.join(g, "FAILED"), encoding="utf-8").read().strip() or "It stopped before giving a verdict.",
                        "suggest": "The product owner's next run retries the check. Start it now if you want the answer sooner.",
                        "actions": [{**po_now, "label": "Retry the check now"}]})
    items = decisions(ops)
    for i, c in cards.items():
        if repo and c["stage"] == "ready to merge":   # the merge never goes over someone's edit, so a person must clear it
            where, busy = blocking(i, repo)
            if busy:
                out.append({"id": f"merge-{i}-" + "-".join(busy), "title": f"“{c['title']}” is ready but cannot be merged",
                            "why": f"It changes {', '.join(busy)}, and {where} has uncommitted or untracked edits to "
                                   f"{'that file' if len(busy) == 1 else 'those files'}. The office never merges over someone's work.",
                            "suggest": "Commit, move or undo those edits (ask Claude in any session if you do not know whose they are);"
                                       " the next lane check then merges it.", "actions": []})
        if c["stage"] == "blocked":
            out.append({"id": "blocked-" + i, "title": f"Dev is stuck on \u201c{c['title']}\u201d",
                        "why": "Dev handed it back blocked: it could not finish the work as ordered.",
                        "suggest": "Let the product owner decide (send it back with new instructions, or close it), or park it.",
                        "actions": [po_now, park(i)]})
        done = os.path.join(ops, "inbox", c["assigned"], f"{i}-done.md")
        if c["stage"] == "approved, queued" and os.path.exists(done) and not answered(items.get(i, {}), ops, done):   # a resend is not stuck
            text = open(done, encoding="utf-8").read().strip()
            if not text.upper().startswith("STATUS: BLOCKED"):
                continue
            why = re.sub(r"(?i)^status:\s*blocked\s*[-:\u2013\u2014]?\s*", "", text)[:1500]
            # Dev never reads OPS/heldout, so hidden-question work cannot go to it: Dev would know the test for its own fix
            to_dev = c["assigned"] != "dev" and not re.search(r"hidden|held.?out", c["title"], re.I)
            acts = ([{"label": "Give it to Dev", "choice": "reassign", "id": i, "note": "dev"}] if to_dev else []) + [po_now, park(i)]
            out.append({"id": "wo-" + i, "title": f"{DEPT.get(c['assigned'], c['assigned'])} is stuck on \u201c{c['title']}\u201d", "why": why,
                        "suggest": ("Give it to Dev: Dev has every tool, including running the bot and the test lab, so it can do what "
                                    f"{DEPT.get(c['assigned'], c['assigned'])} could not. Or let the product owner decide, or park it."
                                    if to_dev else "Let the product owner decide, or park it."),
                        "actions": acts})
    return out


def _jitter(ops, lane):
    """Seconds the app delays this lane's task past its cron time, fixed per task (the scheduled-tasks tool reports it as
    jitterSeconds; OPS/state/jitter-<lane> holds it). Without it the page's countdown was up to 12 minutes early."""
    try:
        return int(open(os.path.join(ops, "state", f"jitter-{lane}"), encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return 0


def lane_cron_now(ops, lane):
    """The cron the lane's scheduled task has now (OPS/state/cron-<lane>, written by --cron-set); the CEO's setting if unset."""
    try:
        return open(os.path.join(ops, "state", f"cron-{lane}"), encoding="utf-8").read().strip() or lane_cron(lane, every(ops))
    except OSError:
        return lane_cron(lane, every(ops))


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
    taken = claims(ops)
    late = stuck(taken, now)
    running = [{**explain(c.get("line", "IDLE"), titles), "state": "now", "lane": c.get("lane"), "since": c.get("at")}
               for c in taken.values()]
    for r, k in zip(running, taken):   # shown, never stopped: a person answers the prompt
        if k in late:
            r["why"] += "; " + stuck_why(late[k])
            r["stuck"] = True
    if running and stalled.startswith("Everything with work is waiting"):
        stalled = ""   # waiting behind work that is running is not a stall
    return {"updated": now.isoformat(timespec="seconds"), "usage": {"five": five, "week": week,
            "minutes_old": None if old == float("inf") else round(old)}, "running": running,
            "mode": dict(zip(("mode", "why"), work_mode(ops, week, next_reset(now), now)), every=every(ops)), "moving": moving, "stalled": stalled, "off": is_off(ops),
            "lanes": [{"lane": n, "cron": lane_cron_now(ops, n), "jitter": _jitter(ops, n)} for n in (1, 2, 3)],   # the page's countdown
            "todo": todo, "items": fit(cards), "alerts": alerts(ops, cards, stalled, is_off(ops), repo, now),
            "report": open(reports[-1], encoding="utf-8").read()[:12000] if reports else ""}


def selftest():
    big = {f"D-{n}": {"title": "t", "stage": "merged" if n % 2 else "approved, queued", "text": "x" * 4000} for n in range(120)}
    fit(big)
    assert len(json.dumps(big).encode()) <= DOC_CAP and big["D-1"]["text"] == "" and big["D-0"]["text"], "fits, open items keep most"
    small = fit({"D-1": {"title": "t", "stage": "merged", "text": "y" * 900}, "D-2": {"title": "t", "stage": "proposed", "text": "z" * 900}})
    assert len(small["D-1"]["text"]) == 400 and len(small["D-2"]["text"]) == 900, "finished items shortened, open ones untouched"
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
        assert r == [{"dept": "Dev", "key": "dev", "can_run": False, "state": "now", "why": "build “Two products”", "lane": 2, "since": "2026-10-08T06:30:00+00:00"}], r
        os.remove(os.path.join(ops, "state", "claims", "dev-D-20261008-01.json"))   # by the clock it is stuck now, and alerts() below would say so
        assert brief(ops, ops, now, (11, 38, 300))["stalled"].startswith("Paused: the office's usage figures are 5 hours old")
        e = explain("WAIT groundedops-office-dev  build D-20261008-01; held (OPS/state/hold)", {"D-20261008-01": "Two products"})
        assert e == {"dept": "Dev", "key": "dev", "can_run": True, "state": "wait", "why": "build \u201cTwo products\u201d; on hold"}, e
        def run(name, bot, base):   # 3 cases, one score each
            d = os.path.join(ops, "runs", name); os.makedirs(d, exist_ok=True)
            json.dump({"cases": [{"id": f"c{k}", "q": "q", "type": "lookup"} for k in range(3)]}, open(os.path.join(d, "cases.json"), "w"))
            json.dump({"grades": {f"c{k}": {"sys_normal": {"score": bot[k]}, "baseline": {"score": base[k]}} for k in range(3)}},
                      open(os.path.join(d, "cases_state.json"), "w"))
            open(os.path.join(d, "DONE"), "w").close()
        run("20261001", [3, 3, 2], [3, 3, 2]); run("20261002", [3, 2, 3], [3, 2, 3])
        got = ready_for_new_product(ops)
        assert got.startswith("The last two blind runs scored 88.9 and 88.9"), got
        run("20261003", [3, 0, 2], [3, 3, 3])
        os.makedirs(os.path.join(ops, "runs", "20261009")); w("runs/20261009/FAILED", "grade\n")
        a = alerts(ops, {}, "", False)
        assert [x["id"] for x in a] == ["run-20261009"] and "grade" in a[0]["why"], a
        assert alerts(ops, {}, "", True)[0]["id"] == "off"
        os.makedirs(os.path.join(ops, "inbox", "customer"))
        for i in ("D-20261009-02", "D-20261010-10"):
            w(f"inbox/customer/{i}-done.md", "Status: BLOCKED - no lab tool\n")
        card = lambda t: {"title": t, "stage": "approved, queued", "assigned": "customer"}
        a = {x["id"]: [b.get("choice") for b in x["actions"]] for x in alerts(ops, {
            "D-20261009-02": card("Fresh hidden test questions for the multi-ask type"),
            "D-20261010-10": card("Re-ask four questions on the lab bot")}, "", False)}
        assert "reassign" not in a["wo-D-20261009-02"], "Dev must not write the hidden test for its own fix"
        assert "reassign" in a["wo-D-20261010-10"], a
        assert ready_for_new_product(ops) == "", "a bad run: keep fixing"
        real, ready = blocking, {"D-1": {"title": "Fix", "stage": "ready to merge", "assigned": "dev"}}
        try:   # a ready merge held up by someone's edit pops up; a clear one does not
            globals()["blocking"] = lambda i, repo: ("C:/repo", ["docs/x.md"])
            m = [x for x in alerts(ops, ready, "", False, "C:/repo") if x["id"].startswith("merge-")]
            assert len(m) == 1 and "docs/x.md" in m[0]["why"] and "C:/repo" in m[0]["why"], m
            assert not [x for x in alerts(ops, ready, "", False) if x["id"].startswith("merge-")], "no repo: no git check"
            globals()["blocking"] = lambda i, repo: ("C:/repo", [])
            assert not [x for x in alerts(ops, ready, "", False, "C:/repo") if x["id"].startswith("merge-")]
        finally:
            globals()["blocking"] = real
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
