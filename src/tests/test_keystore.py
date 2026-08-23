"""Provider API key management: the root-only console endpoints, and that
keystore.set_key/clear_key never leak a real key into a response, log
line, or the wrong .env.

_harness.py points ENV_FILE_PATH at a scratch file for the whole process,
so nothing here can touch the real src/.env.
"""
import os

import _harness
from fastapi.testclient import TestClient

import keystore

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


print("\n== masking ==")
keystore.set_key("deepseek", "sk-abcdefghijklmnop")
m = keystore.masked_key("deepseek")
check(m is not None and "abcdefghijklmnop" not in m,
      "the full key never appears in its own masked form")
check(m.startswith("sk-a") and m.endswith("mnop"),
      f"masked form keeps enough to recognise the key ({m})")
check(keystore.masked_key("openai") is None,
      "an unset provider masks to None, not an empty string")
keystore.clear_key("deepseek")
check(keystore.masked_key("deepseek") is None,
      "clearing removes it from both the process and the mask")

print("\n== level boundaries ==")
r = client.get("/admin/keys", headers=ROOT)
check(r.status_code == 200, f"root reaches the keys list ({r.status_code})")
check(all("value" not in p and "key_value" not in p for p in r.json()["providers"]),
      "the list response contains masked previews only, never a real key")
check(client.get("/admin/keys", headers=SUPPORT).status_code == 403,
      "support cannot see the keys list, even though it can see /admin/providers")
check(client.get("/admin/keys", headers=BASIC).status_code == 403,
      "basic cannot see the keys list")

r = client.post("/admin/keys/deepseek", headers=SUPPORT, json={"value": "sk-nope"})
check(r.status_code == 403, f"support cannot set a key ({r.status_code})")
check(not keystore.has_key("deepseek"),
      "the refused attempt did not set the key anyway")

print("\n== setting and clearing (root) ==")
r = client.post("/admin/keys/deepseek", headers=ROOT, json={"value": "sk-real-key-value"})
check(r.status_code == 200, f"root can set a key ({r.status_code})")
check("sk-real-key-value" not in str(r.json()),
      "the save response echoes a mask, not the key just sent")
check(keystore.get_key("deepseek") == "sk-real-key-value",
      "the key is actually usable immediately, no restart")
check(os.getenv("DEEPSEEK_API_KEY") == "sk-real-key-value",
      "os.environ was updated in the same call")

r = client.post("/admin/keys/deepseek", headers=ROOT, json={"value": "   "})
check(r.status_code == 400, f"a blank key is refused ({r.status_code})")
check(keystore.has_key("deepseek"),
      "the refused blank did not wipe the key that was already set")

r = client.post("/admin/keys/not-a-real-provider", headers=ROOT, json={"value": "x"})
check(r.status_code == 404, f"an unknown provider is a 404 ({r.status_code})")

r = client.delete("/admin/keys/deepseek", headers=ROOT)
check(r.status_code == 200 and not keystore.has_key("deepseek"),
      "root can clear a key")
check(client.delete("/admin/keys/deepseek", headers=SUPPORT).status_code == 403,
      "support cannot clear a key")

print("\n== the .env file is rewritten correctly, not appended forever ==")
env_path = keystore._env_path()
keystore.set_key("openai", "sk-one")
keystore.set_key("openai", "sk-two")
with open(env_path, encoding="utf-8") as f:
    body = f.read()
check(body.count("OPENAI_API_KEY=") == 1,
      "setting the same provider's key twice rewrites the line, not duplicates it")
check("sk-two" in body and "sk-one" not in body,
      "the file holds the latest value only")
keystore.set_key("anthropic", "sk-anthropic")
with open(env_path, encoding="utf-8") as f:
    body = f.read()
check("OPENAI_API_KEY=sk-two" in body and "ANTHROPIC_API_KEY=sk-anthropic" in body,
      "saving a second provider's key does not disturb the first's line")
keystore.clear_key("openai")
with open(env_path, encoding="utf-8") as f:
    body = f.read()
check("OPENAI_API_KEY" not in body and "ANTHROPIC_API_KEY=sk-anthropic" in body,
      "clearing one provider's key removes only its own line")

print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
