"""Measure copies, replaced uploads and the "main shelf only" arm for synced Confluence pages (D-20261024-04, measuring only).

  python tools/confluence_shelves_measure.py dupes   <data_dir> <pre_sync_manuals_dir>
  python tools/confluence_shelves_measure.py arm-c   <data_dir> <out.json>
  python tools/confluence_shelves_measure.py prepare <src_run> <dst_run>
  python tools/confluence_shelves_measure.py cases   <label>=<run_dir> ...
  python tools/confluence_shelves_measure.py --selftest

<data_dir> is a COPY of the data; `label` of tools/confluence_kinds.py must have written <data_dir>/../kinds.json.
Nothing here changes src/.
"""
import glob
import json
import os
import re
import shutil
import statistics as st
import sys
import tempfile
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
# the retained originals live in the main checkout, not in a worktree
os.environ.setdefault("SOURCE_FILE_DIR", os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "documents")))
OPS = "C:/Users/hrizvi/groundedops-ops"

import confluence_kinds as ck  # noqa: E402

RIGHT = ("correct", "correct_refusal", "clarified")
REFUSED = ("correct_refusal", "safe_decline")


def _chunks(data):
    return ck._index(data)


def _first_products(chunks, s):
    first = sorted(chunks[s], key=lambda x: (x[0], x[1]))[0][2]
    return [p for p in (first.get("products") or first.get("product") or "").split(",") if p]


def cmd_dupes(data, pre_dir):
    data = ck._env(data)
    import confluence_sync as cs
    import docstore
    kinds = json.load(open(os.path.join(data, "..", "kinds.json"), encoding="utf-8"))
    items = ck._state(data)
    syn = ck._synced(items)
    internal = set(json.load(open(os.path.join(data, "internal_sources.json"), encoding="utf-8")))
    chunks = _chunks(data)
    groups = defaultdict(list)
    for s in chunks:
        k = cs._title_key(s)
        if k:
            groups[k].append(s)
    groups = {k: sorted(v) for k, v in groups.items() if len(v) > 1}

    def lang(s):
        it = syn.get(s) or {}
        return "es" if re.search(r"\(es\)|manual de usuario|cumplimiento|ap.ndice",
                                 s + " " + " ".join(it.get("path") or []), re.I) else "en"

    print(f"(a) indexed sources sharing a title key: {len(groups)} groups, {sum(len(v) for v in groups.values())} sources")
    gk = Counter()
    for key, v in sorted(groups.items()):
        kind = Counter(kinds.get(s) for s in v).most_common(1)[0][0]
        gk[kind] += 1
        print(f"\n  [{kind}] {key}")
        for s in v:
            print(f"    {s} | chunks={len(chunks[s])} products={','.join(_first_products(chunks, s))} "
                  f"synced={s in syn} internal={s in internal} kind={kinds.get(s)} lang={lang(s)}")
    print("\ngroups per kind:", dict(gk))
    table = Counter()
    for v in groups.values():
        if Counter(kinds.get(s) for s in v).most_common(1)[0][0] == "manual":
            for p in sorted({p for s in v for p in _first_products(chunks, s)} or {"(none)"}):
                table[(p, lang(v[0]))] += 1
    print("manual groups per (product, language):")
    for (p, lg), n in sorted(table.items()):
        print(f"  {p:28}{lg}  {n}")
    keep_syn = keep_big = 0
    nosyn = 0
    for v in groups.values():
        tot = sum(len(chunks[s]) for s in v)
        keep_big += tot - max(len(chunks[s]) for s in v)
        sy = [s for s in v if s in syn]
        if sy:
            keep_syn += tot - len(chunks[sy[0]])
        else:
            nosyn += 1
    print(f"chunks removed if only the synced copy is kept: {keep_syn} (groups with no synced copy, untouched: {nosyn})")
    print(f"chunks removed if only the largest copy is kept: {keep_big}")

    print("\n(b) copies in the answers' sources (sys_normal, r1..r3 of both runs): in sources / listed first")
    inn, first = Counter(), Counter()
    member = {s: k for k, v in groups.items() for s in v}
    for run in ("20261023_reask", "20261024_newdocs_w07"):
        for f in sorted(glob.glob(f"{OPS}/runs/{run}/cases_state*.json")):
            for row in json.load(open(f, encoding="utf-8")).get("sys_normal", {}).values():
                src = [s for s in (row.get("sources") or {}) if s in member]
                for s in src:
                    inn[s] += 1
                if src:
                    first[src[0]] += 1
    for k, v in sorted(groups.items()):
        print(f"  {k}: " + "; ".join(f"{s} [{inn[s]}/{first[s]}]" for s in v))

    print("\n(c) replaced uploads: a document in documents/ that is not indexed, whose title key is an indexed synced source's")
    skey = {}
    for s in syn:
        if s in chunks:
            skey.setdefault(cs._title_key(s), s)
    old = {}
    for p in glob.glob(os.path.join(pre_dir, "*.txt")):
        stem = os.path.splitext(os.path.basename(p))[0]
        for line in open(p, encoding="utf-8", errors="replace"):
            m = re.match(r"^\[(.+?) p\d+\]$", line.strip())
            if m:
                old.setdefault(m.group(1), set()).add(stem)
    rep = lost = 0
    for d in docstore.inventory():
        name = d["source"]
        if name in chunks or name == "manifest.json":
            continue
        new_s = skey.get(cs._title_key(name))
        if not new_s:
            continue
        rep += 1
        o, n = sorted(old.get(name, [])), set(_first_products(chunks, new_s))
        if o and not set(o) <= n:
            lost += 1
            print(f"  LOST {name} -> {new_s}: {o} -> {sorted(n)}")
    print(f"replaced uploads: {rep}; lost a product: {lost}")

    print("\n(d) the Linux page, C20261022c-17")
    for name in ("Accessing my device in Linux Environment-v2-20250224_144232 2.pdf",
                 "Accessing my device in Linux Environment.txt"):
        try:
            indexed = chunks.get(name)
            text = ""
            if indexed:
                got = ck._col().get(ids=[i for _, i, _ in indexed], include=["documents"])["documents"]
                text = "\n".join(got)
            print(f"  {name}: in documents={bool(docstore.find(name))} indexed={bool(indexed)} "
                  f"chunks={len(indexed or [])} products={_first_products(chunks, name) if indexed else []} "
                  f"RNDIS={'RNDIS' in text} udev={'udev' in text} 192.168.137.8={'192.168.137.8' in text}")
        except Exception as e:                       # a missing file is a finding, not a crash
            print(f"  {name}: {e!r}")


