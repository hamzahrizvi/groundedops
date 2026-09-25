#!/usr/bin/env python3
"""Drive the scripted conversations in tests/scenarios/ through a RUNNING
backend and score what the customer actually saw.

    cd src && ../.venv/Scripts/python.exe tests/run_live.py
    ... --url http://127.0.0.1:8001          a backend other than :8000
    ... --only 11 15 22                       scenario id prefixes
    ... --out /c/tmp/stress/live.md           transcript (markdown) path
    ... --json /c/tmp/stress/live.json        raw responses

Unlike run_scenarios.py this DOES call the model: every turn is POSTed to
/query with a fresh session_id per scenario, so the backend's own memory
provides the history. Only scenarios that carry a `live` block on their
turns are run (11 onward); 01-10 have routing labels only.

Scoring is deliberately coarse -- a support engineer's shape check, not a
grader model:

  observed outcome  deflect  role == "sales" (the operator's commercial route)
                    manual   a document download link was offered
                    handoff  the turn was routed to a person (role "handoff")
                    clarify  needs_clarification with options/candidates
                    refuse   role "rejected", or a refusal with no sources
                    answer   anything else with sources

  expect            must equal the observed outcome, unless "any"
  cite              (answer only) one substring must match a cited source
  mention           one of the words must appear in the answer
  avoid             none of the words may appear in the answer

The transcript is written VERBATIM -- question, answer, sources, buttons,
latency -- so a reader who was not in the session can see exactly what the
bot said.
"""
import argparse
import datetime as dt
import json
import pathlib
import sys
import time
import uuid

import requests

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
SCENARIO_DIR = HERE / "scenarios"


def observed_outcome(r):
    role = r.get("role") or ""
    answer = r.get("answer") or ""
    if role == "sales" or r.get("kind") == "deflected":
        return "deflect"
    if role == "document" or any((s or {}).get("download_url") and not (s or {}).get("pages")
                                 for s in r.get("sources") or []):
        return "manual"
    if role == "handoff":
        return "handoff"
    if r.get("needs_clarification") or r.get("faq_candidates"):
        return "clarify"
    # The friendly refusal cites the pages it looked at, so "no sources" is
    # not the test; the switchboard's own verdict is.
    if role == "rejected" or (r.get("offer_support") and (
            not r.get("sources") or r.get("answerability") == "unanswerable")):
        return "refuse"
    if not r.get("sources") and role in ("none", "", None):
        return "refuse"
    return "answer"


def score_turn(turn, r):
    live = turn.get("live") or {}
    ans = (r.get("answer") or "")
    low = ans.lower()
    got = observed_outcome(r)
    fails = []
    want = live.get("expect", "any")
    if want != "any" and want != got:
        fails.append(f"outcome: expected {want}, got {got}")
    if got == "answer" and live.get("cite"):
        srcs = " | ".join(s.get("source", "") for s in r.get("sources") or [])
        if not any(c.lower() in srcs.lower() for c in live["cite"]):
            fails.append(f"cite: none of {live['cite']} in [{srcs}]")
    if live.get("mention") and not any(m.lower() in low for m in live["mention"]):
        fails.append(f"mention: none of {live['mention']}")
    hit = [a for a in live.get("avoid", []) if a.lower() in low]
    if hit:
        fails.append(f"avoid: found {hit}")
    return got, fails


def buttons(r):
    out = []
    for o in r.get("clarification_options") or []:
        out.append(f"[{o.get('label') if isinstance(o, dict) else o}]")
    for c in r.get("faq_candidates") or []:
        out.append(f"[FAQ: {c.get('question') if isinstance(c, dict) else c}]")
    for s in r.get("suggested_replies") or []:
        out.append(f"[reply: {s.get('text') if isinstance(s, dict) else s}]")
    if r.get("offer_support"):
        out.append("[Contact support]")
    return out


