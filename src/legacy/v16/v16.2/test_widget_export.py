"""Exporting the WordPress plugin from the console.

The failure modes worth guarding are the quiet ones:

  - a zip that still points at the example host, which presents as "the
    widget does nothing" long after the person who built it has moved on,
  - the signing secret ending up in a zip that was meant to be safe to pass
    around, which is a credential leak with no symptom at all,
  - a support-level account being able to mint that credential-bearing zip
    when only root should.

The substitution itself is regex over the real plugin source, so there is
also a check that the source still has the shape the regex expects — an
edit to the .php that silently stopped matching would ship the example
address.
"""
import io
import os
import tempfile
import zipfile

import _harness
from fastapi.testclient import TestClient

import widget_export

app = _harness.app
client = TestClient(app)
ROOT = {"x-admin-password": _harness.ROOT_TOKEN}
SUPPORT = {"x-admin-password": _harness.SUPPORT_TOKEN}
BASIC = {"x-admin-password": _harness.BASIC_TOKEN}

# What _harness sets WIDGET_TOKEN_SECRET to, and therefore what an embedded
# export must contain.
SECRET = "test-widget-token-secret-not-a-real-one"

ok = lambda label: print(f"  PASS  {label}")
fails = []


def check(cond, label):
    if cond:
        ok(label)
    else:
        fails.append(label)
        print(f"  FAIL  {label}")


