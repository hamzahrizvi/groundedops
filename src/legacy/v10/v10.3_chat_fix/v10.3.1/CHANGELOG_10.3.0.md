# GroundedOps — internal 10.3.0 (categories, products, admin panel) + 10.2.2 bugfix

GO_v2.0 line. Base folder groundedops/ — copy over repo root.
Backend: src/main.py, src/ingest.py, src/retrieval_db.py + NEW src/catalog.py
Frontend: App.jsx, api.js, styles.css + NEW components/AdminPanel.jsx, Dialogs.jsx
Restart backend. NO re-ingest required for existing docs (scoping matches
on source filename); re-ingest only to refresh product tags if you rely
on the metadata path.

## 10.2.2 — the product-scoped-chat bug from your screenshot (FIXED)
Every turn in a Mini-scoped chat returned "which product do you mean?"
despite the badge showing MyCheckr Mini. Root cause: the dense retrieval
product filter over-fetched only `limit*5` candidates then post-filtered
— for vague queries the product's chunks ranked beyond that pool, so
dense returned NOTHING, the combined score fell under the gate, and the
pipeline fell back to the clarify path. Fix: when a scope is active,
dense now fetches against the whole collection before post-filtering, so
a selected product/category can never silently yield zero results.
(BM25 scoping was already correct; this was dense-only.)

## 10.3.0 — Category -> Product hierarchy
- NEW catalog.py: two-level Category -> Product tree, persisted to
  catalog_config.json (survives restarts). Seeded with Note Validators,
  Coin Hoppers, Biometrics (MyCheckr/Mini live under Biometrics).
- Pre-chat flow is now TWO STEPS: pick a category, then optionally a
  product within it (or "All of <category>"). Chat badge shows
  "Category › Product".
- Scope resolution: category = union of ALL its products' docs; product =
  just that product's docs. /query accepts category + product.
- GET /catalog feeds the picker.

## Admin panel (password-gated)
- NEW AdminPanel.jsx, reached from the category picker's "Admin" button.
- Password "admin" (override with ADMIN_PASSWORD env). Lets you:
  add/delete categories, add/delete products under a category, and
  UPLOAD DOCUMENTS directly into a product (ingested async with a
  progress bar, then tagged to that product/category for scoping).
- Docs uploaded to a product are automatically part of its category too
  (category = union of its products), as requested.

## ⚠⚠ TWO TEMPORARY AUTH SEAMS — NOT PRODUCTION-SAFE ⚠⚠
1. Admin: a single shared password ("admin"), checked via the
   X-Admin-Password header. Fine for you managing the catalog now; NOT a
   real admin auth system. Replace _require_admin with the website's
   admin identity check before public exposure.
2. User identity (from 10.2.x): resolve_user_id still trusts X-User-Id.
Both must be wired to innovative-technology.com's auth before this faces
customers. Do NOT expose admin or conversation endpoints publicly yet.

## Verify
1. New chat -> "Choose a category" -> Biometrics -> "MyCheckr Mini" ->
   ask "what network does it support" -> should ANSWER (USB/IMS), not
   "which product?" (this is the 10.2.2 fix).
2. New chat -> Biometrics -> "All of Biometrics" -> cross-product
   questions answer from any biometrics doc.
3. Category picker -> Admin -> password "admin" -> add a category, add a
   product, upload a PDF into it (progress bar) -> new product appears in
   the picker and scopes to that doc.
4. Run the 16-case eval in local mode (unscoped) — core RAG unchanged.
