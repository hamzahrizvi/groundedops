# GroundedOps — v9.1.2 (opens Online + startup key popup)

Three files only: runtime_config.py, frontend/src/App.jsx,
frontend/src/components/Dialogs.jsx. Apply on top of v9.1.1.

## Changes
1. DEFAULT MODE IS NOW ONLINE (api). runtime_config default flipped;
   GENERATION_MODE=local still forces offline-first (deployments, eval).
2. STARTUP KEY POPUP: on open in Online mode with no key saved for the
   selected provider, a dialog appears. It is LITERALLY the same key UI
   as the sidebar Settings — the section was extracted into a shared
   OnlineKeysSection component used by both, so they can never drift.
   Texts follow the established hint/section-label format.
3. NO-KEY PATH: "Continue online" stays disabled until a key is saved
   for the selected provider; "Switch to Free mode" opens the existing
   Free-mode dialog (RAM/time disclaimer + model checkboxes).

## IMPORTANT — eval interaction (read before next eval run)
Your 16/16 baseline was recorded in LOCAL mode. The backend now BOOTS in
api mode, so an unmodified `python eval.py` would answer via the online
provider and produce a different (not comparable) result. Before eval:
  $env:GENERATION_MODE = "local"   # then start the backend
or toggle to Free in the UI first. Recommendation: make local-mode-for-
eval part of the run ritual, or add a preflight assert later.

## Verify
1. Clear browser localStorage (or use a fresh profile) -> open app ->
   popup appears, Continue disabled.
2. Save a key -> Continue enables -> Online answers work.
3. Fresh profile again -> "Switch to Free mode" -> model-selection
   dialog appears -> Free mode answers work.
4. With a key already saved: open app -> NO popup (straight to Online).
