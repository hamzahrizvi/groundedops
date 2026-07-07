# GroundedOps — v9.1.5 (chats-page polish)

Frontend only. Folders mirror the repo — copy-paste into src/:
  frontend/src/App.jsx
  frontend/src/icons.jsx
  frontend/src/components/Rail.jsx

## Fixes
1. OVERLAP: the floating Rocket/Online toggle was covering the Chats
   page's "New chat" button (it sat at a higher z-index over the page).
   The toggle is now hidden while the Chats page is open — the page has
   its own New chat, and the toggle returns when you're back in a
   conversation.
2. RESUME LAST CHAT: opening the app now restores the most recent chat
   (messages + session id) instead of a blank conversation. Reopening a
   chat does NOT bump its "updated" time (a restore guard prevents the
   persist effect from rewriting the timestamp), so the relative times
   in the list stay honest.
3. RAIL BUTTON: "New chat" (+ icon) is now "Chats" with a speech-bubble
   icon (same icon base/stroke as the rest of the set). Clicking opens
   the all-chats page only — it does not start a new chat; starting one
   happens from the page's own New chat button.

## Honest note
Resuming the last chat restores the DISPLAY fully; if the backend was
restarted since, its server-side follow-up memory for that session is
empty, so the first follow-up resolves via the deterministic combined-
query fallback (usually fine for simple follow-ups). Redis-backed
sessions (deployment phase) make resume complete server-side too.

## Verify
1. Chat -> close tab -> reopen app: last conversation is on screen.
2. Rail shows "Chats" with bubble icon; clicking opens the list, does
   NOT wipe or create anything.
3. On the Chats page the rocket toggle is gone; New chat is clickable.
   Back in a conversation the toggle is visible again.
4. Reopen an old chat, restart nothing: its "x minutes ago" in the list
   is unchanged (no bump from merely opening it).
