# GroundedOps v16.1

Minor: no breaking changes, nothing to migrate. Mostly the result of
finally being able to run the evaluation suite — two items that had been
recorded as blockers were not, and clearing them exposed a real bug behind
them.

## The grounding gate is measured, and it is not a risk

`GROUNDING_THRESHOLD` (0.55) decides whether a generated answer is shown or
discarded as unsupported by its sources. It had never been tested, and
`PENDING.md` carried it as the single biggest unmeasured risk in the
pipeline.

Measured with the new `sweep_grounding.py`: **at 0.55 it discards nothing.**
Every answered case scores between 0.9418 and 0.9994. The gate would have
to rise to 0.95 before it cost a single correct answer.

The sweep is cheap because `check_grounding` computes its score
*independently* of the threshold it is given — the threshold is only the
final `>=` — so one generation pass answers the question for every
candidate value at once. `--report-only` re-reports from the saved run with
no provider calls. `GROUNDING_THRESHOLD` is now env-tunable, so acting on
the result needs no code edit.

**Not claiming the gate is well-calibrated.** Every score sits above 0.94,
so nothing in the suite probes the 0.4–0.9 band where the threshold would
actually bite. What this rules out is the thing that was feared: 0.55
silently eating correct answers. It is not.

## Two "blockers" that were not

- **The DeepSeek key was never broken.** Recorded as returning 401 and
  blocking all evaluation. It returns 200 on both `/models` and
  `/chat/completions`; the note predated the `deepseek-chat` alias
  retirement and was never re-checked. It had been blocking the sweep above
  for weeks, and clearing it took one request.
- **Customer questions *were* categorised by product.** The page already
  grouped by scope and 69 of 82 questions carried one. The real problems
  were different, and are fixed below.

## Reworded questions are counted together

`_gap_key` folded case and punctuation, so only an *exact* repeat counted
twice. It could not fold "does it need wifi" and "is an internet connection
required" — so the backlog split and understated demand. On the real log
that meant six separate entries for "what is the nv9 pinout", each looking
like a one-off.

Now grouped at read time by `faq_store.cluster_gaps`, never rewriting the
stored questions: clustering is a threshold judgement, and a wrong merge
written to disk cannot be undone. Questions are only compared within one
product — the same question under two products is two backlog items, and
merging them would hide which product is underserved.

