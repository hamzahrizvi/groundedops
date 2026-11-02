"""Page-reach check (D-20261030-10): do past blind questions' answer pages still reach the bot?

Every weekly case carries `pages`, the manual pages its writer took the reference from. This sends
each answered, page-anchored question through the production answering path (main._traced_query,
product picked as in the weekly run, PERFORMANCE_MODE=0 like the weekly sys_normal arm) and stops
it at the line that numbers the passages for the prompt (main._numbered_passages). No model is
called: llm._post and every non-loopback socket are blocked, so the condenser falls back as it does
when the provider is down. Follow-ups therefore take the deterministic fallback (turn 1 in history,
no rewrite) and are reported apart as approximate.

Recorded per question: whether all / any gold page is among the passages handed to the prompt
("reach"), the best rank and rerank score of each gold page in any ranking, whether the gold page
still exists in the index at all ("stale" if not), and where the turn ended when it never reached
the prompt (decline, clarify, FAQ).

  run     python -I page_reach.py run --out reading.json [--chroma DIR] [--products a,b] [--limit N]
          saves every 25 questions and resumes from --out, so after a crash run the same command again
  report  python -I page_reach.py report reading.json [--diff older.json] [--fault fault.json] > readout.md
  index   python -I page_reach.py index --out DIR --drop-pdfs PRODUCT   (a TEST copy; never the live one)

Run `run` under the lab lock (tools/lab_lock.py). REPO and OPS default to this laptop's paths.
"""
import argparse, ast, collections, glob, json, os, re, shutil, socket, sys, time, uuid

REPO = os.environ.get("GO_REPO", "C:/Users/hrizvi/Downloads/Git/groundedops")
OPS = os.environ.get("GO_OPS", "C:/Users/hrizvi/groundedops-ops")
FLASH = re.compile(r"\b(flash\w*|blink\w*|led\b|leds\b)", re.I)
TYPES_ANSWERED = ("lookup", "yes_no_documented", "table_condition", "procedure_or_troubleshoot",
                  "comparison", "multi_document", "followup")


def gold_pages(c):
    p = c.get("pages") or []
    if isinstance(p, str):
        try: p = ast.literal_eval(p)
        except Exception: p = [p]
    out = []
    for s in p:
        m = re.match(r"^(.*\S) p(\d+)$", str(s).strip())
        if m: out.append((m.group(1), m.group(2)))
    return out


def case_set(ops=OPS):
    """Distinct answered, page-anchored cases over every run, keyed by (product, turn1, q)."""
    seen = {}
    for f in sorted(glob.glob(os.path.join(ops, "runs", "*", "cases.json"))):
        try: cs = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        cs = cs["cases"] if isinstance(cs, dict) else cs
        run = os.path.basename(os.path.dirname(f))
        for c in cs:
            if c.get("expected") != "answered" or not gold_pages(c): continue
            k = (c.get("product"), (c.get("turn1") or "").strip().lower(), c["q"].strip().lower())
            s = seen.setdefault(k, {"key": f"{c['id']}@{run}", "product": c.get("product"), "type": c["type"],
                                    "q": c["q"], "turn1": c.get("turn1"), "gold": gold_pages(c), "seen_in": []})
            s["seen_in"].append([run, c["id"]])
    return list(seen.values())


def block_network(counter):
    real = socket.socket.connect
    def connect(self, addr, *a, **k):
        host = addr[0] if isinstance(addr, tuple) else str(addr)
        if host not in ("127.0.0.1", "::1", "localhost"):
            counter["socket"] += 1
            raise OSError("page_reach: network blocked (no model calls)")
        return real(self, addr, *a, **k)
    socket.socket.connect = connect


