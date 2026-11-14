"""Question tags: every blind question ever written, with its product, category, marks over time and the
customer fixes tied to it, so a re-ask can pick the questions a merged fix may have changed (the CEO, 2026-11-14).

  python tools/question_tags.py build  <ops> <repo>        rebuild OPS/question_tags.json, keeping Customer's tags
  python tools/question_tags.py todo   <ops>               write OPS/state/tagging/part-<n>.json for Job TAG; prints PARTS <n>
  python tools/question_tags.py apply  <ops>               merge part-<n>.tags.json back; prints APPLIED <n>
  python tools/question_tags.py reask  <ops> <repo> <run>  RUN/cases.json from the questions tied to the customer
                                                           fixes merged since the last test run; prints REASK <n> or NONE
  python tools/question_tags.py --selftest

A question's record: product, category, type, set (the run or bank that wrote it), marks {run: mean 0-3},
found (work orders whose decision names it), fixes (work orders Customer judged it may test, Job TAG),
seen (the work orders Customer has judged it against), fixed_by (a tied fix merged between a wrong mark
and a later right one) and came_back (a tied fix after which it went right, then wrong again).
Only runs/<stamp>/ and bank/<stamp>/ sets count: hidden siblings stay out (the gate's check).
"""
import glob
import json
import os
import re
import subprocess
import sys

STAMP = re.compile(r"^\d{8}[a-z]?$")
PART = 300        # questions per Job TAG part
REASK_MAX = 80    # ponytail: a re-ask asks at most this many; the most recently wrong first


def _load(p, default):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _dump(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)


def work_orders(ops):
    """{id: title} of Customer's work orders that were not rejected (OPS/ledger.json)."""
    cards = _load(os.path.join(ops, "ledger.json"), {}).get("cards", [])
    return {c["id"]: c.get("title", "") for c in cards if c.get("from") == "customer" and c.get("stage") != "rejected"}


