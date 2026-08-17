# GroundedOps v13.0 — Nocturne console, wired

The console's design refresh, with every feature it displays now actually
connected to the backend.

## What to deploy

| File | Where it goes | Notes |
|---|---|---|
| `admin.html` | replaces your existing console file | served at `/admin` |
| `nocturne/styles.css` | sibling folder next to `admin.html` | the only new frontend dependency |
| `main.py` | replaces `main.py` | additive: 324 lines added, 2 replaced |
| `faq_store.py` | replaces `faq_store.py` | existing functions unchanged |
| `gaps.py` | new file | |
| `widget_config.py` | new file | |

No new Python packages. No build step. No database migration — the two
new modules create their JSON files on first write, and the app starts
normally if they don't exist.

New files created at runtime (add to `.gitignore` / mount as volumes in
Docker so they survive a container rebuild):

```
gaps_store.json      GAPS_STORE_PATH
widget_config.json   WIDGET_CONFIG_PATH
widget_leads.json    WIDGET_LEADS_PATH
```

`widget_leads.json` in particular holds customer contact details. Back it
up, and keep it out of any image you publish.

## Buttons that were dead before this release

The console shipped with UI for features that had no backend. These
called endpoints that did not exist and failed silently or with an error:

- **"Write one yourself"** → `POST /faq` — did not exist
- **"Draft answers"** → `POST /admin/faq/autogenerate` — did not exist
- **"Delete all shown"** → `DELETE /faq?confirm=true` — did not exist
- **"People asked, we could not answer"** → `GET /faq/gaps` — did not
  exist, and nothing anywhere was recording unanswered questions, so that
  panel could never have shown anything
- **Editing a question's wording** — `PATCH /faq/{id}` accepted only
  `answer`; the question text sent alongside it was discarded

All of these now work.

## New endpoints

### Answer gaps
```
GET    /faq/gaps                    admin   list, most-asked first
POST   /faq/gaps/{id}/resolve       admin   mark answered
DELETE /faq/gaps/{id}               admin   dismiss spam/noise
```

Recorded automatically inside `/query` at the two points a visitor
genuinely gets nothing:

- the retrieval gate finds nothing relevant (`low_retrieval_confidence`)
- an answer was generated but suppressed as ungrounded
  (`ungrounded_answer_suppressed`)

Clarify turns are deliberately **not** recorded. Asking "which product do
you mean?" is a working conversation, not a failure, and logging those
would bury the real misses under every ambiguous opening question.

Deduplicated on a normalized form (lowercase, punctuation stripped), so
"does it need wifi", "Does it need WiFi?" and "does it need wi-fi" are one
gap asked three times rather than three gaps. The first-seen spelling is
kept for display, since that is real visitor phrasing.

This is lexical, not semantic: it will **not** group "does it need wifi"
with "is an internet connection required". Fixing that needs an embedding
call on the write path of every failed query, which is a cost worth
measuring before imposing. The lexical key is exact, free, and already
collapses the case and punctuation variants that dominate in practice.

Answering a gap resolves it automatically — saving a curated answer whose
question matches clears it, so you don't tick it off in two places.
Resolved gaps are kept, not deleted: the "asked 14×" evidence is what
justified writing the answer, and deleting would let the same question
silently re-accumulate as though it were new.

### Widget design
```
GET  /widget/config                 PUBLIC  what the embedded widget reads
GET  /admin/widget/config           admin   includes admin-only fields
PUT  /admin/widget/config           admin   save
POST /admin/widget/config/reset     admin   back to defaults
```

`public_config()` is not simply the stored config — it strips the
notification email from each form. A blob served to every visitor's
browser is not a place to put an internal address. Keep that asymmetry in
mind when adding fields: default to admin-only and promote deliberately.

Validated on save: hex colour pattern, intro actions and field types
against whitelists, labels length-capped. This is not XSS sanitization —
**the widget must still escape on render** — it keeps malformed config
from reaching a visitor and breaking the widget silently.

### Enquiries
```
POST   /widget/lead                 PUBLIC  visitor submits a form
GET    /admin/leads                 admin   list + stats
POST   /admin/leads/{id}/handled    admin   mark handled
DELETE /admin/leads/{id}            admin   delete
```

