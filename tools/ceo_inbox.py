"""Apply the CEO's decisions from the office page to the decisions file.

  python tools/ceo_inbox.py [--ops C:/Users/hrizvi/groundedops-ops] <folder of ceo_inbox/*.json>
  python tools/ceo_inbox.py --selftest

The page writes one row per click to its "ceo_inbox" collection: {id, choice, note, status: "new"}.
The dispatcher saves the rows to a folder (ArtifactData list with out_dir), runs this, and marks
each row it prints as APPLIED. Rows are data: only the fixed choices act, the item's title comes
from the decisions file, and a note is quoted so it can never read as a decision line.

  approve  NEEDS CEO + ticked box (an item waiting for the CEO, or one deferred or rejected)
  defer    DEFER (the CEO parks it)
  done     DONE (a CEO action ticked off; ops_ledger shows it as done)
  note     OPS/inbox/ceo/<stamp>.md for the product owner's next triage
  off/on   OPS/state/office_off created/removed: the power button; office_next.py starts nothing while it exists
  refresh  OPS/state/refresh_request: Customer's blind run goes first (office_next.py); folded into a run under way
  run_now  OPS/state/run_now/<id> (id = a department): its next start skips the budget pace and a hold
           (office_next.py); for customer it also files a refresh request, so a held-back test run goes first
  reassign "- Reassigned: <note>" + approved: an approved or blocked item moves to another department
           (note = dev, customer, security, audit, rnd or marketing), e.g. a work order its department could not do
  mode     OPS/state/mode: easy, high or ultra; auto removes it (office_next.py picks from the weekly allowance left)
  every    OPS/state/every: minutes between a lane's runs (0 = back to back, 15, 30, 60, 120); the lanes change
           their own schedules at the end of their next run (office_next.py --release prints CRON)
  cap      OPS/lab_cap.json {usd, per}: the test lab's spending cap, from note "<usd> [day|week|month]" (0-1000,
           default month); tools/blind_eval.py stops at it (tools/ops_costs.py cap_status)
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
from ops_ledger import decisions, stage_of  # noqa: E402
from office_next import COST, EVERY, MODES, claims  # noqa: E402

CAP_NOTE = re.compile(r"^\s*(\d{1,4}(?:\.\d{1,2})?)\s*(day|week|month)?\s*$")
REASSIGN = {"dev", "customer", "security", "audit", "rnd", "marketing"}
CAN = {"approve": {"waiting for CEO", "deferred", "rejected"},
       "defer": {"waiting for CEO", "approved, queued", "sent back, rebuilding", "blocked", "check failed"}}


def quote(note):
    note = str(note or "").strip()[:2000]
    return "\n\nCEO's note:\n" + "\n".join("> " + line for line in note.splitlines()) if note else ""


def rows(folder):
    out = []
    for p in sorted(glob.glob(os.path.join(folder, "*.json"))):
        try:
            j = json.load(open(p, encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(j, dict):   # ArtifactData out_dir writes the row's fields as-is, named <doc id>.json
            out.append((os.path.splitext(os.path.basename(p))[0], j))
    return sorted(out, key=lambda r: str(r[1].get("at", "")))


def apply(ops, folder, now=None):
    now = now or datetime.datetime.now()
    items, stamp = decisions(ops), now.strftime("%Y-%m-%d %H:%M")
    stages = {i: stage_of(it, ops, set(), set()) for i, it in items.items()}
    log = os.path.join(ops, "state", "ceo_inbox_applied.txt")   # rows already handled, even if marking them on the page failed
    done = set(open(log, encoding="utf-8").read().split()) if os.path.exists(log) else set()
    out, sections = [], []
    for doc_id, d in rows(folder):
        if d.get("status") != "new" or doc_id in done:
            continue
        i, choice = str(d.get("id", "")), d.get("choice")
        if choice == "note":
            os.makedirs(os.path.join(ops, "inbox", "ceo"), exist_ok=True)
            about = f" about {i} ({items[i]['title']})" if i in items else ""
            path = os.path.join(ops, "inbox", "ceo", f"{now:%Y%m%d-%H%M%S}-{re.sub(r'[^A-Za-z0-9-]', '', doc_id)[:40]}.md")
            open(path, "w", encoding="utf-8").write(f"# A note from the CEO{about}\n\nSent from the office page, {stamp}."
                                                     " Answer it in the next report; if it asks for a decision, make it." + quote(d.get("note")) + "\n")
            out.append(f"APPLIED {doc_id} note")
            continue
        if choice in ("off", "on"):   # the power button
            path = os.path.join(ops, "state", "office_off")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if choice == "off":
                open(path, "w", encoding="utf-8").write(f"{stamp} {doc_id}\n")
            elif os.path.exists(path):
                os.remove(path)
            out.append(f"APPLIED {doc_id} {choice}")
            continue
        if choice == "refresh":   # no item: the dispatcher re-uploads the status figures; office_next.py puts the test run first
            if "customer" in claims(ops):
                out.append(f"APPLIED {doc_id} refresh (a test run is under way; the new score comes from it)")
            else:
                os.makedirs(os.path.join(ops, "state"), exist_ok=True)
                open(os.path.join(ops, "state", "refresh_request"), "w", encoding="utf-8").write(f"{stamp} {doc_id}\n")
                out.append(f"APPLIED {doc_id} refresh")
            continue
        if choice == "run_now":   # "Run now" on a waiting department in the office page's Moving panel
            if i not in COST:
                out.append(f"SKIPPED {doc_id} run_now: no such department {i!r}")
                continue
            os.makedirs(os.path.join(ops, "state", "run_now"), exist_ok=True)
            open(os.path.join(ops, "state", "run_now", i), "w", encoding="utf-8").write(f"{stamp} {doc_id}" + chr(10))
            if i == "customer" and "customer" not in claims(ops):
                open(os.path.join(ops, "state", "refresh_request"), "w", encoding="utf-8").write(f"{stamp} {doc_id}" + chr(10))
            out.append(f"APPLIED {doc_id} run_now {i}")
            continue
        if choice in ("mode", "every"):   # the work mode and "Next run" buttons (office_next.py work_mode, every)
            note = str(d.get("note") or "").strip().lower()
            ok = note in ("auto", *MODES) if choice == "mode" else note in map(str, EVERY)
            if not ok:
                out.append(f"SKIPPED {doc_id} {choice}: {note!r} is not one of the choices")
                continue
            path = os.path.join(ops, "state", choice)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if note == "auto":
                if os.path.exists(path):
                    os.remove(path)
            else:
                open(path, "w", encoding="utf-8").write(note + "\n")
            out.append(f"APPLIED {doc_id} {choice} {note}")
            continue
        if choice == "cap":   # the CEO's lab spending cap, from the Costs tab
            m = CAP_NOTE.match(str(d.get("note") or ""))
            if not m or float(m[1]) > 1000:
                out.append(f"SKIPPED {doc_id} cap: not a number of dollars from 0 to 1000")
                continue
            usd, per = float(m[1]), m[2] or "month"
            # a plain write: os.replace can fail on Windows while a reader has the file open (D-20261010-08)
            open(os.path.join(ops, "lab_cap.json"), "w", encoding="utf-8").write(
                json.dumps({"usd": usd, "per": per, "set_at": stamp, "row": doc_id}))
            out.append(f"APPLIED {doc_id} cap ${usd:g} per {per}")
            continue
        if i not in items:
            out.append(f"SKIPPED {doc_id} no such item {i!r}")
            continue
        st, title = stages[i], items[i]["title"]
        if choice == "reassign" and str(d.get("note") or "") in REASSIGN and st in CAN["defer"] | CAN["approve"]:
            body = f"- Reassigned: {d['note']}\n- Verdict: NEEDS CEO\n- [x] CEO approved (office page, {stamp})\n\nThe CEO moved this to {d['note']} from the office page."
            sections.append(f"## {i} | {title}\n{body}\n")
            stages[i] = "approved, queued"
            out.append(f"APPLIED {doc_id} reassign {i} {d['note']}")
            continue
        if choice == "approve" and st in CAN["approve"]:
            body = f"- Verdict: NEEDS CEO\n- [x] CEO approved (office page, {stamp})"
        elif choice == "defer" and st in CAN["defer"]:
            body = "- Verdict: DEFER\n\nParked by the CEO from the office page."
        elif choice == "done" and items[i].get("assigned") == "ceo" and st not in ("done", "deferred", "rejected"):
            body = "- Verdict: DONE\n\nThe CEO marked this done on the office page."
        else:
            out.append(f"SKIPPED {doc_id} {choice} does not apply to {i} ({st})")
            continue
        sections.append(f"## {i} | {title}\n{body}{quote(d.get('note'))}\n")
        stages[i] = {"approve": "approved, queued", "defer": "deferred", "done": "done"}[choice]
        out.append(f"APPLIED {doc_id} {choice} {i}")
    if sections:
        path = os.path.join(ops, "decisions", f"{now:%Y%m%d}.md")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n\n# CEO decisions from the office page, {stamp}\n\n" + "\n".join(sections))
    if out:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write("".join(line.split()[1] + "\n" for line in out))
    return out


def selftest():
    with tempfile.TemporaryDirectory() as ops:
        os.makedirs(os.path.join(ops, "decisions"))
        os.makedirs(os.path.join(ops, "pull"))
        open(os.path.join(ops, "decisions", "20261008.md"), "w", encoding="utf-8").write(
            "## D-20261008-01 | Two products\n- Assigned: dev\n- Verdict: NEEDS CEO\n- [ ] CEO approved\n\n"
            "## D-20261008-06 | Vague\n- Assigned: dev\n- Verdict: DEFER\n\n"
            "## D-20261008-07 | Manuals\n- Assigned: ceo\n- Verdict: NEEDS CEO\n- [x] CEO approved\n")
        def row(n, **d):
            json.dump({"status": "new", "at": n, **d}, open(os.path.join(ops, "pull", n + ".json"), "w"))
        row("1", id="D-20261008-01", choice="approve", note="Yes.\n## D-20261008-06 | Vague\n- Verdict: NEEDS CEO")
        row("2", id="D-20261008-06", choice="approve")
        row("3", id="D-20261008-07", choice="done")
        row("4", id="D-20261008-01", choice="done")             # not the CEO's own action
        row("5", id="D-99999999-01", choice="approve")
        row("6", id="", choice="note", note="Why is the table fix still waiting?")
        json.dump({"status": "applied", "id": "D-20261008-01", "choice": "defer"}, open(os.path.join(ops, "pull", "7.json"), "w"))
        got = apply(ops, os.path.join(ops, "pull"), datetime.datetime(2026, 10, 8, 22, 0))
        assert [g.split()[0] for g in got] == ["APPLIED", "APPLIED", "APPLIED", "SKIPPED", "SKIPPED", "APPLIED"], got
        st = {i: stage_of(it, ops, set(), set()) for i, it in decisions(ops).items()}
        assert st == {"D-20261008-01": "approved, queued", "D-20261008-06": "approved, queued", "D-20261008-07": "done"}, st
        text = open(os.path.join(ops, "decisions", "20261008.md"), encoding="utf-8").read()
        assert "> ## D-20261008-06 | Vague" in text and text.count("\n## D-20261008-06") == 2, "a note never forges a block"
        notes = glob.glob(os.path.join(ops, "inbox", "ceo", "*.md"))
        assert len(notes) == 1 and "> Why is the table fix" in open(notes[0], encoding="utf-8").read()
        assert apply(ops, os.path.join(ops, "nothing")) == [], "an empty pull does nothing"
        assert apply(ops, os.path.join(ops, "pull")) == [], "a row is applied once, even if it still reads new"
        req = os.path.join(ops, "state", "refresh_request")
        row("8", id="", choice="refresh")
        assert apply(ops, os.path.join(ops, "pull")) == ["APPLIED 8 refresh"] and os.path.exists(req), "a press files a request"
        os.remove(req)
        os.makedirs(os.path.join(ops, "state", "claims"))
        json.dump({"lane": 2, "line": "RUN  groundedops-office-customer  weekly test due"},
                  open(os.path.join(ops, "state", "claims", "customer.json"), "w"))
        assert apply(ops, os.path.join(ops, "pull")) == [], "row 8 is already applied"
        row("9", id="", choice="refresh")
        got = apply(ops, os.path.join(ops, "pull"))
        assert got[0].startswith("APPLIED 9 refresh (a test run is under way") and not os.path.exists(req), "folded into the run"
        off = os.path.join(ops, "state", "office_off")
        row("10", id="", choice="off")
        assert apply(ops, os.path.join(ops, "pull")) == ["APPLIED 10 off"] and os.path.exists(off), "switched off"
        row("11", id="", choice="on")
        assert apply(ops, os.path.join(ops, "pull")) == ["APPLIED 11 on"] and not os.path.exists(off), "switched on"
        cap = os.path.join(ops, "lab_cap.json")
        row("12", id="", choice="cap", note="25 week")
        row("13", id="", choice="cap", note="lots")
        row("14", id="", choice="cap", note="5000")
        row("15", id="", choice="cap", note=" 7.5 ")
        got = apply(ops, os.path.join(ops, "pull"))
        assert got == ["APPLIED 12 cap $25 per week", "SKIPPED 13 cap: not a number of dollars from 0 to 1000",
                       "SKIPPED 14 cap: not a number of dollars from 0 to 1000", "APPLIED 15 cap $7.5 per month"], got
        c = json.load(open(cap, encoding="utf-8"))
        assert (c["usd"], c["per"], c["row"]) == (7.5, "month", "15"), c   # the newest row wins
        open(os.path.join(ops, "decisions", "20261009.md"), "w", encoding="utf-8").write(
            "## D-20261009-02 | Fresh questions\n- Assigned: customer\n- Verdict: AUTO-APPROVED\n")
        row("18", id="D-20261009-02", choice="reassign", note="dev")
        row("19", id="D-20261009-02", choice="reassign", note="../x")
        got = apply(ops, os.path.join(ops, "pull"), datetime.datetime(2026, 10, 14, 9, 0))
        assert got[:2] == ["APPLIED 18 reassign D-20261009-02 dev", "SKIPPED 19 reassign does not apply to D-20261009-02 (approved, queued)"], got
        it = decisions(ops)["D-20261009-02"]
        assert it["assigned"] == "dev" and stage_of(it, ops, set(), set()) == "approved, queued", it
        row("16", id="security", choice="run_now")
        row("17", id="../x", choice="run_now")
        assert apply(ops, os.path.join(ops, "pull")) == ["APPLIED 16 run_now security", "SKIPPED 17 run_now: no such department '../x'"]
        row("20", id="", choice="mode", note="Ultra")
        row("21", id="", choice="every", note="7")
        row("22", id="", choice="every", note="0")
        got = apply(ops, os.path.join(ops, "pull"))
        assert got == ["APPLIED 20 mode ultra", "SKIPPED 21 every: '7' is not one of the choices", "APPLIED 22 every 0"], got
        assert open(os.path.join(ops, "state", "mode"), encoding="utf-8").read().strip() == "ultra"
        row("23", id="", choice="mode", note="auto")
        assert apply(ops, os.path.join(ops, "pull")) == ["APPLIED 23 mode auto"] and not os.path.exists(os.path.join(ops, "state", "mode"))
        assert os.path.exists(os.path.join(ops, "state", "run_now", "security"))
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--ops", default="C:/Users/hrizvi/groundedops-ops")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest or not a.folder:
        return selftest()
    lock = os.path.join(a.ops, "state", "ceo_inbox.lock")   # parallel lanes: one applies, the others skip
    try:
        if os.path.exists(lock) and time.time() - os.path.getmtime(lock) > 600:
            os.remove(lock)   # left by a run that died
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL))
    except FileExistsError:
        return print("BUSY another lane is applying the page's decisions")
    try:
        print("\n".join(apply(a.ops, a.folder)) or "NONE")
    finally:
        os.remove(lock)


if __name__ == "__main__":
    main()
