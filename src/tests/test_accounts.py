"""Account levels, sessions, and the authorisation boundaries between them.

The point of most of these is the negative case: a `basic` account must not
reach the admin surface, a `support` account must not reach account
management, and a token must stop working the moment the account behind it
is disabled or has its password changed. Those are the assertions that would
actually catch a regression that matters.
"""
import _harness
from fastapi.testclient import TestClient
from pathlib import Path

import accounts

app = _harness.app
client = TestClient(app)

ROOT = {"x-admin-password": _harness.ROOT_TOKEN}
SUPPORT = {"x-admin-password": _harness.SUPPORT_TOKEN}
BASIC = {"x-admin-password": _harness.BASIC_TOKEN}

ok = lambda label: print(f"  PASS  {label}")
fails = []


def check(cond, label):
    if cond:
        ok(label)
    else:
        fails.append(label)
        print(f"  FAIL  {label}")


print("\n== password hashing ==")
rec = accounts._hash_password("correct horse battery staple")
check(rec["algo"] == "scrypt", "scrypt is the KDF")
check("correct horse battery staple" not in str(rec),
      "the plaintext password is nowhere in the stored record")
check(accounts._check_password("correct horse battery staple", {"password": rec}),
      "the right password verifies")
check(not accounts._check_password("wrong", {"password": rec}),
      "the wrong password does not verify")
check(accounts._hash_password("same")["salt"] != accounts._hash_password("same")["salt"],
      "two hashes of the same password use different salts")

print("\n== password policy ==")
try:
    accounts.validate_password("short")
    check(False, "a too-short password is refused")
except accounts.AccountError:
    check(True, "a too-short password is refused")

print("\n== sign-in ==")
r = client.post("/admin/login", json={"email": "support@test.local",
                                      "password": "test-password-supp"})
check(r.status_code == 200 and r.json().get("token"),
      f"correct credentials return a token ({r.status_code})")
check(r.json()["user"]["level"] == "support",
      "the response says which level signed in, so the console can render for it")

r = client.post("/admin/login", json={"email": "support@test.local",
                                      "password": "not-the-password"})
check(r.status_code == 401, f"a wrong password is a 401 ({r.status_code})")
wrong_pw_detail = r.json().get("detail")

r = client.post("/admin/login", json={"email": "nobody@test.local",
                                      "password": "test-password-supp"})
check(r.status_code == 401, f"an unknown email is a 401 ({r.status_code})")
check(r.json().get("detail") == wrong_pw_detail,
      "an unknown email and a wrong password are indistinguishable to the caller")

print("\n== session tokens ==")
check(client.get("/admin/auth/me", headers=SUPPORT).status_code == 200,
      "a valid token identifies the caller")
check(client.get("/admin/auth/me",
                 headers={"x-admin-password": "garbage"}).status_code == 401,
      "a garbage token is refused")
check(client.get("/admin/auth/me").status_code == 401,
      "no token at all is refused")

# A token signed with the wrong secret must not be accepted, or the
# signature check is decorative.
_real = accounts._session_secret
accounts._session_secret = lambda: "a-different-secret"
forged = accounts.issue_session(accounts.find_by_email("root@test.local"))
accounts._session_secret = _real
check(client.get("/admin/auth/me",
                 headers={"x-admin-password": forged}).status_code == 401,
      "a token signed with the wrong secret is refused")

print("\n== level boundaries ==")
check(client.get("/admin/sources", headers=SUPPORT).status_code == 200,
      "support reaches the admin surface")
check(client.get("/admin/sources", headers=BASIC).status_code == 403,
      "basic is refused the admin surface (403, not 401 — the session is fine)")
check(client.get("/admin/auth/me", headers=BASIC).status_code == 200,
      "basic still has a real session, so the test chat is reachable")
check(client.get("/admin/users", headers=ROOT).status_code == 200,
      "root reaches account management")
check(client.get("/admin/users", headers=SUPPORT).status_code == 403,
      "support is refused account management")
