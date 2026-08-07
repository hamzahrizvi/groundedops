# GroundedOps — internal 10.9.0 (FAQ generation fix + diagnostics)

GO_v2.0 line. Base groundedops/. Files: src/ingest.py (fix) +
src/diagnose.py (new tool). Restart backend. Re-ingest a doc to generate
FAQ with the fix.

## Why no FAQ was being generated — root cause
You selected "DeepSeek API" in the ingest dropdown. _generate_questions
called _call_deepseek, which returns None when DEEPSEEK_API_KEY is not in
the BACKEND CONTAINER env. The local fallback only fired for provider
"auto", NOT for an explicitly chosen API provider — so it returned []
silently, no questions, no FAQ, and only a single INFO log line. The
admin panel then correctly reported "No FAQ questions were generated."

Second, smaller cause: the question filter discarded any line not ending
in "?", so even when generation worked, unpunctuated questions were all
dropped.

## Fixes
1. Fallback to local for ANY provider when the API returns nothing (not
   just "auto"). Choosing DeepSeek with no key now falls back to local
   mistral instead of producing zero FAQ. Logs:
   "provider 'deepseek' returned nothing ... falling back to local".
2. Loosened the question filter: accepts a trailing "?" OR a clear
   question opener (what/how/can/does/…), adding "?" if missing. Verified
   it keeps well-formed questions the model didn't punctuate.
3. Made empty-FAQ LOUD: if 0 questions are generated, the backend log now
   says why ("check INGEST_PROVIDER + its API key, or that Ollama/mistral
   is available"), instead of failing silently.

## NEW: diagnose.py — check ingest + retrieval against your live DB
Run inside the backend container (no eval corpus needed):
    docker compose exec backend python diagnose.py
    docker compose exec backend python diagnose.py "what network does MyCheckr support"
It prints: collection size + kinds, per-source category/product tags
(flags UNTAGGED), FAQ store contents, and a live retrieval with scores +
sources. This tells you exactly which stage is broken:
  - empty collection  -> nothing ingested
  - untagged sources  -> scoped chats will return "which product?"
  - empty FAQ         -> doc2query issue (provider/key or Ollama)
  - empty retrieval w/ full collection -> retrieval/scoring issue

## To actually fix YOUR instance
1. Deploy 10.9.0, restart backend.
2. Decide the ingest path:
   - Want DeepSeek FAQ generation? Put DEEPSEEK_API_KEY in the BACKEND
     service env (docker-compose, backend: environment:). Then pick
     DeepSeek in the panel.
   - Or just pick "Local" (needs Ollama + mistral pulled), or "Auto".
3. Re-ingest a doc via Admin Control > Documents.
4. Run: docker compose exec backend python diagnose.py
   Confirm FAQ entries > 0 and retrieval returns results.
5. Check backend logs for "doc2query provider = ..." to confirm which
   path ran.

## Retrieval status
The retrieval code path is intact (diagnose.py step 4 proves it live).
Your earlier "which product?" failures were the UNTAGGED-docs issue
(fixed by tagging at upload in 10.5+ / the reassign tool), not a
retrieval-logic regression. diagnose.py will confirm tags are present.
