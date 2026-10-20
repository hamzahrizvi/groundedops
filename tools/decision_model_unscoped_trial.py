"""Throwaway: does a decision model's `choice` beat clarify.classify_unscoped?

Reproduces docs/decision-model-unscoped-trial.md. Three phases, each cached in OUT:

  python tools/decision_model_unscoped_trial.py current  # live classify_unscoped, app venv
      (TRIAL_ROW=current_deepseek PROVIDER_ROLE_DEFAULT=deepseek for the backup alone)
  python tools/decision_model_unscoped_trial.py decide   # decision model, its OWN venv (strands-decider)
  python tools/decision_model_unscoped_trial.py report   # numbers (any venv)

Labels: src/eval_cases_unscoped_{probe,holdout}.json `outcome` (clarify /
answered / rejected). For classify_unscoped the right verdict is "product"
for clarify and answered, "other" for rejected. For the decision model's
product pick, the gold product of an answered case is read off its
`reference` (GOLD below); a clarify case's right pick is "ask".
Nothing here touches the live app: the current phase calls the same
function main.query() calls, on the same catalogue, and only records it.
"""
import json, os, re, sys, time, statistics as st

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
OUT = os.environ.get("TRIAL_OUT", "decision_unscoped_state.json")
FILES = ["eval_cases_unscoped_probe.json", "eval_cases_unscoped_holdout.json"]
# catalog_config.json and .env are untracked: in a worktree, point GO_DATA at
# the main checkout's src. Code still comes from SRC.
DATA = os.environ.get("GO_DATA", SRC)
# $ per 1M tokens (in, out): gpt-5-mini from tools/blind_eval.py; DeepSeek V4
# Flash cache-miss list price per a secondary source (CloudZero, 2026-08).
PRICE = {"openai": (0.25, 2.00), "deepseek": (0.14, 0.28)}
# reference text -> acceptable product keys. The ICU API documents are shared
# by both MyCheckr cameras (clarify.py fault 2), so either is right.
GOLD = [("SMART Coin", {"sku_scs"}), ("MyCheckr Mini", {"mycheckr_mini"}),
        ("MyConnect", {"myconnect"}), ("ICU", {"mycheckr", "mycheckr_mini"}),
        ("BV30", {"bv30"}), ("NV9USB", {"nv9usb"}), ("NV9 Spectral", {"nv9_spectral"}),
        ("NV200", {"nv200s"}), ("MyCheckr", {"mycheckr"})]


def load_json(p):
    return json.load(open(p, encoding="utf-8"))


def cases():
    out = []
    for f in FILES:
        for i, c in enumerate(load_json(os.path.join(SRC, f))["cases"]):
            gold = {"clarify": {"ask"}, "rejected": {"other"}}.get(c["outcome"])
            if gold is None:
                gold = next(g for k, g in GOLD if k in c["reference"])
            out.append({"id": f"{f.split('_')[-1][:5]}-{i:02d}", "q": c["q"],
                        "outcome": c["outcome"], "gold": sorted(gold)})
    return out


def catalog():
    return load_json(os.path.join(DATA, "catalog_config.json"))


def products(cat):
    return [(p["key"], p["name"], c["name"]) for c in cat["categories"]
            for p in c["products"] if not p["key"].endswith("_general")]


def phase_current(state):
    """The live call, exactly as main.query() makes it. _usage_tokens is
    wrapped (this process only) to keep the prompt/completion split."""
    for line in open(os.path.join(DATA, ".env"), encoding="utf-8-sig"):  # as main._load_env_file
        k, eq, v = line.strip().partition("=")
        if eq and not k.startswith("#"):
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    sys.path.insert(0, os.path.abspath(SRC)); os.chdir(DATA)
    import llm, clarify
    seen = {}
    orig = llm._usage_tokens
    def spy(body, provider):
        seen["usage"] = (body or {}).get("usage") or {}
        return orig(body, provider)
    llm._usage_tokens = spy
    orig_gen = llm.generate_with_fallback
    def gen(role, prompt, *a, **k):
        r = orig_gen(role, prompt, *a, **k)
        seen["provider"], seen["model"] = r.get("provider"), r.get("model")
        return r
    llm.generate_with_fallback = gen
    cat = catalog()
    rows = state.setdefault(os.environ.get("TRIAL_ROW", "current"), {})
    for c in cases():
        if c["id"] in rows:
            continue
        seen.clear()
        t = time.perf_counter()
        v = clarify.classify_unscoped(c["q"], cat)
        u = seen.get("usage", {})
        rows[c["id"]] = {"verdict": v, "t": time.perf_counter() - t,
                         "in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
                         "model": f"{seen.get('provider')}/{seen.get('model')}"}
        print(c["id"], c["outcome"], v, round(rows[c["id"]]["t"], 2), rows[c["id"]]["model"], flush=True)
        save(state)


