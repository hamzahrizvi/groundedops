# GroundedOps v2.0.0

The deployable release: GroundedOps now runs anywhere Docker does.

## What's new since v1
- **One-command Docker deployment** — `docker compose up -d --build`
  brings up backend, frontend, and Ollama together (see DOCKER.md).
- **Online / Free modes**, switchable at runtime with a hard first-run
  setup gate (enter an API key, or set up local models explicitly).
- **Multi-provider Online mode** — DeepSeek, OpenAI, or Claude; keys are
  stored per-browser and sent per request.
- **Local model lifecycle in-app** — install check, download-with-
  progress, load/unload.
- **Persistent chat history** — full-page Chats view (search / filter /
  bulk delete); the app resumes your last conversation on open.
- **Answer trust** — NLI grounding enforcement (ungrounded answers are
  refused, with a lexical rescue for table lookups), corpus scoping,
  per-answer response time, preamble stripping.

## Evaluation
16/16 on the primary regression suite (local mode). Methodology and the
full debugging story: BENCHMARKS.md.

