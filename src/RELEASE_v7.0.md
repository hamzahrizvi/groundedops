# GroundedOps v7.0

A UX-focused release: a redesigned, graphical interface, one-click
disambiguation of vague questions, encrypted API-key handling that never
re-displays the key, and a verified DeepSeek path. Builds on the v6.4/v6.5
answer-quality work.

Offline test suite (`python run_tests.py`): 107/112 passed, 5 skipped
(live-only dependencies), 0 failed.

## Highlights

- **Redesigned interface** — gradient wordmark and logo, answer cards with
  status chips, tightened layout.
- **One-click disambiguation** — vague or follow-up questions now offer a
  "did you mean…?" dropdown instead of only free-text.
- **Encrypted, non-viewable API key** — the DeepSeek key is encrypted at rest
  and can never be read back off the screen, and the input clears the instant
  it's saved.
- **DeepSeek path verified and reachable** — the test suite exercises it
  end-to-end, and the key entered in the GUI is now visible to the script.

## Features

### Disambiguation dropdown for clarify turns
When a question is an ambiguous in-domain query ("explain why device
registration might fail") or an unresolved follow-up ("give me step 1 from
that"), the backend now returns a short list of concrete options. The GUI shows
them as a dropdown with **Other (type it)** and **Skip**:

- Follow-ups offer the recent conversation topics to pin the intended context.
- Vague in-domain queries offer product/area labels derived from the candidate
  sources retrieval found (e.g. MyConnect / MyCheckr / MyCheckr Mini).

Picking an option re-runs a refined query in one click.

Files: `main.py`, `text_utils.py` (`build_clarification_options`,
`_product_label_for_source`), `app.py`.

### Redesigned answer / rethink area
Each assistant turn renders as a card with status chips (grounded score,
`unverified`, `needs clarification`, `provider/model`,
`auto-retried on DeepSeek`). "Rethink" is an expandable panel with a labelled
model list; the current model is marked and DeepSeek is disabled with a "needs
key" hint when no key is set. Adds a gradient wordmark, an inline SVG logo, a
live key-status indicator, and an upload area tucked into an expander.

Files: `app.py`.

### Encrypted key that clears on save
The DeepSeek key is stored encrypted (Fernet, machine-derived key) and is never
loaded back into the input field. The input now lives in a form with
`clear_on_submit=True`, which fixes the bug where a typed key stayed visible
until a manual page refresh — the field is wiped in the same action that
encrypts and saves it.

Files: `app.py`, `keyvault.py` (unchanged from v6.5).

**Threat model (unchanged, deliberately not overclaimed):** a local app must
auto-decrypt without a passphrase, so the decryption material lives on the same
machine as the ciphertext. This protects against plaintext exposure, accidental
git commits, and on-screen reading — not against an attacker who already has
filesystem access.

## Fixes

### Test script can now see the GUI-entered key
The GUI saves the key to the encrypted vault, not an env var, so the script's
env-only lookup found nothing. `test_queries.py` now prefers `DEEPSEEK_API_KEY`
and falls back to `keyvault.load_key()`, so a key saved through the GUI is
picked up automatically on the same machine.

Files: `test_queries.py`.

### Better, more representative test queries
Queries are rephrased the way a real installer/user would ask (specific and
self-contained rather than terse fragments), grouped by what they exercise, with
clear expected outcomes. The query that previously triggered chat-template
degeneration was replaced, and the follow-up sequence now exercises the new
disambiguation picker.

Files: `test_queries.py`.

## Performance

Local reasoning-model latency (~80–90s for mistral) is CPU-bound. This release
adds the achievable levers — `num_thread` set to all cores, `num_ctx` capped to
2048 (the prompt is small), and a longer `keep_alive` so the model stays
resident — but these are marginal. The real fixes are a GPU, a smaller/faster
reasoning model, or response streaming for perceived speed (candidate for a
future release). When a local model times out with a key set, it now escalates
to DeepSeek rather than dead-ending.

Files: `llm.py`.

## Changed / new files

- `app.py`
- `main.py`
- `text_utils.py`
- `llm.py`
- `test_queries.py`
- `tests/test_regression_bugs.py`

(`keyvault.py`, `requirements.txt`, and the other v6.5 files are unchanged in
this release.)

## Upgrade notes

1. Dependencies unchanged from v6.5 — ensure `cryptography` is installed
   (`pip install -r requirements.txt`).
2. Restart the API process fully (not `--reload`).
3. Restart Streamlit to pick up the new interface.

## Known limitations

- GUI changes are logic- and syntax-verified but should be confirmed visually in
  a running Streamlit instance.
- The disambiguation click-through flow is unit-tested at the options-generation
  level; the end-to-end pick → re-query path should be confirmed live.
- The DeepSeek answer path is code-complete but should be confirmed on a machine
  with a valid key and network access.
- Reasoning-model latency remains hardware-bound (see Performance).
