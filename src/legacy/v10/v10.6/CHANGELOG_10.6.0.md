# GroundedOps — internal 10.6.0 (ingest toggle UI, FAQ starter chips)

GO_v2.0 line. Base groundedops/. Files: src/main.py, src/ingest.py,
src/faq_store.py, src/frontend/src/{App.jsx,api.js,styles.css},
src/frontend/src/components/AdminPanel.jsx. Restart backend + rebuild
frontend. No re-ingest required for the toggle/chips themselves, BUT
chips only show for docs ingested AFTER 10.4+ (they need FAQ entries) and
category-chip matching needs the 10.6 category field — re-ingest a doc to
populate both.

## The three items — status was "backend done, UI/wiring missing"
Your uploaded ingest.py already had the backend for all three. What was
missing was the GUI surface. Added:

### 1. Local vs API ingest — GUI toggle (was env-only)
Admin panel now has a "Document ingestion" selector: Auto / DeepSeek /
OpenAI / Claude / Local. It sends an `ingest_provider` header with the
upload; the worker applies it for that ingest. So you can pick per-upload
from the UI instead of setting INGEST_PROVIDER in compose.
Verify: pick "Local", upload a doc, `make logs` -> "doc2query provider =
local"; pick "DeepSeek" -> "doc2query provider = deepseek".

### 2. FAQ starter chips + FAQ page (both, as requested)
When a chat starts for a product/category, the top 5 generated questions
appear as clickable chips under "What's on your mind today?" — tap one to
ask it. Chips clear once you send anything. The FAQ page (list + admin
edit) stays. faq_store now keys by product AND category, so a
category-level chat ("All of Biometrics") shows that category's
questions; a product chat shows the product's.

### 3. Attach docs to product/category
This was already working in your ingest.py (direct tagging). No change
needed; confirmed the admin upload passes category_key/product_key and
chunks are tagged. If it "wasn't visible", it was the stale-container
issue from earlier — the code is correct.

## Verify
1. Admin -> Document ingestion = DeepSeek -> upload a PDF into Biometrics
   > MyCheckr Mini -> logs show "provider = deepseek".
2. New chat -> Biometrics -> MyCheckr Mini -> starter chips appear from
   that doc's questions -> tap one -> it asks and answers.
3. FAQ rail page -> filter MyCheckr Mini -> same questions, admin-editable
   -> edit an answer -> the chip text is the question (unchanged), the
   answer feeds retrieval context.
