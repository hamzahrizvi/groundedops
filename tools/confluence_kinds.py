"""Measure what kind each synced Confluence page is, from its title and folder path (D-20261024-04, measuring only).

  python tools/confluence_kinds.py label  <data_dir>
  python tools/confluence_kinds.py sample <data_dir> <out.jsonl> [--n 120]
  python tools/confluence_kinds.py score  <labels.jsonl> <key.json>
  python tools/confluence_kinds.py --selftest

<data_dir> is a COPY of the data (chroma_db, internal_sources.json, confluence_sync.json); the live index is never
opened. Nothing here changes src/.
"""
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

KINDS = ("manual", "test_script", "drawing", "certificate", "change_notice", "release_note", "other")
SECONDARY_KINDS = ("test_script", "drawing", "certificate", "change_notice", "release_note")

_TITLE = [
    ("test_script", r"test\s*scripts?|testing\s*procedure|test\s*report|test\s*plan|intertek|field\s*trials?"),
    ("certificate", r"certificat|declaration|conformity|compliance|\(doc\)|-dec\b|-(?:ul|cb)\b|\brohs\b|\breach\b|\bweee\b|\bukca\b|\bfcc\b"),
    ("change_notice", r"change\s*notif|\bpcn\b"),
    ("release_note", r"release\s*(?:notes?|documentation)|changelog"),
    ("drawing", r"^(?:cn|wr|pa|ga|mc|pm)[\s_-]?\d{3}|^[a-z]{2}\d{5}|drawing|\bassembly\b|\bassy\b"),
]
_PATH = [
    ("test_script", r"test\s*scripts?"),
    ("certificate", r"product\s*compliance|cumplimiento|certificat"),
    ("change_notice", r"change\s*notification"),
    ("release_note", r"release\s*notes"),
    ("drawing", r"spare\s*parts"),
]
_PATH_FILE = [("drawing", r"appendix|ap.ndice|disclaimer\s*and\s*safety")]
_TITLE = [(k, re.compile(p, re.I)) for k, p in _TITLE]
_PATH = [(k, re.compile(p, re.I)) for k, p in _PATH]
_PATH_FILE = [(k, re.compile(p, re.I)) for k, p in _PATH_FILE]


def kind_of(title, path, item_kind):
    t = title or ""
    for k, rx in _TITLE:
        if rx.search(t):
            return k
    rules = _PATH + (_PATH_FILE if item_kind == "file" else [])
    for folder in reversed(list(path or [])):
        for k, rx in rules:
            if rx.search(folder or ""):
                return k
    return "manual"


def _env(data):
    data = os.path.abspath(data)
    os.environ["CHROMA_DIR"] = os.path.join(data, "chroma_db")
    os.environ["INTERNAL_SOURCES_PATH"] = os.path.join(data, "internal_sources.json")
    os.chdir(data)
    return data


def _state(data):
    return json.load(open(os.path.join(data, "confluence_sync.json"), encoding="utf-8"))["items"]


def _synced(items):
    return {it["source"]: it for it in items.values()
            if it.get("present") and it.get("synced_version") is not None and it.get("source")}


def _col():
    import chromadb
    return chromadb.PersistentClient(path=os.environ["CHROMA_DIR"]).get_collection("docs")


def _index(data):
    got = _col().get(include=["metadatas"])
    chunks = defaultdict(list)
    for i, m in zip(got["ids"], got["metadatas"]):
        chunks[m.get("source")].append((m.get("page") or 0, i, m))
    return chunks


def cmd_label(data):
    data = _env(data)
    import retrieval_db
    items = _state(data)
    syn = _synced(items)
    chunks = _index(data)
    kinds = {}
    for s in chunks:
        it = syn.get(s)
        kinds[s] = kind_of(it["title"], it.get("path"), it.get("kind")) if it else kind_of(s, [], "file")
    json.dump(kinds, open(os.path.join(data, "..", "kinds.json"), "w", encoding="utf-8"), indent=0, sort_keys=True)
    print(f"{'kind':14}{'synced items':>14}{'indexed sources':>17}{'chunks':>8}")
    for k in KINDS:
        srcs = [s for s in chunks if kinds[s] == k]
        print(f"{k:14}{sum(1 for s in srcs if s in syn):>14}{len(srcs):>17}{sum(len(chunks[s]) for s in srcs):>8}")
    print(f"{'total':14}{sum(1 for s in chunks if s in syn):>14}{len(chunks):>17}{sum(len(v) for v in chunks.values()):>8}")
    a = [s for s in chunks if retrieval_db.is_secondary(s) and kinds[s] == "manual"]
    b = [s for s in chunks if not retrieval_db.is_secondary(s) and kinds[s] != "manual"]
    print(f"\ntoday's name rule disagrees with (kind != manual) on {len(a) + len(b)} of {len(chunks)} sources:")
    print(f"  secondary today, manual by kind: {len(a)} ({sum(len(chunks[s]) for s in a)} chunks)")
    for s in a[:10]:
        print("    ", s)
    print(f"  full weight today, not manual by kind: {len(b)} ({sum(len(chunks[s]) for s in b)} chunks)")
    for s in b[:10]:
        print("    ", s, "->", kinds[s])
    import confluence_sync as cs
    appr = [it for it in items.values() if it.get("present") and it.get("decision") == "approved"
            and any(cs._INTERNAL_TITLE.search(f or "") for f in (it.get("path") or []))]
    print(f"\nstep 4: approved present items under a folder titled 'internal': {len(appr)}")
    for it in appr[:40]:
        print("    ", it["title"], "|", " > ".join(it.get("path") or []))
    print("items already decision 'internal':", sum(1 for it in items.values() if it.get("decision") == "internal"))