check(client.get("/admin/users", headers=BASIC).status_code == 403,
      "basic is refused account management")

print("\n== console network address ==")
r = client.get("http://10.23.45.67:9123/admin/network", headers=ROOT)
check(r.status_code == 200 and
      r.json().get("primary_url") == "http://10.23.45.67:9123",
      "an address that already reached the console is advertised as primary")

print("\n== sign-in failure wording ==")
admin_html = (Path(__file__).parent.parent / "admin.html").read_text(encoding="utf-8")
check('if (path === "/admin/login")' in admin_html and
      'That email and password were not accepted' in admin_html,
      "a login 401 reports rejected credentials rather than an expired session")

print("\n== account management ==")
r = client.post("/admin/users", headers=ROOT,
                json={"email": "new@test.local", "password": "a-good-password",
                      "level": "support", "name": "New Person"})
check(r.status_code == 200, f"root can create an account ({r.status_code})")
new_id = r.json()["user"]["id"] if r.status_code == 200 else ""
_body = r.json().get("user", {})
check(not any(k in _body for k in ("password", "pw_hash", "salt", "hash"))
      and "a-good-password" not in str(_body),
      "the created account is returned without any password material")

r = client.post("/admin/users", headers=ROOT,
                json={"email": "new@test.local", "password": "a-good-password",
                      "level": "support"})
check(r.status_code == 400, f"a duplicate email is refused ({r.status_code})")

r = client.post("/admin/users", headers=ROOT,
                json={"email": "x@test.local", "password": "short",
                      "level": "support"})
check(r.status_code == 400, f"a weak password is refused at creation ({r.status_code})")

r = client.post("/admin/users", headers=ROOT,
                json={"email": "y@test.local", "password": "a-good-password",
                      "level": "wizard"})
check(r.status_code == 400, f"an unknown level is refused ({r.status_code})")

r = client.post("/admin/users", headers=SUPPORT,
                json={"email": "z@test.local", "password": "a-good-password",
                      "level": "root"})
check(r.status_code == 403,
      f"support cannot create an account, root or otherwise ({r.status_code})")

print("\n== self-enrolment requests ==")
r = client.post("/admin/auth/request",
                json={"email": "applicant@test.local",
                      "password": "applicant-password", "name": "Applicant"})
check(r.status_code == 200 and r.json().get("submitted") is True,
      "a visitor can submit account details for approval")
r2 = client.post("/admin/auth/request",
                 json={"email": "applicant@test.local",
                       "password": "different-password", "name": "Impostor"})
check(r2.status_code == 200 and len(accounts.list_access_requests()) == 1,
      "a repeat submission neither duplicates nor replaces a pending request")
raw_requests = accounts._load_requests()
check("applicant-password" not in str(raw_requests) and
      "different-password" not in str(raw_requests),
      "pending requests never store a plaintext password")
r = client.get("/admin/users", headers=ROOT)
request_rows = r.json().get("requests", [])
check(len(request_rows) == 1 and request_rows[0]["email"] == "applicant@test.local"
      and "password" not in request_rows[0],
      "root sees the pending request without password material")
request_id = request_rows[0]["id"]
check(client.post(f"/admin/account-requests/{request_id}/approve",
                  headers=SUPPORT, json={"level": "basic"}).status_code == 403,
      "support cannot approve account requests")
r = client.post(f"/admin/account-requests/{request_id}/approve",
                headers=ROOT, json={"level": "basic"})
check(r.status_code == 200 and r.json()["user"]["level"] == "basic",
      "root can approve a request at the chosen access level")
check(not accounts.list_access_requests(),
      "approval removes the pending request")
check(client.post("/admin/login", json={"email": "applicant@test.local",
                                        "password": "applicant-password"}).status_code == 200,
      "the approved applicant can sign in with the password they chose")

r = client.post("/admin/auth/request",
                json={"email": "declined@test.local",
                      "password": "declined-password"})
