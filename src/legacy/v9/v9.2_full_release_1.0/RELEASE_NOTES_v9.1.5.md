# GroundedOps v9.1.5

First public-ready release of the v9 line (offline full release was
v8.4.3, 16/16 eval baseline).

## Highlights
- **Online / Free modes** with a hard startup gate: supply an API key
  (DeepSeek / OpenAI / Claude) or set up local models explicitly —
  install check, download-with-progress, load/unload from the UI.
- **Persistent chat history** with a full-page Chats view (search,
  filter, select/delete); resumes your last conversation on open.
- **Retrieval quality**: breadcrumb chunks, doc2query, context score
  floor, conversational follow-up fallback, query normalization.
- **Answer safety**: NLI grounding enforcement (ungrounded answers are
  refused, with a lexical rescue for table lookups), corpus scoping,
  preamble stripping, per-answer response time.
- **One-command install**: install.ps1 / install.sh + start scripts.

## Eval
16/16 on the primary regression suite (local mode). Run `python eval.py`
with `GENERATION_MODE=local` to reproduce against `eval_baseline.json`.

## Known limitations
- Chats persist per-browser (localStorage), not server-side.
- Server-side follow-up memory is per-process (restarting the backend
  resets it; the deterministic fallback covers simple follow-ups).
- OCR for scanned PDFs not yet included (planned: Docker + ocrmypdf).
- Single-worker backend; not yet hardened for public internet exposure
  (rate limiting / auth split planned).