def php_of(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    return z, z.read("groundedops-widget/groundedops-widget.php").decode("utf-8")


def define_line(php, const):
    """The real define(), not the one in the instructions comment block."""
    for line in php.splitlines():
        s = line.strip()
        if s.startswith(f"define('{const}',"):
            return s
    return ""


print("== the address is validated before anything is built ==")
for bad, why in [
        ("", "an empty address is refused"),
        ("support.example.com", "an address with no scheme is refused"),
        ("ftp://support.example.com", "a non-http scheme is refused"),
        ("https://support.yourcompany.com",
         "the example address from the docs is refused by name"),
        ("http://staging.example.com",
         "plain http is refused, because tokens would travel in clear")]:
    try:
        widget_export.build_plugin_zip(bad, include_secret=False)
        check(False, why)
    except widget_export.ExportError:
        check(True, why)

try:
    widget_export.build_plugin_zip("http://localhost:8000", include_secret=False)
    check(True, "but http://localhost is allowed, for a local test")
except widget_export.ExportError:
    check(False, "but http://localhost is allowed, for a local test")

check(widget_export.normalise_api_url("https://a.example.com/") ==
      "https://a.example.com", "a trailing slash is stripped")


print("\n== the zip WordPress gets ==")
data, manifest = widget_export.build_plugin_zip(
    "https://staging.example.com", include_secret=False, exported_by="t@example.com")
z, php = php_of(data)

check(sorted(z.namelist()) == ["groundedops-widget/INSTALL.txt",
                               "groundedops-widget/groundedops-widget.php"],
      "the php sits inside a folder, which is what WordPress unpacks")
check(define_line(php, "GROUNDEDOPS_API") ==
      "define('GROUNDEDOPS_API', 'https://staging.example.com');",
      "the backend address is substituted into the real define()")
check("yourcompany.com" not in define_line(php, "GROUNDEDOPS_API"),
      "and the example host is gone from it")
check("if (!defined('GROUNDEDOPS_API'))" in php,
      "the wp-config guard survives, so a constant there still wins")
check("t@example.com" in php and "GENERATED BUILD" in php,
      "the build is stamped with who exported it")
check(php.index("Plugin Name:") < php.index("GENERATED BUILD"),
      "the stamp goes after the plugin header, so WordPress still reads it")


print("\n== a zip without the secret really has no secret in it ==")
check(SECRET not in php, "the signing secret does not appear in the php")
check(SECRET not in z.read("groundedops-widget/INSTALL.txt").decode("utf-8"),
      "nor in the install notes")
check(define_line(php, "GROUNDEDOPS_SECRET") ==
      "define('GROUNDEDOPS_SECRET', ''); // set in wp-config.php",
      "the secret define is left exactly as shipped")
check(manifest["sensitive"] is False, "and the manifest does not call it sensitive")
check("CONFIDENTIAL" not in manifest["filename"],
      "so the filename carries no warning")


print("\n== a zip with the secret is marked as the credential it is ==")
data2, manifest2 = widget_export.build_plugin_zip(
    "https://staging.example.com", include_secret=True)
z2, php2 = php_of(data2)
check(define_line(php2, "GROUNDEDOPS_SECRET").startswith(
      f"define('GROUNDEDOPS_SECRET', '{SECRET}');"),
      "the server's own secret is embedded, not one typed twice")
check(manifest2["sensitive"] is True, "the manifest flags it sensitive")
check("CONFIDENTIAL" in manifest2["filename"],
      "the filename says so, because that is what a person sees")
check("TREAT THIS ZIP AS A PASSWORD" in
      z2.read("groundedops-widget/INSTALL.txt").decode("utf-8"),
      "and the install notes lead with the warning")


print("\n== who is allowed to export what ==")
body = {"api_url": "https://staging.example.com", "include_secret": False}
r = client.post("/admin/widget/export_plugin", json=body, headers=SUPPORT)
check(r.status_code == 200, f"support can export without the secret ({r.status_code})")
check(r.headers.get("x-plugin-sensitive") == "0",
      "and the response says it is not sensitive")
check(SECRET not in r.content.decode("latin-1"),
      "the bytes on the wire carry no secret")

secret_body = {"api_url": "https://staging.example.com", "include_secret": True}
r = client.post("/admin/widget/export_plugin", json=secret_body, headers=SUPPORT)
check(r.status_code == 403,
      f"support CANNOT embed the secret ({r.status_code})")

r = client.post("/admin/widget/export_plugin", json=secret_body, headers=ROOT)
check(r.status_code == 200, f"root can ({r.status_code})")
check(r.headers.get("x-plugin-sensitive") == "1",
      "and the response marks it sensitive so the console can warn")
check("CONFIDENTIAL" in r.headers.get("content-disposition", ""),
      "the download filename warns too")

r = client.post("/admin/widget/export_plugin", json=body, headers=BASIC)
check(r.status_code == 403, f"a basic account cannot export at all ({r.status_code})")

r = client.post("/admin/widget/export_plugin", json=body)
check(r.status_code == 401, f"and neither can an anonymous caller ({r.status_code})")

r = client.post("/admin/widget/export_plugin",
                json={"api_url": "https://support.yourcompany.com"}, headers=ROOT)
check(r.status_code == 400,
      f"a bad address is a 400 with a reason, not a broken zip ({r.status_code})")


print("\n== detecting a local run.cmd tunnel, without ever touching the real one ==")
# Uses an injected `root` throughout -- this test suite must never read or
# write the ACTUAL tunnel.log at the repo root, which a developer's own
# run.cmd may be writing to at the very moment the suite runs.
with tempfile.TemporaryDirectory() as tmp:
    check(widget_export.detect_tunnel_url(root=tmp) == {"url": None, "age_seconds": None},
          "an empty directory detects nothing")

    stderr_path = os.path.join(tmp, "tunnel.log.err")
    with open(stderr_path, "w") as fh:
        fh.write("2026-08-28T09:00:00Z INF |  https://plum-otter-42.trycloudflare.com  |\n")
    result = widget_export.detect_tunnel_url(root=tmp)
    check(result["url"] == "https://plum-otter-42.trycloudflare.com",
          "the URL is pulled out of tunnel.log.err")
    check(isinstance(result["age_seconds"], int) and 0 <= result["age_seconds"] < 30,
          "and its age is measured from the file's mtime")

    stdout_path = os.path.join(tmp, "tunnel.log")
    with open(stdout_path, "w") as fh:
        fh.write("https://this-one-should-lose.trycloudflare.com\n")
    check(widget_export.detect_tunnel_url(root=tmp)["url"] ==
          "https://plum-otter-42.trycloudflare.com",
          "tunnel.log.err is checked first, same order run.ps1 itself uses")

    os.remove(stderr_path)
    check(widget_export.detect_tunnel_url(root=tmp)["url"] ==
          "https://this-one-should-lose.trycloudflare.com",
          "falls back to tunnel.log when .err is gone")

    with open(stdout_path, "w") as fh:
        fh.write("https://first-attempt.trycloudflare.com\n"
                  "https://second-attempt-is-the-live-one.trycloudflare.com\n")
    check(widget_export.detect_tunnel_url(root=tmp)["url"] ==
          "https://second-attempt-is-the-live-one.trycloudflare.com",
          "the LAST match wins when a file logs more than one candidate")

    garbage_path = os.path.join(tmp, "tunnel.log.err")
    with open(garbage_path, "w") as fh:
        fh.write("cloudflared: connecting...\nno url in here at all\n")
    with open(stdout_path, "w") as fh:
        fh.write("still nothing here either\n")
    check(widget_export.detect_tunnel_url(root=tmp) == {"url": None, "age_seconds": None},
          "files with no matching URL are treated the same as no file at all")


print("\n== the endpoint, without asserting on THIS machine's live tunnel state ==")
# Whether run.cmd happens to be running on the machine that executes this
# suite is not something a test should depend on, so only shape and access
# control are checked here -- the parsing itself is proven above.
r = client.get("/admin/widget/detect_url", headers=SUPPORT)
check(r.status_code == 200, f"support can call it ({r.status_code})")
body = r.json()
check(set(body.keys()) == {"url", "age_seconds"},
      "the response always has exactly these two keys")

r = client.get("/admin/widget/detect_url", headers=ROOT)
check(r.status_code == 200, f"root can too ({r.status_code})")

r = client.get("/admin/widget/detect_url", headers=BASIC)
check(r.status_code == 403, f"a basic account cannot ({r.status_code})")

r = client.get("/admin/widget/detect_url")
check(r.status_code == 401, f"nor an anonymous caller ({r.status_code})")


print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
