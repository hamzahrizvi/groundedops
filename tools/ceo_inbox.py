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
from office_next import claims  # noqa: E402

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
        if i not in items:
            out.append(f"SKIPPED {doc_id} no such item {i!r}")
            continue
        st, title = stages[i], items[i]["title"]
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