def cmd_armc(data, out):
    data = ck._env(data)
    kinds = json.load(open(os.path.join(data, "..", "kinds.json"), encoding="utf-8"))
    chunks = _chunks(data)
    internal = json.load(open(os.path.join(data, "internal_sources.json"), encoding="utf-8"))
    scripts = {s for s, k in kinds.items() if k == "test_script"}
    allout = sorted(set(internal) | scripts)
    json.dump(allout, open(out, "w", encoding="utf-8"), indent=0)
    print(f"arm C: {len(allout)} sources out of reach ({len(internal)} internal today + {len(scripts - set(internal))} test scripts), "
          f"{sum(len(chunks.get(s, [])) for s in allout)} chunks; test scripts alone: {len(scripts)} sources, "
          f"{sum(len(chunks.get(s, [])) for s in scripts)} chunks")


def _filter(cases):
    return [c for c in cases if c.get("type") == "procedure_or_troubleshoot"]


def cmd_prepare(src, dst):
    src, dst = os.path.abspath(src), os.path.abspath(dst)
    if os.path.exists(dst):
        sys.exit(f"refuse: {dst} exists")
    if "heldout" in src.replace("\\", "/").lower():
        sys.exit("refuse: held-out source")
    os.makedirs(dst)
    c = json.load(open(os.path.join(src, "cases.json"), encoding="utf-8"))
    c["cases"] = _filter(c["cases"])
    json.dump(c, open(os.path.join(dst, "cases.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    for f in ("cases_state.json", "cases_state.r2.json", "cases_state.r3.json"):
        shutil.copy(os.path.join(src, f), os.path.join(dst, f))
    shutil.copytree(os.path.join(src, "manuals"), os.path.join(dst, "manuals"))
    print(f"{dst}: {len(c['cases'])} cases")


def _states(run):
    return [json.load(open(f, encoding="utf-8")) for f in [os.path.join(run, "cases_state.json")]
            + sorted(glob.glob(os.path.join(run, "cases_state.r*.json")))]


def _row(arm_run, kinds):
    ids = [c["id"] for c in json.load(open(os.path.join(arm_run, "cases.json"), encoding="utf-8"))["cases"]]
    out = {}
    for cid in sorted(ids):
        verdicts, scr, walls, usds = [], 0, [], []
        for s in _states(arm_run):
            g = ((s.get("grades") or {}).get(cid) or {}).get("sys_normal") or {}
            r = (s.get("sys_normal") or {}).get(cid) or {}
            v = g.get("verdict")
            if v is None:
                continue
            cites = any(kinds.get(src) == "test_script" for src in (r.get("sources") or {}))
            verdicts.append((v, cites))
            scr += cites
            if "wall" in r:
                walls.append(r["wall"])
                usds.append(r.get("usd", 0))
        out[cid] = {"verdicts": verdicts, "script": scr, "wall": st.mean(walls) if walls else None,
                    "usd": st.mean(usds) if usds else None}
    return out


def _bucket(v):
    return "refused" if v in REFUSED else v


def cmd_cases(specs, kinds):
    arms = {}
    for a in specs:
        label, run = a.split("=", 1)
        arms[label] = _row(run, kinds)
    ids = sorted(set().union(*[set(r) for r in arms.values()]))
    labels = list(arms)
    print("| case | " + " | ".join(f"{l} r1..r3 (scripts cited, s, $)" for l in labels) + " |")
    print("|---|" + "---|" * len(labels))
    for cid in ids:
        cells = []
        for l in labels:
            r = arms[l].get(cid)
            if not r:
                cells.append("-")
                continue
            vs = ",".join(v for v, _ in r["verdicts"])
            cells.append(f"{vs} ({r['script']}, {r['wall']:.0f}s, {r['usd']:.4f})" if r["wall"] is not None else vs)
        print(f"| {cid} | " + " | ".join(cells) + " |")
    print("\n| arm | answers | correct | partial | wrong | refused | clarified | citing a script | verdicts of those |")
    print("|---|---|---|---|---|---|---|---|---|")
    for l in labels:
        allv = [x for r in arms[l].values() for x in r["verdicts"]]
        c = Counter(_bucket(v) for v, _ in allv)
        cite = Counter(_bucket(v) for v, k in allv if k)
        print(f"| {l} | {len(allv)} | {c['correct']} | {c['partial']} | {c['wrong']} | {c['refused']} | {c['clarified']} "
              f"| {sum(cite.values())} | {dict(cite)} |")


def selftest():
    cs = [{"id": "a", "type": "procedure_or_troubleshoot"}, {"id": "b", "type": "lookup"}]
    assert [c["id"] for c in _filter(cs)] == ["a"]
    with tempfile.TemporaryDirectory() as d:
        json.dump({"cases": [{"id": "a", "type": "procedure_or_troubleshoot"}]}, open(os.path.join(d, "cases.json"), "w"))
        for name, v in (("cases_state.json", "correct"), ("cases_state.r2.json", "wrong"), ("cases_state.r3.json", "safe_decline")):
            json.dump({"grades": {"a": {"sys_normal": {"verdict": v}}},
                       "sys_normal": {"a": {"wall": 10.0, "usd": 0.001, "sources": {"S.txt": [1]}}}},
                      open(os.path.join(d, name), "w"))
        r = _row(d, {"S.txt": "test_script"})["a"]
        assert [v for v, _ in r["verdicts"]] == ["correct", "wrong", "safe_decline"] and r["script"] == 3, r
        assert _bucket("safe_decline") == "refused" and _bucket("correct") == "correct"
    print("selftest ok")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["--selftest"]:
        selftest()
    elif a[:1] == ["dupes"] and len(a) == 3:
        cmd_dupes(a[1], a[2])
    elif a[:1] == ["arm-c"] and len(a) == 3:
        cmd_armc(a[1], os.path.abspath(a[2]))
    elif a[:1] == ["prepare"] and len(a) == 3:
        cmd_prepare(a[1], a[2])
    elif a[:1] == ["cases"] and len(a) >= 2:
        kp = os.environ.get("KINDS_JSON") or "C:/Users/hrizvi/AppData/Local/Temp/go-D-20261024-04/kinds.json"
        cmd_cases(a[1:], json.load(open(kp, encoding="utf-8")))
    else:
        sys.exit(__doc__)
