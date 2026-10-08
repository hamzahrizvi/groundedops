"""Blind set runner: this bot vs a whole-manual gpt-5-mini baseline, graded blind by gpt-5.

Promoted from eval_runs/20261008_blind6/blind_eval.py (it was copied per run).

  python tools/blind_eval.py <run_dir> system normal|perf | baseline | grade | reset
  python tools/blind_eval.py <run_dir> prompt <case_id>   (print the grading prompt, no API call)

<run_dir> holds <SET>.json ({"cases": [...]}) and manuals/<product>.txt
(tools/export_manuals.py). Each phase caches into <run_dir>/<SET>_state.json, so a
crash resumes. Run each "system" phase in its own process (PERFORMANCE_MODE is
read when policy.py is imported).

Env:
  SET      case file stem (default "cases"; old runs used "blind6" etc.)
  SYSTEMS  graded systems, comma list (default "sys_normal,baseline")
  GO_CODE  src/ whose code is imported (default: this repo's src) -- point at a
           Dev worktree's src to test a branch
  GO_DATA  src/ used as cwd: chroma_db, .env and the JSON stores (default GO_CODE)
  reset    drops every sys_* answer, the grades and the summary but keeps the
           baseline answers, so a copied run dir re-measures new code cheaply.
  grade    shows the grader the full text of each case's cited pages (manuals/,
           capped at 30000 chars) and stores the blind A/B labels per case.
"""
import os, sys, json, time, uuid, random, re, statistics as st
from concurrent.futures import ThreadPoolExecutor