def run_scenario(sc, url, verbose):
    sid = f"live-{sc['id']}-{uuid.uuid4().hex[:8]}"
    rows = []
    for turn in sc["turns"]:
        body = {"q": turn["q"], "session_id": sid}
        if sc.get("scoped") and sc.get("product_key"):
            body["product"] = sc["product_key"]
        t0 = time.perf_counter()
        try:
            resp = requests.post(f"{url}/query", json=body, timeout=240)
            r = resp.json()
            if resp.status_code != 200:
                r = {"answer": f"HTTP {resp.status_code}: {json.dumps(r)[:300]}",
                     "role": "error"}
        except Exception as e:                      # noqa: BLE001
            r = {"answer": f"REQUEST FAILED: {e}", "role": "error"}
        wall = time.perf_counter() - t0
        got, fails = score_turn(turn, r)
        rows.append({"turn": turn, "response": r, "wall": wall,
                     "got": got, "fails": fails})
        if verbose:
            mark = "ok  " if not fails else "FAIL"
            print(f"  {mark} {got:8s} {wall:5.1f}s  {turn['q'][:70]}")
            for f in fails:
                print(f"         {f}")
    return sid, rows


def write_markdown(results, path, url, started):
    L = []
    L.append(f"# Live conversation transcripts — {started:%Y-%m-%d %H:%M}")
    L.append("")
    L.append(f"Backend: `{url}`. Every turn POSTed to `/query` with one "
             "session_id per scenario. Answers are verbatim. `expected` was "
             "written before the run; `got` is the runner's shape check.")
    L.append("")
    tot_p = tot_f = 0
    for sc, sid, rows in results:
        p = sum(1 for r in rows if not r["fails"])
        f = len(rows) - p
        tot_p += p
        tot_f += f
        L.append(f"## {sc['id']} — {p}/{len(rows)} turns as expected")
        L.append("")
        L.append(f"*{sc['persona']}* — product scope: "
                 f"`{sc.get('product_key') if sc.get('scoped') else 'none (unscoped)'}`, "
                 f"session `{sid}`")
        L.append("")
        for i, row in enumerate(rows, 1):
            t, r = row["turn"], row["response"]
            live = t.get("live", {})
            L.append(f"### Turn {i}: {t['q']}")
            L.append("")
            L.append(f"- expected: **{live.get('expect', 'any')}** — got: "
                     f"**{row['got']}** — {'PASS' if not row['fails'] else 'FAIL: ' + '; '.join(row['fails'])}")
            L.append(f"- note: {t['note']}")
            timing = r.get("timing") or {}
            L.append(f"- latency: {row['wall']:.1f}s wall "
                     f"(server {r.get('response_time', timing.get('total_time', '?'))}s; "
                     f"llm {timing.get('llm_time', '?')}s) — role `{r.get('role')}`"
                     f" reason `{r.get('reason')}` answerability `{r.get('answerability')}`"
                     f" exit `{(r.get('pipeline') or {}).get('exit')}` — "
                     f"{r.get('provider')}/{r.get('model')} grounding {r.get('grounding_score')}"
                     f"{' FLAGGED' if r.get('flagged') else ''}")
            srcs = r.get("sources") or []
            if srcs:
                L.append("- sources: " + "; ".join(
                    f"{s.get('source')} p.{','.join(str(x) for x in s.get('pages', []))}"
                    for s in srcs))
            else:
                L.append("- sources: (none)")
            b = buttons(r)
            L.append("- buttons: " + (" ".join(b) if b else "(none)"))
            if r.get("resolved_query"):
                L.append(f"- resolved query: `{r['resolved_query']}`")
            L.append("")
            L.append("> " + (r.get("answer") or "").replace("\n", "\n> "))
            L.append("")
    L.insert(3, f"**Total: {tot_p} of {tot_p + tot_f} turns as expected "
                f"({100.0 * tot_p / max(1, tot_p + tot_f):.1f}%).**")
    L.insert(4, "")
    path.write_text("\n".join(L), encoding="utf-8")
    return tot_p, tot_f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    started = dt.datetime.now()
    files = sorted(SCENARIO_DIR.glob("*.json"))
    results = []
    for f in files:
        sc = json.loads(f.read_text(encoding="utf-8"))
        if not any("live" in t for t in sc["turns"]):
            continue
        if a.only and not any(sc["id"].startswith(o) for o in a.only):
            continue
        print(f"== {sc['id']}")
        sid, rows = run_scenario(sc, a.url, not a.quiet)
        results.append((sc, sid, rows))

    out = pathlib.Path(a.out) if a.out else HERE.parent.parent / "scratchpad" / f"live_{started:%Y%m%d_%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    p, f = write_markdown(results, out, a.url, started)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(
            [{"id": sc["id"], "session": sid, "rows": rows} for sc, sid, rows in results],
            indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n{p} of {p + f} turns as expected ({100.0 * p / max(1, p + f):.1f}%)  -> {out}")
    return 0 if f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