decline_id = accounts.list_access_requests()[0]["id"]
check(client.delete(f"/admin/account-requests/{decline_id}",
                    headers=ROOT).status_code == 200 and
      not accounts.list_access_requests(),
      "root can decline and remove a request")

r = client.post(f"/admin/users/{new_id}/level", headers=ROOT,
                json={"level": "basic"})
check(r.status_code == 200 and r.json()["user"]["level"] == "basic",
      "root can change a level")
r = client.post(f"/admin/users/{new_id}/level", headers=SUPPORT,
                json={"level": "root"})
check(r.status_code == 403, "support cannot change a level")

print("\n== the last root cannot be locked out ==")
root_rec = accounts.find_by_email("root@test.local")
r = client.post(f"/admin/users/{root_rec['id']}/level", headers=ROOT,
                json={"level": "support"})
check(r.status_code == 400,
      f"the only root cannot demote itself ({r.status_code})")
r = client.post(f"/admin/users/{root_rec['id']}/disabled", headers=ROOT,
                json={"disabled": True})
check(r.status_code == 400, f"the only root cannot be disabled ({r.status_code})")
r = client.delete(f"/admin/users/{root_rec['id']}", headers=ROOT)
check(r.status_code == 400, f"the only root cannot be deleted ({r.status_code})")

print("\n== disabling revokes live sessions ==")
victim = accounts.create_user("victim@test.local", "a-good-password",
                              "support", created_by="test",
                              must_change_password=False)
victim_token = accounts.issue_session(accounts.find_by_id(victim["id"]))
VICTIM = {"x-admin-password": victim_token}
check(client.get("/admin/sources", headers=VICTIM).status_code == 200,
      "the new support account works before being disabled")
client.post(f"/admin/users/{victim['id']}/disabled", headers=ROOT,
            json={"disabled": True})
check(client.get("/admin/sources", headers=VICTIM).status_code == 401,
      "its existing token stops working the moment it is disabled")
r = client.post("/admin/login", json={"email": "victim@test.local",
                                      "password": "a-good-password"})
check(r.status_code == 401, "a disabled account cannot sign in either")

print("\n== changing a password revokes live sessions ==")
mover = accounts.create_user("mover@test.local", "a-good-password",
                             "support", created_by="test",
                             must_change_password=False)
mover_token = accounts.issue_session(accounts.find_by_id(mover["id"]))
MOVER = {"x-admin-password": mover_token}
r = client.post("/admin/auth/password", headers=MOVER,
                json={"current_password": "a-good-password",
                      "new_password": "a-different-password"})
check(r.status_code == 200 and r.json().get("token"),
      "changing your own password hands back a fresh token")
check(client.get("/admin/sources", headers=MOVER).status_code == 401,
      "the token held before the change is now dead")
check(client.get("/admin/sources",
                 headers={"x-admin-password": r.json()["token"]}).status_code == 200,
      "the freshly-issued token works")
r = client.post("/admin/auth/password",
                headers={"x-admin-password": accounts.issue_session(
                    accounts.find_by_email("mover@test.local"))},
                json={"current_password": "wrong-current",
                      "new_password": "another-password"})
check(r.status_code == 401,
      f"changing a password requires the current one ({r.status_code})")

print("\n== bootstrap is first-run only ==")
r = client.post("/admin/auth/bootstrap",
                json={"email": "sneaky@test.local", "password": "a-good-password"})
check(r.status_code == 400,
      f"bootstrap refuses once accounts exist, so it is not a back door ({r.status_code})")
r = client.get("/admin/auth/state")
check(r.status_code == 200 and r.json()["initialised"] is True,
      "the unauthenticated state endpoint reports this install as set up")

print("\n== email domain restriction ==")
accounts.ALLOWED_EMAIL_DOMAIN = "innovative-technology.com"
try:
    accounts.create_user("someone@gmail.com", "a-good-password", "basic")
    check(False, "an outside-domain email is refused when a domain is set")
except accounts.AccountError:
    check(True, "an outside-domain email is refused when a domain is set")
accounts.ALLOWED_EMAIL_DOMAIN = ""

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
