"""Every route needs a session unless it is named public here (D-20261008-16).

The admin guard is a call inside each handler (_require_admin and friends),
not a router dependency, so a route that forgets the call is open to every
LAN address. /delete_source, /source_chunks, /upload and /upload/status were
open that way, and so was all of routes_settings.py, /reset (which wipes the
whole index) included. Nothing looked at the route table, so nobody noticed.

This file walks the route table. A new route either answers 401/403 without
a session or is added to PUBLIC below with the reason it is public.
"""
import inspect
import re

import _harness
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import main
import routes_documents

# A handler that crashes is reported as its status, naming the route.
client = TestClient(_harness.app, raise_server_exceptions=False)
SUPPORT = {"x-admin-password": _harness.SUPPORT_TOKEN}
BASIC = {"x-admin-password": _harness.BASIC_TOKEN}

fails = []


def check(cond, label):
    if cond:
        print(f"  PASS  {label}")
    else:
        fails.append(label)
        print(f"  FAIL  {label}")


# (method, path) reachable without a session, each for a stated reason.
PUBLIC = {
    # probes and the pages that carry their own sign-in
    ("GET", "/health"), ("GET", "/status"),     # /status: sign-in page says "still starting"
    ("GET", "/"), ("GET", "/admin"), ("GET", "/favicon.ico"), ("GET", "/stream-test"),
    # the chat itself
    ("POST", "/query"), ("POST", "/query/stream"),
    ("GET", "/faq"),                            # "Public read" (routes_faq.py)
    ("GET", "/catalog"),                        # pre-chat product picker
    # citations open from an answer; token-gated in main.py when proxied
    ("GET", "/source_file/{filename}"), ("GET", "/figure/{source}/{name}"),
    # signing in, before there is a session to send
    ("POST", "/admin/login"), ("GET", "/admin/auth/state"),
    ("POST", "/admin/auth/bootstrap"), ("POST", "/admin/auth/request"),
    ("POST", "/admin/auth/email-link"), ("POST", "/admin/auth/email-login"),
    ("POST", "/admin/auth/reset"), ("POST", "/admin/auth/sso/finish"),
    ("GET", "/admin/auth/sso/{provider}/start"),
    ("GET", "/admin/auth/sso/{provider}/callback"),
}
# The widget's own API: public by design, mirrors main._PUBLIC_PREFIXES.
PUBLIC_PREFIXES = ("/widget/",)
GUARD_CALL = re.compile(r"_require_(admin|root|session)\(")


def walk(routes):
    """Every APIRoute, through included routers (FastAPI 0.141 nests them)."""
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
            continue
        sub = getattr(r, "original_router", None)
        if sub is not None:
            yield from walk(sub.routes)


ROUTES = list(walk(_harness.app.routes))
TABLE = {(m, r.path): r for r in ROUTES for m in r.methods}

print("\n== the walk sees the whole app ==")
check(len(ROUTES) >= 100,
      f"route walk found {len(ROUTES)} routes (>= 100, so it is not vacuous)")
for key in sorted(PUBLIC):
    check(key in TABLE, f"PUBLIC entry still exists: {key}")

print("\n== no session, no service ==")
for (method, path), route in sorted(TABLE.items()):
    if (method, path) in PUBLIC or path.startswith(PUBLIC_PREFIXES):
        continue
    url = re.sub(r"\{[^}]+\}", "x", path)
    status = client.request(method, url, json={}).status_code
    if status in (401, 403):
        continue
    if status == 422:
        # FastAPI validated the body/query before the in-handler guard ran;
        # settle for the guard being there in the source.
        src = inspect.getsource(route.endpoint)
        check(GUARD_CALL.search(src),
              f"{method} {path}: 422 before the guard, and the handler calls one")
        continue
    check(False, f"{method} {path} answered {status} with no session")

print("\n== the four from the work order, with valid bodies ==")
calls = {"delete_source": 0, "ingest_file": 0}


def _rec(name, ret):
    def f(*a, **k):
        calls[name] += 1
        return ret
    return f


