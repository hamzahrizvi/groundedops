# Decision-model trial: does a Jev-style model beat the answerability gate?

Measured 2026-10-20. Measurement only. Nothing in the live app was changed.
Reproduce with `tools/decision_model_trial.py` (phases `chunks`, `decide`,
`report`; see its docstring).

## Verdict

**Reject it as a replacement for `answerability.classify`, and reject it as an
extra yes/no veto on answers.** The one place worth a second trial is
`clarify.classify_unscoped`, which is a multiple-choice question that costs an
LLM call today (see the last section).

Here is why, in one line each:

- `classify` is not the gate that decides "answerable or not". It calls no
  model, runs in about 0 ms, and only sorts refusals that have already
  happened. A decision model has nothing to replace there.
- As a veto on what the pipeline serves, the model catches 2 of the 3 real
  false passes, but it also blocks 12 good answers. Accuracy goes from 0.830
  to 0.770.
- It is not calibrated on our traffic: ECE is 0.218 and its probabilities sit
  between 0.19 and 0.93. Its ranking is good (AUC 0.91), so calibrating it on
  our own labels could make it usable. The model card itself says to do that.
- On this laptop's CPU it takes a median of 29 s per call. Using it inline
  would need a GPU host that the company gateway does not have today.

## 1. What `classify` actually decides

`src/answerability.py:279`. The module says so itself: *"NOTHING HERE CALLS
A MODEL."* It runs once per turn in `main.query()` (`main.py:4288`), after
the answer has been generated and checked. It gets
`refused=is_refusal(answer)`, and when that is False it returns `STATED`
straight away. Only for a turn that was already refused does it choose
between the remaining outcomes:

| outcome | decided from |
|---|---|
| STATED (capability) | the "does X work with Y" target appears in a retrieved title, heading or numbered procedure, or in a document tagged to the product |
| DOCUMENTED_ELSEWHERE | `crossrefs.deferral_for`, a held document that points to one we don't hold |
| INFERABLE | a capability question, nothing documents the target, but passages were retrieved |
| ADVISORY | regex: a recommendation word plus "my/our" |
| UNANSWERABLE | everything else |

Cost per call is $0. Latency is p50 0 ms and p95 13 ms. The real yes/no "can we answer this?" decision
is made earlier and in other places:

1. the sales router, which deflects before retrieval;
2. the generator, which emits the refusal string when its passages don't
   carry the fact;
3. the verifier (grounding).

So the like-for-like slot for a decision model is a check that asks "do these
passages answer this question?", either before generation or as a veto after
it. That is what was measured below. The `classify()` row is there for
completeness only. It consumes the pipeline's own refusal, so it is not an
independent judge.

## 2. Model chosen

**AWS Strands Decider 2B** (`StrandsAgents/strands-decider-2B-hobson-v21`).
It is Apache-2.0, built as Qwen3.5-2B-Base plus a LoRA adapter and a pointer
head, and was run with `strands-decider` 0.2.0 on CPU in a throwaway venv. No
question text left the machine.

- **Cloudflare Clef / Clef-flash** were not used. Clef-flash needs about
  41 GB of VRAM and this machine has an Intel iGPU. Clef's hosted endpoint is
  on Workers AI, which is a third party, so D-20261015-11 rules it out.
- **TypeSafe Jev** was not used. It is hosted, early-access and third-party,
  so D-20261015-11 rules it out.
- **The company LiteLLM gateway** serves only `itl-gpt-pro` and
  `itl-gpt-flash`, and it was unreachable from here today because DNS did not
  resolve.

The question was asked as one `noul` (yes/no) with criteria.

- **State:** the customer question plus the top 5 passages after
  reranking, which is the same retrieval and reranker the pipeline uses.
- **Question:** "Do these documentation passages contain the information
  needed to answer the customer's question?"
- **Criteria:** true = "the passages state the fact, figure or procedure the
  customer asks for"; false = "about something else, or do not contain what
  is asked".

## 3. Labelled set

There are 165 single-turn questions from the blind sets B4, B5, NV4000 and
B6. Their labels are the sets' own `expected` field (answered or rejected).
Questions labelled `clarify` and multi-turn follow-ups were dropped. That
leaves **143 answerable and 22 unanswerable** questions (absent features,
phantom products and off-topic questions).

The "current pipeline" is what it actually served in the recorded runs
`eval_runs/20261007_blind{4,5,nv4000}` and `20261008_blind6` (`sys_normal`,
gpt-5-mini). That means its latency and cost are measured, not estimated. A
turn counts as "said answerable" when it is not a refusal and not a sales or
clarify deflection.

