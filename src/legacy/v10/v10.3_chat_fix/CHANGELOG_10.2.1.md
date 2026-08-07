# GroundedOps — internal 10.2.1 (conversational fixes from production screenshots)

GO_v2.0 line. Base folder groundedops/ — copy over repo root.
Files: src/main.py, src/text_utils.py. Restart backend; no re-ingest.

## The four-turn screenshot, diagnosed
T2 "tell me more about what network connections it supports" -> give-up:
  (a) "supports" lexically collides with the ITL "support" chunks (hence
  the model's advice to contact support — it was literally reading the
  support section); (b) the combined query put the OLD question first,
  letting stale terms dominate ranking.
T4 "what about the MyCheckr?" -> answered about the Mini:
  ROOT CAUSE FOUND IN SIMULATION: "what about <entity>" was NOT
  recognized as a follow-up at all (pattern only matched "what about
  that"), so no condensation fallback ever fired — the bare fragment hit
  retrieval and pulled generic Mini chunks.

## Fixes
1. text_utils: "what/how about X" (with optional and/so/ok prefix) now a
   follow-up marker. Verified: T4 combines; standalones untouched.
2. main: combined fallback is now FRAGMENT-FIRST, and when the fragment
   itself names a domain entity (entity switch), history is appended in
   parentheses so the NEW entity dominates ranking:
   "what about the MyCheckr? (can you tell me what network does MyCheckr
   mini supports?)"
3. text_utils: condense prompt gains an explicit entity-switch rule with
   few-shot examples (API-mode condensation on DeepSeek should now solve
   these BEFORE the fallback is needed).
4. text_utils: paraphrased give-ups ("I don't have the answer for that",
   "please contact support") are now recognized as refusals, so follow-up
   turns route to the clarify path instead of displaying a dead end.

## ⚠ Check one thing about T4 before judging it fixed
If that chat was PRODUCT-SCOPED to MyCheckr Mini, "what about the
MyCheckr?" cannot answer from the full manual BY DESIGN (scope excludes
it). These fixes make the query resolution correct; if the scope blocks
it, the right behavior is a clear "this chat is scoped to MyCheckr Mini"
message — tell me if that's the case and I'll add that UX response.

## Verify (repeat the exact screenshot conversation)
1. "what is a MyCheckr mini?"          -> unchanged, good
2. "can you tell me more about what network connections it supports?"
   -> should now answer USB/IMS connectivity (not "contact support")
3. "what about the MyCheckr?"          -> should now answer about the
   FULL MyCheckr (Ethernet preferred + Wi-Fi) — in an UNSCOPED chat
4. Re-run the 16-case eval (local mode) — cases 11/12/16 guard the
   clarify/follow-up paths these changes touch.
