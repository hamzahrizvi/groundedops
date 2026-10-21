# Decision-model trial 2: does `choice` beat `clarify.classify_unscoped`?

Measured 2026-10-20. This was measurement only, and nothing in the live app
changed. The trial script, `tools/decision_model_unscoped_trial.py`, was
deleted after the trial. It is still in commit 3172542: it runs in three
phases, `current`, `decide` and `report`, and its docstring explains each.
This follows the first trial, `docs/decision-model-trial.md`, which said this
gate was the one worth trying next.

## Verdict

**Reject.** Keep `classify_unscoped` as it is. Strands Decider 2B loses on
every measure except cash cost:

| | current call | decision model |
|---|---|---|
| PRODUCT or OTHER right | **60/60** | 51/60 |
| competitor devices let through as "ours" | **0/20** | 7/20 |
| median time per call | **1.4 s** | 26.9 s |

The richer pick, which names one product or says ask or other, gets
**23/60** right. That is barely above "always ask" (20/60).

## 1. What the gate decides

`src/clarify.py:174` does **not** pick a product. The brief for this trial
said it did, but it doesn't. It answers one two-way question, PRODUCT or
OTHER: "is this unscoped message about one of the catalogue's products at
all?" It runs only for an unscoped, non-follow-up question whose best
rerank score is under `AMBIGUOUS_CEILING` (`main.py:3175`).

- **PRODUCT:** a nothing-found question gets a "which product?" menu built
  by `candidate_products`.
- **OTHER:** the question is refused, so a printer "jam" is never answered
  from a validator's jam page.

The product itself is chosen elsewhere: by retrieval, by
`near_top_products` and by `described_product`.

That gives two comparisons:

1. **`about`, like for like.** The same PRODUCT and OTHER descriptions as
   the live prompt, asked as a 2-option `choice`.
2. **`which`, the extra job.** A 13-option `choice` over the 11 catalogue
   products plus `ask` ("could be several, ask which") and `other`. This is
   the decision model's trained strength, and it would let the gate skip the
   menu when only one product fits. Today's call has no equivalent, so it
   cannot be compared directly.

Both questions were asked in one `eng.ask` pass over the same state: the
catalogue's category and product names plus the customer message. That is
the same product knowledge the live prompt gives the model.

## 2. Set-up

- **Labelled data:** `src/eval_cases_unscoped_probe.json` and
  `src/eval_cases_unscoped_holdout.json`, 30 cases each. That makes 60
  cases: 20 clarify, 20 answered and 20 rejected.
  - Right `about` verdict: PRODUCT for clarify and answered, OTHER for
    rejected.
  - Right `which` pick: `ask` for clarify, `other` for rejected, and for an
    answered case the product named in its `reference`. The ICU API cases
    accept either MyCheckr, because both cameras share those documents.
- **Current call:** run live, through the same function `main.query()`
  calls, on the real `catalog_config.json`.
  - The default role is `openai`/gpt-5-mini, and it returned **429 (no
    credit)** on every call. Every live verdict therefore came from the
    backup, **DeepSeek V4 Flash** with thinking on (`llm.judging()`). That
    is what production serves today.
  - A second run with `PROVIDER_ROLE_DEFAULT=deepseek` removes the failed
    OpenAI hop.
  - gpt-5-mini itself was **not measured**.
- **Decision model:** `StrandsAgents/strands-decider-2B-hobson-v21`, made
  of Qwen3.5-2B-Base plus a LoRA adapter, run with `strands-decider` 0.2.0
  in process on the laptop CPU (i7-1265U, no GPU).
  - It ran in a throwaway venv and HF cache that were deleted afterwards.
  - No question text left the machine, as D-20261015-11 requires.
  - The `flash-linear-attention` kernel was not installed, so transformers
    used its slower reference PyTorch path. A tuned CPU build could be
    somewhat faster, but not by the 20× needed to catch the current call.

Twenty cases per class is small. For 7/20 the 95% Wilson interval is
roughly 18–57%.

## 3. Results

### PRODUCT vs OTHER (the decision `classify_unscoped` makes)

| gate | accuracy | clarify → PRODUCT | answered → PRODUCT | rejected → OTHER | p50 | p95 | $/1k calls |
|---|---|---|---|---|---|---|---|
| **classify_unscoped, live as served** (429 then DeepSeek) | **1.000** | 20/20 | 20/20 | 20/20 | 1.43 s | 2.87 s | $0.064 |
| **classify_unscoped, DeepSeek only** | **1.000** | 20/20 | 20/20 | 20/20 | **1.35 s** | **2.40 s** | **$0.062** |
| classify_unscoped, gpt-5-mini (the configured default) | not run (429) | — | — | — | — | — | ≈$0.07 est.\* |
| decision model `about`, p ≥ 0.5 | 0.850 | 20/20 | 18/20 | **13/20** | 26.9 s | 39.5 s | $0 cash, local CPU† |
| decision model `which` ≠ other | 0.717 | 20/20 | 20/20 | **3/20** | (same pass) | | |

