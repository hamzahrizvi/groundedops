# GroundedOps — internal 10.11.0 (FAQ as a proper LEFT drawer)

GO_v2.0 line. Base groundedops/. Frontend only. Files:
  src/frontend/src/App.jsx
  src/frontend/src/styles.css
  src/frontend/src/components/ChatFaqDrawer.jsx   (new; replaces
                                                   ChatFaqPanel.jsx)
Rebuild frontend. DELETE the old components/ChatFaqPanel.jsx if present.

## Change
The FAQ panel is now a LEFT-side drawer that mirrors your right-side
Details drawer exactly: docked to the left edge, full height, slides in
from the left, dark backdrop, click-outside to close. It no longer
floats over the logo/header as an edge tab.

Opening it: an "FAQ" button now sits next to the product/category badge
in the chat (the "scope bar"). Click it to slide the drawer open.
Content is unchanged: FAQ for the current scope, grouped by product in a
category/General chat, click a question to ask it.

## Still expected: empty until re-ingest
As before, the drawer shows "No FAQ for this selection yet" until you
re-ingest documents with FAQ generation working (DEEPSEEK_API_KEY in the
backend env or local Ollama+mistral). The screenshot's empty state is
correct — no questions have been generated for the current docs yet.

## Note on the flaky MyCheckr answer (from your screenshot)
The same MyCheckr chat answered "which product?" then answered correctly
— a sign the MyCheckr doc's chunks are still UNTAGGED (old-build ingest).
Run:  docker compose exec backend python diagnose.py "what is a mycheckr"
If it shows the doc untagged / kind:'?', do the reset + re-ingest cycle;
that fixes both the flaky scoped answer AND populates FAQ in one pass.
