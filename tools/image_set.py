"""The picture test (D-20261022-14): which manuals get questions, the merge with its fairness check, the score.

  python tools/image_set.py plan  <RUN> <FIGS>      one line per manuals file: "<stem> | <document>=<n> | ..."
                                                    (first drops the figure-description chunks from RUN/manuals)
  python tools/image_set.py merge <RUN> <FIGS>      RUN/parts/*.json -> RUN/cases.json; last line "OK <n>" or "FAIL <part>: <why>"
  python tools/image_set.py add   <RUN> <WEEKLY>    RUN/cases.json appended to the weekly run's cases.json; "ADDED <n>"
  python tools/image_set.py score <RUN> <OUT_DIR>   RUN/cases_state.json summary -> OUT_DIR/score.json; prints the two counts

RUN is OPS/heldout/images/<stamp>: merge stamps every case "src": "images", so blind_eval.py runs them only from a
folder under OPS/heldout. FIGS is REPO/documents/figures. Nothing here prints a question, an answer or a reference.
The score lives in its own folder; ops_pivot.py shows its counts as the Picture row of the weekly run with the same stamp.
"""
import glob, json, os, re, sys

LABEL = re.compile(r"^\[(.+?) p\d+\]", re.M)
NAME = re.compile(r"^p(\d+)_\d+\.png$")
CERT = re.compile(r"certific|declaration|conformity|-UL", re.I)   # paperwork: logos and stamps, no product pictures
BIG = 50   # a manual with this many crops gets 2 questions, else 1 (13 manuals -> 20 questions on 2026-10-22)
FIG_CHUNK = re.compile(r" — Figure(: .*)?\]$")   # a figure chunk's crumb line ends "— Figure]" or "— Figure: <label>]"
NEED = ("q", "reference", "product", "source", "figure", "must_include")


def docs_by_stem(run):
    return {os.path.basename(f)[:-4]: sorted(set(LABEL.findall(open(f, encoding="utf-8").read())))
            for f in sorted(glob.glob(os.path.join(run, "manuals", "*.txt")))}


def prose_only(run):
    """Drops the figure-description chunks from RUN/manuals (the 2026-11-06 backfill indexed what 22 manuals' pictures
    show). Left in, the writers find every picture fact "in the text" and only paperwork logos are left to ask about."""
    for f in glob.glob(os.path.join(run, "manuals", "*.txt")):
        chunks = open(f, encoding="utf-8").read().split("\n\n[")
        keep = [c for c in chunks if not FIG_CHUNK.search((c.split("\n") + [""])[1])]
        open(f, "w", encoding="utf-8").write("\n\n[".join(keep))


def plan(run, figs, limit=None):
    """Each document with crops goes to the manuals file holding the fewest documents (its own manual), ties by name.
    limit (the weekly run's few picture questions): one question each for that many documents, those used least in
    earlier picture sets first, then those with the most pictures (a certificate has a logo or two), so the weeks
    rotate through the real manuals."""
    prose_only(run)
    by, out, docs = docs_by_stem(run), {}, []
    for doc in sorted({d for ds in by.values() for d in ds}):
        side = os.path.join(figs, doc, "figures.json")
        n = len(json.load(open(side, encoding="utf-8")).get("figures") or {}) if os.path.isfile(side) else 0
        if n: docs.append((doc, n))
    if limit:
        used = [c.get("source") for f in glob.glob(os.path.join(os.path.dirname(os.path.abspath(run)), "*", "cases.json"))
                for c in json.load(open(f, encoding="utf-8")).get("cases", [])]
        docs = [(d, 1) for d, _ in sorted((x for x in docs if not CERT.search(x[0])), key=lambda x: (used.count(x[0]), -x[1], x[0]))[:int(limit)]]
    else:
        docs = [(d, 2 if n >= BIG else 1) for d, n in docs]
    for doc, n in docs:
        stem = min((s for s in by if doc in by[s]), key=lambda s: (len(by[s]), s))
        out.setdefault(stem, []).append(f"{doc}={n}")
    return [f"{s} | " + " | ".join(v) for s, v in sorted(out.items())]


