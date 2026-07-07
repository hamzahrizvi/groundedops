# GroundedOps — v8.4.2 (UI-observed fixes)

Two files: `main.py`, `router.py`. Drop into `src/`, restart backend.
No re-ingest needed. Includes everything from v8.4/v8.4.1.

## Fix 1 — phi no longer answers questions (`router.py`)
"what is a mycheckr" classified as `fast` → routed to phi → incoherent /
WEEE-blended definitions, and since the fallback chain fires only on
FAILURE (not on bad answers), mistral never got a chance. Phi is now
condensation-only; `fast` answers with mistral like every other role.
The latency win never justified the quality cliff on the most common
question type a support bot receives.

## Fix 2 — lexical containment rescue for grounding (`main.py`)
v8.4's grounding enforcement suppressed a CORRECT weight answer: the
technical-data chunk (table text, shredded by PDF extraction) contained
"MyCheckr Mini: 152 g" verbatim, the model answered correctly, NLI scored
it 0.028 (NLI is unreliable on table prose), enforcement refused it.
Now: if NLI fails BUT the answer is short (≤3 sentences, ≤400 chars),
contains numbers, and EVERY number appears verbatim in the context, it
counts as grounded. Deliberately narrow — prose answers still live or die
by NLI, long synthesized answers must pass NLI proper, so the Cisco-class
enforcement is NOT weakened (unit-tested against a Cisco-shaped answer).

## Fix 3 — no more "Based solely on the provided context…" (`main.py`)
DeepSeek was parroting the prompt's own instruction as a preamble. The
prompt now explicitly forbids it. Cosmetic, but it's a public bot.

## NOT fixed here
- "tell me more about it" follow-up → could not find: pronoun/entity
  condensation gap (same class as eval case 16). Needs the condensation
  prompt to receive the last-answered entity — separate change, and I
  need the log line for that turn to confirm the failure point.
- WEEE pollution IF still present after v8.4.1's context floor: send the
  rerank scores from the sources panel for "what is a mycheckr" and the
  floor gets tuned on data.

## Eval note
Add a case guarding Fix 2 once the corpus includes technical data, e.g.:
q: "what is the weight of the MyCheckr Mini" → answered, keywords_all
["152"]. And re-run the 16-case suite: Fix 1 changes which model answers
`fast`-class questions, so expect small wording shifts on short factual
cases — content should hold or improve.