**The threshold was set by measurement, and the first guess was wrong.**
0.82 looked conservative and merged five *different* MyCheckr questions
("what hardware does it include", "how can it be mounted", "does it need an
internet connection") at 0.84–0.90: on a short question the product name
dominates the embedding, so any two questions about one product look alike.
At 0.92 the opposite starts and the protocol cluster splits. **0.90** is
shipped; every merge in the real log is then correct. 82 questions become
66 entries, and the top item reads "31 asks" instead of five unremarkable
ones.

## Filter and sort on the customer-questions page

There were no controls at all. `/faq/gaps` now takes `product` (including
`__none__` for questions asked with no product), `sort`
(demand / latest / product) and `group_similar`. The product list is built
from the questions themselves rather than the catalogue, because keys like
`nv9st` were no longer products but still had real questions filed under
them — building the filter from the catalogue would have hidden exactly the
questions that most needed finding.

## Deleting a product no longer orphans its content

`catalog.delete_product` removed the catalogue row and nothing else. Its
document chunks, reviewed answers and customer questions kept a tag
pointing at a key that no longer existed. The console builds every list
*from* the catalogue, so that content became invisible without being gone.

That is not hypothetical — it is where `nv9st` (18 questions) and
`coin_hoppers` came from, and it is why the questions page looked
uncategorised. Deletion now **requires** the caller to say what happens:
`reassign_to=<key>` or `delete_content=true`. Neither is a default; a 400
asking which you meant beats silently deleting documents *or* silently
orphaning them.

Content is dealt with before the catalogue row, so a failure leaves the
product listed and retryable rather than half-deleted. Anything shared with
a second product is never destroyed, only untagged from the one being
removed. `POST /admin/product/retag` is the repair path for keys orphaned
before this existed.

The console asks with real counts in front of the operator — "this product
holds 82 document chunks, 3 reviewed answers, 18 customer questions" —
rather than an abstract warning.

## `nv9st` cleaned up

A key that appeared in no document, no catalogue entry, nothing. 18
customer questions and 7 eval cases were filed under it; all retagged to
`nv9usb` (the NV9USB+ Range manual, which also covers the NV11+).

Measured effect: **the answerable eval suite went from 17 cases reaching
the pipeline to 22**, and the five recovered NV9 cases now score 0.997–0.999
on grounding. They had been refused before generation ever ran, so they
were testing nothing.

## The chat summary was not a summary

The widget offered "use our chat so far" to everyone, but
`/widget/draft_enquiry` only *rewrites* the conversation for signed-in
callers — guests got the raw "Visitor: … Assistant: …" transcript pasted in.
Since guest AI is off by default, in practice almost every enquiry got a
transcript dump labelled as a summary.

The option now appears only when a real summary will be produced. Otherwise
the visitor is asked to describe what they need, or to skip. The box says
which it is: a written summary, their own words, or their conversation.

## "Talk to sales" is no longer a customer question

`record_gap` filed whatever a visitor typed, so button text, one-word
replies ("no") and bare product names ("nv9usb") sat on the backlog beside
real questions — noise that cannot be curated away because it is not a
question. Filtered in `record_gap` itself, which all six callers go
through. An entry now needs a question mark, an interrogative opener, or
four content words. **Not retroactive:** existing noise entries stay until
dismissed by hand.

## CodeQL

Five rounds of the same false positive, now closed. All were HMAC-SHA256
signing a session token being read as password hashing; the real password
hashing (`hashlib.scrypt`) was never flagged. Two things worth knowing:
inline `# lgtm[...]` suppression **does not work on this repository** —
confirmed twice, with an alert staying open on the exact line the comment
sat on — so dismissal goes through the code-scanning API. And editing
either flagged line shifts line numbers and mints a *new* alert number
needing the same dismissal, which is why there were five rounds rather than
five bugs. CI is now fully green with 0 open alerts.

## What deploys

| File | Where it goes | Notes |
|---|---|---|
| `.gitignore` | replaces `.gitignore` | ignores handoff notes, sweep output, and pre-migration snapshots — all of which would otherwise reach a public repo |
| `PENDING.md` | replaces `PENDING.md` | three blockers closed, two new findings recorded |
| `_harness.py` | replaces `src/_harness.py` | neutralises `BACKUP_ALLOW_PLAINTEXT` so local `.env` cannot break tests; stubs the new `db` product operations |
| `accounts.py` | replaces `src/accounts.py` | CodeQL suppression comments removed (they do not work here); no behaviour change |
| `admin.html` | replaces `src/admin.html` | questions filter/sort + clustered display; product-delete dialog with counts and reassign |
| `catalog.py` | replaces `src/catalog.py` | `delete_product` cascades; `product_contents` for the pre-delete preview |
| `db.py` | replaces `src/db.py` | `retag_product` / `delete_by_product` / `count_by_product`; reads both `product` and `products` metadata spellings |
| `eval_cases.json` | replaces `src/eval_cases.json` | 7 cases retagged `nv9st` → `nv9usb` |
| `faq_store.py` | replaces `src/faq_store.py` | `cluster_gaps`, the noise filter, retag/delete by product, `_read_gaps`/`_write_gaps` |
| `main.py` | replaces `src/main.py` | `/faq/gaps` gains sort/filter/grouping; product delete + retag endpoints; `GROUNDING_THRESHOLD` env-tunable |
| `sweep_grounding.py` | new | measures the grounding gate; `--report-only` needs no provider calls |
| `test_gap_clustering.py` | new | leads on the cases that must NOT merge |
| `test_product_delete.py` | new | leads on the orphan cases and on refusing to default |
| `groundedops-widget.js` | replaces `src/widget/groundedops-widget.js` | chat summary gated on a real summary being possible; honest labels |

## Commits in this release

```
6e6c926 chore: keep handoff notes out of a public repo, explicitly
f176126 fix(data): clear the nv9st phantom key, retag 18 questions and 7 eval cases
d488145 feat(catalog): cascade product deletion, and stop filing noise as questions
88b650f feat(gaps): group reworded questions, add filter/sort, gate the chat summary
a5cc3dc feat(eval): measure the grounding threshold; it discards nothing at 0.55
194428a fix(security): actually remove the second dead lgtm tag
2ad9b98 fix(security): dismiss alert #114, drop the dead lgtm tags
a634d94 fix(security): dismiss the 2 CodeQL alerts via the API, not inline comments
68d10d9 fix(security): suppress two CodeQL false positives on HMAC session signing
332990c fix(test): give the harness a WIDGET_TOKEN_SECRET
```

## Verification

- `run_tests.py`: **141/141 passed**, 1 skipped (`test_ui.py`, needs
  playwright — pre-existing).
- `sweep_grounding.py` run twice against the live corpus: 17 → 22 cases
  reaching the grounding gate after the `nv9st` retag.
- Gap clustering swept across thresholds 0.82–0.96 on the real 82-question
  log; every merge at the shipped 0.90 inspected by hand.
- A backup was taken and verified readable before the `nv9st` retag, in
  case the mapping was wrong.
- CI green on PR #8, 0 open CodeQL alerts.

**Not verified:** nothing in this release has been through a browser. The
questions filter/sort, the clustered display, the product-delete dialog and
the widget's contact-form changes are all DOM-driven. No step has run
against a deployed host.

**Two findings left open** (both in `PENDING.md`): one eval case asks about
"NV9ST", a string in no document, so it fails permanently — its own
reference asserts "The NV9ST supports 12VDC", so either a document is
missing that fact or the question needs rewording. And one answer scores
**0.0051** on grounding while everything else is above 0.99 — the gate is
right to refuse it, but something upstream (retrieval, or the documents)
is wrong.
