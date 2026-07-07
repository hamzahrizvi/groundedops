# GroundedOps — v8.4 Stability Patch

One file changed: `main.py` (drop-in, replaces v8.3's). Two fixes, both aimed
at "refuse unless clearly in-domain" for the public bot.

## Fix 1 — Grounding enforcement hole (CRITICAL)
**What:** The answer-suppression step only fired on template leaks / total
generation failures. An answer whose NLI grounding score FAILED the threshold
was flagged, logged… and still served to the user.
**Observed:** "how do I reset my Cisco router password" → retrieved a stray
password-policy chunk (from the ICU doc) at 0.73 → generated a confident
walkthrough → grounding scored it **0.055** → answer displayed anyway.
**Now:** after the DeepSeek escalation attempt, if the best available answer is
still ungrounded, the system refuses ("could not find") instead of serving it.
The last line of defense is now allowed to enforce its verdict.
**Trade-off (honest):** genuine answers that the NLI model can't verify will
now be refused instead of shown. That's the correct trade for precision-first,
but expect the occasional borderline in-domain answer to turn into a refusal.
The eval gate will show if this bites anything important.

## Fix 2 — Corpus scoping (EXCLUDED_SOURCES)
**What:** Internal-only documents are excluded from answering by default.
Configured via env var `EXCLUDED_SOURCES` (comma-separated, case-insensitive
substring match on source filenames). Default: `icu_network_api` — matches the
real ingested filename `ICU_Network_API-1.0.49.1.pdf`.
**Why:** Adding the ICU doc polluted public answers (WEEE/compliance
boilerplate bleeding into the MyCheckr definition, case 01), armed off-domain
queries (its password-policy section powered the Cisco answer, case 14), and
crowded out legitimate chunks (case 08's registration-failure answer dropped
below usable). The doc is internal; the public bot shouldn't see it.
**To run the API eval cases** (`eval_cases_api.json`) or an internal
deployment: `$env:EXCLUDED_SOURCES = ""` before starting the backend.
Explicit `source_filter` in a request also bypasses the exclusion.

## What v8.4 deliberately does NOT change
- **Retrieval gate threshold (0.35) untouched.** The data showed in-domain
  minimum (0.7363) and off-domain maximum (0.7288) just 0.0075 apart — no
  threshold separates them. Fix 1 + Fix 2 attack the same failures at layers
  where the evidence is unambiguous. A margin/concentration gate remains a
  future option if probe data shows a clean signal.
- **Mini-screen paraphrase flake (cases 06/15) and pronoun follow-up wobble
  (case 16):** known 7B-model limitations, documented, deferred to the
  stronger-generator path (DeepSeek-primary in production).

## Apply
1. Copy `main.py` over `src/main.py`. No re-ingest needed (nothing in
   ingestion changed).
2. Restart the backend.
3. `python eval.py` (16-case primary suite).
   Expected: case 14 (Cisco) → rejected; case 01 → clean definition again;
   case 08 → answers again (less competition). Cases 06/15/16 may still
   wobble — known limitations, not regressions.
4. If green: `python eval.py --update-baseline`.

## Stability checklist for "platform is stable, moving on"
- [ ] 16-case suite ≥ 14/16 with the two known-flaky cases the only misses
- [ ] Baseline locked
- [ ] Two consecutive runs agree (no provider=none flapping)
- [ ] Backend + Ollama survive a full run without timeout warnings
Then: Docker compose, doc2query, FAQ cache, OCR — in that order.