def cmd_sample(data, out, n=120):
    data = _env(data)
    items = _state(data)
    syn = _synced(items)
    chunks = _index(data)
    rows = {s: it for s, it in syn.items() if s in chunks}
    pred = {s: kind_of(it["title"], it.get("path"), it.get("kind")) for s, it in rows.items()}
    rng = random.Random(20261024)
    order = sorted(rows)
    rng.shuffle(order)
    pick, per = [], Counter()
    for s in order:
        if per[pred[s]] < 15:
            per[pred[s]] += 1
            pick.append(s)
    for s in order:
        if len(pick) >= n:
            break
        if s not in pick:
            pick.append(s)
    pick = pick[:n]
    rng.shuffle(pick)      # the file's order says nothing about the prediction
    col = _col()
    with open(out, "w", encoding="utf-8") as f:
        for s in pick:
            it = rows[s]
            first = sorted(chunks[s], key=lambda x: (x[0], x[1]))[0]
            text = col.get(ids=[first[1]], include=["documents"])["documents"][0]
            f.write(json.dumps({"source": s, "title": it["title"], "path": " > ".join(it.get("path") or []),
                                "item_kind": it.get("kind"), "text": text[:600]}, ensure_ascii=False) + "\n")
    json.dump({s: pred[s] for s in pick}, open(out + ".key.json", "w", encoding="utf-8"), indent=0)
    print(f"{len(pick)} sampled -> {out}")


def cmd_score(labels, key):
    import retrieval_db
    key = json.load(open(key, encoding="utf-8"))
    lab = [json.loads(line) for line in open(labels, encoding="utf-8") if line.strip()]
    lab = [x for x in lab if x["source"] in key]
    print(f"n = {len(lab)}")
    by_pred, by_true = defaultdict(list), defaultdict(list)
    for x in lab:
        by_pred[key[x["source"]]].append(x["true_kind"])
        by_true[x["true_kind"]].append(key[x["source"]])
    print("\nper predicted kind: n / right / wrong, and what the wrong ones really were")
    for k in KINDS:
        v = by_pred.get(k, [])
        wrong = Counter(t for t in v if t != k)
        print(f"  {k:14} n={len(v):3} right={len(v) - sum(wrong.values()):3} wrong={sum(wrong.values()):3} {dict(wrong)}")
    print("\nper true kind: n / recall, and what they were predicted as")
    for k in KINDS:
        v = by_true.get(k, [])
        r = sum(1 for p in v if p == k)
        print(f"  {k:14} n={len(v):3} recall={r}/{len(v)}" + (f" {100 * r / len(v):.0f}%" if v else "")
              + f" predicted as {dict(Counter(p for p in v if p != k))}")
    bad = sum(1 for x in lab if key[x["source"]] != x["true_kind"])
    print(f"\noverall wrong: {bad}/{len(lab)} = {100 * bad / max(1, len(lab)):.1f}%")
    rel = re.compile(r"change\s*noti|release\s*(?:notes|documentation)", re.I)
    sec, sec2 = set(SECONDARY_KINDS), {"test_script", "drawing", "certificate"}
    head = sum(1 for x in lab if retrieval_db.is_secondary(x["source"]) != (x["true_kind"] in sec))
    b = sum(1 for x in lab if (retrieval_db.is_secondary(x["source"]) and not rel.search(x["source"]))
            != (x["true_kind"] in sec2))
    new = sum(1 for x in lab if (key[x["source"]] in sec) != (x["true_kind"] in sec))
    print("\nsecondary-or-not errors (binary), same sample:")
    print(f"  today's name rule, HEAD (secondary = scripts, drawings, certificates, notices, release notes): {head}/{len(lab)}")
    print(f"  today's name rule, 2e9fbb3 (secondary = scripts, drawings, certificates):                      {b}/{len(lab)}")
    print(f"  the kind rule (predicted kind in the HEAD set), for comparison:                                {new}/{len(lab)}")


def selftest():
    P = ["Products", "SMART Coin System Range"]
    cases = [
        ("General Instructions", P + ["SMART Coin System Integration Test Script"], "page", "test_script"),
        ("NV201 Spectral - Revision 3.13", P + ["NV201 Spectral Product Change Notification"], "page", "change_notice"),
        ("PA02265 - NV9USB+ Full Assembly V2", [], "page", "drawing"),
        ("NV9USB+ Disassembly Guide", P + ["NV9USB+ Range Repair Information"], "page", "manual"),
        ("CN214_1 USB A to USB B Cable Assembly (att644645101)", P + ["NV200 Range Disclaimer and Safety Information"], "file", "drawing"),
        ("EU Declaration of Conformity RoHS NV4000 07May2025 GA02336_1", [], "file", "certificate"),
        ("BV50 Firmware Release Notes", [], "page", "release_note"),
        ("Accessing my device in Linux Environment", ["Products", "Biometrics", "Biometric Range FAQ's", "Technical FAQs"], "page", "manual"),
        ("NV200S Range User Manual", [], "bundle", "manual"),
        ("Get Device Status", ["Products", "Biometrics", "API Specifications"], "page", "manual"),
    ]
    for t, p, k, want in cases:
        got = kind_of(t, p, k)
        assert got == want, (t, got, want)
    print("selftest ok")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["--selftest"]:
        selftest()
    elif a[:1] == ["label"] and len(a) == 2:
        cmd_label(a[1])
    elif a[:1] == ["sample"] and len(a) >= 3:
        cmd_sample(a[1], os.path.abspath(a[2]), int(a[a.index("--n") + 1]) if "--n" in a else 120)
    elif a[:1] == ["score"] and len(a) == 3:
        cmd_score(a[1], a[2])
    else:
        sys.exit(__doc__)
