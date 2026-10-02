"""The daily ceilings that actually bound a determined anonymous caller.

Session caps are keyed on a browser-supplied session id, and the per-visitor
count on a browser-supplied visitor id -- both reset by asking for a new one.
The per-IP count is the only thing such a caller cannot reset, so these pin
that it is enforced on every anonymous path, not just written.
"""
import os
import tempfile

import quota


def _fresh_db(faq_cap, ip_cap):
    """Point quota at an empty store with fixed caps; return a restore fn."""
    saved = (quota.DB_PATH, quota.anon_faq_limit, quota.anon_ip_limit)
    quota.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="quota-ceil-"), "q.db")
    quota.init_db()
    quota.anon_faq_limit = lambda: faq_cap
    quota.anon_ip_limit = lambda: ip_cap

    def restore():
        quota.DB_PATH, quota.anon_faq_limit, quota.anon_ip_limit = saved
    return restore


def _guest(visitor, ip="203.0.113.7"):
    return quota.identify(None, visitor, ip)


def test_a_new_visitor_id_does_not_reset_the_ip_faq_ceiling():
    restore = _fresh_db(faq_cap=60, ip_cap=5)
    try:
        # Five lookups, each from a "new browser" on the same address: the
        # per-visitor cap never comes close, the per-IP one must.
        for n in range(5):
            caller = _guest(f"visitor-{n}")
            assert quota.check_faq_lookup(caller)["allowed"], n
            quota.consume_faq_lookup(caller)
        gate = quota.check_faq_lookup(_guest("visitor-fresh"))
        assert not gate["allowed"]
        assert gate["reason"] == "ip_faq_quota"
        # A different address is unaffected.
        assert quota.check_faq_lookup(_guest("visitor-fresh", "198.51.100.1"))["allowed"]
    finally:
        restore()


def test_the_per_visitor_faq_cap_still_applies_first():
    restore = _fresh_db(faq_cap=2, ip_cap=100)
    try:
        caller = _guest("visitor-a")
        for _ in range(2):
            quota.consume_faq_lookup(caller)
        gate = quota.check_faq_lookup(caller)
        assert not gate["allowed"] and gate["reason"] == "faq_quota"
    finally:
        restore()


def test_remaining_reports_the_tighter_of_the_two():
    restore = _fresh_db(faq_cap=60, ip_cap=3)
    try:
        quota.consume_faq_lookup(_guest("visitor-a"))
        gate = quota.check_faq_lookup(_guest("visitor-b"))
        assert gate["allowed"] and gate["remaining"] == 2
    finally:
        restore()


def test_members_are_not_ip_counted():
    restore = _fresh_db(faq_cap=1, ip_cap=1)
    try:
        member = {"tier": "member", "identity": "u:x", "ip_identity": None, "uid": "x"}
        assert quota.check_faq_lookup(member)["allowed"]
    finally:
        restore()


def test_reset_visitor_clears_both_per_ip_counters():
    restore = _fresh_db(faq_cap=60, ip_cap=1)
    try:
        caller = _guest("visitor-a")
        quota.consume_faq_lookup(caller)
        quota.consume(caller, 1)            # the credit ceiling, guest AI on
        win = quota._window_start()
        with quota._conn() as c:
            assert quota._used(c, caller["ip_identity"], win) == 1
        quota.reset_visitor("visitor-a", "203.0.113.7")
        with quota._conn() as c:
            assert quota._used(c, caller["ip_identity"], win) == 0
            assert quota._used(c, caller["ip_identity"] + ":faq", win) == 0
        assert quota.check_faq_lookup(_guest("visitor-other"))["allowed"]
    finally:
        restore()
