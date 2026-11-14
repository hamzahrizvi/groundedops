"""Feed for the "Where the chatbot is failing today" pop-up on the wrong-answers artifact (the CEO, 2026-11-14).

  python tools/failing_feed.py <ops> <repo> <out_dir>      writes <out_dir>/today.json and <out_dir>/history.json
  python tools/failing_feed.py --selftest

Upload each as ArtifactData `set` collection "failing", doc_id "today" / "history" (file_path = the file).
today:   Customer's open work orders (not merged, done or rejected) with the asked questions tied to them whose
         latest mark is under 2, then the wrong questions no open work order covers.
history: merged customer work orders, newest first, with the questions each fixed (fixed_by) and the ones that
         went wrong again after it (came_back). Reads OPS/question_tags.json (tools/question_tags.py build).
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from question_tags import _dump, _load, merges, sets   # noqa: E402

CLOSED = {"merged", "done", "rejected"}


def feed(ops, repo):
    tags = _load(os.path.join(ops, "question_tags.json"), {})
    cards = [c for c in _load(os.path.join(ops, "ledger.json"), {}).get("cards", []) if c.get("from") == "customer"]
    text = {}
    for _, d, done in sets(ops):
        for c in _load(os.path.join(d, "cases.json"), {}).get("cases", []):
            text[c["id"]] = c.get("q", "")[:160]
    row = lambda cid: {"id": cid, "product": tags[cid].get("product"), "q": text.get(cid, ""),
                       "mark": tags[cid]["marks"][max(tags[cid]["marks"])], "run": max(tags[cid]["marks"])}
    wrong = {cid for cid, t in tags.items() if t.get("marks") and t["marks"][max(t["marks"])] < 2}
    tied = lambda cid: set(tags[cid].get("found", [])) | set(tags[cid].get("fixes", []))
    open_ = [c for c in cards if c.get("stage") not in CLOSED]
    covered = set()
    rows = []
    for c in sorted(open_, key=lambda c: (int(c.get("priority") or 99), c["id"])):
        qs = sorted((cid for cid in wrong if c["id"] in tied(cid)), key=lambda cid: tags[cid]["marks"][max(tags[cid]["marks"])])
        covered |= set(qs)
        rows.append({"id": c["id"], "title": c.get("title", ""), "stage": c.get("stage", ""), "priority": c.get("priority"),
                     "wrong": [row(q) for q in qs]})
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    today = {"updated": now, "wrong_total": len(wrong), "open": rows,
             "uncovered": sorted((row(q) for q in wrong - covered), key=lambda r: (r["product"] or "", r["id"]))}
    when = merges(repo)
    hist = []
    for c in cards:
        if c.get("stage") not in ("merged", "done"):
            continue
        fixed = sorted(cid for cid, t in tags.items() if c["id"] in t.get("fixed_by", []))
        back = [cid for cid in fixed if c["id"] in tags[cid].get("came_back", [])]
        at = when.get(c["id"])
        hist.append({"id": c["id"], "title": c.get("title", ""), "stage": c["stage"],
                     "merged": datetime.datetime.fromtimestamp(at).strftime("%Y-%m-%d") if at else None,
                     "tied": sum(c["id"] in tied(cid) for cid in tags),
                     "fixed": [dict(row(q), back=q in back) for q in fixed]})
    hist.sort(key=lambda h: (h["merged"] or "", h["id"]), reverse=True)
    return today, {"updated": now, "fixes": hist}


def selftest():
    import subprocess
    import tempfile
    ops = tempfile.mkdtemp()
    _dump(os.path.join(ops, "runs/20261101/cases.json"), {"cases": [{"id": "C1", "q": "peak current?"}, {"id": "C2", "q": "escrow?"},
                                                                     {"id": "C3", "q": "weight?"}]})
    open(os.path.join(ops, "runs/20261101/DONE"), "w").close()
    _dump(os.path.join(ops, "question_tags.json"), {
        "C1": {"product": "bv30", "marks": {"20261101": 0}, "found": ["D-1"], "fixes": []},
        "C2": {"product": "nv200s", "marks": {"20261101": 1}, "found": [], "fixes": []},
        "C3": {"product": "cba9", "marks": {"20261020": 0, "20261101": 3}, "found": [], "fixes": ["D-2"], "fixed_by": ["D-2"], "came_back": []}})
    _dump(os.path.join(ops, "ledger.json"), {"cards": [
        {"id": "D-1", "from": "customer", "stage": "approved, queued", "priority": "2", "title": "peak"},
        {"id": "D-2", "from": "customer", "stage": "merged", "title": "weights"},
        {"id": "D-3", "from": "customer", "stage": "rejected", "title": "no"}]})
    repo = os.path.join(ops, "git")
    subprocess.run(f'git init -q "{repo}" && git -C "{repo}" -c user.name=t -c user.email=t commit -q --allow-empty '
                   "-m \"Merge branch 'agents/dev-D-2'\"", shell=True, check=True)
    today, hist = feed(ops, repo)
    assert [r["id"] for r in today["open"]] == ["D-1"] and today["open"][0]["wrong"][0]["id"] == "C1", today
    assert [r["id"] for r in today["uncovered"]] == ["C2"] and today["wrong_total"] == 2, today
    assert [h["id"] for h in hist["fixes"]] == ["D-2"] and hist["fixes"][0]["fixed"][0]["id"] == "C3", hist
    print("selftest ok")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]:
        selftest()
    elif len(a) == 3:
        t, h = feed(a[0], a[1])
        _dump(os.path.join(a[2], "today.json"), t)
        _dump(os.path.join(a[2], "history.json"), h)
        print(f"FEED {t['wrong_total']} wrong, {len(t['open'])} open fixes, {len(h['fixes'])} merged")
    else:
        sys.exit(__doc__)
