"""Key names, low-credit alerts and the mail settings behind them.

Nothing here reaches a real provider or mail server: the balance checks and
mailer.send are replaced with fakes, and _harness.py points the .env writer
and the alert state at scratch files.
"""
import os

import _harness
from fastapi.testclient import TestClient

import credit_watch
import keystore
import mailer

app = _harness.app
client = TestClient(app)

ROOT = {"x-admin-password": _harness.ROOT_TOKEN}
SUPPORT = {"x-admin-password": _harness.SUPPORT_TOKEN}

fails = []


def check(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        fails.append(label)


for var in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_FROM", "SMTP_SECURITY",
            "SMTP_PASSWORD", "CREDIT_ALERT_EMAIL", "CREDIT_ALERT_THRESHOLD",
            "PROVIDER_NAME_OPENAI", "PROVIDER_NAME_DEEPSEEK"):
    os.environ.pop(var, None)
env_path = keystore._env_path()

print("\n== key names ==")
keystore.set_key("openai", "sk-test-openai-key-value")
keystore.set_key("deepseek", "sk-test-deepseek-key-value")
r = client.post("/admin/keys/openai/name", headers=ROOT, json={"name": "Company keys"})
check(r.status_code == 200 and r.json()["label"] == "Company keys", "root can rename a key")
keys = {p["key"]: p for p in client.get("/admin/keys", headers=ROOT).json()["providers"]}
check(keys["openai"]["label"] == "Company keys" and keys["openai"]["kind"].startswith("OpenAI"),
      "the list shows the new name and still says which API it is")
provs = {p["key"]: p["label"] for p in client.get("/admin/providers", headers=SUPPORT).json()["providers"]}
check(provs.get("openai") == "Company keys", "the Test chat picker uses the new name")
check(client.post("/admin/keys/openai/name", headers=SUPPORT,
                  json={"name": "x"}).status_code == 403, "support cannot rename a key")
check(client.post("/admin/keys/openai/name", headers=ROOT,
                  json={"name": "bad\nOPENAI_BASE_URL=http://evil"}).status_code == 400,
      "a name cannot smuggle a second setting into .env")
check(client.post("/admin/keys/openai/name", headers=ROOT,
                  json={"name": "x" * 41}).status_code == 400, "an over-long name is refused")
r = client.post("/admin/keys/openai/name", headers=ROOT, json={"name": ""})
check(r.json()["label"] == keystore.kind_label("openai"), "a blank name restores the built-in one")

print("\n== .env line-break guard ==")
check(client.post("/admin/keys/deepseek", headers=ROOT,
                  json={"value": "sk-a\nOPENAI_BASE_URL=http://evil"}).status_code == 400,
      "a key with a line break is refused")
try:
    keystore.set_model("openai", "m\nOPENAI_BASE_URL=http://evil")
    check(False, "a model name with a line break is refused")
except ValueError:
    check(True, "a model name with a line break is refused")
with open(env_path, encoding="utf-8") as f:
    check("evil" not in f.read(), "nothing was written")

print("\n== mail settings ==")
check(client.get("/admin/credits", headers=SUPPORT).status_code == 403,
      "support cannot see credit or mail settings")
r = client.post("/admin/credits/smtp", headers=ROOT, json={
    "host": "smtp.example.com", "port": "587", "security": "starttls",
    "user": "alerts@example.com", "password": "hunter2-secret"})
body = r.text
check(r.status_code == 200 and r.json()["smtp_configured"], "root can save mail settings")
check("hunter2-secret" not in body, "the SMTP password is never returned")
check(r.json()["smtp"]["password_set"], "only whether a password is set is reported")
r = client.post("/admin/credits/smtp", headers=ROOT, json={"host": "smtp2.example.com"})
check(keystore.get_smtp_password() == "hunter2-secret",
      "saving without a password keeps the saved one")
check(client.post("/admin/credits/smtp", headers=ROOT,
                  json={"security": "rot13"}).status_code == 400, "an unknown security mode is refused")

print("\n== alerts ==")
sent = []
mailer.send = lambda to, subject, body: sent.append((to, subject, body))
os.environ["CREDIT_ALERT_EMAIL"] = "root@example.com"
low = [credit_watch._result("deepseek", "low", balance=1.5, currency="USD")]
check(credit_watch.maybe_alert(low) == ["deepseek"] and len(sent) == 1,
      "a low balance emails the root admins")
check("DeepSeek" in sent[0][1] and "1.50 USD" in sent[0][2], "the email names the key and the balance")
check(credit_watch.maybe_alert(low) == [] and len(sent) == 1,
      "a second check within a day does not email again")
credit_watch.maybe_alert([credit_watch._result("deepseek", "ok", balance=50.0)])
credit_watch.maybe_alert(low)
check(len(sent) == 2, "after recovering, a new drop alerts again")
credit_watch.maybe_alert([credit_watch._result("openai", "unsupported")])
check(len(sent) == 2, "a provider with no balance API never alerts")

credit_watch._CHECKS["deepseek"] = lambda t: credit_watch._result("deepseek", "low", balance=0.5, currency="USD")
credit_watch._CHECKS["openai"] = lambda t: (_ for _ in ()).throw(ConnectionError("http://gw/key/info?k=secret"))
r = client.post("/admin/credits/check", headers=ROOT)
res = {x["provider"]: x for x in r.json()["results"]}
check(res["openai"]["status"] == "error" and "secret" not in r.text,
      "a failed check reports the error type, not the URL")

os.environ.pop("SMTP_HOST", None)
before = len(sent)
credit_watch._maybe_alert([credit_watch._result("anthropic", "empty")])
check(len(sent) == before, "no email is attempted without a mail server")

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
