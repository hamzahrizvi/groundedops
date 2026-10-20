"""Throwaway: does a decision model beat the current answerable/unanswerable gate?

Reproduces docs/decision-model-trial.md. Three phases, each cached in OUT:

  python tools/decision_model_trial.py chunks   # retrieval + reranker + classify, app venv
  python tools/decision_model_trial.py decide   # decision model, its OWN venv (strands-decider)
  python tools/decision_model_trial.py report   # numbers

Labels: the blind sets' `expected` (answered / rejected), single-turn only.
Current gate: what the pipeline actually served in the recorded sys_normal
runs (eval_runs/2026100{7,8}_*), so its latency and cost are measured, not
estimated. Nothing here touches the live app; retrieval reads a COPY of
chroma_db (CHROMA_DIR) because a backend holds the real one open.
"""
import json, os, sys, time, statistics as st

REPO = r"C:/Users/hrizvi/Downloads/Git/groundedops"
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
OUT = os.environ.get("TRIAL_OUT", "decision_trial_state.json")
SETS = [  # (recorded run, cases file)
    ("eval_runs/20261007_blind4/blind4_state.json", "src/blind4_ab_cases.json"),
    ("eval_runs/20261007_blind5/blind5_state.json", "src/blind5_ab_cases.json"),
    ("eval_runs/20261007_blind_nv4000/blind_nv4000_state.json", "src/blind_nv4000_ab_cases.json"),
    ("eval_runs/20261008_blind6/blind6_state.json", "eval_runs/20261008_blind6/blind6.json"),
]
NOT_SERVED_ROLES = {"sales", "clarify", "rejected"}
QUESTION = ("Do these documentation passages contain the information needed to "
            "answer the customer's question?")


def load_json(p):
    return json.load(open(p, encoding="utf-8"))


def cases():
    out = []
    for run, cf in SETS:
        rec = load_json(os.path.join(REPO, run))["sys_normal"]
        cs = load_json(os.path.join(REPO, cf))
        for c in cs["cases"] if isinstance(cs, dict) else cs:
            if c.get("turn1") or c.get("expected") not in ("answered", "rejected"):
                continue
            r = rec[c["id"]]
            out.append({"id": c["id"], "q": c["q"], "product": c.get("product"),
                        "type": c.get("type"), "label": c["expected"] == "answered",
                        "rec": {k: r.get(k) for k in ("answer", "wall", "usd", "role")}})
    return out


def phase_chunks(state):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    sys.path.insert(0, SRC); os.chdir(SRC)
    import torch  # noqa: F401  main thread first (see memory: session-tooling-gotchas)
    import main, answerability
    from retrieval_db import retrieve_from_db
    from reranker import rerank
    from text_utils import is_refusal
    rows = state.setdefault("chunks", {})
    for c in cases():
        if c["id"] in rows:
            continue
        scope = {"product": c["product"]} if c["product"] else None
        t = time.perf_counter()
        res = retrieve_from_db(c["q"], top_k=main.RETRIEVE_K, scope=scope)
        top = rerank(c["q"], res, top_k=5)
        t_ret = time.perf_counter() - t
        refused = is_refusal(c["rec"]["answer"] or "")
        t = time.perf_counter()
        kind = answerability.classify(c["q"], res, refused=refused, product=c["product"])["kind"]
        t_cls = time.perf_counter() - t
        rows[c["id"]] = {"passages": [x["text"][:1200] for x in top],
                         "rerank_top": top[0]["rerank_score"] if top else 0.0,
                         "classify": kind, "t_retrieve": t_ret, "t_classify": t_cls}
        print(c["id"], kind, round(rows[c["id"]]["rerank_top"], 3), flush=True)
    save(state)


def phase_decide(state):
    """One yes/no ('noul') per question, in-process on CPU (strands-decider
    0.2.0, run from its own venv). Same passages the pipeline's reranker kept."""
    from strands_decider.infer import load_engine
    from strands_decider.schema import NoulQuestion
    ckpt = os.environ.get("DECIDER", "StrandsAgents/strands-decider-2B-hobson-v21")
    t = time.perf_counter()
    eng = load_engine(ckpt, device="cpu")
    state["decide_load_s"] = time.perf_counter() - t
    question = {"answerable": NoulQuestion(instructions=QUESTION, criteria={
        "true": "The passages state the fact, figure or procedure the customer asks for.",
        "false": "The passages are about something else, or do not contain what is asked."})}
    rows = state.setdefault("decide", {})
    for c in cases():
        if c["id"] in rows:
            continue
        ch = state["chunks"][c["id"]]
        ctx = (f"Customer question: {c['q']}\n\nDocumentation passages:\n" +
               "\n---\n".join(ch["passages"]))
        t = time.perf_counter()
        r = eng.ask(ctx, question)
        rows[c["id"]] = {"p": r.answers["answerable"].noul, "t": time.perf_counter() - t,
                         "tokens": r.usage.input_tokens}
        print(c["id"], c["label"], round(rows[c["id"]]["p"], 3), round(rows[c["id"]]["t"], 2), flush=True)
        save(state)


