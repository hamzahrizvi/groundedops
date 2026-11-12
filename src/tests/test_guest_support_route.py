"""A guest can always reach a person (the CEO, 2026-11-12).

Before: a guest's no-answer reply offered only "Sign in", and "I want to talk
to a person" got the same sign-in reply, so a guest had no route to support.
"""
from unittest.mock import patch

import _harness
from fastapi.testclient import TestClient

import quota

client = TestClient(_harness.app)
fails = []


def check(cond, label):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        fails.append(label)


def ask(q, n):
    return client.post("/widget/ask", json={"q": q, "visitor_id": f"guest-{n}"}).json()


with patch.object(quota, "anon_llm_enabled", lambda: False):
    d = ask("How do I reset the password on my Cisco router?", 1)
    check(d.get("needs_sign_in") is True, "a guest miss still offers sign-in")
    check(d.get("offer_support") is True, "and now offers support as well")
    check("support team" in d.get("answer", ""), "and says so in words")

    h = ask("I want to talk to a person", 2)
    check(h.get("role") == "handoff", f"a guest asking for a person gets a handoff ({h.get('role')})")
    check(h.get("offer_support") is True and not h.get("needs_sign_in"),
          "with the support offer, not a sign-in prompt")
    check(h.get("from_faq") is False, "and it is not dressed up as a reviewed FAQ answer")

import os
src = open(os.path.join(os.path.dirname(os.path.abspath(_harness.__file__)),
                        "widget", "groundedops-widget.js"), encoding="utf-8").read()
block = src[src.index("if (d.needs_sign_in) {"):][:1500]
check('openContactForm("support")' in block, "the widget's sign-in branch shows Email support")

if fails:
    raise SystemExit(f"{len(fails)} check(s) failed")
print("ALL CHECKS PASSED")