\* About 212 input tokens at $0.25/M, plus a few output tokens at
reasoning-effort minimal, at $2.00/M. This is an estimate, not a
measurement.

DeepSeek used 212 tokens in and 115–122 out on average. Most of the output
is thinking tokens, priced at V4 Flash's cache-miss list price of
$0.14/$0.28 per M (a secondary source, CloudZero, 2026-08).

† Mean input was 609 tokens per call. The model load took 38 s. Self-hosted,
there is no cash cost per call, but it needs hardware we don't have to get
anywhere near the current latency. For reference only, since D-20261015-11
rules it out: a hosted 2B at about $0.09/M input would cost about $0.055 per
1k calls, which is the same as DeepSeek today.

**What the decision model gets wrong is the expensive case.** All 7 of its
rejected→PRODUCT errors are another vendor's payment or vending hardware:

- Ingenico, Coinco, JCM (twice), MEI and Verifone;
- a receipt printer "jamming".

Those are exactly the messages the gate exists to refuse, because they share
our manuals' vocabulary. The two answered→OTHER errors are ICU API and
MyConnect app questions ("phone app", "network API" read as IT).

**Separation and calibration of `about`.** AUC is 0.87 and the
probabilities run from 0.15 to 0.96. ECE on max(p, 1−p) is 0.102, with
5 calls at ≥ 0.9 and all 5 right. Even so, no threshold rescues it. Making it
refuse all 20 rejected cases needs p ≥ 0.884, and that cut then refuses
30 of the 40 in-scope questions. The worst rejected case (MEI, p = 0.88)
sits above the best in-scope floor (0.43).

### Product pick (`which`, 13 options; no current equivalent)

| measure | decision model |
|---|---|
| top-1 right (product, ask or other) | **23/60 (0.383)** |
| clarify → ask | 15/20 |
| clarify → **a specific product** (wrongly picks when it should ask) | **5/20**, all SMART Coin System |
| answered → the right product | **5/20** (9 wrong product, 6 ask) |
| rejected → other | 3/20 (14 ask, 3 a product) |
| top-choice probability (p_max) | 0.12–0.58. It never commits. |
| package `confidence`, (N·p_max−1)/(N−1) | 0.05–0.54; **0 calls at ≥ 0.9**, so "accuracy at ≥ 0.9" is undefined |
| ECE on p_max / Brier | 0.166 / 0.250 |

For comparison, the current gate never picks a product, so it scores 0/20
on "picks when it should ask" by construction. Retrieval picks the product
for answered questions afterwards. A "which" that is right on 5 of 20
answered questions, and puts a sixth of vague ones on the SMART Coin System,
would be worse than the menu it would replace.

Its errors lean on surface words:

- "coin" goes to `sku_scs`, which catches MEI (a competitor's coin mech),
  "won't take my money" and both beep or flash questions;
- "USB" or "validator" goes to `nv9usb`;
- all 10 answered biometrics cases (MyCheckr, Mini, MyConnect, ICU API:
  "screenless age camera", "hub and relays") are missed. Its 5 right picks
  are all coins or note validators.

## 4. Recommendation

- **`clarify.classify_unscoped`: reject the decision model.** The current
  call is 60/60, 20× faster, and costs about $0.06 per 1k calls. The model
  misses a third of the competitor-device messages it exists to stop, and
  on CPU it would add about 27 s to an already slow path.
- **Product pick before the menu: no.** 5/20 on the product and p_max never
  above 0.58 is not a signal to act on. `described_product` already covers
  the narrow case where retrieval has candidates and one is described.
- **Strands Decider 2B as a general option: stop trialling it here.** This
  was its home ground, a choice question that is a real LLM call today, and
  it lost on accuracy as well as latency. Two trials gave the same picture:
  its ranking is good (AUC 0.87–0.91), but uncalibrated on our traffic,
  ~27 s per call on our hardware, and blind on our competitor-device
  negatives. Revisit only with a GPU host inside the company **and** a
  fine-tune or calibration on a few hundred of our own labels.
- **Worth a follow-up, unrelated to the model:** the configured default
  provider (OpenAI) returns 429 on every call, so this gate silently runs on
  the DeepSeek backup. It is correct (60/60), but the default role's key or
  credit needs attention. That is an operations item, not part of this trial.
