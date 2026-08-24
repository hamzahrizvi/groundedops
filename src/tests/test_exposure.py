"""The surface guard, the admin allowlist, and the bootstrap land-grab.

These are the assertions that matter for putting this on a reachable host.
The default must stay closed: a proxied request must not reach the admin
surface, and an unauthenticated "create the root account" endpoint must not
be usable from outside without a deliberate token.

TestClient reports its socket as the literal string "testclient", which
_harness adds to PRIVATE_NETWORKS. Sending any proxy header is therefore
what makes a request look external here, exactly as it does in production
behind nginx or Cloudflare.
"""
import _harness
from fastapi.testclient import TestClient

import main

app = _harness.app
client = TestClient(app)

ADMIN = {"x-admin-password": _harness.SUPPORT_TOKEN}
# What a reverse proxy adds. Its presence is the "this came from outside"
# signal the guard keys on.
EXTERNAL = {"x-forwarded-for": "203.0.113.7"}

ok = lambda label: print(f"  PASS  {label}")
fails = []


def check(cond, label):
    if cond:
        ok(label)
    else:
        fails.append(label)
        print(f"  FAIL  {label}")


print("\n== the default is closed ==")
check(main.ADMIN_ALLOWED_IPS == [],
      "ADMIN_ALLOWED_IPS is empty unless an operator sets it")

r = client.get("/admin/sources", headers={**ADMIN, **EXTERNAL})
check(r.status_code == 404,
      f"a proxied request to the admin surface is 404, even with a valid "
      f"session ({r.status_code})")

r = client.get("/admin/sources", headers=ADMIN)
check(r.status_code == 200,
      f"the same request without proxy headers is served ({r.status_code})")

print("\n== the widget surface stays public ==")
check(client.get("/widget/config", headers=EXTERNAL).status_code == 200,
      "/widget/config is reachable from outside, as the widget requires")
check(client.get("/health", headers=EXTERNAL).status_code == 200,
      "/health is reachable from outside, so a load balancer can probe it")

print("\n== the allowlist opens it deliberately ==")
main.ADMIN_ALLOWED_IPS = ["203.0.113."]
try:
    r = client.get("/admin/sources", headers={**ADMIN, **EXTERNAL})
    check(r.status_code == 200,
          f"an allowlisted external address reaches the admin surface ({r.status_code})")

    # The allowlist is in FRONT of auth, not instead of it.
    r = client.get("/admin/sources", headers=EXTERNAL)
    check(r.status_code == 401,
          f"but it still has to sign in — 401, not 200 ({r.status_code})")
    r = client.get("/admin/sources",
                   headers={"x-admin-password": _harness.BASIC_TOKEN, **EXTERNAL})
    check(r.status_code == 403,
          f"and level checks still apply through it ({r.status_code})")

    # A different external address must not inherit the exemption.
    r = client.get("/admin/sources",
                   headers={**ADMIN, "x-forwarded-for": "198.51.100.9"})
    check(r.status_code == 404,
          f"a non-allowlisted address is still refused ({r.status_code})")

    # The real client is read from the proxy header, not the socket, so a
    # caller cannot get in by looking local to the proxy.
    r = client.get("/admin/sources",
                   headers={**ADMIN, "x-forwarded-for": "127.0.0.1"})
    check(r.status_code == 404,
          "claiming to be 127.0.0.1 via the proxy header does not help "
          f"({r.status_code})")
finally:
    main.ADMIN_ALLOWED_IPS = []

print("\n== prefix matching is a prefix, not a substring ==")
main.ADMIN_ALLOWED_IPS = ["10.1."]
try:
    check(main._admin_ip_allowed("10.1.2.3") is True, "10.1.2.3 matches 10.1.")
    check(main._admin_ip_allowed("110.1.2.3") is False,
          "110.1.2.3 does not match 10.1. — it is not a prefix")
    check(main._admin_ip_allowed(None) is False, "a missing IP never matches")
    check(main._admin_ip_allowed("") is False, "an empty IP never matches")
finally:
    main.ADMIN_ALLOWED_IPS = []
check(main._admin_ip_allowed("10.1.2.3") is False,
      "with an empty allowlist nothing matches, whatever the address")

print("\n== bootstrap cannot be grabbed from outside ==")
# Two layers, and the order matters.
#
# OUTER: /admin/auth/bootstrap lives under /admin, so while ADMIN_ALLOWED_IPS
# is empty the surface guard 404s it along with everything else. An
# unauthenticated create-the-root-account endpoint is not reachable at all
# from the internet in the default configuration.
import os
os.environ.pop("BOOTSTRAP_TOKEN", None)
r = client.post("/admin/auth/bootstrap", headers=EXTERNAL,
                json={"email": "attacker@example.com", "password": "a-good-password"})
check(r.status_code == 404,
      f"with the allowlist empty, external bootstrap is not even reachable "
      f"({r.status_code})")

# INNER: once the allowlist is opened so the console can be used, bootstrap
# becomes reachable from those addresses. That matters because an allowlist
# is usually a whole office or VPN range, not one person -- so the token gate
# is what stops anyone inside that range claiming root on a fresh install.
main.ADMIN_ALLOWED_IPS = ["203.0.113."]
try:
    r = client.post("/admin/auth/bootstrap", headers=EXTERNAL,
                    json={"email": "attacker@example.com",
                          "password": "a-good-password"})
    check(r.status_code == 403,
          f"allowlisted but with no BOOTSTRAP_TOKEN set: refused ({r.status_code})")
    check("server" in (r.json().get("detail") or "").lower(),
          "and it says to do first-run setup on the server instead")

    os.environ["BOOTSTRAP_TOKEN"] = "the-setup-token"
    r = client.post("/admin/auth/bootstrap", headers=EXTERNAL,
                    json={"email": "attacker@example.com",
                          "password": "a-good-password"})
    check(r.status_code == 403, f"a missing token is refused ({r.status_code})")

    r = client.post("/admin/auth/bootstrap",
                    headers={**EXTERNAL, "x-bootstrap-token": "wrong-token"},
                    json={"email": "attacker@example.com",
                          "password": "a-good-password"})
    check(r.status_code == 403, f"a wrong token is refused ({r.status_code})")

    r = client.post("/admin/auth/bootstrap",
                    headers={**EXTERNAL, "x-bootstrap-token": "the-setup-token"},
                    json={"email": "someone@example.com",
                          "password": "a-good-password"})
    # 400 = "accounts already exist", i.e. the token gate passed and the
    # endpoint's own first-run check refused it. Anything else means the
    # token gate is in the wrong place.
    check(r.status_code == 400,
          f"the right token gets past the gate, then hits the real first-run "
          f"check ({r.status_code})")
finally:
    os.environ.pop("BOOTSTRAP_TOKEN", None)
    main.ADMIN_ALLOWED_IPS = []

# A local/LAN first-run must stay simple: no token, no ceremony.
r = client.post("/admin/auth/bootstrap",
                json={"email": "someone@example.com", "password": "a-good-password"})
check(r.status_code == 400,
      f"an unproxied bootstrap needs no token — it reaches the first-run "
      f"check directly ({r.status_code})")

print("\n== the sign-in page can tell what setup will accept ==")
r = client.get("/admin/auth/state")
check(r.status_code == 200, "/admin/auth/state is reachable unauthenticated")
check(r.json()["initialised"] is True, "and reports this install as set up")
check("bootstrap_token_required" in r.json(),
      "and says whether a setup token is required, so the gate can explain "
      "itself rather than offering a form that will 403")

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
