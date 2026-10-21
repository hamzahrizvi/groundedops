"""Blind set runner: this bot vs a whole-manual baseline answer, graded blind by a third model.
LAB_PROVIDER picks who serves all three (default deepseek: V4 Flash, thinking off; openai:
gpt-5-mini bot and baseline, gpt-5 grader, the lab's models until the credit ran out on 2026-10-10).

Promoted from eval_runs/20261008_blind6/blind_eval.py (it was copied per run).

  python tools/blind_eval.py <run_dir> system normal|perf | baseline | grade | remark | reset | models
  python tools/blind_eval.py <run_dir> prompt <case_id>   (print the grading prompt, no API call)
  ... --repeats N   (anywhere: runs the phase N times, repeat k in its own process with REPEAT=k;
           stops at the first non-zero exit and returns it. `reset --repeats N` resets every repeat)
  python tools/blind_eval.py <run_dir> vendor kapa|fin    (a market leader answers the cases; then
           SYSTEMS=sys_normal,baseline,kapa,fin ... remark grades all of them blind together)

Cases merged from heldout/ (they carry "src") only run from a folder under OPS/heldout/.

<run_dir> holds <SET>.json ({"cases": [...]}) and manuals/<product>.txt
(tools/export_manuals.py). Each phase caches into <run_dir>/<SET>_state.json, so a
crash resumes. Run each "system" phase in its own process (PERFORMANCE_MODE is
read when policy.py is imported).

Env:
  REPEAT   which answer of each question this process works on (default 1): repeat 1 is
           <SET>_state.json, repeat k>1 is <SET>_state.r<k>.json (D-20261018-02). Grading a repeat
           k>1 copies a SYSTEMS arm it lacks from repeat 1 (listed in "copied_from_r1"), so a weekly
           with one baseline answer grades every repeat beside the same baseline.
  ADAPTIVE=1  the CEO, 2026-10-21: only questions not yet answered right cost more. Repeat k>1 asks only the
           questions whose repeat-1 answer scored 3; the rest copy repeat 1's answer and grade (copied_from_r1,
           usd 0). The baseline asks only questions no repeat scored 3 on; the rest get a "not_asked" row, graded
           3 / "not_asked" without a call (a whole manual cannot beat a right answer). Order: system, grade
           (SYSTEMS=sys_normal), the later repeats and their grades, baseline, grade (sys_normal,baseline)
           -- tools/weekly_blind.sh.
  SET      case file stem (default "cases"; old runs used "blind6" etc.)
  SYSTEMS  graded systems, comma list (default "sys_normal,baseline")
  GO_CODE  src/ whose code is imported (default: this repo's src) -- point at a
           Dev worktree's src to test a branch
  GO_DATA  src/ used as cwd: chroma_db, .env and the JSON stores (default GO_CODE)
  LAB_OPS  the office folder whose lab_cap.json caps the lab's spend (default C:/Users/hrizvi/groundedops-ops).
           At the cap (tools/ops_costs.py cap_status) a paid phase stops with exit code 3 and
           "lab stopped at the cap": answers so far are saved, a re-mark keeps its old grades.
  reset    drops every sys_* answer, the grades and the summary but keeps the
           baseline answers, so a copied run dir re-measures new code cheaply.
  grade    shows the grader the full text of each case's cited pages (manuals/, capped at 30000 chars),
           plus the pages of the case's own manual(s) an answer copies 3+ eight-word runs from (2 per
           answer, 12000 chars, D-20261019-05), and stores the blind A/B labels per case.
           A clean refusal on an expected-"rejected" case scores 3 whatever the other arm says (D-20261010-28).
           A correct_refusal on a case not expected 'rejected' is stored as safe_decline, score <= 1 (D-20261016-14).
           A missing must_include fact caps an "answered" case at 2 (partial); a clarifying question scores 3 on any product (D-20261020-04).
"""
import os, sys, json, time, uuid, random, re, statistics as st
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # tools/, also when run via runpy
from lab_lock import lab_lock
from market_compare import under, clamp_refusal

