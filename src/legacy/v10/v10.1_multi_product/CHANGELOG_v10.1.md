# GroundedOps v2.1 — per-product chats + server-side history

Repo-mirrored layout — copy `src/` over your `src/`. Backend: main.py,
ingest.py, retrieval_db.py, eval.py + NEW products.py, conversations.py.
Frontend: App.jsx, api.js, styles.css.

## ⚠ REQUIRES A RE-INGEST
Chunks are now tagged with product metadata at ingest. Existing chunks
have no product tag, so product scoping won't work until you wipe
chroma_db/ and re-ingest. Do this alongside ingesting the two new API
PDFs (ICU v1.0.50 supersedes v1.0.49 — replace, don't add both).

## 1. Per-product chats (product scoping)
- `products.py`: a many-to-many registry mapping products -> source docs.
  MyCheckr and MyCheckr Mini each include the shared MyConnect / ICU /
  Certificate docs but NOT each other's manual. Edit the defaults there
  or ship a products_config.json (env PRODUCTS_CONFIG).
- Retrieval (`retrieval_db.py`) now filters BM25 + dense by the selected
  product's sources. Verified: a MyCheckr Mini chat sees the Mini manual
  + shared docs but NOT the full MyCheckr manual.
- New chat now opens a PRODUCT PICKER first (your requested flow); the
  choice scopes every turn and shows as a badge above the conversation.
- `GET /products` feeds the picker. `/query` accepts a `product` field.
- This structurally fixes much of the old cross-product contamination
  (the MyCheckr-vs-Mini disambiguation we spent ages on).

## 2. Server-side conversation history (registered users)
- `conversations.py`: SQLite store (users/conversations/messages),
  Postgres-ready (set DATABASE_URL + swap _connect). Anonymous users are
  untouched — they keep the browser-local path and never hit this store.
- New endpoints: GET /conversations, GET /conversations/{id},
  DELETE /conversations/{id}. Registered users' turns persist and resume
  across devices.
- Verified: cross-user isolation — user B cannot read user A's chats.

## ⚠⚠ SECURITY SEAM — NOT PRODUCTION-SAFE YET ⚠⚠
Identity currently comes from an X-User-Id HEADER that the server
TRUSTS BLINDLY (conversations.resolve_user_id). Anyone could impersonate
any user. This is deliberate scaffolding so the feature works end-to-end
for testing NOW. Before customer-facing deployment, resolve_user_id()
MUST verify the signed token from innovative-technology.com's auth (the
integration you're confirming with the website manager). DO NOT expose
these endpoints publicly until then. Everything else can be built/tested
against this seam; only that one function changes.

## Eval
eval.py now forwards a `product` field, so you can add a scoping guard
case, e.g.: product="mycheckr_mini", q="does the full MyCheckr have
connection ports", expect it NOT to answer from the full manual. Add one
per product; this is the test that proves scoping doesn't leak.

## Apply
1. Copy src/ over your src/. 2. Wipe chroma_db/ and re-ingest (product
tags are written at ingest). 3. Restart backend. 4. Run eval in local
mode against baseline. 5. Test: New chat -> pick MyCheckr Mini -> ask a
full-MyCheckr-only fact -> should not answer it.
