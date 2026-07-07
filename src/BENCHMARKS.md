# Benchmarks & Evaluation

GroundedOps ships with a regression harness (`eval.py`) that gates every
change: outcome checks (answered / clarify / rejected), required and
forbidden keywords, LLM-graded correctness against reference facts, and
a baseline diff that fails the run on any regression.

The numbers below were produced against a real technical corpus — four
vendor hardware manuals plus an internal API specification (age-
verification retail devices; ~200 pages). The corpus itself is not
included in this repository (see *Bring your own corpus* in the README);
a tagged benchmark snapshot preserves the full reproducible setup.

## Final result — primary suite (local mode, fully offline)

**16/16 (100%)** — mistral-7B generation, all verification local.

| # | Behavior under test | Outcome | Retrieval | Grounding |
|---|---|---|---|---|
| 1 | Product definition | ✅ answered, graded correct | 0.9996 | 0.9983 |
| 2 | App login credentials (conflict case A) | ✅ correct pair, forbidden pair absent | 0.9987 | 0.9970 |
| 3 | API credentials (conflict case B) | ✅ correct pair, forbidden pair absent | 0.9997 | 0.9992 |
| 4 | Shouty/malformed phrasing ("...???") | ✅ answered correctly | 0.9961 | 0.9967 |
| 5 | Network requirement fact | ✅ | 0.9995 | 0.9994 |
| 6 | Cross-document contamination guard | ✅ | 0.9999 | 0.9210 |
| 7 | Hardware power fact | ✅ | 0.9986 | 0.9941 |
| 8 | Troubleshooting synthesis | ✅ graded correct | 0.7363 | 0.9991 |
| 9 | Architecture/protocol fact | ✅ | 0.9995 | 0.9989 |
| 10 | Support-process synthesis | ✅ | 0.9995 | 0.9970 |
| 11 | Ambiguous query → clarify | ✅ asked which product | 0.0155 | — |
| 12 | Ambiguous follow-up → clarify | ✅ | 0.0709 | — |
| 13 | Off-domain question → refuse | ✅ | 0.0 | — |
| 14 | **Adversarial**: domain-adjacent words, wrong product | ✅ refused (was a fail-open before the grounding-enforcement fix) | 0.1166 | — |
| 15 | Paraphrase robustness | ✅ | 0.9999 | 0.9210 |
| 16 | Pronoun follow-up ("how is **it** powered") | ✅ resolved across turns | 0.9983 | 0.9981 |

## What the harness caught along the way (the honest part)

The suite exists because it kept catching real problems:

- **Silently wrong ground truth**: the original eval accepted the wrong
  credential pair as correct; fixing the reference exposed a genuine
  retrieval-disambiguation bug (two near-identical credential blocks),
  fixed with section-breadcrumb chunk enrichment. Both directions of the
  question are now permanently guarded (cases 2–3).
- **A fail-open safety hole**: an off-domain query ("reset my Cisco
  router password") retrieved a stray policy chunk and produced a
  confident answer that scored **0.055** on grounding — and was served
  anyway. The grounding verdict is now enforced (case 14).
- **Infrastructure poisoning**: a 44-case survey collapsed to 43% purely
  from local-model timeouts cascading into fallback failures. The fix
  was capacity/timeout tuning plus a preflight health gate in the
  harness itself — a degraded backend now refuses to run rather than
  produce a misleading log.
- **Answer suppression of a CORRECT table lookup**: NLI grounding scored
  a correct numeric answer 0.028 (NLI is weak on table text); fixed with
  a narrow lexical-containment rescue that does NOT weaken case 14.

Suite progression across the tuning period: 69% (contaminated baseline)
→ 81% → 88% → 100%, with every change applied one at a time and diffed
against a locked baseline.

## Reproducing

With your own corpus: ingest documents, write cases in
`eval_cases.json` (a schema template ships in this repo), lock a
baseline (`python eval.py --update-baseline`), then gate every change
with `python eval.py`. Grading can run on a local model or DeepSeek
(`EVAL_GRADER_PROVIDER=deepseek` is ~2x faster wall-clock).
