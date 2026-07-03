"""
Tests for memory.py — pure stdlib, no ML deps.

test_two_sessions_never_see_each_others_history is the direct regression
test for the actual production bug: a single global, never-cleared
MEMORY list meant any two unrelated callers hitting the same running
server (two browser tabs, or a fresh test_queries.py run against a
server that still had state from an earlier interactive session) could
silently contaminate each other's conversational follow-up resolution.
"""

import time

import memory


def setup_function(_):
    """Each test starts from a clean slate — memory.py's storage is
    module-level state shared across the whole test process."""
    memory.clear_memory()


# ── Core regression: session isolation ──────────────────────────────────

def test_two_sessions_never_see_each_others_history():
    memory.add_to_memory("session-a", "how does myconnect work with mycheckr", "via REST API")
    memory.add_to_memory("session-b", "what is the hub ip", "192.168.0.1")

    history_a = memory.get_history("session-a")
    history_b = memory.get_history("session-b")

    assert len(history_a) == 1
    assert history_a[0]["q"] == "how does myconnect work with mycheckr"

    assert len(history_b) == 1
    assert history_b[0]["q"] == "what is the hub ip"

    # Neither session's history should contain the other's query
    assert all(h["q"] != "what is the hub ip" for h in history_a)
    assert all(h["q"] != "how does myconnect work with mycheckr" for h in history_b)


def test_fresh_session_id_starts_with_empty_history_even_if_others_are_populated():
    memory.add_to_memory("old-session", "some previous unrelated query", "some previous answer")
    # A brand new session_id (e.g. a fresh test_queries.py run, or a new
    # browser tab) must start completely clean regardless of what other
    # sessions on the same server have accumulated.
    assert memory.get_history("brand-new-session") == []
    assert memory.get_last_query("brand-new-session") is None


def test_get_last_query_is_scoped_to_session():
    memory.add_to_memory("session-a", "first query in a", "answer")
    memory.add_to_memory("session-b", "first query in b", "answer")
    memory.add_to_memory("session-a", "second query in a", "answer")

    assert memory.get_last_query("session-a") == "second query in a"
    assert memory.get_last_query("session-b") == "first query in b"


# ── clear_memory scoping ─────────────────────────────────────────────────

def test_clear_memory_with_session_id_only_clears_that_session():
    memory.add_to_memory("session-a", "query a", "answer a")
    memory.add_to_memory("session-b", "query b", "answer b")

    memory.clear_memory("session-a")

    assert memory.get_history("session-a") == []
    assert len(memory.get_history("session-b")) == 1


def test_clear_memory_with_no_args_clears_everything():
    memory.add_to_memory("session-a", "query a", "answer a")
    memory.add_to_memory("session-b", "query b", "answer b")

    memory.clear_memory()

    assert memory.get_history("session-a") == []
    assert memory.get_history("session-b") == []


# ── MAX_MEMORY truncation, per session ───────────────────────────────────

def test_max_memory_truncation_is_per_session():
    for i in range(memory.MAX_MEMORY + 3):
        memory.add_to_memory("session-a", f"query {i}", f"answer {i}")
    memory.add_to_memory("session-b", "only query in b", "answer")

    history_a = memory.get_history("session-a")
    history_b = memory.get_history("session-b")

    assert len(history_a) == memory.MAX_MEMORY
    # Oldest entries should have been dropped, newest retained
    assert history_a[-1]["q"] == f"query {memory.MAX_MEMORY + 2}"

    assert len(history_b) == 1


# ── Refusals are not stored ──────────────────────────────────────────────

def test_refusal_answers_are_not_added_to_memory():
    memory.add_to_memory("session-a", "what is the capital of france",
                         "I could not find that in the knowledge base.")
    assert memory.get_history("session-a") == []


# ── TTL reaping ───────────────────────────────────────────────────────────

def test_expired_session_is_reaped_on_next_write():
    memory.add_to_memory("stale-session", "old query", "old answer")
    # Simulate the session having gone quiet long enough to expire by
    # back-dating its last-seen timestamp directly.
    memory._last_seen["stale-session"] = time.time() - memory.SESSION_TTL_SECONDS - 1

    # Reaping is opportunistic and runs on the next write to ANY session.
    memory.add_to_memory("other-session", "new query", "new answer")

    assert memory.get_history("stale-session") == []
    assert len(memory.get_history("other-session")) == 1