RUN = os.path.abspath(sys.argv[1])
CODE = os.path.abspath(os.environ.get("GO_CODE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")))
DATA = os.path.abspath(os.environ.get("GO_DATA", CODE))
SET = os.environ.get("SET", "cases")
SYSTEMS = os.environ.get("SYSTEMS", "sys_normal,baseline").split(",")
OUT = os.path.join(RUN, f"{SET}_state.json")
cases = json.load(open(os.path.join(RUN, f"{SET}.json"), encoding="utf-8"))["cases"]
state = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
def save(): json.dump(state, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
IN, CACHED, OUTP = 0.25, 0.025, 2.00   # gpt-5-mini $ per 1M tokens
def usd(u):
    p = u.get("prompt_tokens", 0); c = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return ((p - c) * IN + c * CACHED + u.get("completion_tokens", 0) * OUTP) / 1e6

def load_env(path):
    """GO_DATA's .env, same rules as main._load_env_file. main.py reads the .env
    beside its own file, which a Dev worktree (GO_CODE) does not have."""
    if not os.path.isfile(path): return
    for line in open(path, encoding="utf-8-sig"):
        key, eq, value = line.strip().partition("=")
        if eq and key.strip() and not key.startswith("#") and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")
load_env(os.path.join(DATA, ".env"))
if os.path.isfile(os.path.join(DATA, ".secrets.enc")):
    os.environ.setdefault("SECRETS_VAULT_PATH", os.path.join(DATA, ".secrets.enc"))

phase = sys.argv[2]
if phase == "reset":
    for k in [k for k in state if k.startswith("sys_") or k in ("grades", "summary")]:
        del state[k]
    save(); print("reset", OUT, "kept:", list(state)); sys.exit(0)
if phase in ("system", "baseline"):
    sys.path.insert(0, CODE); os.chdir(DATA)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    # docstore.store_dir() resolves <GO_CODE>/../documents, which a Dev
    # worktree does not have: point it at GO_DATA's.
    if CODE != DATA:
        os.environ.setdefault("SOURCE_FILE_DIR", os.path.join(os.path.dirname(DATA), "documents"))
    # Every role pinned, or the DeepSeek backup leaks in (S29 run 1).
    for _r in ("DEFAULT", "ADVANCED", "BACKUP"):
        os.environ[f"PROVIDER_ROLE_{_r}"] = "openai"; os.environ[f"MODEL_ROLE_{_r}"] = "gpt-5-mini"
    if phase == "system":
        os.environ["PERFORMANCE_MODE"] = "1" if sys.argv[3] == "perf" else "0"
    import torch  # noqa: F401  main thread first
    import main, llm
    main.capability = lambda *_a, **_k: True
import requests

def chat(model, messages, effort=None, timeout=300):
    body = {"model": model, "messages": messages}
    if effort: body["reasoning_effort"] = effort
    for attempt in range(3):
        try:
            r = requests.post("https://api.openai.com/v1/chat/completions", json=body, timeout=timeout,
                              headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"})
            r.raise_for_status(); j = r.json()
            return j["choices"][0]["message"]["content"], j.get("usage", {})
        except Exception as e:
            if attempt == 2: return f"[ERROR {e}]", {}
            time.sleep(5)

if phase == "system":
    mode = sys.argv[3]
    assert bool(main._policy_setting("performance_mode", False)) == (mode == "perf"), "mode not applied"
    usage = []
    _post = llm._post
    def spy(url, *a, **k):
        r = _post(url, *a, **k)
        try:
            if "openai" in url: usage.append(r.json().get("usage", {}))
        except Exception: pass
        return r
    llm._post = spy
    rows = state.setdefault(f"sys_{mode}", {})
    for c in cases:
        if c["id"] in rows: continue
        sid = f"{SET}-{uuid.uuid4()}"
        if c.get("turn1"):
            main._traced_query(main.QueryRequest(q=c["turn1"], product=c.get("product"), session_id=sid), None, attach=True)
        usage.clear(); t = time.time()
        d = main._traced_query(main.QueryRequest(q=c["q"], product=c.get("product"), session_id=sid), None, attach=True)
        rows[c["id"]] = {"answer": d.get("answer", ""), "wall": round(time.time() - t, 2),
                         "usd": round(sum(map(usd, usage)), 6), "calls": len(usage), "cached": sum((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) for u in usage), "prompt_tokens": sum(u.get("prompt_tokens", 0) for u in usage),
                         "flagged": d.get("flagged"), "role": d.get("role"), "model": d.get("model")}
        save(); print(mode, c["id"], rows[c["id"]]["wall"], rows[c["id"]]["usd"], repr(rows[c["id"]]["answer"][:60]), flush=True)

elif phase == "baseline":
    MAN = os.path.join(RUN, "manuals"); names = main._product_names()
    def manual(product, extra=()):
        keys = ([product] + [k for k in extra if k and k != product]) if product else sorted(f[:-4] for f in os.listdir(MAN))
        seen, parts = set(), []
        for k in keys:
            for chunk in open(os.path.join(MAN, f"{k}.txt"), encoding="utf-8").read().split("\n\n["):
                if chunk not in seen: seen.add(chunk); parts.append(chunk)
        return "\n\n[".join(parts)
    SYS = ("You are the customer-support assistant on Innovative Technology's website. Answer the customer's "
           "question using only the product documentation below. If the documentation does not cover it, say so "
           "plainly rather than guessing. Be concise and practical.\n\n<documentation>\n{doc}\n</documentation>")
    def run(c):
        sel = f"(The customer is chatting about: {names.get(c['product'], c['product'])})\n" if c.get("product") else ""
        msgs = [{"role": "system", "content": SYS.format(doc=manual(c.get("product"), c.get("compare_with") or ()))}]
        first = "turn1" if c.get("turn1") else "q"
        for key in ("turn1", "q"):
            if not c.get(key): continue
            msgs.append({"role": "user", "content": (sel if key == first else "") + c[key]})
            t = time.time(); a, u = chat("gpt-5-mini", msgs, effort="minimal")
            msgs.append({"role": "assistant", "content": a})
        return c["id"], {"answer": a, "wall": round(time.time() - t, 2), "usd": round(usd(u), 6)}
    rows = state.setdefault("baseline", {})
    with ThreadPoolExecutor(6) as ex:
        for cid, out in ex.map(run, [c for c in cases if c["id"] not in rows]):
            rows[cid] = out; save(); print("baseline", cid, out["wall"], flush=True)

elif phase in ("grade", "prompt"):
    L = "ABCDEFG"[:len(SYSTEMS)]
    G = """You are grading {n} customer-support answers to the same question, written by {n} different systems. You do not know which system wrote which. Judge only substance against the reference; ignore formatting, length, citations, tone and any offer to contact support or list of suggested questions.

PRODUCT SELECTED IN THE CHAT: {product}
{turn1}CUSTOMER QUESTION: {q}
QUESTION TYPE: {type}
EXPECTED BEHAVIOUR: {expected}
REFERENCE (from the manual, {pages}): {reference}
KEY FACTS A CORRECT ANSWER CONTAINS: {must}

CITED PAGES (full text of the manual pages the reference comes from): {cited}

The reference is an extract, not the whole manual. A figure or fact the reference does not quote is fine if it appears in the cited pages, or if it contradicts neither the reference nor the question. Deduct only for a contradiction, or for a figure found in neither the reference nor the cited pages.

Score each answer 0-3:
- expected "answered": 3 = correct and complete, including any condition/qualifier the question needs; also 3 when every key fact is present and only a qualifier the question did not ask for is left out; 2 = core fact right but misses a qualifier the question needs, or adds a minor inaccuracy; 1 = declines, says it cannot find it, or only asks a question (safe but unhelpful), or gives a wrong method or wrong fact for one of several parts the question asks; 0 = states something wrong or invented as the main answer, or answers about the wrong product.
- expected "rejected" (not documented / product does not exist / off-topic): 3 = clearly says it is not documented or it cannot help, asserting nothing; 2 = says it is not documented but adds speculation or an overreaching conclusion; 0 = the main answer asserts a Yes/No or a fact as if documented.
- expected "clarify" (too vague): verdict "clarified" and 3 only if the answer asks the customer which product/what is meant, or briefly and correctly covers each candidate. An answer that asks nothing is graded as an answer: a long dump or one that picks a single product scores at most 2 (2 only if it states which product and is correct); any mislabelled product or wrong part = 1; answers confidently for an unstated or wrong product = 0.
Verdict per answer, one of: correct, partial, safe_decline, wrong, correct_refusal, clarified.

{answers}

Reply with JSON only: {{{shape}, "best": {best}, "why": "<one sentence>"}}"""
    MAN, CAP = os.path.join(RUN, "manuals"), 30000
    pages = {}   # "<file>.pdf pN" -> every chunk of that page
    if os.path.isdir(MAN):
        for f in sorted(os.listdir(MAN)):
            for chunk in open(os.path.join(MAN, f), encoding="utf-8").read().split("\n\n["):
                chunk = chunk.lstrip("[")
                pages.setdefault(chunk.split("]", 1)[0], []).append("[" + chunk)
    def cited(c):
        text = "\n\n".join(ch for p in c.get("pages") or [] for ch in pages.get(p, []))
        return "\n" + text[:CAP] if text else "n/a"
    rng = random.Random(4)
    order = {c["id"]: rng.sample(SYSTEMS, len(SYSTEMS)) for c in cases}
    def prompt(c):
        return G.format(n=len(SYSTEMS), product=c.get("product") or "none (unscoped chat)",
                        turn1=f"EARLIER MESSAGE IN THIS CHAT: {c['turn1']}\n" if c.get("turn1") else "",
                        q=c["q"], type=c["type"], expected=c["expected"], pages=", ".join(c.get("pages") or []) or "n/a",
                        reference=c["reference"], must=", ".join(c.get("must_include") or []) or "n/a", cited=cited(c),
                        answers="\n\n".join(f"ANSWER {k}:\n{state.get(s, {}).get(c['id'], {}).get('answer', '(no answer yet)')}"
                                             for k, s in zip(L, order[c["id"]])),
                        shape=", ".join(f'"{k}": {{"score": n, "verdict": "..."}}' for k in L),
                        best=" | ".join(f'"{k}"' for k in L) + ' | "tie"')
    if phase == "prompt":
        sys.stdout.reconfigure(encoding="utf-8")  # manuals carry symbols cp1252 cannot print
        print(prompt(next(c for c in cases if c["id"] == sys.argv[3]))); sys.exit(0)
    def grade(c):
        o = order[c["id"]]
        raw, _ = chat("gpt-5", [{"role": "user", "content": prompt(c)}], effort="medium")
        try:
            g = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        except Exception:
            return c["id"], {"raw": raw}
        return c["id"], {**{s: g[k] for k, s in zip(L, o)}, "why": g.get("why"), "labels": dict(zip(L, o)),
                         "best": "tie" if g.get("best") == "tie" else dict(zip(L, o)).get(g.get("best"))}
    grades = state.setdefault("grades", {})
    with ThreadPoolExecutor(6) as ex:
        for cid, g in ex.map(grade, [c for c in cases if c["id"] not in grades or "raw" in grades[c["id"]]]):
            grades[cid] = g; save(); print("graded", cid, g.get("best"), flush=True)
    ok = [c for c in cases if "raw" not in grades[c["id"]]]
    summary = {"ungraded": [c["id"] for c in cases if c not in ok]}
    for s in SYSTEMS:
        sc = [grades[c["id"]][s]["score"] for c in ok]
        vd = [grades[c["id"]][s]["verdict"] for c in ok]
        walls = [state[s][c["id"]]["wall"] for c in ok]
        cost = [state[s][c["id"]]["usd"] for c in ok]
        summary[s] = {"pct_of_max": round(100 * sum(sc) / (3 * len(sc)), 1), "mean": round(st.mean(sc), 2),
                      "verdicts": {v: vd.count(v) for v in sorted(set(vd))},
                      "median_s": st.median(walls), "p90_s": sorted(walls)[int(.9 * len(walls))],
                      "usd_mean": round(st.mean(cost), 6), "usd_median": round(st.median(cost), 6), "usd_max": max(cost),
                      "by_type": {t: f"{sum(grades[c['id']][s]['score'] for c in ok if c['type'] == t)}/{3 * sum(1 for c in ok if c['type'] == t)}"
                                  for t in sorted({c['type'] for c in ok})}}
    bests = [grades[c["id"]].get("best") for c in ok]
    summary["best"] = {k: bests.count(k) for k in SYSTEMS + ["tie"]}
    state["summary"] = summary; save()
    print(json.dumps(summary, indent=1))
