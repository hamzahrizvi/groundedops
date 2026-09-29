#!/usr/bin/env python3
"""Drive the scripted conversations in tests/scenarios/ through a RUNNING
backend and score what the customer actually saw.

    cd src && ../.venv/Scripts/python.exe tests/run_live.py
    ... --url http://127.0.0.1:8001          a backend other than :8000
    ... --only 11 15 22                       scenario id prefixes
    ... --out /c/tmp/stress/live.md           transcript (markdown) path
    ... --json /c/tmp/stress/live.json        raw responses
    ... --path widget                         through /widget/ask instead
    ... --path both                           both, plus per-turn agreement

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

  expect            must equal the observed outcome, unless "any"; a list
                    accepts any of its outcomes
  answer_mention    (answer only) one of the words must appear -- for a
                    turn where a clarify is also right, so the check does
                    not fail the clarify for lacking the answer's words
  cite              (answer only) one substring must match a cited source
  mention           one of the words must appear in the answer
  avoid             none of the words may appear in the answer

--path widget (M12) sends the same turns through /widget/ask, the only
surface a customer reaches, as a member (a token minted locally with the
backend's WIDGET_TOKEN_SECRET, one uid per scenario so the 25-credit daily
member allowance is never the limit). That endpoint hand-builds its own
response dict, and every key it forgets is invisible to /query-only runs:
8.1 was once recorded as shipped without reaching a customer. So each
widget turn is also checked for WIDGET_KEYS, and --path both runs each
scenario on both paths against the same backend and reports per-turn
outcome agreement. The widget dict carries no resolved_query, so the M10
needs_history check runs on the /query path only. Generation is sampled,
so a lone answer/refuse disagreement can be noise; a disagreement next to
a missing key (handoff or deflect read as "answer" because role is gone)
is not.

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

import re

import requests

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
SCENARIO_DIR = HERE / "scenarios"

import text_utils as T  # noqa: E402

# main.py:1894's _add_selected_product_context appends this to a resolved
# query when a product is scoped; it is retrieval scope, not something
# condensation pulled from the conversation, so is_followup_turn's own
# "added word came from history" check must not see it.
_PRODUCT_SUFFIX = re.compile(r"\s*\([^()]+\)\s*$")


def _strip_product_suffix(resolved_query: str) -> str:
    return _PRODUCT_SUFFIX.sub("", resolved_query or "").strip()


# What /widget/ask must carry for the customer to see what /query decided
# (upgrade plan M12; 8.1 adds role/reason/request_id/service_degraded, 8.12
# more_context, K03 product_options). Presence is checked, not truthiness:
# an empty list is a decision, a missing key is a dropped one.
WIDGET_KEYS = ("role", "reason", "request_id", "service_degraded",
               "more_context", "product_options", "suggested_replies",
               "sources")

_ENV_FILE = HERE.parent / ".env"


def _token_secret():
    """WIDGET_TOKEN_SECRET from the environment, else from src/.env -- the
    file the backend itself loads. Never printed."""
    import os
    if os.getenv("WIDGET_TOKEN_SECRET"):
        return os.environ["WIDGET_TOKEN_SECRET"]
    try:
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, sep, val = line.strip().partition("=")
            if sep and key.strip() == "WIDGET_TOKEN_SECRET":
                return val.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def member_token(uid):
    """A member token signed exactly as the website signs one (quota.py is
    the one implementation; its secret is read at import, hence the env)."""
    import os
    secret = _token_secret()
    if not secret:
        raise SystemExit("--path widget needs WIDGET_TOKEN_SECRET (env or "
                         "src/.env) to mint a member token")
    os.environ["WIDGET_TOKEN_SECRET"] = secret
    import quota
    quota.WIDGET_TOKEN_SECRET = secret
    return quota.issue_token(uid, "member", 3600)


def missing_widget_keys(r):
    return [k for k in WIDGET_KEYS if k not in r]


def _cites_pages(s):
    """/query sources carry `pages`; the widget's public sources carry only
    the pre-formatted `page_label` main._build_sources makes from them
    ("page 12", "pages 3, 4"), so either one means a cited page rather
    than a bare document download."""
    s = s or {}
    return bool(s.get("pages")) or (s.get("page_label") or "").startswith("page")


def observed_outcome(r):
    role = r.get("role") or ""
    answer = r.get("answer") or ""
    if role == "sales" or r.get("kind") == "deflected":
        return "deflect"
    if role == "document" or any((s or {}).get("download_url") and not _cites_pages(s)
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
    allowed = want if isinstance(want, list) else [want]
    if "any" not in allowed and got not in allowed:
        fails.append(f"outcome: expected {'/'.join(allowed)}, got {got}")
    if got == "answer" and live.get("answer_mention") and not any(
            m.lower() in low for m in live["answer_mention"]):
        fails.append(f"answer_mention: none of {live['answer_mention']}")
    if got == "answer" and live.get("cite"):
        srcs = " | ".join(s.get("source", "") for s in r.get("sources") or [])
        if not any(c.lower() in srcs.lower() for c in live["cite"]):
            fails.append(f"cite: none of {live['cite']} in [{srcs}]")
    if live.get("mention") and not any(m.lower() in low for m in live["mention"]):
        fails.append(f"mention: none of {live['mention']}")
    if live.get("reply") and live["reply"] not in (r.get("suggested_replies") or []):
        fails.append(f"reply: no {live['reply']!r} button")
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


def score_needs_history(turn, r, history):
    """M10: needs_history against the LIVE resolved_query, with the real
    accumulated conversation (not a placeholder) -- the one thing
    tests/run_scenarios.py's own replay can only approximate from a saved
    transcript. None (a deflect or an intent reached the question first,
    so the pipeline never made this decision) is not scored."""
    want = turn.get("needs_history")
    if want is None:
        return None
    raw_q = turn["q"]
    resolved_q = _strip_product_suffix(r.get("resolved_query") or raw_q)
    got = T.is_followup_turn(raw_q, history, resolved_q)
    return None if got == want else f"needs_history: expected {want}, got {got}"


def run_scenario(sc, url, verbose, path="query"):
    sid = f"live-{sc['id']}-{uuid.uuid4().hex[:8]}"
    rows = []
    history: list = []
    endpoint, headers = f"{url}/query", {}
    if path == "widget":
        endpoint = f"{url}/widget/ask"
        headers = {"Authorization": "Bearer " + member_token(sid)}
    for turn in sc["turns"]:
        body = {"q": turn["q"], "session_id": sid}
        if sc.get("scoped") and sc.get("product_key"):
            body["product"] = sc["product_key"]
        t0 = time.perf_counter()
        try:
            resp = requests.post(endpoint, json=body, headers=headers,
                                 timeout=240)
            r = resp.json()
            if resp.status_code != 200:
                r = {"answer": f"HTTP {resp.status_code}: {json.dumps(r)[:300]}",
                     "role": "error"}
        except Exception as e:                      # noqa: BLE001
            r = {"answer": f"REQUEST FAILED: {e}", "role": "error"}
        wall = time.perf_counter() - t0
        got, fails = score_turn(turn, r)
        missing = []
        if path == "widget":
            if r.get("role") != "error":
                missing = missing_widget_keys(r)
        else:
            history_fail = score_needs_history(turn, r, history)
            if history_fail:
                fails = [*fails, history_fail]
        rows.append({"turn": turn, "response": r, "wall": wall,
                     "got": got, "fails": fails, "missing_keys": missing})
        if verbose:
            mark = "ok  " if not fails else "FAIL"
            print(f"  {mark} {got:8s} {wall:5.1f}s  {turn['q'][:70]}")
            for f in fails:
                print(f"         {f}")
            if missing:
                print(f"         widget dropped: {', '.join(missing)}")
        history.append({"q": turn["q"], "a": r.get("answer") or ""})
    return sid, rows


def agreement(query_results, widget_results):
    """Per-turn outcome agreement between the two paths, same scenarios in
    the same order. Returns (agreed, total, disagreements)."""
    agreed, total, diffs = 0, 0, []
    for (sc, _, q_rows), (_, _, w_rows) in zip(query_results, widget_results):
        for i, (qr, wr) in enumerate(zip(q_rows, w_rows), 1):
            total += 1
            if qr["got"] == wr["got"]:
                agreed += 1
            else:
                diffs.append({"scenario": sc["id"], "turn": i,
                              "q": qr["turn"]["q"], "query": qr["got"],
                              "widget": wr["got"],
                              "missing_keys": wr.get("missing_keys") or []})
    return agreed, total, diffs


def missing_key_counts(results):
    counts = {}
    for _, _, rows in results:
        for row in rows:
            for k in row.get("missing_keys") or []:
                counts[k] = counts.get(k, 0) + 1
    return counts


def write_markdown(results, path, url, started, route="/query"):
    L = []
    L.append(f"# Live conversation transcripts — {started:%Y-%m-%d %H:%M}")
    L.append("")
    L.append(f"Backend: `{url}`. Every turn POSTed to `{route}` with one "
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
            if row.get("missing_keys"):
                L.append("- widget dropped: " + ", ".join(row["missing_keys"]))
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
    ap.add_argument("--path", choices=("query", "widget", "both"),
                    default="query")
    a = ap.parse_args()

    started = dt.datetime.now()
    files = sorted(SCENARIO_DIR.glob("*.json"))
    scenarios = []
    for f in files:
        sc = json.loads(f.read_text(encoding="utf-8"))
        if not any("live" in t for t in sc["turns"]):
            continue
        if a.only and not any(sc["id"].startswith(o) for o in a.only):
            continue
        scenarios.append(sc)

    paths = ("query", "widget") if a.path == "both" else (a.path,)
    by_path = {}
    for path in paths:
        results = []
        for sc in scenarios:
            print(f"== {sc['id']} ({path})")
            sid, rows = run_scenario(sc, a.url, not a.quiet, path)
            results.append((sc, sid, rows))
        by_path[path] = results

    out = pathlib.Path(a.out) if a.out else HERE.parent.parent / "scratchpad" / f"live_{started:%Y%m%d_%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    status = 0
    summary = {"path": a.path, "url": a.url, "started": started.isoformat()}
    for path, results in by_path.items():
        target = out if len(paths) == 1 else out.with_name(f"{out.stem}_{path}{out.suffix}")
        route = "/widget/ask" if path == "widget" else "/query"
        p, f = write_markdown(results, target, a.url, started, route)
        print(f"\n[{path}] {p} of {p + f} turns as expected "
              f"({100.0 * p / max(1, p + f):.1f}%)  -> {target}")
        summary[path] = {"passed": p, "turns": p + f, "transcript": str(target)}
        if f:
            status = 1
        if path == "widget":
            counts = missing_key_counts(results)
            summary[path]["missing_keys"] = counts
            if counts:
                status = 1
                print("[widget] keys dropped by /widget/ask (turns): " +
                      ", ".join(f"{k} {n}" for k, n in sorted(counts.items())))
    if a.path == "both":
        agreed, total, diffs = agreement(by_path["query"], by_path["widget"])
        summary["agreement"] = {"agreed": agreed, "turns": total,
                                "disagreements": diffs}
        print(f"\nagreement /query vs /widget/ask: {agreed}/{total} turns")
        for d in diffs:
            extra = (f"  (widget dropped {', '.join(d['missing_keys'])})"
                     if d["missing_keys"] else "")
            print(f"  {d['scenario']} T{d['turn']}: query={d['query']} "
                  f"widget={d['widget']}{extra}  {d['q'][:60]}")
        if agreed != total:
            status = 1
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(
            {"summary": summary,
             "paths": {path: [{"id": sc["id"], "session": sid, "rows": rows}
                              for sc, sid, rows in results]
                       for path, results in by_path.items()}}
            if len(paths) > 1 else
            [{"id": sc["id"], "session": sid, "rows": rows}
             for sc, sid, rows in by_path[paths[0]]],
            indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return status


if __name__ == "__main__":
    sys.exit(main())
