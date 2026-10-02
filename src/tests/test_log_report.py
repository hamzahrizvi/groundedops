"""10.1: the weekly report over a fixture log -- old rows without M2's keys
beside new ones -- reconciled exactly."""
import json
import tempfile
from datetime import datetime
from pathlib import Path

import log_report

OLD = [  # pre-M2: no session_id / origin / outcome
    {"timestamp": "2026-09-20T10:00:00", "role": "fast", "answer": "It weighs 1 kg.",
     "query_normalized": "weight", "timing": {"total_time": 2.0}},
    {"timestamp": "2026-09-20T10:01:00", "role": "rejected", "answer": "Sorry.",
     "query_normalized": "capital of france", "timing": {"total_time": 1.0}},
]
NEW = [
    # s1: answered, then answered -> resolved
    {"timestamp": "2026-09-28T09:00:00", "session_id": "s1", "origin": "widget",
     "role": "reasoning", "outcome": "respond", "answer": "Pin 1 is +12V.",
     "query_normalized": "nv9 pinout", "timing": {"total_time": 4.0}},
    {"timestamp": "2026-09-28T09:01:00", "session_id": "s1", "origin": "widget",
     "role": "faq", "outcome": "faq.answer", "answer": "Use the USB cable.",
     "query_normalized": "how do i connect", "timing": {"total_time": 0.5}},
    # s2: refused by the gate -> not resolved
    {"timestamp": "2026-09-28T10:00:00", "session_id": "s2", "origin": "widget",
     "role": "rejected", "outcome": "none.reject", "answer": "I couldn't find that.",
     "query_normalized": "price of nv9", "timing": {"total_time": 1.5}},
    # s3: answered, then asked for a person -> not resolved
    {"timestamp": "2026-09-28T11:00:00", "session_id": "s3", "origin": "widget",
     "role": "fast", "outcome": "respond", "answer": "Reset it from the menu.",
     "query_normalized": "reset", "timing": {"total_time": 3.0}},
    {"timestamp": "2026-09-28T11:02:00", "session_id": "s3", "origin": "widget",
     "role": "handoff", "outcome": None, "answer": "I'll hand this over.",
     "query_normalized": "talk to a person", "timing": {"total_time": 0.1}},
    # s4: same question twice -> not resolved
    {"timestamp": "2026-09-28T12:00:00", "session_id": "s4", "origin": "console",
     "role": "fast", "outcome": "respond", "answer": "Use 24V.",
     "query_normalized": "power", "timing": {"total_time": 2.5}},
    {"timestamp": "2026-09-28T12:01:00", "session_id": "s4", "origin": "console",
     "role": "fast", "outcome": "respond", "answer": "Use 24V.",
     "query_normalized": "power", "timing": {"total_time": 2.5}},
    # s5: the model declined (exit is "respond"; the text says no)
    {"timestamp": "2026-09-28T13:00:00", "session_id": "s5", "origin": "widget",
     "role": "fast", "outcome": "respond",
     "answer": "I don't have that information in the documentation.",
     "query_normalized": "colour options", "timing": {"total_time": 5.0}},
    # s6: asked back
    {"timestamp": "2026-09-28T14:00:00", "session_id": "s6", "origin": "widget",
     "role": "clarify", "outcome": "none.vague", "answer": "Which product is this about?",
     "query_normalized": "error 5", "timing": {"total_time": 1.0}},
    # harness turns: excluded by default
    {"timestamp": "2026-09-28T15:00:00", "session_id": "eval-1", "origin": "eval",
     "role": "fast", "outcome": "respond", "answer": "x",
     "query_normalized": "x", "timing": {"total_time": 9.0}},
    {"timestamp": "2026-09-28T15:01:00", "session_id": "live-1", "origin": "live",
     "role": "fast", "outcome": "respond", "answer": "y",
     "query_normalized": "y", "timing": {"total_time": 9.0}},
    # votes
    {"timestamp": "2026-09-28T09:05:00", "event": "feedback", "request_id": "a",
     "session_id": "s1", "outcome": "voted_up"},
    {"timestamp": "2026-09-28T10:05:00", "event": "feedback", "request_id": "b",
     "session_id": "s2", "outcome": "voted_down"},
]
LEADS = [{"created_at": "2026-09-28T11:05:00+00:00", "kind": "support"},
         {"created_at": "2026-08-01T00:00:00+00:00", "kind": "sales"}]


def test_turns_and_kinds():
    r = log_report.summarise(OLD + NEW, LEADS)
    assert r["turns"] == 2 + 9
    assert r["answered"] == 1 + 5      # old fast; s1 x2, s3, s4 x2
    assert r["refused"] == 1 + 2       # old rejected; s2 gate, s5 model
    assert r["clarify"] == 1
    assert r["handed_off"] == 1
    assert r["answered"] + r["refused"] + r["clarify"] + r["handed_off"] + r["other"] == r["turns"]


def test_resolved_only_clean_conversations():
    r = log_report.summarise(OLD + NEW, LEADS)
    assert r["conversations"] == 6
    assert r["resolved_provisional"] == 1          # s1 only
    assert r["turns_without_session"] == 2         # the old rows


def test_origin_outcome_and_missing_buckets():
    r = log_report.summarise(OLD + NEW, LEADS)
    assert r["by_origin"] == {"missing": 2, "widget": 7, "console": 2,
                              "eval": 1, "live": 1}
    assert r["rows_missing_origin"] == 2
    assert r["by_outcome"] == {"missing": 3, "respond": 5, "faq.answer": 1,
                               "none.reject": 1, "none.vague": 1}
    everything = log_report.summarise(OLD + NEW, LEADS, include_tests=True)
    assert everything["turns"] == 13


def test_speed_votes_leads_and_window():
    r = log_report.summarise(OLD + NEW, LEADS)
    t = sorted([2.0, 1.0, 4.0, 0.5, 1.5, 3.0, 0.1, 2.5, 2.5, 5.0, 1.0])
    assert r["speed"]["n"] == 11
    assert r["speed"]["p50_s"] == 2.0 == t[5]
    assert r["speed_by_role"]["fast"]["n"] == 5
    assert r["votes"] == {"up": 1, "down": 1}
    assert r["leads"] == 2

    week = log_report.summarise(OLD + NEW, LEADS, since=datetime(2026, 9, 25))
    assert week["turns"] == 9 and week["leads"] == 1
    assert week["rows_missing_origin"] == 0


def test_load_rows_reads_rotated_files_and_skips_torn_lines():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "logs_20260901_000000_0.jsonl").write_text(
            json.dumps(OLD[0]) + "\n", encoding="utf-8")
        Path(d, "logs.jsonl").write_text(
            json.dumps(NEW[0]) + "\n{\"timestamp\": \"2026", encoding="utf-8")
        rows = log_report.load_rows(d)
    assert [r["timestamp"] for r in rows] == [OLD[0]["timestamp"], NEW[0]["timestamp"]]
