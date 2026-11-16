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
          saves every 25 questions; a crash or a question stuck past 300 s restarts the reading from --out by itself
  report  python -I page_reach.py report reading.json [--diff older.json] [--fault fault.json] > readout.md
  index   python -I page_reach.py index --out DIR --drop-pdfs PRODUCT   (a TEST copy; never the live one)
  gold_reach / failure_cause: blind_eval's per-failure bucket (D-20261109-01), from the answer's trace; passage_reach judges it per passage (D-20261115-02).

`pages_now` on a case is today's page for it (preferred over `pages`, which stays as the run's grader reads it);
`gold_gone` skips the case. Both are set in OPS runs/*/cases.json.
Gold names are resolved against today's index once, in resolve_name; OPS state/page_reach_renames.json holds renames it cannot see.

Run `run` under the lab lock (tools/lab_lock.py). REPO and OPS default to this laptop's paths.
"""
import argparse, ast, collections, glob, json, os, re, shutil, socket, subprocess, sys, time, uuid

REPO = os.environ.get("GO_REPO", "C:/Users/hrizvi/Downloads/Git/groundedops")
OPS = os.environ.get("GO_OPS", "C:/Users/hrizvi/groundedops-ops")
FLASH = re.compile(r"\b(flash\w*|blink\w*|led\b|leds\b)", re.I)
TYPES_ANSWERED = ("lookup", "yes_no_documented", "table_condition", "procedure_or_troubleshoot",
                  "comparison", "multi_document", "followup")
RENAMES = os.path.join(OPS, "state", "page_reach_renames.json")
STALL_SECS = 300   # a question takes 2-3 s (p99 12 s); past this it is stuck or the laptop slept
EXT = re.compile(r"\.(pdf|txt|docx?|md|html?)$", re.I)
SUFFIX = re.compile(r"(-v\d+)?(-\d{6}-\d{6}|-\d{8}_\d{6}( \d+)?)?$")
GOLD = re.compile(r"^(?:(?P<doc>.*?\S)\s+)?pp?\.?\s*(?P<pages>\d+(?:\s*(?:,|-|\u2013|and)\s*p?\d+)*)\s*(?:\(.*\))?$", re.I)


def internal_gold(gold_strs, is_excluded):
    """Gold pages ("Source.pdf p3") of documents the bot never puts in a prompt."""
    return [g for g in gold_strs if is_excluded(g.rsplit(" p", 1)[0])]


def reach_flags(gold, live_gold, internal, ctx):
    """(reach_all_live, reach_all_public); same "s pN" form or (s, p) tuples throughout."""
    pub = [g for g in live_gold if g not in internal]
    return (bool(live_gold) and all(g in ctx for g in live_gold), bool(pub) and all(g in ctx for g in pub))


CAUSES = ("never_found", "found_and_cut", "found_and_misread", "found_and_declined", "unknown")


def gold_reach(case, pipeline, in_index, sources, copy_of=None, renames=None):
    """D-20261109-01: where a case's answer pages went on one turn, from the trace's pages_retrieved /
    pages_prompt (src/pipeline_trace.add_pages). "prompt": every gold page was in the prompt; "cut": a gold
    page missing from the prompt was retrieved, and none was never retrieved; "never": a gold page was never
    retrieved (or the turn ended before any search); "stale": a gold page is not in today's index, its
    document does not resolve, or the case is gold_gone; "no_gold": the case names no page; "no_trace": no
    trace, or one from code that did not record pages. Names resolve as in run() (resolve_name, copies)."""
    if case.get("gold_gone"): return "stale"
    gold = gold_pages(case)
    if not gold: return "no_gold"
    if not pipeline: return "no_trace"
    meta = pipeline.get("meta") or {}
    if "pages_retrieved" not in meta and any(s.get("id") == "retrieve" for s in pipeline.get("stages") or []):
        return "no_trace"
    copy_of = copy_of or {}
    def pages(key): return {(copy_of.get(s, s), str(p)) for s, ps in (meta.get(key) or {}).items() for p in ps}
    got, kept = pages("pages_retrieved"), pages("pages_prompt")
    gold = [(resolve_name(s, sources, renames, copy_of)[0], p) for s, p in gold]
    if any(s is None or (s, p) not in in_index for s, p in gold): return "stale"
    missing = [g for g in gold if g not in kept]
    if not missing: return "prompt"
    return "cut" if all(g in got for g in missing) else "never"


def failure_cause(reach, verdict):
    """The bucket of an answer graded under 3: one of CAUSES, or "no_page" for a case that names no page
    (expected rejected or clarify: nothing to find, so it is kept out of the five)."""
    if reach == "no_gold": return "no_page"
    if reach == "prompt": return "found_and_declined" if verdict == "safe_decline" else "found_and_misread"
    return {"never": "never_found", "cut": "found_and_cut"}.get(reach, "unknown")


QUOTED = re.compile(r"[\"\u201c\u2018']([^\"\u201d\u2019']{12,})[\"\u201d\u2019']")


def answer_terms(case):
    """D-20261115-02: what a passage must hold to hold the answer: the case's must_include values, else the quoted
    spans of its reference; [] when the case gives nothing to match on."""
    terms = [str(t).strip() for t in case.get("must_include") or [] if str(t).strip()]
    return terms or [m.strip() for m in QUOTED.findall(case.get("reference") or "")]


def _flat(s):
    return re.sub(r"\s+", " ", re.sub(r"[\u2010-\u2015]", "-", s or "")).casefold()


def holds(text, terms):
    """Every term is in the text, any case or spacing, as a whole word or figure ("7" is not in "70")."""
    t = _flat(text)
    return bool(terms) and all(re.search(r"(?<!\w)" + re.escape(_flat(x)) + r"(?!\w)", t) for x in terms)


def passage_reach(case, pipeline, in_index, sources, copy_of=None, renames=None, passages=None):
    """(reach, level): gold_reach's reach judged per passage (D-20261115-02). passages = {"pages_retrieved":
    [(source, page, text)], "pages_prompt": [...], "index": {(source, page): [text, ...]}}: the chunks this turn
    handed the ranker and the prompt (blind_eval keeps them at pipeline_trace.add_pages, where the trace's pages
    come from) and every chunk of each page in today's index. On a page stored as several passages, "found" means a
    ranked passage holds the answer (answer_terms, holds) and "kept" that such a passage reached the prompt. A page
    of one passage reads as the page. A page where no passage holds the terms, or a case with none, keeps the
    page-level reading and level is "page"; so is every row without passages (old rows, the stream path)."""
    r = gold_reach(case, pipeline, in_index, sources, copy_of, renames)
    if passages is None or r not in ("prompt", "cut"):
        return r, ("passage" if passages is not None and r == "never" else "page")
    copy_of = copy_of or {}
    meta = (pipeline or {}).get("meta") or {}
    def pages(key): return {(copy_of.get(s, s), str(p)) for s, ps in (meta.get(key) or {}).items() for p in ps}
    def chunks(key): return [((copy_of.get(s, s), str(p)), t) for s, p, t in passages.get(key) or []]
    got_pg, kept_pg = pages("pages_retrieved"), pages("pages_prompt")
    got, kept = chunks("pages_retrieved"), chunks("pages_prompt")
    terms, level, missing, found = answer_terms(case), "passage", 0, True
    for s, p in gold_pages(case):
        g = (resolve_name(s, sources, renames, copy_of)[0], p)
        texts = passages["index"].get(g) or []
        here = [t for t in terms if any(holds(x, [t]) for x in texts)]   # the terms this page states
        if len(texts) > 1 and here and any(holds(x, here) for x in texts):
            is_kept = any(k == g and holds(x, here) for k, x in kept)
            is_found = any(k == g and holds(x, here) for k, x in got)
        else:
            if len(texts) > 1: level = "page"
            is_kept, is_found = g in kept_pg, g in got_pg
        if not is_kept:
            missing += 1; found = found and is_found
    if not missing: return "prompt", level
    return ("cut" if found else "never"), level


def gold_pages(c):
    p = c.get("pages_now") or c.get("pages") or []
    if isinstance(p, str):
        try: p = ast.literal_eval(p)
        except Exception: p = [p]
    out, doc = [], None
    for s in p:
        m = GOLD.match(str(s).strip())
        if not m: continue
        doc = m.group("doc") or doc          # a bare "p6" belongs to the document named before it
        if not doc: continue
        for part in re.split(r"\s*(?:,|and)\s*", m.group("pages")):
            a, _, b = re.sub(r"[p\s]", "", part, flags=re.I).replace("\u2013", "-").partition("-")
            for n in (range(int(a), int(b) + 1) if b and 0 < int(b) - int(a) <= 30 else [int(a)]):
                out.append((doc, str(n)))
    return out


def _norm(s, strip_suffix=False):
    s = EXT.sub("", (s or "").replace("\u200e", "").replace("\u200f", ""))
    s = re.sub(r"[\u2010-\u2015]", "-", s)
    s = re.sub(r"\s*-\s*", "-", re.sub(r"\s+", " ", s).strip().casefold())
    return SUFFIX.sub("", s) if strip_suffix else s


def resolve_name(name, sources, renames=None, copy_of=None):
    """Today's index source for a gold document name and how it was found: exact, rename table, the same
    name ignoring dashes/spaces/case/extension ("name"), the same also ignoring a -vN or export-date suffix
    ("suffix"), then the one source whose name starts with it ("prefix"). The first step that finds anything
    decides: two hits = (None, "ambiguous: ..."), nothing = (None, None). An explicit extension never matches
    another one (a .txt copy replaced by a PDF has other page numbers: that needs pages_now)."""
    copy_of = copy_of or {}
    if name in sources: return copy_of.get(name, name), "exact"
    for k, v in (renames or {}).items():
        if not k.startswith("_") and _norm(k) == _norm(name) and v in sources: return copy_of.get(v, v), "table"
    m = EXT.search(name or ""); ext = m.group(0).lower() if m else None
    pool = [s for s in sources if s and (ext is None or s.lower().endswith(ext))]
    n = _norm(name, True)
    for how, hit in (("name", [s for s in pool if _norm(s) == _norm(name)]),
                     ("suffix", [s for s in pool if _norm(s, True) == n]),
                     ("prefix", [s for s in pool if n and _norm(s, True).startswith((n + "-", n + " "))])):
        if len(hit) == 1: return copy_of.get(hit[0], hit[0]), how
        if hit: return None, "ambiguous: " + ", ".join(sorted(hit)[:3])
    return None, None


def supervise(cmd, out, max_restarts=20):
    """Run the reading in a child and start it again after a crash or stall. The child writes the key it is
    reading to <out>.current; a key that took the child down twice becomes an error row (see run)."""
    crashes_f, cur_f = out + ".crashes.json", out + ".current"
    crashes = json.load(open(crashes_f, encoding="utf-8")) if os.path.exists(crashes_f) else {}
    for _ in range(max_restarts + 1):
        if os.path.exists(cur_f): os.remove(cur_f)
        rc = subprocess.call(cmd)
        key = open(cur_f, encoding="utf-8").read().strip() if os.path.exists(cur_f) else ""
        if os.path.exists(cur_f): os.remove(cur_f)
        if rc == 0: return 0
        if not key: return rc       # died before its first question: not a mid-reading crash
        crashes[key] = crashes.get(key, 0) + 1
        with open(crashes_f, "w", encoding="utf-8") as f: json.dump(crashes, f, indent=1, ensure_ascii=False)
        print(f"page_reach: reading stopped (exit {rc}) on {key}; resuming", flush=True)
    return 1


def case_set(ops=OPS):
    """Distinct answered, page-anchored cases over every run, keyed by (product, turn1, q)."""
    seen = {}
    for f in sorted(glob.glob(os.path.join(ops, "runs", "*", "cases.json"))):
        try: cs = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        cs = cs["cases"] if isinstance(cs, dict) else cs
        run = os.path.basename(os.path.dirname(f))
        for c in cs:
            if c.get("expected") != "answered" or c.get("gold_gone") or not gold_pages(c): continue
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
    if not args.child:
        sys.exit(supervise([sys.executable, "-I", os.path.abspath(__file__)] + sys.argv[1:] + ["--child"],
                           os.path.abspath(args.out)))
    import faulthandler; faulthandler.enable()   # a native crash (seen once at case 690) leaves its stack in the log
    if os.name == "nt":   # the 2 Nov reading lost 15 h to one question overnight; idle sleep is the likely cause
        import ctypes; ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)   # ES_CONTINUOUS|ES_SYSTEM_REQUIRED, ends with the process
    args.out = os.path.abspath(args.out)   # run() chdirs to REPO/src below
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
    renames = json.load(open(RENAMES, encoding="utf-8")) if os.path.exists(RENAMES) else {}
    found = {}
    def resolve(s_):
        if s_ not in found: found[s_] = resolve_name(s_, sources, renames, copy_of)
        return found[s_][0] or s_
    for c in cases:
        for s_, _p in c["gold"]: resolve(s_)
    print(f"{len(cases)} cases; index {len(meta)} chunks, {len(sources)} sources, chroma={db.CHROMA_DIR}", flush=True)

    prev = json.load(open(args.out, encoding="utf-8")) if os.path.exists(args.out) else {}
    rows, t_all = prev.get("rows", []), time.time() - prev.get("secs", 0)
    if args.redo_stale:
        rows = [r for r in rows if not r["stale_doc"]]
    done = {r["key"] for r in rows}
    if done: print(f"resuming: {len(done)} already read", flush=True)
    crashes = json.load(open(args.out + ".crashes.json", encoding="utf-8")) if os.path.exists(args.out + ".crashes.json") else {}
    def save():
        json.dump({"at": time.strftime("%Y-%m-%d %H:%M"), "chroma": db.CHROMA_DIR, "chunks": len(meta),
                   "blocked_calls": dict(collections.Counter(prev.get("blocked_calls", {})) + blocked),
                   "restarts": prev.get("restarts", -1) + 1 if done else 0,
                   "secs": round(time.time() - t_all), "rows": rows,
                   "resolved": {k: list(v) for k, v in found.items() if v[1] != "exact"}},
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
        if crashes.get(c["key"], 0) >= 2:
            exit_ = "error: stopped the reading twice (native crash or stall); not read again"
        else:
            with open(args.out + ".current", "w", encoding="utf-8") as f: f.write(c["key"])
            faulthandler.dump_traceback_later(STALL_SECS, exit=True)
            try:
                res = main._traced_query(main.QueryRequest(q=c["q"], product=c["product"], session_id=sid), None, attach=True)
                exit_ = ((res or {}).get("pipeline") or {}).get("exit", {}).get("id") or (res or {}).get("role")
            except Reached:
                exit_ = "prompt"
            except Exception as e:  # a crash is reported, never hidden
                exit_ = f"error: {type(e).__name__}: {e}"[:160]
            finally:
                faulthandler.cancel_dump_traceback_later()
        ctx = [(x.get("source"), str(x.get("page"))) for x in turn.get("ctx", [])]
        best = {}
        for rk in turn.get("ranks", []):
            for j, (s, p, sc) in enumerate(rk):
                if (s, p) in gold and ((s, p) not in best or j + 1 < best[(s, p)][0]):
                    best[(s, p)] = (j + 1, round(float(sc or 0), 4))
        live_gold = [g for g in gold if g in in_index]
        hit = [g for g in gold if g in ctx]
        internal = [g for g in gold if retrieval_db.is_excluded(g[0])]
        flags = reach_flags(gold, live_gold, internal, ctx)
        bnd = set(turn.get("bundle", []))
        rows.append({**{k: c[k] for k in ("key", "product", "type", "seen_in")},
                     "flash": bool(FLASH.search(c["q"] + " " + (c.get("turn1") or ""))),
                     "gold": [f"{s} p{p}" for s, p in gold],
                     "stale": [f"{s} p{p}" for s, p in gold if (s, p) not in in_index],
                     "stale_doc": [s for s, p in gold if s not in sources],
                     "exit": exit_, "n_ctx": len(ctx),
                     "reach_all": bool(gold) and len(hit) == len(gold),
                     "reach_any": bool(hit),
                     "reach_all_live": flags[0],
                     "internal": [f"{s} p{p}" for s, p in internal],
                     "reach_all_public": flags[1],
                     "in_bundle_all": bool(gold) and all(g in bnd for g in gold),
                     "ctx": [f"{s} p{p}" for s, p in ctx],
                     "ctx_rank": {f"{s} p{p}": ctx.index((s, p)) + 1 for s, p in hit},
                     "best_rank": {f"{s} p{p}": v for (s, p), v in best.items()},
                     "secs": round(time.time() - t, 2)})
        if i % 25 == 0 or i == len(cases) - 1:
            save()
            print(i + 1, round(time.time() - t_all), "s", c["key"], exit_, rows[-1]["reach_all"], flush=True)
    if os.path.exists(args.out + ".current"): os.remove(args.out + ".current")   # past the questions: a later error is no question's crash
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
    sys.stdout.reconfigure(encoding="utf-8")
    R = json.load(open(args.reading, encoding="utf-8")); rows = R["rows"]
    old = [r for r in rows if "internal" not in r or "reach_all_public" not in r]
    if old:
        sys.path.insert(0, os.path.join(REPO, "src")); os.chdir(os.path.join(REPO, "src"))   # the internal-marked set is read relative to src/
        try: import retrieval_db
        except Exception as e:
            retrieval_db = None; print(f"warning: retrieval_db not importable ({e}); internal pages not marked in old readings", file=sys.stderr)
        for r in old:
            if "internal" not in r:
                r["internal"] = internal_gold(r["gold"], retrieval_db.is_excluded) if retrieval_db else []
            if "reach_all_public" not in r:
                pub = [g for g in r["gold"] if g not in r["stale"] and g not in r["internal"]]
                r["reach_all_public"] = bool(pub) and all(g in r["ctx"] for g in pub)
    std = [r for r in rows if r["type"] != "followup"]
    print(f"# Page-reach reading {R['at']}\n\nIndex: {R['chroma']} ({R['chunks']} chunks). {len(rows)} questions, "
          f"{R['secs'] // 60} min (the questions alone: {round(sum(r['secs'] for r in rows) / 60)} min). Model calls attempted and blocked: {R['blocked_calls'] or 'none'}.\n")
    print(f"**Headline (standalone questions, n={len(std)}):** all answer pages reach the prompt "
          f"{pct([r['reach_all'] for r in std])}%; at least one page {pct([r['reach_any'] for r in std])}%; "
          f"counting only gold pages still in the index {pct([r['reach_all_live'] for r in std])}%; "
          f"excluding internal answer pages (reach_all_public) {pct([r['reach_all_public'] for r in std])}%.\n")
    io = [r for r in rows if r["internal"] and any(g not in r["ctx"] for g in r["gold"])
          and all(g in r["internal"] for g in r["gold"] if g not in r["ctx"])]
    print(f"## Internal-only questions (not counted as misses in reach_all_public)\n\n{len(io)} questions moved out of the public misses.\n")
    for r in io: print(f"- {r['key']} ({r['type']}, {r['product']})")
    print()
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
    st = collections.defaultdict(list)
    for r in rows:
        for s in dict.fromkeys(r["stale_doc"]): st[s].append(r["key"])
    print("## Gold documents no longer in the index\n\nNames that match no source today. Truly gone: mark the cases "
          f"`gold_gone`. Renamed: add `\"old name\": \"index name\"` to {RENAMES}.\n")
    print("\n".join(f"- {s} ({len(k)}): {', '.join(k[:5])}{' ...' if len(k) > 5 else ''}"
                    for s, k in sorted(st.items(), key=lambda x: -len(x[1]))) or "none")
    rs = R.get("resolved") or {}
    if rs:
        print("\n## Gold names read as another source\n\n| name in the case | read as | how |\n|---|---|---|")
        for k, v in sorted(rs.items()): print(f"| {k} | {v[0] or '-'} | {v[1] or 'no match'} |")
    print()

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
    a.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    a.add_argument("--products"); a.add_argument("--limit", type=int)
    a.add_argument("--redo-stale", action="store_true", help="read again the rows whose gold document was not found")
    b = sub.add_parser("report"); b.add_argument("reading"); b.add_argument("--diff"); b.add_argument("--fault")
    b.add_argument("--since", default=time.strftime("%Y%m%d", time.localtime(time.time() - 7 * 86400)))
    c = sub.add_parser("index"); c.add_argument("--out", required=True); c.add_argument("--drop-pdfs", required=True)
    A = ap.parse_args()
    {"run": run, "report": report, "index": make_index}[A.cmd](A)
