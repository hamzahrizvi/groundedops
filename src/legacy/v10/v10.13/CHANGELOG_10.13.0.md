# GroundedOps — internal 10.13.0 (frontend FAQ generation, RHS drawer, refresh fix)

GO_v2.0 line. Base groundedops/. Files:
  src/main.py                                  (+ /faq/generate, /admin/source_sample)
  src/frontend/src/api.js                      (browser-side generation)
  src/frontend/src/components/AdminPanel.jsx   (Generate FAQ button + refresh fix)
  src/frontend/src/App.jsx                     (RHS handle arrows)
  src/frontend/src/styles.css                  (RHS centered 1.5x drawer)
Rebuild both: docker compose up -d --build

## 1. FAQ generation now runs in the BROWSER — no backend key needed
This removes the whole backend-key problem. In Admin Control > FAQ:
choose a document, pick a provider (DeepSeek/OpenAI/Claude), click
"Generate FAQ". The BROWSER calls that provider's API directly using the
key you already set in Settings (same key Online chat uses), gets the
questions, and stores them. The server only persists them — it needs no
API key at all.
New endpoints: POST /faq/generate (store), GET /admin/source_sample
(gives the browser the doc text to generate from). Both admin-gated.
You can regenerate anytime; it replaces that doc's questions.

## 2. FAQ drawer moved to the RIGHT, centered, 1.5x
No longer left/overlay. A themed handle sits mid-height on the right
edge; click to slide out a 480px panel (was 320), vertically centered,
matching the app theme (accent headings, surface bg). Click again / ✕ to
close. Width: change the 480px values in styles.css (.faq-dock transform,
.faq-drawer-panel width, .faq-handle right) together.

## 3. Post-ingest "still untagged" — refresh fix
The doc WAS being tagged (your diagnose.py confirmed biometrics/mini);
the panel just re-read the list before the async tag committed. Added a
700ms settle before refresh so a freshly ingested doc shows its
assignment immediately. If it ever still shows untagged, the Assign
dropdown fixes it in place (and now so does Generate FAQ regardless).

## Why this fixes your FAQ saga for good
FAQ kept being empty because doc2query needed a backend provider/key that
was never set. Now generation is decoupled from ingest and runs
browser-side with your existing key — so FAQ works even with zero backend
keys, on demand, per document, re-runnable. Ingest-time doc2query still
works too if you DO set a backend key, but it's no longer required.

## Verify
1. Set a DeepSeek (or OpenAI/Claude) key in Settings if not already.
2. Admin Control > FAQ > choose the Mini doc > provider DeepSeek >
   Generate FAQ -> questions appear -> write an answer -> Save.
3. Open a MyCheckr Mini chat > FAQ handle on the RIGHT > questions show.
4. (Optional) diagnose.py still shows the store; /faq returns entries.
