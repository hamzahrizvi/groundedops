"""End-of-chat star ratings from the widget, and issue reports from staff
testing it in the console's Test chat.

Ratings stay on the console: an average, the spread, and the complaints that
come up again and again. Reports also go to the office's Customer department
(tools/ops_pivot.py --export-real copies them into the run), which checks
them against the manuals and files findings to the product owner. Reports
are staff text, not visitor text, so D-20261015-11 does not keep them out.
"""
import os
import re
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone

import jsonstore

RATINGS_PATH = os.getenv("WIDGET_RATINGS_PATH", "widget_ratings.json")
REPORTS_PATH = os.getenv("TESTER_REPORTS_PATH", "tester_reports.json")
_lock = threading.Lock()

# A rating at or under this counts as a complaint for the repeat themes.
LOW = 3
STOP = set("""a an the and or but if so to of in on at for with from by is are was
were be been it its this that these those i me my we our you your they them their
he she not no yes do does did can could would should will just very too also
really more less than then there here what when where which who how why have has
had get got any some all about into out up down over as answer answers bot chat
please thanks thank much bit""".split())


def _now():
    return datetime.now(timezone.utc).isoformat()


def save_rating(session_id: str, stars: int, comment: str, scope, questions) -> None:
    """One rating per conversation, so rating it again replaces the first."""
    with _lock:
        data = jsonstore.load(RATINGS_PATH, {}, label="widget ratings")
        if not isinstance(data, dict):
            data = {}
        data[session_id] = {"stars": stars, "comment": comment.strip(),
                            "scope": scope, "questions": questions[:10],
                            "created_at": _now()}
        jsonstore.save(RATINGS_PATH, data, label="widget ratings")


def add_report(note: str, conversation: list, product, reporter: str) -> dict:
    row = {"id": uuid.uuid4().hex[:12], "note": note.strip(),
           "conversation": conversation, "product": product,
           "reporter": reporter, "created_at": _now()}
    with _lock:
        data = jsonstore.load(REPORTS_PATH, [], label="tester reports")
        if not isinstance(data, list):
            data = []
        data.append(row)
        jsonstore.save(REPORTS_PATH, data, label="tester reports")
    return row


def list_reports() -> list:
    data = jsonstore.load(REPORTS_PATH, [], label="tester reports")
    return sorted(data if isinstance(data, list) else [],
                  key=lambda r: r.get("created_at", ""), reverse=True)


def delete_report(report_id: str) -> bool:
    with _lock:
        data = jsonstore.load(REPORTS_PATH, [], label="tester reports")
        keep = [r for r in data if r.get("id") != report_id]
        if len(keep) == len(data):
            return False
        jsonstore.save(REPORTS_PATH, keep, label="tester reports")
    return True


def _words(text: str) -> set:
    # ponytail: a trailing-s strip, not a stemmer; swap in a real one if
    # "slow"/"slowly" splitting into two themes starts to matter.
    out = set()
    for w in re.findall(r"[a-z][a-z'-]{2,}", text.lower()):
        w = w.strip("'-")
        if w.endswith("s") and not w.endswith("ss") and len(w) > 4:
            w = w[:-1]
        if w not in STOP:
            out.add(w)
    return out


def themes(comments: list, top: int = 8) -> list:
    """Words that recur across low-rated comments: [{word, count, examples}].

    A word counts once per comment and needs two comments behind it, so one
    long rant cannot make a theme on its own.
    """
    seen = Counter()
    for c in comments:
        seen.update(_words(c))
    out = []
    for word, n in seen.most_common():
        if n < 2 or len(out) >= top:
            break
        out.append({"word": word, "count": n,
                    "examples": [c for c in comments if word in _words(c)][:3]})
    return out


def rating_summary() -> dict:
    data = jsonstore.load(RATINGS_PATH, {}, label="widget ratings")
    rows = sorted((v for v in (data.values() if isinstance(data, dict) else [])
                   if isinstance(v, dict) and isinstance(v.get("stars"), int)),
                  key=lambda r: r.get("created_at", ""), reverse=True)
    spread = Counter(r["stars"] for r in rows)
    low = [r["comment"] for r in rows if r["stars"] <= LOW and r.get("comment")]
    return {
        "count": len(rows),
        "average": round(sum(r["stars"] for r in rows) / len(rows), 2) if rows else None,
        "spread": {str(s): spread.get(s, 0) for s in range(1, 6)},
        "themes": themes(low),
        "recent": [r for r in rows if r.get("comment")][:30],
    }


if __name__ == "__main__":
    t = themes(["Too slow and the answer was wrong", "slow to reply",
                "wrong product manual", "Loved it"])
    assert [x["word"] for x in t] == ["slow", "wrong"], t
    assert t[0]["count"] == 2 and len(t[0]["examples"]) == 2, t
    assert themes(["slow slow slow"]) == []          # one comment is no theme
    assert _words("Answers were slow") == {"slow"}   # stopwords and plurals
    print("ok")