def doc_text(run, doc):
    """Every chunk the index holds for one document, from all manuals files, without the [doc pN] labels."""
    return "\n".join(ch.split("]", 1)[1] for f in glob.glob(os.path.join(run, "manuals", "*.txt"))
                     for ch in open(f, encoding="utf-8").read().split("\n\n[") if ch.lstrip("[").startswith(doc + " p"))


def merge(run, figs):
    stamp, cases, dropped = os.path.basename(os.path.normpath(run)), [], []
    for part in sorted(glob.glob(os.path.join(run, "parts", "*.json"))):
        name = os.path.basename(part)
        try: got = json.load(open(part, encoding="utf-8"))["cases"]
        except Exception as e: return [f"FAIL {name}: unreadable ({type(e).__name__})"]
        for c in got:
            miss = [k for k in NEED if not c.get(k)]
            if miss: return [f"FAIL {name}: {c.get('id')} lacks {', '.join(miss)}"]
            m = NAME.match(c["figure"])
            if not m or not os.path.isfile(os.path.join(figs, c["source"], c["figure"])):
                return [f"FAIL {name}: {c.get('id')} names no crop of its document"]
            text = doc_text(run, c["source"])
            if any(re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", text, re.I) for t in c["must_include"]):
                dropped.append(c.get("id")); continue   # the answer is in the page text: not a picture question
            cases.append({**c, "type": "figure", "expected": "answered", "src": "images",
                          "pages": [f"{c['source']} p{m.group(1)}"]})
    for i, c in enumerate(cases, 1): c["id"] = f"I{stamp}-{i:02d}"
    json.dump({"cases": cases}, open(os.path.join(run, "cases.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return ([f"dropped, answer in the page text: {', '.join(map(str, dropped))}"] if dropped else []) + [f"OK {len(cases)}"]


def add(pics, run):
    """The CEO, 2026-11-07: picture questions are asked with the weekly set, in its conversations, as simple or
    technical questions. Appends pics/cases.json to run/cases.json, numbered on from the last C<stamp>-NN; dropping
    "src" lets them run outside OPS/heldout. figure and source stay, so blind_eval.py still marks figure_found."""
    path = os.path.join(run, "cases.json")
    cases = json.load(open(path, encoding="utf-8"))["cases"]
    new = [c for c in json.load(open(os.path.join(pics, "cases.json"), encoding="utf-8"))["cases"]
           if c["q"] not in {x["q"] for x in cases}]   # a re-run of this step adds nothing twice
    stamp = os.path.basename(os.path.normpath(run))
    for i, c in enumerate(new, len(cases) + 1):
        c.pop("src", None)
        c.update(id=f"C{stamp}-{i:02d}", category=c.get("category") if c.get("category") in ("simple", "technical") else "technical")
    json.dump({"cases": cases + new}, open(path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return [f"ADDED {len(new)}"]


def score(run, out_dir):
    s = json.load(open(os.path.join(run, "cases_state.json"), encoding="utf-8"))["summary"]["sys_normal"]["images"]
    by = {}
    for c in json.load(open(os.path.join(run, "cases.json"), encoding="utf-8"))["cases"]:
        if not c.get("figure"): continue   # a weekly run also holds its normal questions
        b = by.setdefault(c["product"], {"n": 0, "figure_found": 0, "explanation_right": 0})
        b["n"] += 1; b["figure_found"] += c["id"] in s["found"]; b["explanation_right"] += c["id"] in s["right"]
    os.makedirs(out_dir, exist_ok=True)
    json.dump({**s, "run": os.path.abspath(run), "by_product": by}, open(os.path.join(out_dir, "score.json"), "w", encoding="utf-8"), indent=1)
    return [f"figure found {s['figure_found']} of {s['n']}, explanation right {s['explanation_right']} of {s['n']}"]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    lines = {"plan": plan, "merge": merge, "add": add, "score": score}[sys.argv[1]](*sys.argv[2:])
    print("\n".join(lines))
    sys.exit(1 if lines and lines[-1].startswith("FAIL") else 0)