Submitted values are matched against the **configured** fields; unknown
keys are dropped rather than stored. A public write endpoint whose output
the admin console renders is an obvious abuse path, and dropping unknown
keys closes it. The response deliberately does not echo the stored lead
back to the public caller.

### Drafting
```
POST /admin/faq/autogenerate        admin   draft Q/A from a document
```

The existing `/faq/generate` expects the *browser* to have called an LLM
and to POST finished questions. That works in the chat UI where a key has
been pasted in, but the console holds no key — which is why its "Draft
answers" button never worked. This route runs generation through the app's
own configured chain, so drafting works in Free mode with no key anywhere.

Drafts are stored un-edited so the console shows them as needing review.
Re-drafting **merges** rather than replaces: an admin who has reviewed 30
answers and clicks "Draft answers" again to pick up a few more should not
lose that review work.

Unparseable model output returns 422 and stores nothing. A small local
model returning prose instead of JSON is a normal outcome, not an
exception — better to say so than to persist garbage or report a false
success.

## Bugs found and fixed while building this

**Edited questions came back as near-duplicates.** Once an admin rewrote a
drafted question, the regenerated version no longer matched lexically and
re-drafting created a near-twin. Now the pre-edit wording is preserved as
`original_question` and matched against as well.

**Hand-written answers could be destroyed by a re-ingest.**
`record_questions()` deletes every entry for a source before rewriting it.
Manual entries are stored under the sentinel source `__manual__`, so
re-ingesting a real document cannot touch them.

**`el()` stringified nested arrays.** Children were flattened one level,
so any view returning `[heading, lede, rowsArray]` rendered the literal
text `[object HTMLDivElement]` instead of the list. This affected the new
Unanswered and Enquiries pages and was already latent on Products. Now
`flat(Infinity)`.

## Deliberately not implemented

**Email notification on form submission.** A form that promises "we'll get
back to you" and silently files the response is worse than one that
doesn't. Doing it properly means credentials, retries, and a bounce path —
a feature in its own right, not a line in a config module. `notify_email`
is stored and the Enquiries page states plainly that nothing is emailed,
so the operator knows to check the console.

**Rate limiting on `POST /widget/lead`.** It is a public write endpoint
and bots will find it. `MAX_LEADS` caps file growth but does not stop the
noise. This belongs at the edge — reverse proxy, WAF, or a captcha — not
half-implemented in application code.

## Known limits

- **Leads are stored as JSON under a lock**, matching the rest of the app.
  Fine for a single-tenant deployment; not durable or concurrent-safe
  under real load. Leads are the one thing here an operator will be upset
  to lose, so if volume becomes meaningful move **this** module to SQLite
  first — `conversations.py` already carries the dependency and its
  pattern copies directly.
- **Gap grouping is lexical**, as described above.
- **`nocturne/styles.css` imports Inter from Google Fonts.** In an
  air-gapped or offline deployment — which this app otherwise supports —
  that request fails and the design falls back to `system-ui`. Everything
  still works and still looks reasonable, but it is not the intended type.
  If offline fidelity matters, self-host the Inter files and replace the
  `@import` at the top of the stylesheet.
- **Admin auth is still the single shared password** flagged in `main.py`.
  Nothing here changes that, and the new admin routes inherit it. The new
  public routes (`/widget/config`, `/widget/lead`) are unauthenticated by
  necessity, since the widget is embedded on a public page.

## Testing

Two suites were run against this build:

- **43 API checks** via FastAPI's `TestClient` with retrieval and
  generation stubbed — auth boundaries on every new route, validation
  rejections, the public/admin field split, unknown-key dropping,
  merge-not-replace behaviour, and gap dedup/resolution.
- **24 UI checks** driving the real console in headless Chromium against a
  live backend — every page renders without a JS error, the widget preview
  updates live and persists across reload, a real miss recorded through
  `/query` appears on Unanswered, and answering it there resolves it.

Both pass clean. Neither exercises real retrieval, generation, or the
ingest pipeline — those need the actual models and documents.