routes_documents.delete_source = _rec("delete_source", {"deleted": 0})
routes_documents.ingest_file = _rec("ingest_file", 0)
# The harness stub of clear_memory requires a session id; the real one does not.
routes_documents.clear_memory = lambda *a: None
main.APP_STATE["ready"] = True


def upload(headers):
    return client.post("/upload", headers=headers,
                       files={"file": ("guard-test.txt", b"hello", "text/plain")})


for label, headers, want in (("no session", {}, 401),
                             ("basic account", BASIC, 403)):
    jobs = len(routes_documents._INGEST_JOBS)
    r = client.post("/delete_source", headers=headers, json={"source": "x"})
    check(r.status_code == want, f"{label}: /delete_source {r.status_code} == {want}")
    r = client.post("/source_chunks", headers=headers, json={"chunk_ids": ["a"]})
    check(r.status_code == want, f"{label}: /source_chunks {r.status_code} == {want}")
    r = upload(headers)
    check(r.status_code == want, f"{label}: /upload {r.status_code} == {want}")
    check(calls == {"delete_source": 0, "ingest_file": 0},
          f"{label}: the index was not touched ({calls})")
    check(len(routes_documents._INGEST_JOBS) == jobs,
          f"{label}: no ingest job was started")

r = client.post("/delete_source", headers=SUPPORT, json={"source": "x"})
check(r.status_code == 200 and calls["delete_source"] == 1,
      f"support: /delete_source {r.status_code}, delete called once")
r = client.post("/source_chunks", headers=SUPPORT, json={"chunk_ids": ["a"]})
check(r.status_code == 200, f"support: /source_chunks {r.status_code}")
r = upload(SUPPORT)
check(r.status_code == 200, f"support: /upload {r.status_code}")
job = r.json().get("job_id") if r.status_code == 200 else "missing"
for label, headers, want in (("no session", {}, 401), ("basic account", BASIC, 403),
                             ("support", SUPPORT, 200)):
    r = client.get(f"/upload/status/{job}", headers=headers)
    check(r.status_code == want,
          f"{label}: /upload/status/<job> {r.status_code} == {want}")

print("\n== the body is not read before the sign-in check (D-20261010-02) ==")
import starlette.formparsers as _fp

parses = {"multipart": 0, "form": 0}
_real_mp, _real_form = _fp.MultiPartParser.parse, _fp.FormParser.parse


async def _count_mp(self):
    parses["multipart"] += 1          # this parser writes the upload to a temp file
    return await _real_mp(self)


async def _count_form(self):
    parses["form"] += 1
    return await _real_form(self)


_fp.MultiPartParser.parse, _fp.FormParser.parse = _count_mp, _count_form
FILE = {"file": ("guard-test.txt", b"x" * 4096, "text/plain")}
for label, path, headers, want in (
        ("no session", "/upload", {}, 401),
        ("basic account", "/upload", BASIC, 403),
        ("no session", "/admin/faq/import", {}, 401),
        ("no session", "/admin/backup/import", {}, 401),
        ("support account", "/admin/backup/import", SUPPORT, 403),
        ("support account", "/admin/backup/inspect", SUPPORT, 403)):
    before = dict(parses)
    r = client.post(path, headers=headers, files=FILE)
    check(r.status_code == want, f"{label}: {path} {r.status_code} == {want}")
    check(parses == before, f"{label}: {path} body never parsed, nothing spooled to disk")
before = dict(parses)
r = client.post("/upload", data={"a": "b"})          # not multipart: the path alone decides
check(r.status_code == 401 and parses == before,
      f"no session, url-encoded: /upload {r.status_code} == 401, body never parsed")
r = upload(SUPPORT)
check(r.status_code == 200 and parses["multipart"] == before["multipart"] + 1,
      f"support: /upload {r.status_code}, body parsed once (the counter sees parsing)")
_fp.MultiPartParser.parse, _fp.FormParser.parse = _real_mp, _real_form

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
