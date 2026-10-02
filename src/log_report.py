"""10.1: the weekly report -- how many questions were resolved, refused,
handed off, and how fast -- read from logs.jsonl.

    python -m log_report --since 7d            # real use only (default)
    python -m log_report --since 30d --all     # include eval/live/preflight

`summarise(rows)` is pure; `load_rows()` reads logs.jsonl plus the rotated
logs_*.jsonl (logger._rotate_if_needed). GET /admin/report wraps both.

Rows written before M2 (2026-09-27) carry no session_id/origin/outcome. They
land in a 'missing' bucket rather than being guessed at: their turns still
count as answered/refused (that comes from the role and the answer text),
but they cannot be grouped into conversations, so they never count towards
'resolved'.
"""

import argparse
import glob
import json
import os
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone

from text_utils import is_refusal

LOG_DIR = os.path.dirname(os.path.abspath(__file__))
TEST_ORIGINS = {"eval", "live", "preflight"}

# How a turn ended, from M2's outcome (pipeline_trace.OUTCOMES) or, when that
# is missing, the role. The model saying no ends at "respond" like an answer
# does, so the answer text is checked too (is_refusal, the phrase list
# offer_support already uses).
_REFUSED_OUTCOMES = {"none.reject", "suppress", "refusal"}
_CLARIFY_OUTCOMES = {"none.follow", "none.vague", "clarify", "faq.ask",
                     "span.ask"}
_ANSWER_OUTCOMES = {"respond", "faq.answer", "faq.picked", "extract",
                    "verbatim", "steps", "more", "sales", "capability",
                    "inference"}
# The plan's 'resolved' exits (docs/upgrade-plan-7-to-10.md 10.1).
_RESOLVED_OUTCOMES = {"respond", "faq.answer", "extract", "verbatim", "steps"}


def turn_kind(row: dict) -> str:
    """answered / refused / clarify / handoff / other."""
    role = row.get("role")
    outcome = row.get("outcome")
    if role == "handoff":
        return "handoff"
    if role == "greeting":
        return "other"
    if (outcome in _REFUSED_OUTCOMES or role in ("rejected", "none")
            or is_refusal(row.get("answer") or "")):
        return "refused"
    if outcome in _CLARIFY_OUTCOMES or role == "clarify":
        return "clarify"
    return "answered"


def _pct(values: list[float], q: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    return round(statistics.quantiles(values, n=100,
                                      method="inclusive")[q - 1], 2)


def _speed(rows: list[dict]) -> dict:
    t = [float(r["timing"]["total_time"]) for r in rows
         if isinstance((r.get("timing") or {}).get("total_time"), (int, float))]
    return {"n": len(t), "p50_s": _pct(t, 50), "p90_s": _pct(t, 90)}


def _parse_ts(s) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    # logger writes naive UTC; leads are tz-aware. Compare as naive UTC.
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def summarise(rows: list[dict], leads: list[dict] | None = None,
              since: datetime | None = None, until: datetime | None = None,
              include_tests: bool = False) -> dict:
    """The report over `rows` (logs.jsonl dicts) in [since, until)."""
    def in_window(ts) -> bool:
        d = _parse_ts(ts)
        return (d is not None and (since is None or d >= since)
                and (until is None or d < until))

    rows = [r for r in rows if in_window(r.get("timestamp"))]
    votes = Counter(r.get("outcome") for r in rows if r.get("event") == "feedback")
    turns = [r for r in rows if r.get("event") is None]
    by_origin_all = Counter(r.get("origin") or "missing" for r in turns)
    if not include_tests:
        turns = [r for r in turns if r.get("origin") not in TEST_ORIGINS]

    kinds = Counter(turn_kind(r) for r in turns)
    by_role: dict[str, list[dict]] = {}
    for r in turns:
        by_role.setdefault(r.get("role") or "missing", []).append(r)

    # Conversations: only rows that know their session.
    sessions: dict[str, list[dict]] = {}
    for r in turns:
        if r.get("session_id"):
            sessions.setdefault(r["session_id"], []).append(r)
    resolved = 0
    for convo in sessions.values():
        convo.sort(key=lambda r: r.get("timestamp") or "")
        asked = [r.get("query_normalized") for r in convo]
        last = convo[-1]
        if (last.get("outcome") in _RESOLVED_OUTCOMES
                and turn_kind(last) == "answered"
                and not any(turn_kind(r) == "handoff" for r in convo)
                and len(set(asked)) == len(asked)):
            resolved += 1

    leads_in = [l for l in (leads or []) if in_window(l.get("created_at"))]
    return {
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "include_tests": include_tests,
        "turns": len(turns),
        "answered": kinds["answered"],
        "refused": kinds["refused"],
        "clarify": kinds["clarify"],
        "handed_off": kinds["handoff"],
        "other": kinds["other"],
        "leads": len(leads_in),
        "leads_by_kind": dict(Counter(l.get("kind") or "missing" for l in leads_in)),
        # Provisional: a conversation whose last turn answered, with no
        # handoff and no repeated question. A visitor who left unhappy
        # without saying so still counts; thumbs-down is not in it yet.
        "conversations": len(sessions),
        "resolved_provisional": resolved,
        "turns_without_session": sum(1 for r in turns if not r.get("session_id")),
        "votes": {"up": votes["voted_up"], "down": votes["voted_down"]},
        "speed": _speed(turns),
        "speed_by_role": {k: _speed(v) for k, v in sorted(by_role.items())},
        "by_outcome": dict(Counter(r.get("outcome") or "missing" for r in turns)),
        "by_origin": dict(by_origin_all),
        "rows_missing_origin": by_origin_all["missing"],
    }


def load_rows(log_dir: str = LOG_DIR) -> list[dict]:
    rows = []
    paths = sorted(glob.glob(os.path.join(log_dir, "logs_*.jsonl")))
    paths.append(os.path.join(log_dir, "logs.jsonl"))
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue   # a torn last line from a crash mid-write
    return rows


def load_leads() -> list[dict]:
    try:
        import widget_config
        return widget_config.list_leads(None, True)
    except Exception:
        return []


def report(days: int = 7, include_tests: bool = False,
           now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return summarise(load_rows(), load_leads(), since=now - timedelta(days=days),
                     until=None, include_tests=include_tests)


def _format(r: dict) -> str:
    sp = r["speed"]
    lines = [
        f"GroundedOps report since {r['since']}"
        + ("" if r["include_tests"] else " (real use; tests excluded)"),
        f"  questions      {r['turns']}",
        f"  answered       {r['answered']}",
        f"  refused        {r['refused']}",
        f"  asked back     {r['clarify']}",
        f"  handed off     {r['handed_off']} in chat, {r['leads']} forms sent",
        f"  resolved       {r['resolved_provisional']} of {r['conversations']}"
        f" conversations (provisional)",
        f"  votes          {r['votes']['up']} up, {r['votes']['down']} down",
        f"  speed          p50 {sp['p50_s']}s, p90 {sp['p90_s']}s (n={sp['n']})",
    ]
    if r["rows_missing_origin"]:
        lines.append(f"  note: {r['rows_missing_origin']} rows predate M2 and"
                     " have no origin/session; they cannot be resolved.")
    return "\n".join(lines)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default="7d", help="window, e.g. 7d")
    ap.add_argument("--all", action="store_true",
                    help="include eval/live/preflight turns")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = report(int(a.since.rstrip("d")), include_tests=a.all)
    print(json.dumps(r, indent=2) if a.json else _format(r))


if __name__ == "__main__":
    main()
