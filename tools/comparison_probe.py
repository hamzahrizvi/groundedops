"""S21 measurement (PENDING 10.4): are comparison questions still refused after 8.6?

    .venv/Scripts/python tools/comparison_probe.py [--repeats 3] [--out eval_runs/x.json]

Replays the comparison turns of the suites in-process through the production
query() (same recipe as tools/escalation_replay.py), one session per
conversation so follow-ups see their history. Counts refused / answered /
asked-back per turn. Pass --repeats N to see sampling wobble.
"""
import argparse, json, os, sys, time, uuid
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
os.chdir(SRC); sys.path.insert(0, SRC)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
from dotenv import load_dotenv; load_dotenv(".env")

# (label, product scope, [turns]) -- every comparison turn in the suites.
CONVOS = [
    ("18 NV9S vs NV9USB+", None, [
        "what is the difference between the NV9 Spectral and the NV9USB+",
        "which one validates notes faster",
        "do they both use the same SSP interface",
    ]),
    ("14 product switch", None, [
        "what bezel options are there for the NV9 Spectral",
        "ok now the NV9USB+, does it use the same bezel",
        "and how big is the cashbox on it",
        "which of the two takes more notes",
    ]),
    ("07 Mini vs full", "mycheckr_mini", [
        "how is the MyCheckr Mini different from the full size one",
    ]),
    ("ext MyCheckr vs Mini", None, [
        "what is the difference between MyCheckr and MyCheckr Mini",
    ]),
    ("tuned BV30 vs NV200S", None, [
        "Does the BV30 run on the same supply voltage as the NV200 Spectral?",
    ]),
    ("8.15 notes faster (single turn)", None, [
        "which validates notes faster, the NV9 Spectral or the NV9USB+",
    ]),
]
COMPARISON_TURNS = {  # the turns that ARE comparisons (the others are set-up)
    ("18 NV9S vs NV9USB+", 1), ("18 NV9S vs NV9USB+", 2), ("18 NV9S vs NV9USB+", 3),
    ("14 product switch", 4), ("07 Mini vs full", 1), ("ext MyCheckr vs Mini", 1),
    ("tuned BV30 vs NV200S", 1), ("8.15 notes faster (single turn)", 1),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--out", default=os.path.join(ROOT, "eval_runs", "s21_comparisons.json"))
    a = ap.parse_args()

    import main as m, faq_store
    m.log_interaction = lambda *a, **k: None
    faq_store.record_gap = lambda *a, **k: None
    m.convo_store.save_turn = lambda *a, **k: None
    m._warmup_stack()

    rows = []
    for rep in range(1, a.repeats + 1):
        for label, product, turns in CONVOS:
            sid = f"s21-{uuid.uuid4().hex[:8]}"
            for i, q in enumerate(turns, 1):
                t0 = time.time()
                try:
                    res = m.query(m.QueryRequest(q=q, product=product, session_id=sid))
                except Exception as e:
                    res = {"answer": f"ERROR {e}", "role": "error", "sources": []}
                ans = res.get("answer") or ""
                role = res.get("role")
                if role in ("clarify", "disambiguate") or res.get("options") or res.get("candidates"):
                    verdict = "asked back"
                elif role == "error":
                    verdict = "error"
                elif m.is_refusal(ans):
                    verdict = "REFUSED"
                else:
                    verdict = "answered"
                srcs = sorted({(s.get("source") or s.get("document") or "?").split(" User")[0]
                               for s in (res.get("sources") or []) if isinstance(s, dict)})
                is_cmp = (label, i) in COMPARISON_TURNS
                rows.append({"rep": rep, "convo": label, "turn": i, "comparison": is_cmp, "q": q,
                             "verdict": verdict, "role": role, "secs": round(time.time() - t0, 1),
                             "sources": srcs, "answer": ans})
                flag = "*" if is_cmp else " "
                print(f"[{rep}] {flag} {label} T{i}: {verdict:<10} role={role} {rows[-1]['secs']}s "
                      f"src={srcs}\n      {q}\n      -> {ans[:160].replace(chr(10), ' ')}", flush=True)

    cmp_rows = [r for r in rows if r["comparison"]]
    n = len(cmp_rows)
    ref = sum(r["verdict"] == "REFUSED" for r in cmp_rows)
    ask = sum(r["verdict"] == "asked back" for r in cmp_rows)
    ansd = sum(r["verdict"] == "answered" for r in cmp_rows)
    print(f"\nCOMPARISON TURNS: {n}  answered {ansd}  refused {ref}  asked back {ask}")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump({"summary": {"n": n, "answered": ansd, "refused": ref, "asked_back": ask},
               "rows": rows}, open(a.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
