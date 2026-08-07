# GroundedOps — internal 10.8.0 (Admin Control panel overhaul)

GO_v2.0 line. Base groundedops/. Files changed (frontend only — no
backend change, no re-ingest):
  src/frontend/src/App.jsx
  src/frontend/src/components/AdminPanel.jsx
  src/frontend/src/styles.css
Rebuild frontend only: docker compose up -d --build frontend

## All six requested changes
1. Renamed panel to "Admin Control".
2. Ingestion is now ONLY through this panel. The plain Documents dialog
   is removed; the sidebar document icon now opens Admin Control (still
   password-gated on open). There is no other upload path.
3. Tabs: Documents / Categories / Products — each section is its own tab
   instead of one long scroll.
4. Deleting a category now RE-PROMPTS for the admin password and verifies
   it before deleting (password on open AND again to delete).
5. After a document ingests, its generated FAQ questions are shown for
   review with editable answer boxes; "Save answer" stores each to the
   FAQ store (same store the FAQ page + starter chips read).
6. The unassigned-documents (re-assign) list now shows UNTAGGED docs
   ONLY. A doc you ingest into a product via the Documents tab is tagged
   immediately, so it never appears in that list — no double assignment.

## Notes / honest caveats
- Backend unchanged: this reuses existing endpoints (/admin/*, /faq,
  /upload with category/product headers). If your running backend is
  pre-10.7, deploy that first — these UI calls need those endpoints.
- Point 5 stores answers as the FAQ entry's answer (used for the FAQ
  page and as the chip's underlying Q/A). It does NOT yet short-circuit
  retrieval as a pure answer-cache — that FAQ semantic-cache is still a
  future item; today the stored answer is curation, not a retrieval
  bypass. Flagging so the behaviour isn't oversold.
- Dialogs.jsx still contains DocumentsDialog (now unused/unreferenced).
  Left in place to avoid touching other code; safe to delete later.
- catalog_config.json note from 10.7 still applies: if it predates the
  "General in every category" seed, add General via the Products tab or
  delete the config to re-seed.

## Verify
1. Click the sidebar document icon -> Admin Control opens, asks password.
2. Documents tab -> pick Biometrics > MyCheckr, provider DeepSeek, add
   the MyCheckr PDF -> progress -> FAQ review screen with editable
   answers -> Save one -> Done.
3. Categories tab -> try to delete a category -> it re-asks the password;
   wrong password refuses.
4. Products tab -> add/remove products.
5. Unassigned list shows only docs with no product tag.