def ece(ps, ys, bins=5):
    """Expected calibration error on P(answerable), equal-width bins."""
    tot, n = 0.0, len(ps)
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(ps) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if idx:
            tot += len(idx) / n * abs(st.mean(ps[i] for i in idx) - st.mean(ys[i] for i in idx))
    return tot


def score(name, preds, ys, probs=None, lat=None, usd=None):
    n = len(ys)
    acc = sum(p == y for p, y in zip(preds, ys)) / n
    neg = [i for i, y in enumerate(ys) if not y]
    pos = [i for i, y in enumerate(ys) if y]
    fpr = sum(preds[i] for i in neg) / len(neg)            # says answerable when it isn't
    frr = sum(not preds[i] for i in pos) / len(pos)        # refuses an answerable one
    line = f"{name:<34} acc {acc:.3f}  false-pass {fpr:.3f} ({sum(preds[i] for i in neg)}/{len(neg)})  false-refuse {frr:.3f}"
    if probs is not None:
        hi = [i for i, p in enumerate(probs) if p >= 0.9 or p <= 0.1]
        hi_acc = sum(preds[i] == ys[i] for i in hi) / len(hi) if hi else float("nan")
        brier = st.mean((p - y) ** 2 for p, y in zip(probs, ys))
        line += f"  ECE {ece(probs, ys):.3f}  Brier {brier:.3f}  conf>=0.9 n={len(hi)} acc {hi_acc:.3f}"
    if lat:
        q = st.quantiles(lat, n=20)
        line += f"  p50 {st.median(lat)*1000:.0f}ms  p95 {q[18]*1000:.0f}ms"
    if usd is not None:
        line += f"  ${usd*1000:.4f}/1k"
    print(line)


def auc_and_cut(name, probs, ys):
    """AUC (can the score separate the classes at all?) and the threshold that
    holds false passes at <= the served pipeline's, with what it costs."""
    pos = [p for p, y in zip(probs, ys) if y]
    neg = [p for p, y in zip(probs, ys) if not y]
    auc = st.mean(1.0 if a > b else 0.5 if a == b else 0.0 for a in pos for b in neg)
    for cut in sorted(set(probs)):
        fp = sum(p >= cut for p in neg)
        if fp <= 3:
            fr = sum(p < cut for p in pos)
            print(f"  {name}: AUC {auc:.3f}; cut {cut:.3f} keeps false passes at {fp}/{len(neg)} "
                  f"but refuses {fr}/{len(pos)} answerable ({fr/len(pos):.1%})")
            return


def phase_report(state):
    sys.path.insert(0, SRC)
    from text_utils import is_refusal
    cs = cases()
    ys = [c["label"] for c in cs]
    print(f"n={len(cs)}  answerable={sum(ys)}  unanswerable={len(ys)-sum(ys)}\n")
    served = [not is_refusal(c["rec"]["answer"] or "") and c["rec"]["role"] not in NOT_SERVED_ROLES
              for c in cs]
    score("pipeline as served (recorded)", served, ys,
          lat=[c["rec"]["wall"] for c in cs], usd=st.mean(c["rec"]["usd"] or 0 for c in cs))
    ch = state.get("chunks", {})
    if ch:
        k = [ch[c["id"]] for c in cs]
        score("classify() alone (on its own)", [x["classify"] == "stated" for x in k], ys,
              lat=[x["t_classify"] for x in k], usd=0.0)
        rr = [x["rerank_top"] for x in k]
        score("reranker top score >= 0.5", [p >= 0.5 for p in rr], ys, probs=rr,
              lat=[x["t_retrieve"] for x in k], usd=0.0)
    dec = state.get("decide", {})
    if dec and all(c["id"] in dec for c in cs):
        p = [dec[c["id"]]["p"] for c in cs]
        lat = [dec[c["id"]]["t"] for c in cs]
        score("decision model p >= 0.5", [x >= 0.5 for x in p], ys, probs=p, lat=lat, usd=0.0)
        score("pipeline AND decision veto", [s and x >= 0.5 for s, x in zip(served, p)], ys)
        print(f"  decision model load {state.get('decide_load_s', 0):.0f}s, "
              f"mean input {st.mean(dec[c['id']]['tokens'] for c in cs):.0f} tokens/call")
        auc_and_cut("decision model", p, ys)
        print("  decision model on the served pipeline's false passes: " + ", ".join(
            f"{c['id']} p={dec[c['id']]['p']:.2f}" for c, s in zip(cs, served) if s and not c["label"]))
    if ch:
        auc_and_cut("reranker top", rr, ys)
    print("\nfalse passes of the served pipeline:")
    for c, s in zip(cs, served):
        if s and not c["label"]:
            print(f"  {c['id']} [{c['type']}] {c['q'][:70]!r} -> {c['rec']['answer'][:90]!r}")


def save(state):
    json.dump(state, open(OUT, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    OUT = os.path.abspath(OUT)
    state = load_json(OUT) if os.path.exists(OUT) else {}
    {"chunks": phase_chunks, "decide": phase_decide, "report": phase_report}[sys.argv[1]](state)
