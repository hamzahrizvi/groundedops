# GroundedOps — v9.1.1 (api-mode bug fix + mode dialog)

Supersedes v8.6. Backend: main.py, llm.py, router.py, runtime_config.py.
Frontend: App.jsx, api.js, styles.css, components/Dialogs.jsx.

## BUG FIX (the "Attempt 1: local/phi" in api mode)
Root cause, honestly: v8.6 put the api-mode override in router.route_model,
but main.py's generation path calls generate_with_fallback(role), which
reads llm.FALLBACK_CHAIN directly — the override was never consulted.
Worse, FALLBACK_CHAIN["fast"] still STARTED WITH PHI, meaning the v8.4.2
"phi never answers" fix was incomplete: short queries ("what is a
MyCheckr?") classified fast and were phi-answered all along; the 16/16
eval didn't catch it because its phrasing classified "accurate".
FIX: enforcement moved to llm._chain_for(role) — the single choke point
generation actually uses. api mode => DeepSeek-only chain, Ollama never
touched. phi removed from ALL answering chains.

Your "Failed to fetch": the log shows "[sys] shutting down…" — the
backend restarted/stopped mid-request (likely uvicorn --reload picking up
the copied v8.6 files), killing the in-flight fetch. Aggravated by the
phi cold-load. After this fix + a deliberate restart, api mode makes no
Ollama calls at all.

## UI: proper mode dialogs (replaces confirm() + inline note)
- Toggle restyled: labeled segmented control, "⚡ Online / 🔒 Free".
- Switching to Free opens a dialog: RAM/time disclaimer + checkboxes for
  WHICH models to load (mistral ~4 GB answering; phi ~2 GB follow-up
  resolver — noted that without phi, follow-ups use the built-in
  deterministic fallback). "Switch without loading" is allowed (first
  answer pays cold-load).
- Switching to Online opens a dialog: if local models are loaded,
  checkboxes for WHICH to shut down (or keep for instant switch-back).
- /models/warmup and /models/unload now accept {models: [...]}.

## Apply
Backend files -> src/ (STOP uvicorn first if running with --reload, copy,
then start — avoids the mid-copy restart that caused Failed to fetch).
Frontend files -> frontend/src/..., npm run dev.

## Verify
1. Online mode + a query: backend log must show NO "local/phi" or
   Ollama lines. 2. Free mode via dialog with both models: answers as
   before. 3. Run eval (local mode, models loaded): should hold 16/16 —
   and note case behavior for SHORT phrasings now goes to mistral, which
   is a quality improvement over silent phi answers.
