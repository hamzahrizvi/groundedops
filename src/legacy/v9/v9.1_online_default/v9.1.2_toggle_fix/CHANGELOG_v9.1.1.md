# GroundedOps — v9.1.1

Backend: main.py, llm.py, runtime_config.py, router.py.
Frontend: App.jsx, api.js, icons.jsx, styles.css, components/Dialogs.jsx.
(User renumbered: v8.4.3 shipped as the offline full release; this line
continues as v9.x.)

## 1. Mode toggle — $ icon, Online/Offline, lit when online
Single control top-right, matching the sidebar icon set (same stroke
style, currentColor). Shows a $ icon + "Online" or "Offline"; the icon
lights up (accent color + glow) when online. Clicking opens the existing
ModeDialog for the opposite mode — popups retained, texts kept in the
established hint/section-label format.

## 2. OpenAI + Claude keys, provider choice
Settings now has an "Online API keys" section: provider selector
(DeepSeek / OpenAI / Claude) + a key field per provider. Same pattern as
before: stored only in this browser, sent with each query, never shown
again, status dots. Backend: new /settings/online_provider endpoint;
llm.py gains _call_openai and _call_anthropic; Online mode answers with
the selected provider (default models: deepseek-chat / gpt-4o-mini /
claude-sonnet-4-6 — env-overridable via ONLINE_*_MODEL).
HONEST NOTE: DeepSeek remains the escalation provider for flagged local
answers; OpenAI/Claude are used for Online-mode answering. Unifying
escalation across providers is a later change.

## 3. Persistent chats (right edge)
Vertical "Chats" tab on the right edge opens a slide-out panel listing
saved chats (title = first user message). New chat ARCHIVES the current
one instead of erasing; chats persist in localStorage across app AND
backend restarts (last 50). Click to reopen, ✕ to delete.
HONEST LIMITATION: server-side follow-up memory is per-process. A
restored chat displays fully after a backend restart, but its first
follow-up starts from fresh server context (the deterministic combined-
query fallback still uses the on-screen last question, so simple
follow-ups usually still resolve). Redis-backed memory (deployment
phase) removes this limitation.

## OCR question — answered
NO — OCR is not implemented yet in EITHER mode. Online/Offline only
changes which model ANSWERS; ingestion is identical (pypdf, digital-text
PDFs only). A scanned PDF will ingest as empty/near-empty in both modes.
OCR arrives in Phase 3 (Docker + ocrmypdf sidecar + scanned-page
detection in parsing.py) per the agreed plan.

## Apply
Stop uvicorn -> copy backend files to src/ -> start. Frontend files to
frontend/src/... -> npm run dev. No re-ingest. Suggested checks:
1. Toggle shows $ + Offline; switch to Online -> icon lights, dialog OK.
2. Settings: save an OpenAI or Claude key, select provider, ask a
   question in Online mode — answer arrives from that provider.
3. Chat, click New, confirm old chat appears in the right panel; restart
   frontend AND backend, confirm chats persist.
4. Run eval (offline mode) — expect baseline hold; provider changes
   don't touch the local path.
