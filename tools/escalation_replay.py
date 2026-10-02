"""9.13: does a stronger model rescue answers the verifier flags?

The plan's population -- 79 flagged turns in logs.jsonl -- went with the
2026-10-02 log loss (verifier timings were only logged from 09-24 23:51; 2
such rows survive). So the population is rebuilt by replay: every distinct
question flagged in the surviving log (no product scope; the log never
stored it) plus every single-turn "answered" case of the main eval suites
(with their product), each run once through the PRODUCTION query() in this
process. The turns that come back flagged -- after production's own backup
escalation and grounding retries -- are the population.

For each, the exact answer prompt is regenerated on --model (default
deepseek-v4-pro) with thinking on, through llm.generate (the backup
escalation's call), and judged by the production verifier chain on the same
top_chunks: NLI, then the lexical rescue, then _llm_verified; a table answer
goes straight to _llm_verified, as in query(). Reported: n passed of n
flagged, per-turn latency and tokens. No pipeline change.

Writes nothing production reads: log_interaction, record_gap and
conversation saves are stubbed. Needs the backend STOPPED (one Chroma
writer) and src/.env's DeepSeek key.

  python tools/escalation_replay.py                # -> eval_runs/<stamp>/escalation_9.13.json
  python tools/escalation_replay.py --limit 5      # smoke run
"""
import argparse, json, os, re, sys, time, uuid
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SRC = os.path.join(ROOT, "src")
os.chdir(SRC)
sys.path.insert(0, SRC)
from dotenv import load_dotenv; load_dotenv(".env")

SUITES = ["eval_cases.json", "eval_cases_blind.json", "eval_cases_blind2.json",
          "eval_cases_retrieval.json", "eval_cases_tables.json"]


def population():
    """(question, product, origin), deduped on the lower-cased question."""
    seen, out = set(), []

    def add(q, product, origin):
        k = q.strip().lower()
        if q.strip() and k not in seen:
            seen.add(k)
            out.append((q.strip(), product, origin))

    with open("logs.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line) if line.strip() else {}
            if str(r.get("flagged")) == "True" and r.get("query"):
                add(r["query"], None, "log")
    for name in SUITES:
        with open(name, encoding="utf-8") as f:
            d = json.load(f)
        for c in (d if isinstance(d, list) else d.get("cases", [])):
            if (c.get("outcome") == "answered" and c.get("new_session", True)
                    and isinstance(c.get("q"), str)):
                add(c["q"], c.get("product"), name)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", default="deepseek-v4-pro")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    import main as m, llm, faq_store
    m.log_interaction = lambda *a, **k: None
    faq_store.record_gap = lambda *a, **k: None
    m.convo_store.save_turn = lambda *a, **k: None
    m._warmup_stack()

    # Capture what query() built: the answer prompt and the chunks it verified.
    cap = {}
    _bap, _cg, _lv = m.build_answer_prompt, m.check_grounding, m._llm_verified

    def bap(hist, context, question):
        p = _bap(hist, context, question)
        cap.setdefault("prompt", p)          # the first one = the backup's `prompt`
        return p

    def cg(answer, chunks, **kw):
        cap["chunks"] = chunks
        return _cg(answer, chunks, **kw)

    def lv(answer, chunks, *a, **kw):
        cap["chunks"] = chunks
        return _lv(answer, chunks, *a, **kw)

    m.build_answer_prompt, m.check_grounding, m._llm_verified = bap, cg, lv

    def verify(answer, chunks, question):
        """query()'s chain, without the side branches. -> (ok, via, nli)."""
        if m.is_refusal(answer):
            return False, "refused", None
        if re.search(r"^\s*\|", answer, re.M):
            return _lv(answer, chunks, None, question), "llm(table)", None
        ok, score = _cg(answer, chunks, threshold=m.GROUNDING_THRESHOLD)
        if ok:
            return True, "nli", score
        if score is not None and m._lexically_supported(answer, chunks):
            return True, "lexical", score
        if score is not None and _lv(answer, chunks, None, question):
            return True, "llm", score
        return False, "none", score

    pop = population()
    if args.limit:
        pop = pop[:args.limit]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = args.out or os.path.join(ROOT, "eval_runs", stamp, "escalation_9.13.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print(f"{len(pop)} questions replayed; regenerating flagged ones on {args.model}\n")

    rows = []
    for i, (q, product, origin) in enumerate(pop, 1):
        cap.clear()
        t0 = time.time()
        try:
            res = m.query(m.QueryRequest(q=q, product=product,
                                         session_id=f"replay-913-{uuid.uuid4().hex[:8]}"))
        except Exception as e:
            print(f"[{i}] ERROR {q[:60]}: {e}")
            continue
        flagged = bool(res.get("flagged"))
        row = {"q": q, "product": product, "origin": origin, "flagged": flagged,
               "prod_secs": round(time.time() - t0, 2), "prod_answer": res.get("answer")}
        if flagged and cap.get("prompt") and cap.get("chunks"):
            t1 = time.time()
            with llm.judging():              # thinking on for this DeepSeek call
                g = llm.generate("deepseek", cap["prompt"], args.model) or {}
            gen_secs = time.time() - t1
            text = m.normalize_markdown_tables(m._strip_meta(m._strip_preamble(
                (g.get("text") or "").strip())))
            t2 = time.time()
            ok, via, nli = verify(text, cap["chunks"], q) if text else (False, "no_text", None)
            row.update(escalated_answer=text, passed=ok, via=via, nli=nli,
                       gen_secs=round(gen_secs, 2), verify_secs=round(time.time() - t2, 2),
                       tokens=int(g.get("tokens") or 0))
            print(f"[{i}] FLAGGED -> {'PASS' if ok else 'fail'} ({via}, {gen_secs:.1f}s, "
                  f"{row['tokens']} tok)  {q[:60]}")
        else:
            print(f"[{i}] {'flagged, nothing captured' if flagged else 'ok'}  {q[:60]}")
        rows.append(row)

    esc = [r for r in rows if "passed" in r]
    passed = [r for r in esc if r["passed"]]

    def pct(xs, p):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None

    summary = {
        "model": args.model, "thinking": True, "replayed": len(rows),
        "flagged": sum(r["flagged"] for r in rows), "escalated": len(esc),
        "passed": len(passed),
        "passed_via": {v: sum(r["via"] == v for r in passed) for v in {r["via"] for r in passed}},
        "gen_secs_p50": pct([r["gen_secs"] for r in esc], .5),
        "gen_secs_p90": pct([r["gen_secs"] for r in esc], .9),
        "tokens_total": sum(r["tokens"] for r in esc),
        "tokens_p50": pct([r["tokens"] for r in esc], .5),
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=1, default=str)
    print("\n" + json.dumps(summary, indent=1))
    print(f"\nwritten {out}")


if __name__ == "__main__":
    main()
