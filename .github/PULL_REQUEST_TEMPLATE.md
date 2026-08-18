## What changed and why

<!-- The problem this solves or the feature this adds. Link an issue if there is one. -->

## Checklist

- [ ] `python run_tests.py` passes locally (see CI's `unit` job for what it covers)
- [ ] If this changes `main.py`/`faq_store.py`/routes: `src/tests/test_new_routes.py` still passes
- [ ] If this changes architecture, directory layout, or how the app is run: **updated `PROJECT_MAP.md`**
- [ ] If this changes user-facing setup/usage: updated `README.md` / `QUICKSTART.md` / `USER_GUIDE.md`
- [ ] No secrets (`.env`, tokens, `handover.txt`) in the diff

<!--
This checklist exists because of a real incident: a release was dropped onto a
stale base without diffing against HEAD, silently reverting committed features,
and the docs describing what changed were wrong in ways nobody caught until a
long debugging session found it. A PR that changes behavior without updating
the doc that describes that behavior is exactly the failure mode this guards
against.
-->
