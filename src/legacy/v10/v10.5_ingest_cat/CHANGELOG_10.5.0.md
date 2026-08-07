# GroundedOps — internal 10.5.0 (direct category/product tagging — no filename matching)

GO_v2.0 line. Base groundedops/ — copy src/ over your src/. Restart backend.
⚠ REQUIRES re-upload of docs (see below). 3 files changed: main.py,
ingest.py, retrieval_db.py.

## The fix + redesign (your call — direct tagging, no auto-detect)
Old design guessed which product a doc belonged to by SUBSTRING-MATCHING
filenames against catalog `sources` lists. That was the bug: seed strings
like "MyCheckr_Mini" didn't match your real filenames -> scope matched
zero chunks -> empty retrieval -> "which product?" clarify (your
screenshot: retrieval_score 0, sources []).

Now: a doc is tagged with the category+product you UPLOAD it into. Chunks
carry `category` and `product` metadata set at ingest; retrieval filters
on those tags directly (exact-match Chroma `where` + BM25 meta filter).
No filenames, no guessing, no catalog `sources` lists involved in
scoping.

## What changed (for your reference, files included in full)
- ingest.py: ingest_file() takes category_key/product_key; every chunk +
  doc2query entry is tagged with them. Removed product_for_source()
  filename guessing.
- retrieval_db.py: _matches_scope() now checks explicit product/category
  tags; _dense_ranking() uses exact-match `where`; _bm25_ranking filters
  on the tags; added _invalidate_bm25_cache() for re-tagging.
- main.py: /query builds scope = {"product": ...} or {"category": ...}
  from the request; upload worker forwards category/product into ingest;
  NEW admin endpoints /admin/sources (list ingested docs + tags) and
  /admin/reassign_source (re-tag an existing doc's chunks in place).

## Handling existing docs (you chose BOTH)
1. Re-upload now: wipe DB (Reset), then upload each doc into its category/
   product via the admin panel — clean tags from the start.
2. Or re-assign without re-ingest: POST /admin/reassign_source
   {source, category_key, product_key} re-tags an already-ingested doc's
   chunks in place (no re-embed). GET /admin/sources lists what's ingested
   and its current tags. (Frontend button for this is a small follow-up;
   the endpoints work now via the API.)

## "General" category docs — now works the way you wanted
Upload a doc into a category's general product (or any product); a
CATEGORY-level chat sees ALL docs tagged with that category, so a shared
API doc added under Biometrics is available to every Biometrics chat
without adding it to each product. Because tags are explicit, "upload to
this category" just means product_key = the category's general product.

## Verify
1. Reset KB. Upload the Mini manual via admin into Biometrics > MyCheckr
   Mini. Ask "what is a MyCheckr mini?" in that scoped chat -> ANSWERS
   (was the failing case).
2. Upload a shared doc into Biometrics general. Category-level Biometrics
   chat retrieves from it; a MyCheckr-Mini-only chat does not (unless the
   doc is under Mini too).
3. GET /admin/sources shows each doc's category/product tags.