if "--repeats" in sys.argv:   # the parent only loops; each repeat is its own process (PERFORMANCE_MODE, torch)
    _i = sys.argv.index("--repeats"); _n = int(sys.argv[_i + 1]); _argv = sys.argv[1:_i] + sys.argv[_i + 2:]
    import subprocess
    for _k in range(1, _n + 1):
        _rc = subprocess.call([sys.executable, "-X", "faulthandler", os.path.abspath(__file__)] + _argv, env={**os.environ, "REPEAT": str(_k)})
        if _rc: sys.exit(_rc)
        # ponytail: a fixed pause longer than lab_lock's 20 s poll, so a job waiting for the lab gets it between
        # repeats (back to back, 6 gate passes held the weekly run off the lab for over an hour, 2026-10-19)
        if sys.argv[2:3] == ["system"] and _k < _n: time.sleep(25)
    sys.exit(0)
REPEAT = int(os.environ.get("REPEAT", "1"))
RUN = os.path.abspath(sys.argv[1])
CODE = os.path.abspath(os.environ.get("GO_CODE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")))
DATA = os.path.abspath(os.environ.get("GO_DATA", CODE))
SET = os.environ.get("SET", "cases")
SYSTEMS = os.environ.get("SYSTEMS", "sys_normal,baseline").split(",")
OUT = os.path.join(RUN, f"{SET}_state.json" if REPEAT == 1 else f"{SET}_state.r{REPEAT}.json")
cases = json.load(open(os.path.join(RUN, f"{SET}.json"), encoding="utf-8"))["cases"]
# ponytail: only cases stamped "src" by market_compare merge are caught; a hand-built hidden set is not
if any(c.get("src") for c in cases) and not under(RUN, os.path.join(os.environ.get("LAB_OPS", "C:/Users/hrizvi/groundedops-ops"), "heldout")): sys.exit("these cases are hidden (merged from heldout/); run folder must be under OPS/heldout/")
state = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
def save(): json.dump(state, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
PROV = os.environ.get("LAB_PROVIDER", "deepseek")
# DeepSeek grades with thinking off: on, it failed right answers that add true detail (grader_ab.py, 2026-09-28)
MODELS = {"openai": {"bot": "gpt-5-mini", "baseline": "gpt-5-mini", "grader": "gpt-5"},
          "deepseek": {"bot": "deepseek-v4-flash", "baseline": "deepseek-v4-flash", "grader": "deepseek-v4-flash"}}[PROV]
REMARK = None   # the backup file while a "remark" phase re-grades this run
URL = {"openai": "https://api.openai.com/v1/chat/completions", "deepseek": "https://api.deepseek.com/v1/chat/completions"}[PROV]
# $ per 1M tokens: input, cached input, output. ponytail: DeepSeek at its peak rate; off-peak (most hours) is half
PRICE = {"gpt-5-mini": (0.25, 0.025, 2.00), "gpt-5": (1.25, 0.125, 10.00), "deepseek-v4-flash": (0.30, 0.006, 1.20)}
def cached(u): return (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens", 0)
def usd(u, model):
    i, c_, o = PRICE[model]; p, c = u.get("prompt_tokens", 0), cached(u)
    return ((p - c) * i + c * c_ + u.get("completion_tokens", 0) * o) / 1e6

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
R1 = os.path.join(RUN, f"{SET}_state.json")
ADAPT = os.environ.get("ADAPTIVE") == "1"
def right(st):
    """Ids whose bot answer scored 3 in that state's grades."""
    return {cid for cid, g in st.get("grades", {}).items() if (g.get("sys_normal") or {}).get("score") == 3}
NOT_ASKED = {"score": 3, "verdict": "not_asked"}
if phase in ("grade", "remark") and REPEAT > 1 and os.path.exists(R1):   # e.g. a weekly's one baseline answer
    _r1 = json.load(open(R1, encoding="utf-8"))
    for _s in SYSTEMS:
        if _s not in state and _s in _r1:
            state[_s] = _r1[_s]; state.setdefault("copied_from_r1", []).append(_s)
    save()
# Any blind test, a lane's or one started by hand, holds the office's claim on Customer's test run
# (tools/office_next.py claims): no lane starts a second one meanwhile, and the office page shows it as running.
if phase in ("system", "baseline", "grade", "remark", "vendor"):
    import atexit, datetime
    _claim = os.path.join(os.environ.get("LAB_OPS", "C:/Users/hrizvi/groundedops-ops"), "state", "claims", "customer.json")
    try:
        os.makedirs(os.path.dirname(_claim), exist_ok=True)
        with os.fdopen(os.open(_claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY), "w", encoding="utf-8") as f:
            json.dump({"lane": "hand", "pid": os.getpid(), "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                       "line": f"RUN  groundedops-office-lab  blind test {'/'.join(RUN.replace(os.sep, '/').split('/')[-2:])} ({phase})"}, f)
        atexit.register(lambda: os.path.exists(_claim) and os.remove(_claim))
    except FileExistsError:
        pass   # a lane's Customer run (or another test) already holds it
CAP, spent = None, 0.0   # the CEO's lab cap (D-20261010-31) and what this process has spent against it
def cap_read():
    global CAP
    from ops_costs import cap_status
    CAP = cap_status(os.environ.get("LAB_OPS", "C:/Users/hrizvi/groundedops-ops"))
def capped(): return bool(CAP) and (CAP["stopped"] or (CAP["usd"] is not None and CAP["spent"] + spent >= CAP["usd"]))
def stop():
    print(f"lab stopped at the cap: ${(CAP['spent'] or 0) + spent:.2f} of ${CAP['usd']:.2f} this {CAP['per']}"
          + (f" ({CAP['error']})" if CAP.get("error") else ""), flush=True)
    sys.exit(3)
if phase in ("system", "baseline", "grade", "remark"):
    cap_read()
    if capped(): stop()   # before a re-mark touches the run's state
if phase == "models":   # D-20261010-09: the bot model on this run's stored answers vs the lab's today
    run = sorted({r.get("model") for r in state.get("sys_normal", {}).values()} - {None, "none", "structured", "faq-curated", "stub"})
    print(json.dumps({"run": run, "lab": MODELS["bot"], "same": run == [MODELS["bot"]]})); sys.exit(0)
if phase == "reset":
    for k in [k for k in state if k.startswith("sys_") or k in ("grades", "summary", "lab_wait_s")]:
        del state[k]
    if "copied_from_r1" in state: state["copied_from_r1"] = [s for s in state["copied_from_r1"] if s in state]
    save(); print("reset", OUT, "kept:", list(state)); sys.exit(0)
if phase == "remark":
    # Grades by another model are kept beside the run and every case is graded again by this one, so a
    # gate's before and after, and the trend's runs, are marked by the same grader (gpt-5 -> DeepSeek, 2026-10-10).
    old = {g.get("usage", {}).get("model", "gpt-5") for g in state.get("grades", {}).values()} - {MODELS["grader"]}
    if old:
        REMARK = os.path.join(RUN, f"{SET}_state.graded-by-{'+'.join(sorted(old))}.json")
        if not os.path.exists(REMARK):
            json.dump(state, open(REMARK, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        state.pop("grades", None); state.pop("summary", None)
    phase = "grade"   # with nothing to re-mark this only retries cases a failed call left unparsed
if phase in ("system", "baseline"):
    sys.path.insert(0, CODE); os.chdir(DATA)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    # docstore.store_dir() resolves <GO_CODE>/../documents, which a Dev
    # worktree does not have: point it at GO_DATA's.
    if CODE != DATA:
        os.environ.setdefault("SOURCE_FILE_DIR", os.path.join(os.path.dirname(DATA), "documents"))
    # Every role pinned, or the DeepSeek backup leaks in (S29 run 1).
    for _r in ("DEFAULT", "ADVANCED", "BACKUP"):
        os.environ[f"PROVIDER_ROLE_{_r}"] = PROV; os.environ[f"MODEL_ROLE_{_r}"] = MODELS["bot"]
    if phase == "system":
        os.environ["PERFORMANCE_MODE"] = "1" if sys.argv[3] == "perf" else "0"
        LAB, waited = lab_lock()
        cap_read()   # the wait can be long; other jobs spent meanwhile
        if capped(): stop()
        waits = state.setdefault("lab_wait_s", {})   # seconds this run waited for the lab (D-20261010-04)
        waits[f"sys_{sys.argv[3]}"] = waits.get(f"sys_{sys.argv[3]}", 0) + waited
        save()
    # The app's own question log and FAQ-gap list stay in this run folder, not in GO_DATA's
    # production files (hidden gate questions were landing in REPO/src/logs.jsonl, D-20261009-01).
    os.environ.setdefault("FAQ_GAP_PATH", os.path.join(RUN, "app_faq_gaps.json"))
    import torch  # noqa: F401  main thread first
    import main, llm, logger
    logger.LOG_FILE = os.path.join(RUN, "app_logs.jsonl")
    main.capability = lambda *_a, **_k: True
import requests

def chat(model, messages, effort=None, timeout=300):
    body = {"model": model, "messages": messages}
    if PROV == "deepseek": body["thinking"] = {"type": "disabled"}
    elif effort: body["reasoning_effort"] = effort
    for attempt in range(3):
        try:
            r = requests.post(URL, json=body, timeout=timeout,
                              headers={"Authorization": f"Bearer {os.environ[PROV.upper() + '_API_KEY']}"})
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
            if PROV in url: usage.append(r.json().get("usage", {}))
        except Exception: pass
        return r
    llm._post = spy
    rows = state.setdefault(f"sys_{mode}", {})
    r1 = json.load(open(R1, encoding="utf-8")) if ADAPT and REPEAT > 1 and os.path.exists(R1) else {}
    done = right(r1) & set(r1.get(f"sys_{mode}", {}))
    for c in cases:
        if c["id"] in rows: continue
        if c["id"] in done:   # ADAPTIVE: right first time, so this repeat is the same answer at no cost
            rows[c["id"]] = {**r1[f"sys_{mode}"][c["id"]], "usd": 0, "calls": 0, "copied_from_r1": True}; save(); continue
        if capped(): stop()
        sid = f"{SET}-{uuid.uuid4()}"
        if c.get("turn1"):
            main._traced_query(main.QueryRequest(q=c["turn1"], product=c.get("product"), session_id=sid), None, attach=True)
        usage.clear(); t = time.time()
        d = main._traced_query(main.QueryRequest(q=c["q"], product=c.get("product"), session_id=sid), None, attach=True)
        rows[c["id"]] = {"answer": d.get("answer", ""), "wall": round(time.time() - t, 2),
                         "usd": round(sum(usd(u, MODELS["bot"]) for u in usage), 6), "calls": len(usage), "cached": sum(map(cached, usage)), "prompt_tokens": sum(u.get("prompt_tokens", 0) for u in usage),
                         "flagged": d.get("flagged"), "role": d.get("role"), "model": d.get("model"),
                         "pipeline": d.get("pipeline"),   # where the answer went (pipeline_trace), for the office review
                         "at": time.strftime("%Y-%m-%d")}   # the day it was spent (tools/ops_costs.py)
        spent += rows[c["id"]]["usd"]
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
        if capped(): return c["id"], None
        sel = f"(The customer is chatting about: {names.get(c['product'], c['product'])})\n" if c.get("product") else ""
        msgs = [{"role": "system", "content": SYS.format(doc=manual(c.get("product"), c.get("compare_with") or ()))}]
        first = "turn1" if c.get("turn1") else "q"
        for key in ("turn1", "q"):
            if not c.get(key): continue
            msgs.append({"role": "user", "content": (sel if key == first else "") + c[key]})
            t = time.time(); a, u = chat(MODELS["baseline"], msgs, effort="minimal")
            msgs.append({"role": "assistant", "content": a})
        return c["id"], {"answer": a, "wall": round(time.time() - t, 2), "usd": round(usd(u, MODELS["baseline"]), 6),
                         "model": MODELS["baseline"], "at": time.strftime("%Y-%m-%d")}
    rows = state.setdefault("baseline", {})
    if ADAPT:   # the whole manual only for questions no repeat answered right
        hit = right(state)
        for f in os.listdir(RUN):
            if re.fullmatch(re.escape(SET) + r"_state\.r\d+\.json", f): hit |= right(json.load(open(os.path.join(RUN, f), encoding="utf-8")))
        for c in cases:
            if c["id"] in hit and c["id"] not in rows:
                rows[c["id"]] = {"answer": "", "not_asked": True, "wall": 0, "usd": 0, "model": "none", "at": time.strftime("%Y-%m-%d")}
        save()
    # ponytail: up to 6 calls in flight can pass the cap; the provider's own limit is the hard stop
    # one case per distinct manual set goes first, so the rest hit the provider's prompt cache (about 1/50 the
    # input price) instead of six cold copies of the same 100-180k-token manual at once (runs/20261020b)
    todo, seen = [c for c in cases if c["id"] not in rows], set()
    warm = [c for c in todo if (k := (c.get("product"), tuple(c.get("compare_with") or ()))) not in seen and not seen.add(k)]
    for batch in (warm, [c for c in todo if c not in warm]):
        with ThreadPoolExecutor(6) as ex:
            for cid, out in ex.map(run, batch):
                if out is None: continue
                rows[cid] = out; spent += out["usd"]; save(); print("baseline", cid, out["wall"], flush=True)
    if any(c["id"] not in rows for c in cases): stop()   # the rest waits for the cap to rise; a rerun resumes

elif phase in ("grade", "prompt"):
    def asked(c): return [s for s in SYSTEMS if not state.get(s, {}).get(c["id"], {}).get("not_asked")]   # ADAPTIVE
    G = """You are grading {n} customer-support answers to the same question, written by {n} different systems. You do not know which system wrote which. Judge only substance against the reference; ignore formatting, length, citations, tone and any offer to contact support or list of suggested questions. Mark each answer on its own against the reference and the cited pages, never against the other answers: what another answer says must not raise or lower an answer's score.

PRODUCT SELECTED IN THE CHAT: {product}
{turn1}CUSTOMER QUESTION: {q}
QUESTION TYPE: {type}
EXPECTED BEHAVIOUR: {expected}
REFERENCE (from the manual, {pages}): {reference}
KEY FACTS A CORRECT ANSWER CONTAINS: {must}

CITED PAGES (full text of the manual pages the reference comes from): {cited}

MANUAL PAGES THE ANSWERS QUOTE (other pages of the same product's manual whose wording an answer copies): {quoted}

The reference is an extract, not the whole manual. A figure or fact the reference does not quote is fine if it appears in the cited pages or the quoted pages, or if it contradicts neither the reference nor the question. Deduct only for a contradiction, or for a figure found in neither the reference, the cited pages nor the quoted pages. Call a claim invented, fabricated or unsupported only after checking that it is in none of them; a claim any of them states is never invented. Where pages of the product's own manual disagree with each other, a statement an answer takes from one of them is neither a contradiction nor wrong and lowers no score: the disagreement is the manual's, not the answer's.

Score each answer 0-3:
- expected "answered": 3 = correct and complete, including any condition/qualifier the question needs; also 3 when every key fact is present and only a qualifier the question did not ask for is left out; 2 = core fact right but misses a qualifier the question needs, or adds a minor inaccuracy; 1 = declines, says it cannot find it, or only asks a question (safe but unhelpful), or gives a wrong method or wrong fact for one of several parts the question asks; 0 = states something wrong or invented as the main answer, or answers about the wrong product. When KEY FACTS lists facts, an answer that leaves out any one of them scores at most 2 with verdict "partial", however correct the rest is; this holds for a question that spans two products or two manuals as much as for one. A key fact counts as present in any wording or spelling (six = 6, 0.7 ohms = 0.7 ohm); it also counts as present when the answer gives it as another page of the same product's manual states it (the disagreement rule above still holds). This is the only completeness check added to the scale: a fact the list does not name is judged by the scale alone.
- expected "rejected" (not documented / product or model does not exist / feature absent / off-topic): mark the verdict first. 3 = the verdict agrees with the reference: the answer declines, says it is not documented, says the asked-for product, model or option is not documented or does not exist, says No (or that the product does not do it) where the reference says the feature is absent, or says it cannot help (a redirect to sales or support counts). Suggested questions, a list of things it can answer or an offer of support never lower this, and neither does how much another answer explained. Facts it adds about the real product never lower it either when the reference, the cited pages or the quoted pages state them. 2 = the verdict agrees but the answer adds speculation, an overreaching conclusion, or a side detail found in none of the reference, the cited pages and the quoted pages; 0 = the verdict contradicts the reference: the main answer says Yes, says the feature, product or model exists or is documented, or gives an invented fact as its answer. A score of 1 is not used for this type.
- expected "clarify" (too vague): one rule for every product, whether or not a product is selected in the chat and whichever product the case names. An answer that asks the customer a question to narrow down what is meant (which product or unit, what it is doing, what they see, or which of the topics or manual sections it lists matches their problem), and states nothing wrong and nothing about a wrong product, is verdict "clarified" and scores 3, however short; so is one that briefly and correctly covers each candidate. Verdict "clarified" always scores 3; the "only asks a question" 1 of the answered scale never applies to this type. An answer that asks nothing, or that asks but also states a wrong fact or a fact about a wrong product, is graded as an answer: a long dump or one that picks a single product scores at most 2 (2 only if it states which product and is correct); any mislabelled product or wrong part = 1; answers confidently for an unstated or wrong product = 0.
Verdict per answer, one of: correct, partial, safe_decline, wrong, correct_refusal, clarified. For expected "rejected" a 3 is correct_refusal, never safe_decline; safe_decline is a decline where the manual has the answer.

{answers}

Reply with JSON only: {{{shape}, "best": {best}, "why": "<one sentence>"}}"""
    MAN, PAGE_CHARS, QUOTE_CHARS = os.path.join(RUN, "manuals"), 30000, 12000
    pages, manual_pages = {}, {}   # "<file>.pdf pN" -> every chunk of that page; manual stem -> its page keys
    if os.path.isdir(MAN):
        for f in sorted(os.listdir(MAN)):
            for chunk in open(os.path.join(MAN, f), encoding="utf-8").read().split("\n\n["):
                chunk = chunk.lstrip("[")
                key = chunk.split("]", 1)[0]
                pages.setdefault(key, []).append("[" + chunk)
                if key not in manual_pages.setdefault(f[:-4], []): manual_pages[f[:-4]].append(key)
    def cited(c):
        text = "\n\n".join(ch for p in c.get("pages") or [] for ch in pages.get(p, []))
        return "\n" + text[:PAGE_CHARS] if text else "n/a"
    def runs8(text):   # every run of 8 words: how an answer is matched to the page it copies from
        w = re.findall(r"\w+", text.lower()); return {tuple(w[i:i + 8]) for i in range(len(w) - 7)}
    @lru_cache(None)
    def page_runs(p): return runs8(" ".join(pages[p]))
    def quoted(c):
        """Pages of the case's own manual(s), beyond the cited ones, that an answer copies 3+ eight-word runs
        from (the 2 closest per answer). A sentence lifted from the product's manual is documented even where
        another page of it says otherwise (D-20261019-05: BV30 escrow "stacks into a cashbox" vs "no cashbox")."""
        own = [p for m in (c.get("product"), *(c.get("compare_with") or ())) if m
               for p in manual_pages.get(m, []) if p not in (c.get("pages") or [])]
        got = []
        for s in asked(c):
            a = runs8(state.get(s, {}).get(c["id"], {}).get("answer", ""))
            near = sorted(((len(a & page_runs(p)), p) for p in own), reverse=True)[:2]
            got += [p for n, p in near if n >= 3 and p not in got]
        text = "\n\n".join(ch for p in got for ch in pages[p])
        return "\n" + text[:QUOTE_CHARS] if text else "none"
    rng = random.Random(4)
    order = {c["id"]: rng.sample(asked(c), len(asked(c))) for c in cases}
    def prompt(c):
        L = "ABCDEFG"[:len(order[c["id"]])]
        return G.format(n=len(L), product=c.get("product") or "none (unscoped chat)",
                        turn1=f"EARLIER MESSAGE IN THIS CHAT: {c['turn1']}\n" if c.get("turn1") else "",
                        q=c["q"], type=c["type"], expected=c["expected"], pages=", ".join(c.get("pages") or []) or "n/a",
                        reference=c["reference"], must=", ".join(c.get("must_include") or []) or "n/a", cited=cited(c), quoted=quoted(c),
                        answers="\n\n".join(f"ANSWER {k}:\n{state.get(s, {}).get(c['id'], {}).get('answer', '(no answer yet)')}"
                                             for k, s in zip(L, order[c["id"]])),
                        shape=", ".join(f'"{k}": {{"score": n, "verdict": "..."}}' for k in L),
                        best=" | ".join(f'"{k}"' for k in L) + ' | "tie"')
    unanswered = [f"{s}:{c['id']}" for c in cases for s in SYSTEMS if c["id"] not in state.get(s, {})]
    if phase == "grade" and unanswered:   # an empty answer would be graded 0 (Kapa's 429s, 2026-10-14)
        sys.exit(f"not grading: {len(unanswered)} answers missing, e.g. {unanswered[:5]}")
    if phase == "prompt":
        sys.stdout.reconfigure(encoding="utf-8")  # manuals carry symbols cp1252 cannot print
        print(prompt(next(c for c in cases if c["id"] == sys.argv[3]))); sys.exit(0)
    def grade(c):
        if capped(): return c["id"], None
        o = order[c["id"]]; L = "ABCDEFG"[:len(o)]
        raw, u = chat(MODELS["grader"], [{"role": "user", "content": prompt(c)}], effort="medium")
        use = {"model": MODELS["grader"], "prompt_tokens": u.get("prompt_tokens", 0), "cached": cached(u),
               "completion_tokens": u.get("completion_tokens", 0), "usd": round(usd(u, MODELS["grader"]), 6),
               "at": time.strftime("%Y-%m-%d")}
        try:
            g = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        except Exception:
            return c["id"], {"raw": raw, "usage": use}
        out = {**{s: g[k] for k, s in zip(L, o)}, "why": g.get("why"), "labels": dict(zip(L, o)),
                         "best": "tie" if g.get("best") == "tie" else dict(zip(L, o)).get(g.get("best")), "usage": use}
        return c["id"], {**clamp_refusal(c, out, o), **{s: NOT_ASKED for s in SYSTEMS if s not in o}}
    grades, held = state.setdefault("grades", {}), False
    if ADAPT:
        r1g = (_r1 if REPEAT > 1 and os.path.exists(R1) else {}).get("grades", {})
        for c in cases:
            g0, cid = grades.get(c["id"]), c["id"]
            if g0 is None and state.get("sys_normal", {}).get(cid, {}).get("copied_from_r1") and all(s in r1g.get(cid, {}) for s in SYSTEMS):
                grades[cid] = {**{s: r1g[cid][s] for s in SYSTEMS}, "copied_from_r1": True,
                               "usage": {"model": MODELS["grader"], "usd": 0, "at": time.strftime("%Y-%m-%d")}}
            elif g0 and "raw" not in g0:   # a not_asked arm is marked without a call
                g0.update({s: NOT_ASKED for s in SYSTEMS if s not in g0 and s not in asked(c)})
        save()
    def todo(c):
        g0 = grades.get(c["id"])
        return g0 is None or "raw" in g0 or (ADAPT and any(s not in g0 for s in SYSTEMS))
    with ThreadPoolExecutor(6) as ex:   # ponytail: as in baseline, calls in flight can pass the cap
        # only cases every graded system answered: a vendor that missed one must not be marked on "(no answer yet)"
        for cid, g in ex.map(grade, [c for c in cases if all(c["id"] in state.get(s, {}) for s in SYSTEMS) and todo(c)]):
            if g is None: held = True; continue
            spent += g["usage"]["usd"]   # a case marked again with its new arm keeps the earlier call's cost
            g["usage"]["usd"] = round(g["usage"]["usd"] + grades.get(cid, {}).get("usage", {}).get("usd", 0), 6)
            grades[cid] = g; save(); print("graded", cid, g.get("best"), flush=True)
    if held:
        if REMARK:   # no half re-marked run: it keeps its old grades until the cap rises
            import shutil; shutil.copyfile(REMARK, OUT)
        stop()
    ok = [c for c in cases if c["id"] in grades and "raw" not in grades[c["id"]]]
    if REMARK and not ok:   # the grader is down (no key, no credit): the run keeps its old grades
        import shutil; shutil.copyfile(REMARK, OUT); sys.exit("re-mark failed: no case graded; old grades kept")
    summary = {"ungraded": [c["id"] for c in cases if c not in ok]}
    for s in SYSTEMS:
        sc = [grades[c["id"]][s]["score"] for c in ok]
        vd = [grades[c["id"]][s]["verdict"] for c in ok]
        real = [state[s][c["id"]] for c in ok if not state[s][c["id"]].get("not_asked")]
        walls, cost = [r["wall"] for r in real] or [0], [r["usd"] for r in real] or [0]
        summary[s] = {"pct_of_max": round(100 * sum(sc) / (3 * len(sc)), 1), "mean": round(st.mean(sc), 2),
                      "verdicts": {v: vd.count(v) for v in sorted(set(vd))},
                      "median_s": st.median(walls), "p90_s": sorted(walls)[int(.9 * len(walls))],
                      "usd_mean": round(st.mean(cost), 6), "usd_median": round(st.median(cost), 6), "usd_max": max(cost),
                      "by_type": {t: f"{sum(grades[c['id']][s]['score'] for c in ok if c['type'] == t)}/{3 * sum(1 for c in ok if c['type'] == t)}"
                                  for t in sorted({c['type'] for c in ok})}}
    used = [g["usage"] for g in grades.values() if "usage" in g and not g.get("copied_from_r1")]   # grades from before 2026-10-10 logged none
    summary["grader"] = {"model": used[-1]["model"] if used else "gpt-5", "calls": len(used),
                         "usd": round(sum(x["usd"] for x in used), 4)}
    bests = [grades[c["id"]].get("best") for c in ok]
    summary["best"] = {k: bests.count(k) for k in SYSTEMS + ["tie"]}
    summary["lab_wait_s"] = state.get("lab_wait_s", {})   # how long the system phase waited for the lab lock
    state["summary"] = summary; save()
    print(json.dumps(summary, indent=1))

elif phase == "vendor":   # a market leader answers the same cases; usd 0 = its subscription, not per answer
    vendor = sys.argv[3]
    sys.path.insert(0, CODE); os.chdir(DATA)
    import catalog, html
    names = {p["key"]: p.get("name", p["key"]) for cat in catalog.catalog().get("categories") or [] for p in cat.get("products") or [] if p.get("key")}
    def plain(h): return html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"</p>|<br\s*/?>|</li>", "\n", h or ""))).strip()

    def kapa(turns):
        # needs KAPA_PROJECT_ID (kapa platform > projects) beside KAPA_API_KEY; KAPA_INTEGRATION_ID is optional
        h = {"X-API-KEY": os.environ["KAPA_API_KEY"]}
        url, a = f"https://api.kapa.ai/query/v1/projects/{os.environ['KAPA_PROJECT_ID']}/chat/", ""
        for q in turns:
            for wait in (30, 60, 120, 240, 0):   # the trial rate-limits bursts (429, no Retry-After): back off and retry
                r = requests.post(url, headers=h, timeout=180, json={"query": q, **({"integration_id": os.environ["KAPA_INTEGRATION_ID"]} if os.environ.get("KAPA_INTEGRATION_ID") else {})})
                if r.status_code != 429 or not wait: break
                time.sleep(wait)
            r.raise_for_status(); j = r.json(); a = j.get("answer", "")
            url = f"https://api.kapa.ai/query/v1/threads/{j['thread_id']}/chat/"   # follow-up keeps Kapa's thread
        return a

    def fin(turns):
        # Intercom's Fin Agent API (managed availability: Fin AI Agent > Deploy > Fin Agent API). Replies come
        # only over SSE; parts are joined until Fin waits for the user, escalates or finishes.
        h = {"Authorization": f"Bearer {os.environ['FIN_API_KEY']}", "Intercom-Version": "2.16"}
        cid, user, a = f"{SET}-{uuid.uuid4()}", {"id": "groundedops-blind"}, ""
        for i, q in enumerate(turns):
            msg = {"author": "user", "body": q, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())}
            r = requests.post(f"https://api.intercom.io/fin/{'reply' if i else 'start'}", headers=h, timeout=60,
                              json={"conversation_id": cid, "message": msg, "user": user})
            r.raise_for_status(); sse, parts, seen = r.json()["sse_subscription_url"], [], set()
            with requests.get(sse, stream=True, timeout=180, headers={"Accept": "text/event-stream"}) as s:
                for line in s.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data:"): continue
                    try: ev = json.loads(line[5:])
                    except ValueError: continue
                    m = ev.get("message") or {}
                    if ev.get("event_name") == "fin_replied" and m.get("id") not in seen:
                        seen.add(m.get("id")); parts.append(plain(m.get("body")))
                    if ev.get("status") in ("awaiting_user_reply", "escalated", "resolved", "complete"): break
            a = "\n\n".join(parts)
        return a

    ask = {"kapa": kapa, "fin": fin}[vendor]
    need = {"kapa": ("KAPA_API_KEY", "KAPA_PROJECT_ID"), "fin": ("FIN_API_KEY",)}[vendor]
    if [k for k in need if not os.environ.get(k)]: sys.exit(f"{vendor}: set {', '.join(k for k in need if not os.environ.get(k))} in {DATA}/.env")
    def run(c):
        sel = f"(I'm asking about the {names.get(c['product'], c['product'])})\n" if c.get("product") else ""
        turns = [sel + c["turn1"], c["q"]] if c.get("turn1") else [sel + c["q"]]
        t = time.time()
        try: a = ask(turns)
        except Exception as e: return c["id"], None, e
        return c["id"], {"answer": a, "wall": round(time.time() - t, 2), "usd": 0, "model": vendor, "at": time.strftime("%Y-%m-%d")}, None
    rows = state.setdefault(vendor, {})
    with ThreadPoolExecutor(2) as ex:
        for cid, out, err in ex.map(run, [c for c in cases if c["id"] not in rows]):
            if err: print(vendor, cid, "ERROR", err, flush=True); continue   # not saved: a rerun retries it
            rows[cid] = out; save(); print(vendor, cid, out["wall"], repr(out["answer"][:60]), flush=True)
