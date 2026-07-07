# GroundedOps — v8.4.3 (conversational follow-up fix)

Four files: main.py, llm.py, text_utils.py, router.py (router carries the
v8.4.2 phi fix; llm.py carries v8.3 stability + this). Drop into src/,
restart backend. No re-ingest. Supersedes v8.4.2.

## The problem
Follow-ups were systematically dying in production and eval:
"how is it powered" -> retrieval 0.005 -> rejected; "can you please tell
me more about it" -> could not find; "and what about the MyCheckr
devices" -> answered about the wrong entity.

## Root causes found (three links, any one breaks the conversation)
1. condense_query ran phi on a 20s timeout — HALF its normal budget. A
   cold/busy phi silently timed out and the raw fragment went straight
   to retrieval, scoring ~0. (llm.py: timeout 20 -> 40)
2. The reference-marker patterns were over-anchored: pronouns matched
   only sentence-INITIALLY, so "how is IT powered" wasn't recognized as
   a follow-up; "tell me more" was ^-anchored, so "can you please tell
   me more about it" missed. (text_utils.py: pronouns match anywhere in
   short queries <= 8 words — long standalone questions that mention
   "it" incidentally are still excluded; "tell me more" unanchored.)
3. No net when condensation failed or under-resolved. (main.py: NEW
   deterministic fallback — if a query has reference markers, history
   exists, and condensation returned it (near) unchanged, retrieval runs
   on "LAST QUESTION — fragment" combined. Zero latency, no LLM
   dependency. Verified on all observed failing turns; standalone,
   long, and successfully-condensed queries untouched.)

## Verification (offline simulation)
COMBINED: "how is it powered" / "tell me more about it" / "and what
about the MyCheckr devices". UNTOUCHED: successfully condensed queries,
standalone questions, long questions with incidental pronouns.

## Eval
Re-run the 16-case suite: case 16 (pronoun follow-up) should now pass or
at worst clarify with the right entity. Consider adding "tell me more
about it" as a second conversational case once stable.

## Known remaining conversational gap (honest)
The combined query helps RETRIEVAL find the right chunks, but the
generation prompt still sees only the fragment as the "Question". If
answers come back topically right but oddly phrased, next step is to
pass the combined form as the question too — kept out of this change to
alter one behavior at a time.
