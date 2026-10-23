"""D-15: visitor text older than RETENTION_DAYS is deleted from every store
that holds it, and nothing else is touched.

Every store is seeded with one row 91 days old and one 89 days old, in a
temp folder; retention.prune(days_=90, now=fixed) must remove exactly the
91-day rows, and the console views must still read the pruned stores.
"""
# run-in-own-process
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

TMP = tempfile.mkdtemp(prefix="retention_")
P = lambda name: os.path.join(TMP, name)  # noqa: E731
# Store paths are read at import, so they are set before any import below.
os.environ.update({
    "CONVO_DB_PATH": P("conversations.db"),
    "WIDGET_FEEDBACK_PATH": P("widget_feedback.json"),
    "WIDGET_LEADS_PATH": P("widget_leads.json"),
    "WIDGET_CONFIG_PATH": P("widget_config.json"),
    "FAQ_GAP_PATH": P("faq_gaps.json"),
    "FAQ_STORE_PATH": P("faq_store.json"),
    "QUOTA_DB_PATH": P("quota.db"),
})
os.environ.pop("FAQ_GAP_VECTOR_CACHE", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(TMP)                     # logger writes logs.jsonl to the cwd

import conversations   # noqa: E402
import faq_store       # noqa: E402
import log_report      # noqa: E402
import retention       # noqa: E402
import widget_config   # noqa: E402

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
OLD, NEW = NOW - timedelta(days=91), NOW - timedelta(days=89)


def naive(d):
    return d.replace(tzinfo=None).isoformat()


def write(name, data):
    with open(P(name), "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))


def read(name):
    with open(P(name), "rb") as f:
        return f.read()


def seed():
    row = lambda d, q: json.dumps({"timestamp": naive(d), "query": q,  # noqa: E731
                                   "answer": "a", "origin": "widget"}) + "\n"
    write("logs.jsonl", row(OLD, "old live q") + row(NEW, "new live q"))
    write("logs_20260601_000000_000000.jsonl", row(OLD, "old rotated q"))
    write("logs_20260801_000000_000000.jsonl", row(OLD, "old mixed q") + row(NEW, "new mixed q"))
    write("logs.json", [{"timestamp": naive(OLD), "query": "old legacy q"},
                        {"timestamp": naive(NEW), "query": "new legacy q"}])

    conversations.init_db()
    c = sqlite3.connect(P("conversations.db"))
    o, n = OLD.timestamp(), NEW.timestamp()
    c.executemany("INSERT INTO conversations VALUES (?,?,?,?,?,?)", [
        ("A", "u1", None, "old chat", o, o),
        ("B", "u1", None, "first old question", o, n),
        ("C", "u1", None, "recent chat", n, n)])
    c.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?)", [
        ("a1", "A", "user", "old chat", None, o),
        ("a2", "A", "assistant", "old reply", None, o),
        ("b1", "B", "user", "first old question", None, o),
        ("b2", "B", "assistant", "old reply", None, o),
        ("b3", "B", "user", "second newer question", None, n),
        ("b4", "B", "assistant", "newer reply", None, n),
        ("c1", "C", "user", "recent chat", None, n)])
    c.commit()
    c.close()

    write("widget_feedback.json", {
        "r-old": {"question": "old fb", "vote": "down", "created_at": OLD.isoformat()},
        "r-new": {"question": "new fb", "vote": "up", "created_at": NEW.isoformat()}})
    write("widget_leads.json", [
        {"id": "old-lead", "kind": "support", "values": {"e": {"label": "Email",
         "value": "old@example.org"}}, "created_at": OLD.isoformat(),
         "handled": False, "notified": False},
        {"id": "new-lead", "kind": "sales", "values": {"e": {"label": "Email",
         "value": "new@example.org"}}, "created_at": NEW.isoformat(),
         "handled": False, "notified": False},
        {"id": "old-stub", "kind": "sales", "created_at": OLD.isoformat(),
         "notified": True, "handled": True}])
    write("faq_gaps.json", [
        {"id": "old gap", "question": "old gap?", "ts": OLD.timestamp(), "times_asked": 1},
        {"id": "new gap", "question": "new gap?", "ts": NEW.timestamp(), "times_asked": 2}])
    write("faq_gaps.json.vectors.npz", "fake")
    write("faq_store.json", [{"id": "f1", "question": "q", "answer": "a"}])
    write("eval_results.json", {"old": "untouched"})
    os.makedirs(P("documents"), exist_ok=True)
    write(os.path.join("documents", "manual.pdf"), "%PDF fake")


def test_days_setting():
    os.environ.pop("RETENTION_DAYS", None)
    assert retention.days() == 90
    os.environ["RETENTION_DAYS"] = "0"
    assert retention.days() == 0 and retention.prune() == {}
    os.environ["RETENTION_DAYS"] = "abc"
    assert retention.days() == 90
    os.environ["RETENTION_DAYS"] = "30"
    assert retention.days() == 30
    os.environ.pop("RETENTION_DAYS")


def test_prune_every_store():
    seed()
    untouched = {n: read(n) for n in ("faq_store.json", "eval_results.json",
                                       os.path.join("documents", "manual.pdf"))}
    out = retention.prune(days_=90, now=NOW)
    assert out == {"logger": 4, "conversations": 5, "widget_api": 1,
                   "widget_config": 1, "faq_store": 1}, out

    # logs: old rows gone everywhere, an all-old rotated file deleted
    rows = log_report.load_rows(TMP)
    qs = sorted(r["query"] for r in rows)
    assert qs == ["new live q", "new mixed q"], qs
    assert not os.path.exists(P("logs_20260601_000000_000000.jsonl"))
    assert [r["query"] for r in json.loads(read("logs.json"))] == ["new legacy q"]
    assert log_report.summarise(rows)["turns"] == 2

    # conversations: A gone, B keeps its newer turn and is retitled, C intact
    convos = {c["id"]: c for c in conversations.list_conversations("u1")}
    assert sorted(convos) == ["B", "C"], convos
    b = conversations.get_conversation("u1", "B")
    assert b["title"] == "second newer question", b["title"]
    assert [m["content"] for m in b["messages"]] == ["second newer question", "newer reply"]
    assert len(conversations.get_conversation("u1", "C")["messages"]) == 1

    # feedback
    assert list(json.loads(read("widget_feedback.json"))) == ["r-new"]

    # leads: unsent old enquiry gone, emailed stub kept for counting
    ids = [l["id"] for l in widget_config.list_leads(include_sent=True)]
    assert sorted(ids) == ["new-lead", "old-stub"], ids
    assert widget_config.lead_stats()["total"] == 1

    # gaps, and the question-keyed vector cache with them
    assert [g["id"] for g in faq_store.list_gaps()] == ["new gap"]
    assert not os.path.exists(P("faq_gaps.json.vectors.npz"))
    assert faq_store._gap_vec_by_text is None

    # customer content and eval files are byte-identical
    for n, before in untouched.items():
        assert read(n) == before, n

    # a second run finds nothing more to delete
    assert retention.prune(days_=90, now=NOW) == {
        "logger": 0, "conversations": 0, "widget_api": 0,
        "widget_config": 0, "faq_store": 0}