def merges(repo):
    """{work order id: unix time of its merge} from the product checkout's merge commits."""
    try:
        log = subprocess.run(["git", "-C", repo, "log", "--format=%ct %s", "--grep=agents/dev-"],
                             capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return {}
    out = {}
    for line in log.splitlines():
        m = re.match(r"(\d+) .*agents/dev-(D-\d{8}-\d+)", line)
        if m:
            out.setdefault(m.group(2), int(m.group(1)))   # newest first: keep the latest merge
    return out


def sets(ops):
    """[(stamp, folder, done time or None)] for every run and bank set, oldest first."""
    out = []
    for kind in ("runs", "bank"):
        for d in glob.glob(os.path.join(ops, kind, "*")):
            name = os.path.basename(d)
            if STAMP.match(name) and os.path.exists(os.path.join(d, "cases.json")):
                done = os.path.join(d, "DONE")
                out.append((name, d, os.path.getmtime(done) if kind == "runs" and os.path.exists(done) else None))
    return sorted(out, key=lambda s: (s[2] is None, s[2] or 0, s[0]))


def marks(run):
    """{case id: mean sys_normal score} over the run's repeats."""
    got = {}
    for p in glob.glob(os.path.join(run, "cases_state*.json")):
        if not re.search(r"cases_state(\.r\d+)?\.json$", p):
            continue
        for cid, g in (_load(p, {}).get("grades") or {}).items():
            v = (g.get("sys_normal") or {}).get("score")
            if v is not None:
                got.setdefault(cid, []).append(float(v))
    return {c: round(sum(v) / len(v), 2) for c, v in got.items()}


def found(ops):
    """{case id: [work orders whose decision text names it]}; "C-39" counts under the section's "Run:" stamp."""
    out = {}
    for p in glob.glob(os.path.join(ops, "decisions", "*.md")):
        text = open(p, encoding="utf-8").read()
        for sec in re.split(r"(?m)^## ", text)[1:]:
            m = re.match(r"(D-\d{8}-\d+)", sec)
            if not m:
                continue
            run = re.search(r"(?m)^- Run: runs/(\d{8}[a-z]?)", sec)
            ids = set(re.findall(r"\bC\d{8}[a-z]?-\d+\b", sec))
            if run:
                ids |= {f"C{run.group(1)}-{n}" for n in re.findall(r"\bC-(\d+)\b", sec)}
            for cid in ids:
                out.setdefault(cid, [])
                if m.group(1) not in out[cid]:
                    out[cid].append(m.group(1))
    return out


def build(ops, repo):
    path = os.path.join(ops, "question_tags.json")
    old = _load(path, {})
    when = merges(repo)
    names = found(ops)
    tags = {}
    for stamp, d, done in sets(ops):
        m = marks(d) if done else {}
        for c in _load(os.path.join(d, "cases.json"), {}).get("cases", []):
            t = tags.setdefault(c["id"], {"product": c.get("product"), "category": c.get("category"), "type": c.get("type"),
                                          "set": stamp, "marks": {}, "fixes": [], "seen": []})
            if c["id"] in m:
                t["marks"][stamp] = m[c["id"]]
                t.setdefault("_at", {})[stamp] = done
    for cid, t in tags.items():
        t["fixes"] = (old.get(cid) or {}).get("fixes", [])
        t["seen"] = (old.get(cid) or {}).get("seen", [])
        t["found"] = sorted(names.get(cid, []))
        tied = set(t["found"]) | set(t["fixes"])
        at = t.pop("_at", {})
        hist = sorted((at[s], v) for s, v in t["marks"].items())   # (time, mark), oldest first
        t["fixed_by"], t["came_back"] = [], []
        for (t0, v0), (t1, v1) in zip(hist, hist[1:]):
            for fx in sorted(tied):
                if t0 < when.get(fx, 0) <= t1 and v0 < 2 <= v1 and fx not in t["fixed_by"]:
                    t["fixed_by"].append(fx)
            if v0 >= 2 > v1:
                t["came_back"] += [fx for fx in t["fixed_by"] if fx not in t["came_back"]]
    _dump(path, tags)
    return tags


def todo(ops):
    """Part files for Job TAG: every question not yet judged against every live work order, with the work orders
    it still needs judging against. Grouped by product so a part reads like one product's set."""
    tags = _load(os.path.join(ops, "question_tags.json"), {})
    wo = work_orders(ops)
    sets_ = {d: _load(os.path.join(d, "cases.json"), {}) for _, d, _ in sets(ops)}
    text = {c["id"]: c.get("q", "") for s in sets_.values() for c in s.get("cases", [])}
    groups = {}   # the work orders still to judge -> [(product, id)]: one part judges all its questions against one list
    for cid, t in tags.items():
        need = tuple(sorted(set(wo) - set(t.get("seen", []))))
        if need:
            groups.setdefault(need, []).append((t.get("product") or "", cid))
    out = os.path.join(ops, "state", "tagging")
    for p in glob.glob(os.path.join(out, "part-*.json")):
        os.remove(p)
    n = 0
    for need, rows in sorted(groups.items()):
        rows.sort()
        for i in range(0, len(rows), PART):
            n += 1
            _dump(os.path.join(out, f"part-{n}.json"), {
                "work_orders": {w: wo[w] for w in need},
                "questions": [{"id": cid, "product": tags[cid].get("product"), "type": tags[cid].get("type"),
                               "q": text.get(cid, "")} for _, cid in rows[i:i + PART]]})
    return n


def apply(ops):
    """Merge part-<n>.tags.json ({case id: [work orders it may test]}) into the tags; every work order the part
    asked about counts as seen, so a question is judged against each work order once."""
    path = os.path.join(ops, "question_tags.json")
    tags = _load(path, {})
    n = 0
    for p in sorted(glob.glob(os.path.join(ops, "state", "tagging", "part-*.tags.json"))):
        part = _load(p.replace(".tags.json", ".json"), {})
        got = _load(p, {})
        asked = list(part.get("work_orders", {}))
        for q in part.get("questions", []):
            t = tags.get(q["id"])
            if t is None:
                continue
            t["fixes"] = sorted(set(t.get("fixes", [])) | {w for w in got.get(q["id"], []) if w in asked})
            t["seen"] = sorted(set(t.get("seen", [])) | set(asked))
            n += 1
        os.remove(p)
        os.remove(p.replace(".tags.json", ".json"))
    _dump(path, tags)
    return n


def reask(ops, repo, run):
    """RUN/cases.json: the asked questions tied (found or tagged) to Customer's work orders merged since the last
    finished run, the most recently wrong first, at most REASK_MAX. "NONE" when nothing is tied."""
    last = max((done for _, _, done in sets(ops) if done), default=0)
    wo = work_orders(ops)
    fixed = {w for w, t in merges(repo).items() if t > last and w in wo}
    tags = _load(os.path.join(ops, "question_tags.json"), {})
    pick = [cid for cid, t in tags.items() if t.get("marks") and fixed & (set(t.get("found", [])) | set(t.get("fixes", [])))]
    if not pick:
        return "NONE"
    pick = sorted(pick, key=lambda cid: max(tags[cid]["marks"]), reverse=True)              # newest set first,
    pick = sorted(pick, key=lambda cid: min(tags[cid]["marks"].values()) >= 2)[:REASK_MAX]   # then wrong once first
    cases = {}
    for _, d, done in sets(ops):
        if done:
            for c in _load(os.path.join(d, "cases.json"), {}).get("cases", []):
                if c["id"] in pick:
                    cases[c["id"]] = c   # the latest copy
    _dump(os.path.join(run, "cases.json"), {"cases": [cases[c] for c in pick if c in cases]})
    return f"REASK {len(cases)} for {', '.join(sorted(fixed))}"


def selftest():
    import tempfile
    ops = tempfile.mkdtemp()
    repo = os.path.join(ops, "git")
    w = lambda rel, obj: _dump(os.path.join(ops, rel), obj)
    case = lambda i, p="bv30": {"id": i, "product": p, "category": "technical", "type": "lookup", "q": f"q {i}"}
    w("runs/20261101/cases.json", {"cases": [case("C20261101-01"), case("C20261101-02", "nv4000")]})
    w("runs/20261101/cases_state.json", {"grades": {"C20261101-01": {"sys_normal": {"score": 0}},
                                                    "C20261101-02": {"sys_normal": {"score": 3}}}})
    open(os.path.join(ops, "runs/20261101/DONE"), "w").close()
    os.utime(os.path.join(ops, "runs/20261101/DONE"), (1000, 1000))
    w("bank/20261102/cases.json", {"cases": [case("C20261102-01")]})
    w("runs/_trial/cases.json", {"cases": [case("Cx-01")]})   # not a stamp folder: left out
    w("ledger.json", {"cards": [{"id": "D-20261101-01", "from": "customer", "stage": "merged", "title": "peak current"},
                                {"id": "D-20261101-02", "from": "customer", "stage": "rejected", "title": "no"}]})
    os.makedirs(os.path.join(ops, "decisions"))
    open(os.path.join(ops, "decisions/20261101.md"), "w").write("## D-20261101-01 | peak\n- Run: runs/20261101\n\nC-01 said 3.72A.\n")
    subprocess.run(f'git init -q "{repo}" && git -C "{repo}" -c user.name=t -c user.email=t commit -q --allow-empty '
                   "-m \"Merge branch 'agents/dev-D-20261101-01'\"", shell=True, check=True)
    t = build(ops, repo)
    assert set(t) == {"C20261101-01", "C20261101-02", "C20261102-01"}, t
    assert t["C20261101-01"]["found"] == ["D-20261101-01"] and t["C20261101-01"]["marks"] == {"20261101": 0.0}, t
    assert todo(ops) == 1
    part = _load(os.path.join(ops, "state/tagging/part-1.json"), {})
    assert list(part["work_orders"]) == ["D-20261101-01"], "rejected work orders are not asked about"
    _dump(os.path.join(ops, "state/tagging/part-1.tags.json"), {"C20261101-02": ["D-20261101-01", "D-9"]})
    assert apply(ops) == 3
    t = build(ops, repo)
    assert t["C20261101-02"]["fixes"] == ["D-20261101-01"], "only work orders it was asked about"
    assert todo(ops) == 0, "every question judged against every work order"
    run = os.path.join(ops, "runs/20261103")
    r = reask(ops, repo, run)
    assert r == "REASK 2 for D-20261101-01", r
    got = [c["id"] for c in _load(os.path.join(run, "cases.json"), {})["cases"]]
    assert got == ["C20261101-01", "C20261101-02"], "the wrong one first; the bank's unasked question stays out"
    # the re-ask's marks: fixed by the merge, then wrong again
    t0 = 1000
    for stamp, score in (("20261103", 3), ("20261104", 0)):
        w(f"runs/{stamp}/cases.json", {"cases": [case("C20261101-01")]})
        w(f"runs/{stamp}/cases_state.json", {"grades": {"C20261101-01": {"sys_normal": {"score": score}}}})
        open(os.path.join(ops, f"runs/{stamp}/DONE"), "w").close()
        t0 += 10 ** 10
        os.utime(os.path.join(ops, f"runs/{stamp}/DONE"), (t0, t0))
    t = build(ops, repo)["C20261101-01"]
    assert t["fixed_by"] == ["D-20261101-01"] and t["came_back"] == ["D-20261101-01"], t
    assert reask(ops, repo, run) == "NONE", "nothing merged since the last run"
    print("selftest ok")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--selftest"]:
        selftest()
    elif a[:1] == ["build"] and len(a) == 3:
        print(f"BUILT {len(build(a[1], a[2]))}")
    elif a[:1] == ["todo"] and len(a) == 2:
        print(f"PARTS {todo(a[1])}")
    elif a[:1] == ["apply"] and len(a) == 2:
        print(f"APPLIED {apply(a[1])}")
    elif a[:1] == ["reask"] and len(a) == 4:
        print(reask(a[1], a[2], a[3]))
    else:
        sys.exit(__doc__)
