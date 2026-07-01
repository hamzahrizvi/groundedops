# GroundedOps v6.4

Reliability and answer-quality fixes for the retrieval/generation path. All
changes are covered by the offline test suite (`python run_tests.py`), which
passes with 0 failures and 5 honest skips (live-only dependencies).

## Highlights

- **DeepSeek fallback actually reaches DeepSeek.** The fallback chain no longer
  burns its whole time budget retrying a slow local model before handing off.
- **Follow-up questions get a real clarification** instead of a flat rejection.
- **Vague-but-in-domain questions are asked to be more specific** instead of
  being treated as out-of-domain.
- **Chat-template boilerplate is no longer mistaken for a grounded answer.**

## Fixes

### Fallback chain no longer double-retries the same model
`generate_with_fallback` previously called `safe_generate` for each chain entry,
which retried the *same* model twice before advancing. For the `reasoning` /
`accurate` chains (`[mistral, deepseek]`), a slow or timing-out mistral could
consume up to ~180s on mistral alone — hitting typical request timeouts before
DeepSeek was ever attempted. Each chain entry now gets exactly one attempt, so
the chain advances to DeepSeek immediately on a local failure.

Files: `llm.py`. Tests: `tests/test_llm.py` (mock-based, no live models needed).

### Follow-up clarification vs. out-of-domain rejection
The "no relevant content" branch treated a genuine in-context follow-up
(e.g. "is there anything else? I checked the above and they're fine") the same
as a completely out-of-domain query ("what is the capital of France") — both got
the identical flat "I could not find that in the knowledge base." Follow-ups now
receive a clarifying question that references the prior topic; standalone
out-of-domain queries are unaffected.

Files: `main.py`, `text_utils.py` (`is_followup_turn`).

### Vague-but-in-domain queries ask which device/product
Standalone queries that clearly concern the domain but omit the specific
device/product (e.g. "explain why device registration might fail") now prompt
for which device rather than flatly rejecting. Queries with no domain vocabulary
still get a clean rejection.

Files: `main.py`, `text_utils.py` (`has_domain_vocabulary`).

### Chat-template leak no longer scored as "grounded"
Local models occasionally emit their default chat-template system message
("…a chat between a curious user and an artificial intelligence assistant…").
Because that text makes no concrete factual claim, the NLI grounding check
scored it as grounded (~0.93). A deterministic `is_template_leak` check now runs
before grounding and force-flags such output.

Files: `main.py`, `text_utils.py` (`is_template_leak`).

## Changed files

- `main.py`
- `text_utils.py`
- `llm.py`
- `tests/test_regression_bugs.py`
- `tests/test_llm.py` (new)

## Upgrade notes

No configuration or dependency changes. Restart the API process fully after
deploying — do not rely on `--reload` to pick up the changes.

## Known limitations (carried into v6.5)

- A flagged template-leak/failed answer was still *displayed* to the user even
  when flagged. Fixed in v6.5.
- The DeepSeek path was still unverified end-to-end (no forced-DeepSeek test).
  Addressed in v6.5.
- Local reasoning-model latency (~90s for mistral) is hardware-bound and
  unchanged.