def questions(cat):
    """Two choice questions asked in one pass over the same state.
    `about` mirrors classify_unscoped's PRODUCT/OTHER prompt word for word in
    its option descriptions; `which` is the richer pick: one product, ask, or
    other."""
    from strands_decider.schema import ChoiceQuestion
    about = ChoiceQuestion(
        instructions="Is this customer-support message about one of the listed products?",
        criteria={
            "product": "The message is about one of these products or something they do: "
                       "a fault, symptom, light, sound, error, setting, part, installation, "
                       "cleaning or maintenance - even if it does not say which product.",
            "other": "The message is about something else: another company's device, a "
                     "phone, computer, router or printer, an order, delivery or account, "
                     "general knowledge, or small talk."})
    opts = {k: f"{n} ({cn}): the message can only be about this product." for k, n, cn in products(cat)}
    opts["ask"] = ("The message is about these products but could apply to several of them; "
                   "the assistant must ask which product the customer means.")
    opts["other"] = about.criteria["other"]
    which = ChoiceQuestion(
        instructions="Which product is this customer-support message about?", criteria=opts)
    return {"about": about, "which": which}


def state_text(cat, q):
    lines = [f"- {c['name']}: " + ", ".join(p["name"] for p in c["products"]
                                             if not p["key"].endswith("_general"))
             for c in cat["categories"]]
    msg = " ".join(q.split())[:400]
    return ("A customer-support assistant covers ONLY these products:\n" + "\n".join(lines)
            + f'\n\nCustomer message: "{msg}"')


def phase_decide(state):
    from strands_decider.infer import load_engine
    ckpt = os.environ.get("DECIDER", "StrandsAgents/strands-decider-2B-hobson-v21")
    t = time.perf_counter()
    eng = load_engine(ckpt, device="cpu")
    state["decide_load_s"] = time.perf_counter() - t
    cat = catalog()
    qs = questions(cat)
    rows = state.setdefault("decide", {})
    for c in cases():
        if c["id"] in rows:
            continue
        t = time.perf_counter()
        r = eng.ask(state_text(cat, c["q"]), qs)
        rows[c["id"]] = {k: r.answers[k].probabilities for k in qs}
        rows[c["id"]]["which_conf"] = r.answers["which"].confidence  # (N*p_max-1)/(N-1)
        rows[c["id"]].update(t=time.perf_counter() - t, tokens=r.usage.input_tokens)
        w = rows[c["id"]]["which"]
        top = max(w, key=w.get)
        print(c["id"], c["outcome"], top, round(w[top], 3),
              round(rows[c["id"]]["about"]["product"], 3), round(rows[c["id"]]["t"], 2), flush=True)
        save(state)


def ece(conf, right, bins=5):
    """Expected calibration error of top-choice confidence, equal-width bins."""
    tot, n = 0.0, len(conf)
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(conf) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if idx:
            tot += len(idx) / n * abs(st.mean(conf[i] for i in idx) - st.mean(right[i] for i in idx))
    return tot


def lat(ts):
    q = st.quantiles(ts, n=20)
    return f"p50 {st.median(ts)*1000:.0f}ms  p95 {q[18]*1000:.0f}ms"


def binary(name, preds, cs):
    """PRODUCT/OTHER: the decision classify_unscoped makes."""
    ys = [c["outcome"] != "rejected" for c in cs]
    acc = sum(p == y for p, y in zip(preds, ys)) / len(cs)
    by = {o: (sum(p == y for p, y, c in zip(preds, ys, cs) if c["outcome"] == o),
              sum(c["outcome"] == o for c in cs)) for o in ("clarify", "answered", "rejected")}
    print(f"{name:<38} acc {acc:.3f}  " + "  ".join(f"{o} {a}/{n}" for o, (a, n) in by.items()))


