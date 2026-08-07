# GroundedOps — internal 10.15.0 (FIX: auto-doc2query wiping curated FAQ)

GO_v2.0 line. Single file: src/ingest.py. Restart backend.

## The bug you hit
On restart / re-ingest, your hand-generated 5-6 FAQ questions vanished
and were replaced by "loads of" random ones.

Root cause: TWO things wrote to the FAQ store —
  (a) doc2query AT INGEST (automatic, per-chunk — produces dozens), and
  (b) your admin "Generate FAQ" (curated).
Both called faq_store.record_questions(), which REPLACES all entries for
a document each time. So whenever the document got (re-)ingested,
doc2query's bulk auto-questions overwrote your curated set. The per-chunk
generator is where the "loads of random questions" came from.

## Fix
Ingest no longer writes to the FAQ store at all. doc2query still builds
its RETRIEVAL entries (kind="query" vectors that improve search recall —
unchanged), but it no longer calls record_questions. The FAQ store is now
owned SOLELY by the admin "Generate FAQ" action. record_questions now has
exactly one caller: the /faq/generate endpoint (i.e. only when you click
Generate). Nothing automatic can wipe your curated FAQ again.

## Effect
- Your curated FAQ persists across restarts and re-ingests.
- Regenerating via the admin panel still intentionally replaces that
  document's FAQ (that's the point of the button).
- Ingesting a doc no longer auto-populates FAQ — you generate it
  deliberately (which is the workflow you've moved to anyway).

## Clean up the mess from the old behaviour
Your current faq_store.json has the bulk auto-generated questions in it.
After deploying:
  1. Restart backend.
  2. Admin Control > FAQ > pick the document > Generate FAQ (this
     replaces the junk with a fresh curated set) — or edit/delete the
     unwanted entries individually.
Optionally wipe faq_store.json first for a clean slate (it's just the
FAQ; retrieval is unaffected):
  docker compose exec backend sh -c "rm -f faq_store.json"

## Verify
1. Generate FAQ for a doc (5-10 Qs). 2. Restart backend. 3. FAQ still
shows exactly your generated set — no random flood.