def run(args):
    import faulthandler; faulthandler.enable()   # a native crash (seen once at case 690) leaves its stack in the log
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["PERFORMANCE_MODE"] = "0"
    lab = os.path.join(os.environ.get("TEMP", "/tmp"), "groundedops-lab", "page_reach")
    os.makedirs(lab, exist_ok=True)
    os.environ.setdefault("LOG_FILE", os.path.join(lab, "logs.jsonl"))
    os.environ.setdefault("FAQ_GAP_PATH", os.path.join(lab, "faq_gaps.json"))
    if args.chroma:
        live = os.path.normcase(os.path.abspath(os.path.join(REPO, "src", "chroma_db")))
        assert os.path.normcase(os.path.abspath(args.chroma)) != live, "--chroma must be a test copy"
        os.environ["CHROMA_DIR"] = os.path.abspath(args.chroma)
    cases = case_set()
    if args.products: cases = [c for c in cases if c["product"] in args.products.split(",")]
    if args.limit: cases = cases[:args.limit]
    blocked = collections.Counter()
    block_network(blocked)
    sys.path.insert(0, os.path.join(REPO, "src")); os.chdir(os.path.join(REPO, "src"))
    import torch  # noqa: F401  main thread first
    import main, llm, logger, memory, retrieval_db, db, pipeline_trace as ptrace
    logger.LOG_FILE = os.environ["LOG_FILE"]
    main.capability = lambda *_a, **_k: True
    assert not main._policy_setting("performance_mode", False), "performance mode is on"
    def no_post(*_a, **_k):
        blocked["llm"] += 1
        raise RuntimeError("page_reach: no model calls")
    llm._post = no_post

    class Reached(Exception):
        pass
    turn = {}
    def numbered(chunks, *_a, **_k):
        turn["ctx"] = [dict(c) for c in chunks]
        turn["trace"] = ptrace.snapshot()
        raise Reached()
    main._numbered_passages = numbered
    real_rerank = main.rerank
    def rerank(q, results, *a, **k):
        out = real_rerank(q, results, *a, **k)
        turn.setdefault("ranks", []).append([(r.get("source"), str(r.get("page")), r.get("rerank_score")) for r in out])
        return out
    main.rerank = rerank
    real_bundle = retrieval_db.product_bundle
    def bundle(*a, **k):
        out = real_bundle(*a, **k)
        turn.setdefault("bundle", []).extend((c.get("source"), str(c.get("page"))) for c in out)
        return out
    retrieval_db.product_bundle = bundle

    meta = db.get_collection().get(include=["metadatas"])["metadatas"]
    in_index = {(m.get("source"), str(m.get("page"))) for m in meta}
    sources = {m.get("source") for m in meta}
    copy_of = retrieval_db.copies_of()
    # Early runs' writers saw sources without their extension ("X p3" for "X.pdf"): map a unique stem match.
    stems = collections.defaultdict(list)
    for s_ in sources: stems[os.path.splitext(s_ or "")[0]].append(s_)
    def resolve(s_):
        if s_ not in sources and len(stems.get(s_, ())) == 1: s_ = stems[s_][0]
        return copy_of.get(s_, s_)
    print(f"{len(cases)} cases; index {len(meta)} chunks, {len(sources)} sources, chroma={db.CHROMA_DIR}", flush=True)

    prev = json.load(open(args.out, encoding="utf-8")) if os.path.exists(args.out) else {}
    rows, t_all = prev.get("rows", []), time.time() - prev.get("secs", 0)
    if args.redo_stale:
        rows = [r for r in rows if not r["stale_doc"]]
    done = {r["key"] for r in rows}
    if done: print(f"resuming: {len(done)} already read", flush=True)
    def save():
        json.dump({"at": time.strftime("%Y-%m-%d %H:%M"), "chroma": db.CHROMA_DIR, "chunks": len(meta),
                   "blocked_calls": dict(collections.Counter(prev.get("blocked_calls", {})) + blocked),
                   "restarts": prev.get("restarts", -1) + 1 if done else 0,
                   "secs": round(time.time() - t_all), "rows": rows},
                  open(args.out + ".tmp", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        os.replace(args.out + ".tmp", args.out)
    for i, c in enumerate(cases):
        if c["key"] in done: continue
        turn.clear()
        sid = f"pagereach-{uuid.uuid4()}"
        if c.get("turn1"):
            memory.add_to_memory(sid, c["turn1"], "(earlier answer)")
        gold = [(resolve(s), p) for s, p in c["gold"]]
        t = time.time(); exit_ = None
        try:
            res = main._traced_query(main.QueryRequest(q=c["q"], product=c["product"], session_id=sid), None, attach=True)
            exit_ = ((res or {}).get("pipeline") or {}).get("exit", {}).get("id") or (res or {}).get("role")
        except Reached:
            exit_ = "prompt"
        except Exception as e:  # a crash is reported, never hidden
            exit_ = f"error: {type(e).__name__}: {e}"[:160]
        ctx = [(x.get("source"), str(x.get("page"))) for x in turn.get("ctx", [])]
        best = {}
        for rk in turn.get("ranks", []):
            for j, (s, p, sc) in enumerate(rk):
                if (s, p) in gold and ((s, p) not in best or j + 1 < best[(s, p)][0]):
                    best[(s, p)] = (j + 1, round(float(sc or 0), 4))
        live_gold = [g for g in gold if g in in_index]
        hit = [g for g in gold if g in ctx]
        bnd = set(turn.get("bundle", []))
        rows.append({**{k: c[k] for k in ("key", "product", "type", "seen_in")},
                     "flash": bool(FLASH.search(c["q"] + " " + (c.get("turn1") or ""))),
                     "gold": [f"{s} p{p}" for s, p in gold],
                     "stale": [f"{s} p{p}" for s, p in gold if (s, p) not in in_index],
                     "stale_doc": [s for s, p in gold if s not in sources],
                     "exit": exit_, "n_ctx": len(ctx),
                     "reach_all": bool(gold) and len(hit) == len(gold),
                     "reach_any": bool(hit),
                     "reach_all_live": bool(live_gold) and all(g in ctx for g in live_gold),
                     "in_bundle_all": bool(gold) and all(g in bnd for g in gold),
                     "ctx": [f"{s} p{p}" for s, p in ctx],
                     "ctx_rank": {f"{s} p{p}": ctx.index((s, p)) + 1 for s, p in hit},
                     "best_rank": {f"{s} p{p}": v for (s, p), v in best.items()},
                     "secs": round(time.time() - t, 2)})
        if i % 25 == 0 or i == len(cases) - 1:
            save()
            print(i + 1, round(time.time() - t_all), "s", c["key"], exit_, rows[-1]["reach_all"], flush=True)
    save()
    print("wrote", args.out, len(rows), "rows; blocked model calls:", dict(blocked), flush=True)


def pct(xs):
    return f"{100 * sum(xs) / len(xs):.1f}" if xs else "-"


def table(rows, key, title):
    g = collections.defaultdict(list)
    for r in rows: g[key(r)].append(r)
    lines = [f"| {title} | n | all pages reach % | any page % | gold page gone from index | no prompt (declined/clarified) |",
             "|---|---|---|---|---|---|"]
    for k in sorted(g, key=lambda k: (sum(r["reach_all"] for r in g[k]) / len(g[k]), str(k))):
        rs = g[k]
        lines.append(f"| {k} | {len(rs)} | {pct([r['reach_all'] for r in rs])} | {pct([r['reach_any'] for r in rs])} | "
                     f"{sum(bool(r['stale']) for r in rs)} | {sum(r['exit'] != 'prompt' for r in rs)} |")
    return "\n".join(lines)


def spearman(a, b):
    def rk(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0] * len(v)
        for pos, i in enumerate(o): r[i] = pos
        return r
    ra, rb, n = rk(a), rk(b), len(a)
    return 1 - 6 * sum((x - y) ** 2 for x, y in zip(ra, rb)) / (n * (n * n - 1))


def marks():
    """sys_normal mark per (run, case id), with the run's date (from the folder name)."""
    out = {}
    for f in glob.glob(os.path.join(OPS, "runs", "*", "cases_state.json")):
        run = os.path.basename(os.path.dirname(f))
        try: g = json.load(open(f, encoding="utf-8")).get("grades") or {}
        except Exception: continue
        for cid, v in g.items():
            s = (v.get("sys_normal") or {}).get("score")
            if isinstance(s, (int, float)): out[(run, cid)] = s
    return out


def report(args):
    R = json.load(open(args.reading, encoding="utf-8")); rows = R["rows"]
    std = [r for r in rows if r["type"] != "followup"]
    print(f"# Page-reach reading {R['at']}\n\nIndex: {R['chroma']} ({R['chunks']} chunks). {len(rows)} questions, "
          f"{R['secs'] // 60} min. Model calls attempted and blocked: {R['blocked_calls'] or 'none'}.\n")
    print(f"**Headline (standalone questions, n={len(std)}):** all answer pages reach the prompt "
          f"{pct([r['reach_all'] for r in std])}%; at least one page {pct([r['reach_any'] for r in std])}%; "
          f"counting only gold pages still in the index {pct([r['reach_all_live'] for r in std])}%.\n")
    print("## By type (followup = approximate: no condenser)\n\n" + table(rows, lambda r: r["type"], "type") + "\n")
    print("## By product\n\n" + table(rows, lambda r: r["product"], "product") + "\n")
    fl = [r for r in rows if r["flash"]]
    print("## Flash-code questions (D-20261030-14)\n\n" + table(fl, lambda r: "flash-code", "set") + "\n")
    miss = [r for r in fl if not r["reach_all"]]
    if miss:
        print("Flash-code questions whose pages did not all reach the prompt (gold page: best rank in any ranking, score):\n")
        for r in miss:
            br = {g: r["best_rank"].get(g) for g in r["gold"]}
            print(f"- {r['key']} ({r['product']}, {r['type']}, exit {r['exit']}): " +
                  "; ".join(f"{g} -> {('not in ranking' if v is None else f'#{v[0]} {v[1]}')}"
                            + (" [GONE from index]" if g in r["stale"] else "") for g, v in br.items()))
        print()
    ex = collections.Counter(r["exit"] for r in rows if r["exit"] != "prompt")
    print("## Turns that never reached the prompt\n\n" + (", ".join(f"{k}: {v}" for k, v in ex.most_common()) or "none") + "\n")
    st = collections.Counter(s for r in rows for s in r["stale_doc"])
    print("## Gold documents no longer in the index\n\n" + (", ".join(f"{k} ({v})" for k, v in st.most_common()) or "none") + "\n")

    # Validation 1: types rank as the weekly marks do.
    M = marks(); bytype = collections.defaultdict(list)
    for r in rows:
        for run, cid in r["seen_in"]:
            if (run, cid) in M: bytype[r["type"]].append(M[(run, cid)])
    ts = [t for t in TYPES_ANSWERED if bytype[t] and any(r["type"] == t for r in rows)]
    reach = [sum(r["reach_all"] for r in rows if r["type"] == t) / sum(r["type"] == t for r in rows) for t in ts]
    mark = [sum(bytype[t]) / len(bytype[t]) for t in ts]
    print("## Validation 1: does page-reach rank the types as the weekly marks do?\n")
    print("| type | page-reach % | weekly mark (mean of 0-3, all runs) | marks counted |\n|---|---|---|---|")
    for t, a, m in sorted(zip(ts, reach, mark), key=lambda x: x[1]):
        print(f"| {t} | {100 * a:.1f} | {m:.2f} | {len(bytype[t])} |")
    sp_std = spearman([a for t, a in zip(ts, reach) if t != "followup"], [m for t, m in zip(ts, mark) if t != "followup"])
    print(f"\nSpearman rank correlation, all {len(ts)} types: {spearman(reach, mark):.2f}; "
          f"without followup: {sp_std:.2f}.\n")

    # Validation 2: questions whose pages missed carry lower marks (cases graded in the last 7 days).
    print("## Validation 2: marks of the same questions, by whether their pages reach the prompt today\n")
    print("Standalone types only (followup excluded). Mark = mean sys_normal score (0-3) over every run that graded the question.\n")
    print("| graded | pages | n | mean mark | share scoring 3 |\n|---|---|---|---|---|")
    for since, label in ((args.since, f"since {args.since}"), ("0", "any time")):
        for name, want in (("all reach", True), ("some missing", False)):
            xs = []
            for r in std:
                if r["reach_all"] != want: continue
                ss = [M[(run, cid)] for run, cid in r["seen_in"] if run[:8] >= since and (run, cid) in M]
                if ss: xs.append(sum(ss) / len(ss))
            if xs: print(f"| {label} | {name} | {len(xs)} | {sum(xs) / len(xs):.2f} | {pct([x >= 3 for x in xs])}% |")
    print()

    for flag, label in ((args.diff, "Diff against an earlier reading"), (args.fault, "Replayed fault (test index) against this reading")):
        if not flag: continue
        O = json.load(open(flag, encoding="utf-8")); old = {r["key"]: r for r in O["rows"]}
        both = [r for r in rows if r["key"] in old]
        print(f"## {label}: {O['at']} ({O['chroma']})\n")
        g = collections.defaultdict(lambda: [0, 0, 0])
        for r in both:
            o = old[r["key"]]; g[r["product"]][0] += 1; g[r["product"]][1] += r["reach_all"]; g[r["product"]][2] += o["reach_all"]
        print("| product | n | reach % here | reach % there | change |\n|---|---|---|---|---|")
        for p, (n, a, b) in sorted(g.items()):
            print(f"| {p} | {n} | {100 * a / n:.1f} | {100 * b / n:.1f} | {100 * (b - a) / n:+.1f} |")
        lost = [r["key"] for r in both if r["reach_all"] and not old[r["key"]]["reach_all"]]
        won = [r["key"] for r in both if not r["reach_all"] and old[r["key"]]["reach_all"]]
        print(f"\nReach lost there: {len(lost)}; gained there: {len(won)}.\n")


def make_index(args):
    """A TEST copy of the live index with one product's PDF chunks deleted (the 24 Oct fault)."""
    live = os.path.join(REPO, "src", "chroma_db")
    out = os.path.abspath(args.out)
    assert not os.path.normcase(out).startswith(os.path.normcase(os.path.abspath(REPO))), "test index must live outside REPO"
    if os.path.exists(out): shutil.rmtree(out)
    shutil.copytree(live, out)
    import sqlite3   # a consistent copy of the sqlite file, whatever the backend is doing
    src = sqlite3.connect(f"file:{live}/chroma.sqlite3?mode=ro", uri=True); dst = sqlite3.connect(os.path.join(out, "chroma.sqlite3"))
    src.backup(dst); src.close(); dst.close()
    os.environ["CHROMA_DIR"] = out; os.environ["HF_HUB_OFFLINE"] = "1"
    sys.path.insert(0, os.path.join(REPO, "src"))
    import db
    assert os.path.normcase(os.path.abspath(db.CHROMA_DIR)) == os.path.normcase(out)
    col = db.get_collection()
    got = col.get(where={f"prod_{args.drop_pdfs}": True}, include=["metadatas"])
    ids = [i for i, m in zip(got["ids"], got["metadatas"]) if (m.get("source") or "").lower().endswith(".pdf")]
    srcs = sorted({m["source"] for m in got["metadatas"] if (m.get("source") or "").lower().endswith(".pdf")})
    for k in range(0, len(ids), 500): col.delete(ids=ids[k:k + 500])
    print(f"test index {out}: deleted {len(ids)} PDF chunks of {args.drop_pdfs}: {srcs}; {col.count()} chunks left")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("run"); a.add_argument("--out", required=True); a.add_argument("--chroma")
    a.add_argument("--products"); a.add_argument("--limit", type=int)
    a.add_argument("--redo-stale", action="store_true", help="read again the rows whose gold document was not found")
    b = sub.add_parser("report"); b.add_argument("reading"); b.add_argument("--diff"); b.add_argument("--fault")
    b.add_argument("--since", default=time.strftime("%Y%m%d", time.localtime(time.time() - 7 * 86400)))
    c = sub.add_parser("index"); c.add_argument("--out", required=True); c.add_argument("--drop-pdfs", required=True)
    A = ap.parse_args()
    {"run": run, "report": report, "index": make_index}[A.cmd](A)
