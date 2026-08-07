# GroundedOps — internal 10.12.0 (FAQ pull-out drawer + how to set the key)

GO_v2.0 line. Base groundedops/. Frontend files:
  src/frontend/src/App.jsx
  src/frontend/src/styles.css
  src/frontend/src/components/ChatFaqDrawer.jsx
Rebuild frontend: docker compose up -d --build frontend

## FAQ drawer is now a thin PULL-OUT
No more big overlay. A slim "FAQ" handle sits on the far-left screen edge
whenever a product/category chat is open. Click it -> a narrow (320px)
panel slides out and pushes nothing off-screen; click again (or the ✕) ->
it slides back to just the handle. No dark backdrop.
- Width: change all three "320px" values in styles.css (.faq-dock
  transform, .faq-drawer-panel width, .faq-handle left) if you want it
  narrower/wider.
- Handle height: change .faq-handle "top: 140px" if it overlaps anything.

## THE DEEPSEEK KEY — where it actually goes (this was the confusion)
Your docker-compose.yml ALREADY has, under backend > environment:
    DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY:-}
That line means "take the value from a .env file / shell var, else empty."
It was empty because nothing supplied the value. You do NOT edit
docker-compose.yml. Instead:

1. Create a file named  .env  next to docker-compose.yml (repo root:
   C:\Users\hrizvi\Downloads\Git\groundedops\.env ). See env.EXAMPLE in
   this package — rename it to .env and put your real key in it:
       DEEPSEEK_API_KEY=sk-your-real-key
2. Apply it:
       docker compose up -d backend
3. Confirm it landed:
       docker compose exec backend printenv DEEPSEEK_API_KEY
   (must now print your key, not blank)
4. Re-ingest a doc via Admin Control > Documents (provider DeepSeek/Auto).
5. Confirm FAQ generated:
       docker compose exec backend python diagnose.py
   Section 1 should now show kind: {chunk, query}; section 3 FAQ > 0.

.env is already in .gitignore, so the key won't be committed. Good.

## Status recap (from your diagnostics — all healthy except the key)
- Tagging: WORKING (docs show biometrics/mycheckr, biometrics/mini).
- Retrieval: WORKING (results returned).
- FAQ: empty ONLY because doc2query had no provider (no key). Fixing the
  key + re-ingesting is the last step.
