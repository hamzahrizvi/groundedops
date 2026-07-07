# GroundedOps — v9.1.3 (hard startup gate + model auto-install + toggle rework)

Backend: main.py, runtime_config.py. Frontend: App.jsx, api.js, icons.jsx,
styles.css, components/Dialogs.jsx. Apply on top of v9.1.2.

## 1. The startup gate is now HARD (fixes the cold-mistral hole)
Problem: with no key saved, the popup could be dismissed and a typed
query would hit the pipeline anyway — cold-loading mistral every time.
Now:
- The startup popup has NO close button and outside-click does nothing.
  The only exits are: save a key -> "Continue online", or "Switch to
  Free mode".
- The Free-mode dialog reached from the gate is REQUIRED: "Switch
  without loading" is removed, the confirm button stays disabled until
  at least mistral is selected ("Select mistral to continue"), and
  Cancel becomes "Back to key entry" (returns to the gate). There is no
  dismissed-with-no-decision state anywhere in the flow.
- (Toggle-initiated mode switches keep the old relaxed behavior.)

## 2. Model install check + download-with-progress
Choosing Free now runs the full path:
  /models/status  -> is Ollama up? which models are INSTALLED?
  /models/pull    -> background download of missing models via Ollama
  /models/pull_status -> polled every 1.5s; per-model progress bars
  /models/warmup  -> load into memory
The dialog shows live progress bars ("mistral ▓▓▓░ 63%") during
download, with the note that this can take several minutes. Ollama-down
and download-failure paths produce explicit messages, not silence.

## 3. Toggle rework — Rocket / Runner, 1.5x
- $ icon replaced: RocketIcon when Online, RunnerIcon when Offline —
  both drawn in the same icon base (currentColor, 1.6 stroke) as the
  sidebar set.
- Icon is 24px (1.5x the previous 16px).
- Lit state fixed: the previous version leaned on a theme CSS var that
  may not exist in your theme; colors are now explicit (#e8912d + glow),
  so the rocket WILL light when online regardless of theme variables.

## Verify
1. Fresh browser profile, no keys: popup appears, cannot be closed by X
   (none) or outside click. Type-into-chat is impossible (modal).
2. "Switch to Free mode" -> confirm disabled until mistral ticked.
3. With mistral NOT installed in Ollama (`ollama rm mistral` to test):
   confirm -> progress bar appears and advances -> loads -> Free works.
4. Toggle: rocket lit + amber glow when Online; runner (dim) when
   Offline; icons noticeably larger than before.
