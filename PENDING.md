# Pending — GroundedOps general product (`main`)

`main` is the general base product: one product for anyone who sells products
online and needs first-line support, tuned per customer. It carries no
customer's names, documents or rules.

The Innovative Technology (ITL) product line, its plan, its office work
orders and its marketing website live on `experimental/v17.3`. Its
`PENDING.md` is that line's plan; this file is the general product's.

Each item: what it is, why it matters, and what "done" looks like. Move an
item to **Resolved** with the commit that closed it.

## Open — general product only, not needed for ITL (v17.3)

### 1. Hosted, multi-customer version
**What.** One install serves many customers. Each customer has their own
documents, settings, FAQs and widget key, fully separated from every other
customer's.

**Why.** Today one install serves one customer (`docs/it-reviewer-sheet.md`:
"One customer per installation"). Selling to many online shops means running
many customers on shared hosting rather than one machine each. The
try-it-yourself demo (item 4) cannot exist without it.

**Waits on.** IT's AWS account. A temporary Linux server guide exists on
branch `claude/linux-server-cb4876`.

**Done when.** Two customers on one install cannot see, search, or be
answered from each other's documents, FAQs, logs or settings, proven by a
test that tries each path (retrieval, FAQ match, console lists, widget key,
logs, backups), and one customer's delete removes only that customer's data.

### 2. Per-customer tuning loop with its own spending cap
**What.** Agents test the base product against one customer's documents, add
that customer's FAQs, and tune that customer's settings.

**Rules.**
- Code fixes go to the shared base product (`main`), never into a
  per-customer fork.
- Each customer has a monthly AI-spend cap, and the loop runs on a schedule,
  not continuously.

**Why.** In the office, the tuning loop is what costs money, not the answers
(about $0.0015 per answer). A loop without a per-customer cap spends a
customer's margin on testing.

**Done when.** A customer's loop runs on its schedule, stops at its monthly
cap with a visible "stopped at cap" status, files code fixes against `main`,
and keeps that customer's FAQs and settings in that customer's own store.

### 3. A rule for when an agent may approve an FAQ without a person
**What.** Approved FAQs are served word for word with no check against the
documents, so one wrong FAQ reaches everyone who asks.

**Proposal.** An agent may approve an FAQ only if the answer passes the same
check against the documents that normal answers get, and it cites a page.
Everything else waits for a person.

**Done when.** The approve path for agents runs the answer through the
grounding check, refuses an answer without a page citation, records who (or
which agent) approved it and why, and a test shows an unsupported answer
staying in the queue for a person.

### 4. Try-it-yourself demo
**What.** A prospect creates an account, uploads up to 3 of their own
documents, and asks about 10 questions. They see cited answers, plus at least
one honest refusal on a question their documents do not cover.

**Why.** Proof on the buyer's own manual is stronger than a sample manual.
Competitors offer no-card trials. The refusal on their own content is the
centrepiece: it is what this product does that others do not.

**Notes.**
- Limits: pages per document, 3 documents, about 10 questions, an end date,
  all stated on screen.
- A terms checkbox against uploading other people's files.
- Trial data is deleted on a stated schedule.
- Reuse from Marketing's one-PDF, no-account proposal
  (`groundedops-ops/inbox/marketing/20261109-try-your-own-pdf-demo.md`):
  suggested questions drawn from the uploaded document plus one deliberately
  absent question that should be refused; answers shown with page citations;
  an "Install this on your site" step at the end; suggest lookup-style
  questions first, since multi-document and table questions are the weakest
  area. The user wants an account first and 3 documents, not one PDF without
  an account.
- Depends on item 1, the hosted version.

**Measure.** Share of sign-ups who reach a cited answer on their own document
within 10 minutes, and share who go on to install the widget. Also read the
absent question's outcome on every trial: it should be refused at least 9
times in 10.

**Done when.** The flow runs end to end on the hosted version with the
limits enforced, the deletion schedule running, and both measures recorded.

### 5. Website crawl from a sitemap
**What.** The customer gives a website address. GroundedOps reads its
sitemap, or follows links within the same domain, and indexes product, FAQ
and policy pages.

**How.** Reuse the Confluence sync's pattern (`src/confluence_sync.py`,
`src/routes_confluence.py`): re-read only new or changed pages, store each
page's kind, keep one copy per document.
- Re-crawls on a schedule and re-indexes only pages that changed.
- Respects robots.txt.
- A page limit and an include/exclude path list.
- Every answer cites the page URL.

**Why.** Almost every online seller has a website; not all have manuals. It
also makes the demo "give us your website address".

**Priority.** The next sync to build.

**Done when.** A crawl of a test site indexes its pages within the limits,
skips what robots.txt or the exclude list forbid, a second crawl re-indexes
only the changed pages, and answers cite page URLs.

### 6. SharePoint / OneDrive and Google Drive sync
**What.** The customer picks folders. Documents in them (PDF, Word, text) are
indexed and kept in step, including changes and deletions.

**How.** Same pattern as the Confluence sync and item 5.
- Use the existing Microsoft and Google sign-in apps where possible.
- Ask only for read-only access to the chosen folders.
- A document deleted at the source is removed from the index without
  touching the rest of the corpus. (On the ITL line, deleting a product with
  its content once wiped other documents' chunks from the live index; test
  this path against a copy of an index, never a live one.)

**Priority.** After the website crawl.

**Done when.** Adding, changing and deleting a file in a chosen folder shows
up in the index on the next sync, with only that file's chunks touched, and
the app holds read-only access to the chosen folders only.

## Open — found in the general test of 2026-11-11

From `docs/general-test-20261111.md` (Tesla, Microsoft, Huawei manuals). The
same code is on v17.3, so each may also apply there.

### 7. A comparison refused because the draft misread one side
**Why.** "Which has the bigger front trunk, the Model 3 or the Model Y
Performance?" retrieved both manuals' pages, but the draft said the Model 3's
figure was missing (it is on p.230). The checker rejected it, so the visitor
got a refusal. Repeated 2 of 2.
**Done when.** That question is answered with both figures (3.1 vs 4.1 cu
ft) on 3 of 3 runs, with no new wrong answers on `src/eval_cases.json`.

### 8. The copy-the-checklist shortcut picks the wrong lines
**Why.** "How do I add a second HUAWEI router wirelessly using the H
button?" was answered in 2 seconds with five loose lines from the indicator
table, copied with no model call and no check, instead of the steps on p.11.
**Done when.** The shortcut copies only a block that answers the question
(or hands over to the normal path), and that question gets the p.11 steps.

### 9. A vague question across products is answered for one of them
**Why.** "How do I reset it?" in a range with two watches and a router got
the router's reset without asking which device.
**Done when.** It asks which product, offering the three, and a test covers
a vague question whose results span several products.

### 10. A procedure answer dropped the key step
**Why.** "Make the Model Y liftgate open lower" was answered "save the
height" without saying how (hold the button under the liftgate until the
chime, or the touchscreen prompts), though p.34 was retrieved.
**Done when.** The answer includes the save step on 3 of 3 runs.

### 11. README screenshots show the ITL ranges
**Done when.** `docs/img/*` re-taken (`tools/capture_screenshots.py`) on a
neutral demo corpus whose documents may be shown publicly.

## Resolved
