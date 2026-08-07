# GroundedOps — internal 10.10.0 (admin FAQ tab, persistent assign, chat-side FAQ)

GO_v2.0 line. Base groundedops/. Frontend only. Files:
  src/frontend/src/App.jsx
  src/frontend/src/styles.css
  src/frontend/src/components/AdminPanel.jsx        (rewritten)
  src/frontend/src/components/ChatFaqPanel.jsx      (new)
Rebuild frontend: docker compose up -d --build frontend

## Your four issues
1. "Doc doesn't self-assign after ingest."
   The backend DOES tag chunks + attach the source at upload (verified in
   code: worker calls ingest_file with category/product AND attach_source).
   If yours landed untagged, the running backend was behind the code
   (your diagnose.py showed kind:'?' = old-build chunks). Regardless, the
   new persistent assign list (below) makes this a non-issue — you can
   assign/reassign any doc any time, not only in a popup after upload.

2. "Tab to always re-assign docs + see assignments." DONE.
   Documents tab now has a permanent "All documents — assignment" list:
   every ingested doc, its current Category › Product tag (or "untagged"),
   and an always-available Assign/Reassign dropdown. Not just untagged,
   not just right after upload.

3. "FAQ link broken; want a tab to view/edit/answer FAQ per doc." DONE.
   Replaced the transient post-ingest FAQ popup with a permanent FAQ tab:
   pick a document, see its generated questions, edit/answer/save or
   delete each. (The rail FAQ page still exists for browsing too.)

4. "Chat-side openable FAQ panel, categorised by product in general chat."
   DONE. New ChatFaqPanel: a slim "FAQ" tab on the left edge of the chat
   view. Click to slide open a panel of questions relevant to the current
   chat's scope. In a product chat -> that product's questions. In a
   category/"General" chat -> questions grouped by product. Clicking a
   question asks it.

## Important — why FAQ may STILL look empty
FAQ entries only exist for docs ingested by a build WITH doc2query AND a
working provider. Your current 512 chunks are old-build (no FAQ ever
generated). To populate FAQ you must, once:
  1. Ensure a provider works: DEEPSEEK_API_KEY in the BACKEND service env
     (docker-compose backend: environment:), OR Ollama+mistral for local.
  2. Reset the knowledge base (clear old untagged chunks).
  3. Re-ingest each doc via Admin Control > Documents into its
     category/product (provider = DeepSeek or Local).
  4. diagnose.py should then show kind:{chunk,query}, tags set, FAQ > 0.
Until a re-ingest with a working provider happens, all FAQ views
(admin tab, rail page, chat-side panel, starter chips) will be empty —
correctly, because no questions have been generated yet.

## Verify
1. Admin Control > Documents: see every doc + reassign dropdown any time.
2. Admin Control > FAQ: pick a doc, edit an answer, save.
3. Chat with a product/category selected: "FAQ" tab on the left edge ->
   opens panel; general chat groups by product.
