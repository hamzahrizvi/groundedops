"""The test lab's live feed for the office page: each blind question asked so far and the chatbot's answer.

  python tools/lab_feed.py <run_dir> <out.json> [--wait SECONDS]
  python tools/lab_feed.py --selftest

Writes <out.json> = {run, updated, phase, done, failed, total, asked, rows: [{id, product, q, a, s, r}]} in asking
order (repeat r=1 for all 40, then the repeats that re-ask the misses), and prints one line: DONE, FAILED or
RUNNING <asked>/<total>, plus " SAME" when nothing changed since the last write, so there is nothing to upload.
customer-weekly.md step 3 loops on --wait, which first waits up to SECONDS or until the run finishes, and uploads
the file to the page (collection "labfeed", doc "current"). Answers are cut to 600 characters: three repeats of 40
stay well under the page store's 256 KiB per row. Only the chatbot's answers: the whole-manual baseline is not shown.
Never a hidden set: a run under OPS/heldout is refused (office-status.md).
"""
import glob
import json
import os
import re
import sys
import time

CUT = 600


def feed(run):
    cases = json.load(open(os.path.join(run, "cases.json"), encoding="utf-8"))["cases"]
    files = sorted(glob.glob(os.path.join(run, "cases_state*.json")),
                   key=lambda p: int((re.search(r"\.r(\d+)\.json$", p) or [0, 1])[1]))
    rows = []
    for f in files:
        r = int((re.search(r"\.r(\d+)\.json$", f) or [0, 1])[1])
        try:
            got = json.load(open(f, encoding="utf-8")).get("sys_normal", {})
        except ValueError:   # caught mid-write by blind_eval's save(); the next call reads it whole
            continue
        for c in cases:
            x = got.get(c["id"])
            if x and not x.get("copied_from_r1"):
                a = x.get("answer") or ""
                rows.append({"id": c["id"], "product": c.get("product") or "none", "q": c["q"],
                             "a": a if len(a) <= CUT else a[:CUT].rstrip() + "...", "s": x.get("wall"), "r": r})
    log = os.path.join(run, "run.log")
    steps = [l for l in open(log, encoding="utf-8", errors="replace")] if os.path.exists(log) else []
    step = next((l for l in reversed(steps) if l.startswith("== ")), "")
    phase = ("Grading the answers" if "grade" in step else "The whole-manual AI is answering" if "baseline" in step
             else "Asking the chatbot" if step else "Starting")
    failed = os.path.exists(os.path.join(run, "FAILED"))
    done = os.path.exists(os.path.join(run, "DONE"))
    return {"run": os.path.basename(run), "phase": "Finished" if done else "Stopped" if failed else phase,
            "done": done, "failed": failed, "total": len(cases), "asked": len({x["id"] for x in rows}), "rows": rows}


def main(run, out, wait=0):
    run = os.path.abspath(run)
    if "heldout" in os.path.normpath(run).lower().split(os.sep):
        sys.exit("hidden questions never go to the office page")
    end = time.time() + wait
    while time.time() < end and not any(os.path.exists(os.path.join(run, m)) for m in ("DONE", "FAILED")):
        time.sleep(5)
    new = feed(run)
    try:
        old = json.load(open(out, encoding="utf-8"))
        old.pop("updated", None)
    except (OSError, ValueError):
        old = None
    same = old == new
    if not same:
        new["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        json.dump(new, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    state = "DONE" if new["done"] else "FAILED" if new["failed"] else f"RUNNING {new['asked']}/{new['total']}"
    return state + (" SAME" if same else "")


def selftest():
    import tempfile
    d = tempfile.mkdtemp()
    run, out = os.path.join(d, "20261102"), os.path.join(d, "feed.json")
    os.makedirs(run)
    json.dump({"cases": [{"id": "C1", "q": "How?", "product": "nv9"}, {"id": "C2", "q": "Why?"}]}, open(os.path.join(run, "cases.json"), "w"))
    json.dump({"sys_normal": {"C1": {"answer": "x" * 700, "wall": 3.1}}}, open(os.path.join(run, "cases_state.json"), "w"))
    open(os.path.join(run, "run.log"), "w").write("== system normal (sys_normal) 10:00:00\n")
    assert main(run, out) == "RUNNING 1/2"
    f = json.load(open(out))
    assert f["phase"] == "Asking the chatbot" and len(f["rows"][0]["a"]) == CUT + 3 and f["updated"]
    assert main(run, out) == "RUNNING 1/2 SAME", "unchanged: nothing to upload"
    json.dump({"sys_normal": {"C1": {"copied_from_r1": True}, "C2": {"answer": "b", "wall": 1}}}, open(os.path.join(run, "cases_state.r2.json"), "w"))
    json.dump({"sys_normal": {"C2": {"answer": "c", "wall": 1}}}, open(os.path.join(run, "cases_state.r10.json"), "w"))
    open(os.path.join(run, "run.log"), "a").write("== grade --repeats 3 (sys_normal) 10:05:00\n")
    open(os.path.join(run, "DONE"), "w").close()
    assert main(run, out, wait=60) == "DONE", "a finished run never waits"
    f = json.load(open(out))
    assert [(x["id"], x["r"], x["product"]) for x in f["rows"]] == [("C1", 1, "nv9"), ("C2", 2, "none"), ("C2", 10, "none")]
    assert f["asked"] == 2 and f["phase"] == "Finished"
    try:
        main(os.path.join(d, "heldout", "x"), out)
        raise AssertionError("heldout refused")
    except SystemExit:
        pass
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        a = sys.argv[1:]
        w = int(a[a.index("--wait") + 1]) if "--wait" in a else 0
        print(main(a[0], a[1], w))
