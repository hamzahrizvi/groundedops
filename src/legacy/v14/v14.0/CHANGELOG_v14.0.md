# GroundedOps v14.0 — console overhaul, FAQ suggestions wired, LAN address fixed

Three things that turned out to be connected. The widget's "is this what you
meant?" suggestions were never rendered, the console pages that manage those
answers were confusing enough to hide it, and a launcher bug meant the person
who could confirm any of it was pointed at an unreachable address.

Adds one route (`GET /admin/providers`) and renames two console tabs, hence the
major bump rather than a `v13.2`.

## What deploys

| File | Where it goes | Notes |
|---|---|---|
| `main.py` | replaces `main.py` | provider default model from env, `/admin/providers`, gap spam + bulk routes, `answer_gap` |
| `faq_store.py` | replaces `faq_store.py` | `spam` flag, `mark_spam`, `mark_spam_bulk`, `dismiss_gap_bulk` |
| `admin.html` | replaces `admin.html` | walkthrough, product-scoped FAQs, single Generate button, sub-nav, sign-in errors |
| `test_new_routes.py` | replaces `tests/test_new_routes.py` | 9 assertions for the gap/spam/answer_gap work |
| `App.jsx` | replaces `frontend/src/App.jsx` | `runQuery` opts bag for FAQ pick/reject |
| `api.js` | replaces `frontend/src/api.js` | `faq_id` + `skip_faq` on `/query` |
| `Message.jsx` | replaces `frontend/src/components/Message.jsx` | renders `FaqChoices` — this is the fix |
| `FaqChoices.jsx` | replaces `frontend/src/components/FaqChoices.jsx` | theme tokens instead of hardcoded dark hex |
| `run.ps1` | replaces `run.ps1` | LAN address detection; repo root, not `src/` |
| `QUICKSTART.md` | replaces `QUICKSTART.md` | section 5 rewritten for the renamed, scoped page |

Nothing else changed. `llm.py`, `widget_config.py`, `styles.css`, `_harness.py`
are all untouched from HEAD.

## The DeepSeek alias, for the third time

`_PROVIDER_DEFAULT_MODEL` pinned deepseek to `deepseek-chat`, retired by
DeepSeek on 24 July 2026. Any console-initiated generation with DeepSeek
selected failed with "returned nothing — check the key is set", which reads as
a missing key when the key was fine. `viewTest` in `admin.html` held a third
copy of the same map.

`query()`'s escalation path already carried a comment about being burned by
exactly this and reads `ONLINE_DEEPSEEK_MODEL`. There is now one accessor,
`_default_model_for()`, and no second copy of a value that goes stale.

## Providers are no longer a hardcoded list

`GET /admin/providers` reports what this install can actually reach: an online
provider needs its key in the environment, local needs Ollama warmed. Every
provider picker in the console builds from it, so it cannot offer a provider
guaranteed to fail. An empty list means "nothing configured, fall back to the
server's own chain" — not an error.

The model box is disabled until a provider is chosen and shows that provider's
current default as its placeholder, which is the affordance whose absence let a
retired model name sit unnoticed.

## Console

- The prose modal guide is replaced by a per-page walkthrough. Steps target
  `[data-tour]`; the highlighted element pulses and a popover sits beside it,
  flipping side when there is no room. It ends on navigation — it explains the
  page you are on rather than driving you across pages. Steps whose target is
  absent are dropped at start, so an empty-state page gets a correctly numbered
  shorter tour instead of a step pointing at nothing.
- "Reviewed answers" → **Generate/Edit FAQs**; "Unanswered" → **FAQs from
  customers**. Help moved from a rail button to a `?` icon, top right.
- `buildSubnav()` lists the active page's sections under its nav row, read from
  `[data-sec]` in the rendered DOM, so a view opts in by tagging a card.
- **FAQs are listed per product, not all at once.** The picker is built from
  scopes that actually hold answers, so entries tagged with a key the catalogue
  does not know about stay reachable and marked as such. Gating on the catalogue
  alone would have hidden precisely the rows that need retagging — the 37
  `myconnect` entries.
- "Draft answers" and "Draft all" collapse into one **Generate FAQs** button.
  They differed only in which documents they covered, and that is a property of
  the picker: the Document dropdown gained "Every document with no FAQs yet".
- The generate row's five equal flex columns had minimums that overflowed the
  card, pushing the model field under the buttons. Explicit flex bases now.
- The test chat ignored `faq_candidates`, so a clarify turn arrived as a bare
  "is this what you meant?" with nothing to click — the one place it did *not*
  match what a customer sees. It renders the candidates and a "none of these"
  fallback now.

## Sign-in errors told you the wrong thing

The gate reported every failure as "that password was not accepted" — a wrong
password, an unreachable server, a proxy eating the request and a 404 from the
private-surface middleware were indistinguishable. A LAN connectivity problem
was consequently diagnosed as an auth problem for some time. It now separates a
real 401 from an unreachable origin (naming it) from anything else, and catches
the silent case where a 401 on one of `refresh()`'s reads clears the password
and bounces you back to the gate saying nothing.

## The LAN address was a coin flip

`Get-LanIP` sorted the default routes by metric, took the first, and never
checked whether the adapter was connected. Two routes tied at metric 0
(Ethernet and Wi-Fi) made the winner arbitrary — and a disconnected Wi-Fi keeps
both its route and its IP in the local stack, so the launcher published an
address that answers a connection test on the host and is dead from everywhere
else. That address went into `handover.txt` and out to other people.

`Get-LanAddresses` takes only adapters that are `Up`, drops virtual switches
and link-local, ranks internet-connected then wired then metric, and the
summary prints every live address with its interface name.

The firewall check is three-state. `Get-NetFirewallRule` returns an empty set
both when nothing matches and when it cannot enumerate at all (no elevation, or
domain policy), and the port may already be open via a policy rule under
another name — so treating "cannot tell" as "missing" produced a red warning on
every run of a working setup plus a UAC prompt for a rule already in place.

## Known gaps

- **Not verified in a browser past the login gate.** `node --check` passes on
  the extracted script, `main.py` parses, and `/admin` boots with no console
  errors, but the walkthrough, the sub-nav, the renames and the test-chat
  suggestions are all DOM-driven and want a real click-through.
- 4 pre-existing `test_llm.py` failures are unrelated to this drop: the test's
  own mock lacks the `api_keys` kwarg. `llm.py` is untouched.
- `ADMIN_PASSWORD` still defaults to the literal `admin`.
- The widget still reads branding from `data-*` attributes and never fetches
  `/widget/config`, so the console's Widget design page saves without effect.
