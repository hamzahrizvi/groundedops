"""Work-account sign-in (Microsoft / Google) against faked provider endpoints.

sso.requests is replaced so no request leaves the machine: discovery returns
a fixed document, and the token endpoint returns whatever ID token the test
sets up -- which is how each validation rule is exercised.
"""
import base64
import json
import os
import time
from urllib.parse import parse_qs, urlsplit

import _harness
from fastapi.testclient import TestClient

import keystore
import sso

client = TestClient(_harness.app)
ROOT = {"x-admin-password": _harness.ROOT_TOKEN}
SUPPORT = {"x-admin-password": _harness.SUPPORT_TOKEN}
fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        fails.append(label)


TENANT_GUID = "11111111-2222-3333-4444-555555555555"
MS_ISS = f"https://login.microsoftonline.com/{TENANT_GUID}/v2.0"
DISCOVERY = {
    "microsoft": {"issuer": MS_ISS,
                  "authorization_endpoint": "https://login.example/authorize",
                  "token_endpoint": "https://login.example/token"},
    "google": {"issuer": "https://accounts.google.com",
               "authorization_endpoint": "https://google.example/auth",
               "token_endpoint": "https://google.example/token"},
}


class FakeRes:
    def __init__(self, body, status=200):
        self._b, self.status_code, self.text = body, status, json.dumps(body)

    def json(self):
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


next_claims = {}
posted = []


def fake_get(url, timeout=None, **kw):
    return FakeRes(DISCOVERY["microsoft" if "microsoftonline" in url else "google"])


def fake_post(url, timeout=None, data=None, headers=None):
    posted.append(data)
    b64 = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return FakeRes({"id_token": b64({"alg": "RS256"}) + "." + b64(next_claims) + ".sig"})


sso.requests.get = fake_get
sso.requests.post = fake_post
sso._discovery_cache.clear()
os.environ["ADMIN_PUBLIC_URL"] = "https://console.example.com"

print("\n== configuration ==")
check(client.get("/admin/sso", headers=SUPPORT).status_code == 403, "support cannot see SSO settings")
r = client.post("/admin/sso/microsoft", headers=ROOT,
                json={"client_id": "ms-client", "tenant": "common", "secret": "ms-secret-value"})
check(r.status_code == 200 and "ms-secret-value" not in r.text, "the client secret is never returned")
check(r.json()["providers"]["microsoft"]["problem"] is not None,
      "a multi-tenant Microsoft setup is refused (any organisation could sign in)")
check(client.get("/admin/auth/state").json()["sso_providers"] == [],
      "a refused provider gets no button")
client.post("/admin/sso/microsoft", headers=ROOT, json={"tenant": TENANT_GUID})
client.post("/admin/sso/google", headers=ROOT,
            json={"client_id": "g-client", "domain": "test.local", "secret": "g-secret"})
st = client.get("/admin/auth/state").json()
check({p["key"] for p in st["sso_providers"]} == {"microsoft", "google"},
      "configured providers are offered on the sign-in page")
ru = client.get("/admin/sso", headers=ROOT).json()["providers"]["microsoft"]["redirect_uri"]
check(ru == "https://console.example.com/admin/auth/sso/microsoft/callback",
      "the console shows the exact redirect URI to register")


binding = {"v": ""}


def begin(provider):
    r = client.get(f"/admin/auth/sso/{provider}/start", follow_redirects=False)
    q = parse_qs(urlsplit(r.headers["location"]).query)
    # The cookie is Secure (the console URL is https) and TestClient speaks
    # http, so carry it across by hand the way a browser would.
    ck = r.headers.get("set-cookie", "")
    binding["v"] = ck.split(sso.STATE_COOKIE + "=", 1)[1].split(";", 1)[0] if ck else ""
    return r, q


def ms_claims(q, **over):
    c = {"iss": MS_ISS, "aud": "ms-client", "exp": time.time() + 300,
         "nonce": q["nonce"][0], "tid": TENANT_GUID,
         "preferred_username": "support@test.local"}
    c.update(over)
    return c


