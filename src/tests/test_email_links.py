"""Password reset by email and one-time sign-in links.

mailer.send is replaced with a recorder, so the "email" is read back from
memory; _harness.py keeps the token store on a scratch path.
"""
import os
import re
import time

import _harness
from fastapi.testclient import TestClient

import accounts
import mailer

import main

client = TestClient(_harness.app)
fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        fails.append(label)


sent = []
mailer.send = lambda to, subject, body: sent.append({"to": to, "subject": subject, "body": body})
mailer.is_configured = lambda: True
os.environ["ADMIN_PUBLIC_URL"] = "https://console.example.com"
EMAIL = "basic@test.local"


def request_link(purpose, email=EMAIL, **kw):
    return client.post("/admin/auth/email-link", json={"email": email, "purpose": purpose}, **kw)


def last_token(kind, n_before):
    for _ in range(50):          # the mail goes out on a thread
        if len(sent) > n_before:
            break
        time.sleep(0.05)
    if len(sent) <= n_before:
        return None
    m = re.search(r"https://console\.example\.com/admin#" + kind + r"=([A-Za-z0-9_-]+)",
                  sent[-1]["body"])
    return m.group(1) if m else None


def reset_limits():
    main._link_hits.clear()


print("\n== state ==")
check(client.get("/admin/auth/state").json().get("email_links") is True,
      "the sign-in page is told email links are available")

print("\n== no account enumeration ==")
n = len(sent)
a = request_link("reset")
b = request_link("reset", email="nobody@test.local")
check(a.status_code == b.status_code == 200 and a.json() == b.json(),
      "an unknown address gets exactly the same reply as a real one")
time.sleep(0.3)
check(len(sent) == n + 1 and sent[-1]["to"] == [EMAIL], "only the real account is emailed")

print("\n== password reset ==")
reset_limits()
n = len(sent)
request_link("reset")
tok = last_token("reset", n)
check(tok is not None, "the email carries a reset link to the configured console address")
stored = open(os.environ["AUTH_TOKENS_PATH"], encoding="utf-8").read()
check(tok not in stored, "the token store holds a hash, not the token")
r = client.post("/admin/auth/reset", json={"token": tok, "password": "short"})
check(r.status_code == 400, "a too-short password is refused")
r = client.post("/admin/auth/reset", json={"token": tok, "password": "a-brand-new-password"})
check(r.status_code == 200 and r.json().get("token"), "a valid link sets the password and signs in")
check(accounts.verify_password(EMAIL, "a-brand-new-password") is not None,
      "the new password works")
check(client.get("/admin/auth/me", headers={"x-admin-password": _harness.BASIC_TOKEN}).status_code == 401,
      "sessions issued before the reset are signed out")
r = client.post("/admin/auth/reset", json={"token": tok, "password": "another-new-password"})
check(r.status_code == 400, "a reset link works only once")

print("\n== one-time sign-in ==")
reset_limits()
n = len(sent)
request_link("login")
tok = last_token("login", n)
check(tok is not None, "the email carries a sign-in link")
check(client.post("/admin/auth/reset", json={"token": tok, "password": "x" * 20}).status_code == 400,
      "a sign-in link cannot be used to reset the password")
reset_limits(); n = len(sent); request_link("login"); tok = last_token("login", n)
r = client.post("/admin/auth/email-login", json={"token": tok})
check(r.status_code == 200 and r.json()["user"]["email"] == EMAIL, "a sign-in link signs you in")
session = r.json()["token"]
check(client.get("/admin/auth/me", headers={"x-admin-password": session}).status_code == 200,
      "the session it issues works")
check(client.post("/admin/auth/email-login", json={"token": tok}).status_code == 400,
      "a sign-in link works only once")

reset_limits(); n = len(sent); request_link("login"); first = last_token("login", n)
n = len(sent); request_link("login"); second = last_token("login", n)
check(client.post("/admin/auth/email-login", json={"token": first}).status_code == 400,
      "asking again voids the earlier link")
check(client.post("/admin/auth/email-login", json={"token": second}).status_code == 200,
      "the newest link still works")

reset_limits(); n = len(sent); request_link("login"); tok = last_token("login", n)
basic = accounts.find_by_email(EMAIL)
accounts.set_password(basic["id"], "changed-elsewhere-pw")
check(client.post("/admin/auth/email-login", json={"token": tok}).status_code == 400,
      "a password change voids outstanding links")

print("\n== expiry, disabled, abuse ==")
reset_limits(); n = len(sent); request_link("login"); tok = last_token("login", n)
store = accounts._load_tokens()
for rec in store.values():
    rec["exp"] = int(time.time()) - 1
accounts.jsonstore.save(accounts._TOKENS_PATH, store)
check(client.post("/admin/auth/email-login", json={"token": tok}).status_code == 400,
      "an expired link is refused")

accounts.set_disabled(basic["id"], True)
reset_limits(); n = len(sent); request_link("login"); time.sleep(0.3)
check(len(sent) == n, "a disabled account is not emailed")
accounts.set_disabled(basic["id"], False)

reset_limits(); n = len(sent)
for _ in range(6):
    request_link("reset")
time.sleep(0.3)
check(len(sent) - n == main._LINK_PER_EMAIL, "requests per address are rate-limited")

print("\n== link address ==")
reset_limits()
os.environ.pop("ADMIN_PUBLIC_URL", None)
os.environ.pop("ADMIN_NETWORK_URL", None)
r = request_link("reset", headers={"Host": "attacker.example"})
check(r.status_code == 503, "without a configured address, a foreign Host header is not trusted")
r = request_link("reset", headers={"X-Forwarded-For": "203.0.113.9"})
check(r.status_code in (404, 503), "behind a proxy, links need ADMIN_PUBLIC_URL")
mailer.is_configured = lambda: False
os.environ["ADMIN_PUBLIC_URL"] = "https://console.example.com"
check(request_link("reset").status_code == 503, "with no mail server the page is told plainly")

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