With only 22 negatives, the false-pass rates are rough. For 3/22 the 95%
Wilson interval is about 5–33%.

## 4. Results

| gate | accuracy | false-pass (says answerable, isn't) | false-refuse | ECE | Brier | p50 | p95 | $/1k calls |
|---|---|---|---|---|---|---|---|---|
| **pipeline as served** (generator + verifier + router) | **0.830** | **3/22 (13.6%)** | 17.5% | n/a (binary) | n/a | 4.8 s* | 14.9 s* | $0.72* |
| `classify()` on the pipeline's refusal | 0.879 | 5/22 (22.7%) | 10.5% | n/a | n/a | 0 ms | 13 ms | $0 |
| reranker top score ≥ 0.5 (free, already computed) | 0.697 | 7/22 (31.8%) | 30.1% | 0.234 | 0.237 | — | — | $0 |
| **decision model** p ≥ 0.5, standalone | 0.861 | 6/22 (27.3%) | 11.9% | 0.218 | 0.123 | **29.0 s** | **64.0 s** | $0 local CPU† |
| pipeline **AND** decision-model veto | 0.770 | **1/22 (4.5%)** | 25.9% | — | — | +29 s | +64 s | — |

\* The pipeline's figures are for the whole answer, not just its gate.
`classify` runs inside it for free.
† Mean input was 836 tokens per call. Self-hosted, it costs no cash per
call, but it needs hardware we don't have. As a reference only (off-limits
under D-20261015-11): Clef-flash on Workers AI at the secondary-source
$0.09/M input tokens would be about $0.075 per 1k calls. The model load took
256 s.

The `classify()` row looks better than the pipeline only because sales
deflections count as STATED there (`refused=False`). It is not a better gate.

**Calibration.** The decision model's probabilities run from 0.19 to 0.93,
with deciles of 0.36, 0.50, 0.58, 0.65, 0.70, 0.73, 0.76, 0.82 and 0.86. Only
8 of 165 calls were at or above 0.9 or at or below 0.1. Those 8 were all
right, but they are too few to build a policy on. ECE 0.218 means a "0.7"
does not mean 70%.

**Separation is good:** AUC is 0.910, against 0.788 for the reranker score.
But at the cut that holds false passes to the pipeline's 3/22 (p ≥ 0.587),
the model alone refuses 31 of 143 answerable questions (21.7%). The reranker
at the same false-pass level refuses 57 (39.9%).

**On the pipeline's 3 real false passes**, all graded "wrong" by the blind
grader:

| case | what the pipeline served | decision-model p |
|---|---|---|
| B4-35 | "Hub can SMS/email on underage alert" (an invented capability) | 0.68: missed |
| B5-35 | "MyCheckr PoE? No." (should be "not documented") | 0.40: caught |
| B6-41 | "Mini PoE/battery? No." (should be "not documented") | 0.44: caught |

The two it catches are the definitive-"No"-to-an-undocumented-feature
pattern, which `mentions.py` / NOT_MENTIONED already aims at. The one that
invents a capability, which is the worse failure, gets through. The 12 good
answers the veto would block are mostly lookups and troubleshooting procedures
whose answer sits in the passages, for example B6-10 microSD, N4-01 payout
speed and B4-20 feeder light codes. That fits the model card's own warning
that `noul` "transfers poorly" to yes/no tasks unlike its training data.

## 5. Recommendation

- **`answerability.classify`: reject.** It is free, deterministic and not an
  answerability judge, so there is nothing to win.
- **Answer veto: reject for now.** It costs 12 good answers to stop 2 bad
  ones, and it is too slow on available hardware. Revisit only if both of
  these hold:
  1. it is Platt-scaled on our own labels (a few hundred, more negatives than
     these 22);
  2. it runs on a GPU host inside the company. Its AUC is the one promising
     number.
- **Try next: `clarify.classify_unscoped`** (`src/clarify.py:174`). It is a
  real LLM call today (`llm.generate_with_fallback("fast", …)` inside
  `llm.judging()`) that picks one product from the catalogue for an unscoped
  question. That is the decision model's `choice` primitive, which is what it
  is trained for. The labelled sets already exist in
  `src/eval_cases_unscoped_probe.json` and
  `src/eval_cases_unscoped_holdout.json`, with 30 cases each. Measure
  top-1 accuracy, latency against the current fast-model call, and
  calibration of the choice confidence.
- **`text_utils.is_followup_turn`: no.** It is word rules with no model call,
  so there is no cost or latency to win.