def callback(provider, q, cookies=None):
    kw = {"follow_redirects": False}
    if cookies is None:
        cookies = {sso.STATE_COOKIE: binding["v"]}
    client.cookies.clear()
    for k, v in cookies.items():
        client.cookies.set(k, v)
    r = client.get(f"/admin/auth/sso/{provider}/callback",
                   params={"code": "the-code", "state": q["state"][0]}, **kw)
    return r.headers.get("location", "")


print("\n== Microsoft ==")
r, q = begin("microsoft")
check(r.status_code == 302 and r.headers["location"].startswith("https://login.example/authorize"),
      "start sends the browser to the provider")
check(q.get("code_challenge_method") == ["S256"] and q.get("redirect_uri") == [ru],
      "the request uses PKCE and the registered redirect URI")
check(sso.STATE_COOKIE in r.headers.get("set-cookie", "") and "httponly" in r.headers["set-cookie"].lower(),
      "the state is bound to this browser with an HttpOnly cookie")
next_claims = ms_claims(q)
loc = callback("microsoft", q)
check(loc.startswith("/admin#sso="), "a valid Microsoft sign-in hands back to the console")
check(posted[-1].get("code_verifier") and posted[-1].get("client_secret") == "ms-secret-value",
      "the code is redeemed with the secret and PKCE verifier")
handoff = loc.split("=", 1)[1]
r = client.post("/admin/auth/sso/finish", json={"token": handoff})
check(r.status_code == 200 and r.json()["user"]["email"] == "support@test.local",
      "the handoff becomes a session for the matching account")
check(client.post("/admin/auth/sso/finish", json={"token": handoff}).status_code == 400,
      "the handoff works only once")

r, q = begin("microsoft")
next_claims = ms_claims(q)
check(callback("microsoft", q, cookies={}).endswith("sso_error=expired"),
      "a callback without this browser's state cookie is refused (login CSRF)")
check(callback("microsoft", q).endswith("sso_error=expired"), "a state value works only once")

for label, over, code in [
        ("a token from another tenant is refused",
         {"iss": "https://login.microsoftonline.com/99999999-0000-0000-0000-000000000000/v2.0"}, "wrong_tenant"),
        ("a token for another app is refused", {"aud": "someone-elses-app"}, "bad_token"),
        ("a replayed nonce is refused", {"nonce": "not-ours"}, "bad_token"),
        ("an expired token is refused", {"exp": time.time() - 5}, "bad_token"),
        ("an address with no account is told so", {"preferred_username": "stranger@test.local"}, "no_account")]:
    r, q = begin("microsoft")
    next_claims = ms_claims(q, **over)
    check(callback("microsoft", q).endswith("sso_error=" + code), label)

print("\n== Google ==")
def g_claims(q, **over):
    c = {"iss": "https://accounts.google.com", "aud": "g-client", "exp": time.time() + 300,
         "nonce": q["nonce"][0], "email": "basic@test.local", "email_verified": True,
         "hd": "test.local"}
    c.update(over)
    return c

r, q = begin("google")
check(q.get("hd") == ["test.local"], "Google is asked to show only the Workspace domain")
next_claims = g_claims(q)
check(callback("google", q).startswith("/admin#sso="), "a valid Google sign-in hands back")
r, q = begin("google"); next_claims = g_claims(q, email_verified=False)
check(callback("google", q).endswith("sso_error=unverified"), "an unverified Google email is refused")
r, q = begin("google"); next_claims = g_claims(q, hd="gmail.example")
check(callback("google", q).endswith("sso_error=wrong_tenant"), "an account outside the domain is refused")

r = client.get("/admin/auth/sso/google/callback", params={"error": "access_denied"},
               follow_redirects=False)
check(r.headers["location"].endswith("sso_error=cancelled"), "pressing Cancel comes back cleanly")

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