def phase_report(state):
    cs = cases()
    print(f"n={len(cs)}  " + "  ".join(f"{o}={sum(c['outcome']==o for c in cs)}"
                                       for o in ("clarify", "answered", "rejected")) + "\n")
    for row in ("current", "current_deepseek"):
        cur = state.get(row, {})
        if not (cur and all(c["id"] in cur for c in cs)):
            continue
        k = [cur[c["id"]] for c in cs]
        binary(f"classify_unscoped [{row}]", [x["verdict"] != "other" for x in k], cs)
        none = sum(x["verdict"] is None for x in k)
        pr = [PRICE[x["model"].split("/")[0]] for x in k]
        usd = st.mean(x["in"] * a + x["out"] * b for x, (a, b) in zip(k, pr)) / 1e6
        print(f"  {lat([x['t'] for x in k])}  ${usd*1000:.4f}/1k  in {st.mean(x['in'] for x in k):.0f} "
              f"out {st.mean(x['out'] for x in k):.0f} tok  no-verdict {none}  "
              f"models {sorted({x['model'] for x in k})}")
        print("  wrong: " + ", ".join(f"{c['id']}({c['outcome'][:3]})->{x['verdict']}"
                                     for c, x in zip(cs, k) if (x["verdict"] != "other") != (c["outcome"] != "rejected")))
    dec = state.get("decide", {})
    if not (dec and all(c["id"] in dec for c in cs)):
        return
    d = [dec[c["id"]] for c in cs]
    pa = [x["about"]["product"] for x in d]
    binary("decision model `about` p>=0.5", [p >= 0.5 for p in pa], cs)
    ya = [c["outcome"] != "rejected" for c in cs]
    ca = [max(p, 1 - p) for p in pa]
    ra = [(p >= 0.5) == y for p, y in zip(pa, ya)]
    hi = [i for i, x in enumerate(ca) if x >= 0.9]
    print(f"  ECE {ece(ca, ra):.3f}  conf>=0.9 n={len(hi)} acc "
          f"{(sum(ra[i] for i in hi)/len(hi)) if hi else float('nan'):.3f}")
    tops = [max(x["which"], key=x["which"].get) for x in d]
    binary("decision model `which` != other", [t != "other" for t in tops], cs)
    conf = [x["which"][t] for x, t in zip(d, tops)]
    right = [t in c["gold"] for t, c in zip(tops, cs)]
    print(f"\n`which` top-1 (product / ask / other): {sum(right)}/{len(cs)} = {sum(right)/len(cs):.3f}")
    for o in ("clarify", "answered", "rejected"):
        ix = [i for i, c in enumerate(cs) if c["outcome"] == o]
        print(f"  {o:<9} right {sum(right[i] for i in ix)}/{len(ix)}")
    ix = [i for i, c in enumerate(cs) if c["outcome"] == "clarify"]
    print(f"  picks a product when it should ask: {sum(tops[i] not in ('ask','other') for i in ix)}/{len(ix)}")
    ix = [i for i, c in enumerate(cs) if c["outcome"] == "answered"]
    print(f"  answered: right product {sum(right[i] for i in ix)}, ask {sum(tops[i]=='ask' for i in ix)}, "
          f"other {sum(tops[i]=='other' for i in ix)}, wrong product "
          f"{sum(tops[i] not in ('ask','other') and not right[i] for i in ix)} of {len(ix)}")
    pc = [x["which_conf"] for x in d]
    hi = [i for i, x in enumerate(pc) if x >= 0.9]
    print(f"  ECE(p_max) {ece(conf, right):.3f}  Brier-top {st.mean((p - r)**2 for p, r in zip(conf, right)):.3f}  "
          f"confidence>=0.9 n={len(hi)} acc {(sum(right[i] for i in hi)/len(hi)) if hi else float('nan'):.3f}  "
          f"p_max range {min(conf):.2f}-{max(conf):.2f}  confidence range {min(pc):.2f}-{max(pc):.2f}")
    print(f"  {lat([x['t'] for x in d])}  load {state.get('decide_load_s', 0):.0f}s  "
          f"mean input {st.mean(x['tokens'] for x in d):.0f} tokens/call")
    print("  rows: " + "; ".join(f"{c['id']}({c['outcome'][:3]}) {t} {p:.2f}{'' if r else ' X'}"
                                 for c, t, p, r in zip(cs, tops, conf, right)))


def save(state):
    json.dump(state, open(OUT, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    OUT = os.path.abspath(OUT)
    state = load_json(OUT) if os.path.exists(OUT) else {}
    {"current": phase_current, "decide": phase_decide, "report": phase_report}[sys.argv[1]](state)
