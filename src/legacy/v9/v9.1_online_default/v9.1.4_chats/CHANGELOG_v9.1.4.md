# GroundedOps — v9.1.4 (full-page Chats view)

Frontend only — folder structure mirrors the repo, copy-paste into src/:
  frontend/src/App.jsx
  frontend/src/styles.css
  frontend/src/components/ChatsPage.jsx   (NEW file)

## Where chats are saved (your question, answered)
Browser localStorage, key "groundedops.chats" (DevTools -> Application
-> Local Storage -> your origin). Per-browser; survives frontend AND
backend restarts; does NOT sync across machines/browsers. Server-side
chat storage is a deployment-phase item (Redis/DB).

## What changed
The right-edge tab/panel (which wasn't discoverable) is REPLACED by a
full-page Chats view modeled on the requested design:
- "Chats" heading; Search box; "Filter by" dropdown (All / Today /
  Last 7 days); "Select chats" (checkbox mode with bulk Delete);
  prominent "New chat" button.
- Rows: chat title, "RAG chat" tag, relative time ("17 minutes ago",
  "3 days ago"). Click a row to reopen that chat. Current chat
  highlighted.
- Pressing NEW CHAT in the rail now opens this page (as requested);
  the page's own "New chat" button starts the fresh conversation.
  "Back to current chat" returns without starting one.

## Verify
1. Have a conversation -> press rail New chat -> Chats page appears
   with the previous conversation listed ("just now").
2. Click the row -> conversation restores. Search narrows the list.
3. Select chats -> tick -> Delete -> rows gone (and localStorage
   updated). 4. Restart frontend + backend -> chats still listed.
