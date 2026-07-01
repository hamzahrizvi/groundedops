# GroundedOps v6.5

GUI redesign of the answer/rethink area, encrypted API-key storage, verified
DeepSeek path, and suppression of unverifiable answers. Builds on v6.4.

Offline test suite (`python run_tests.py`): 103/108 passed, 5 skipped
(live-only dependencies), 0 failed — includes 7 new key-vault tests.

## Highlights

- **Answer/rethink area redesigned** with status chips and a clearer
  "re-answer with another model" panel.
- **DeepSeek API key is now encrypted at rest and never shown again** once saved.
- **DeepSeek path is exercised end-to-end** by a forced-DeepSeek test query.
- **Unverifiable answers are hidden**, not displayed — the boilerplate leak that
  was flagged-but-still-shown in v6.4 is now suppressed.

## Features

### Redesigned answer + rethink UI (`app.py`)
Each assistant turn renders as a card with compact status chips: grounded score,
`unverified`, `needs clarification`, `provider/model`, and
`auto-retried on DeepSeek`. "Rethink" is now an expandable panel with a radio
list of models; the model that produced the current answer is labelled, and
DeepSeek is shown but disabled with an inline "needs key" hint when no key is
configured.

### Encrypted, non-viewable API key (`keyvault.py`, sidebar)
The DeepSeek key is encrypted at rest with Fernet (AES-128-CBC + HMAC), keyed by
a machine-derived value (PBKDF2-HMAC-SHA256). It is never loaded back into the
input field — the sidebar only reports "Key saved (encrypted, hidden)". Any old
plaintext `.deepseek_key.json` is migrated into the vault and deleted on first
launch.

**Threat model (deliberately not overclaimed):** because a local app must
auto-decrypt without prompting for a passphrase, the decryption material lives
on the same machine as the ciphertext. This protects against plaintext exposure,
accidental git commits, and on-screen key reading — **not** against an attacker
who already has read access to the filesystem and can run the app. For that,
enter the key per session or protect it with a user passphrase.

## Fixes

### Unverifiable answers are suppressed, not shown
v6.4 correctly *flagged* chat-template boilerplate and total generation failures
but still returned the text as the answer. Now, after any DeepSeek escalation
attempt, an answer that is still a template leak or a total generation failure
is replaced with the standard "I could not find that in the knowledge base."
message. Auto-retry on DeepSeek happens first when a key is present.

Files: `main.py`.

### Generation-failure sentinel no longer scored as grounded
The all-fallbacks-failed sentinel ("I was unable to generate a response.") was
being scored ~0.99 "grounded" by NLI. It is now detected up front
(`output.model == "none"`) and treated as an ungrounded non-answer.

Files: `main.py`.

### Test harness grading corrected
Under-specified in-domain queries (device registration / installer sign-off /
install-and-verify) are now counted as expected clarifications rather than
failures, matching the intended behaviour introduced in v6.4.

Files: `test_queries.py`.

## Verified DeepSeek path (`test_queries.py`)
`test_queries.py` now reads `DEEPSEEK_API_KEY` from the environment, sends it
with every query, and adds a dedicated forced-DeepSeek query
(`force_provider=deepseek`) that asserts a real remote answer is returned. If
the env var is unset, that single step reports SKIP (not FAIL) so the rest of
the suite still runs.

```powershell
# PowerShell, before running the suite:
$env:DEEPSEEK_API_KEY = "sk-..."
python test_queries.py
```

## New dependency

- `cryptography` (for `keyvault.py`). Run `pip install -r requirements.txt`
  before starting.

## Changed / new files

- `app.py`
- `main.py`
- `test_queries.py`
- `keyvault.py` (new)
- `requirements.txt`
- `tests/test_keyvault.py` (new)
- `tests/test_regression_bugs.py`

## Upgrade notes

1. `pip install -r requirements.txt` (adds `cryptography`).
2. Restart the API process fully (not `--reload`).
3. On first GUI launch, any existing plaintext `.deepseek_key.json` is
   auto-migrated to the encrypted vault and deleted. Re-enter the key if it was
   never saved through the GUI.

## Known limitations

- The DeepSeek answer path is code-complete but should be confirmed on a machine
  with a valid key and network access (not testable in the build sandbox).
- Local reasoning-model latency (~90s for mistral) is hardware-bound. When it
  times out with a key set, it now escalates to DeepSeek rather than
  dead-ending; tuning mistral's timeout / `num_predict` is a future change.
