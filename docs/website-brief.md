# Design brief: GroundedOps website, sign-up and customer portal

> Paste this whole document into Claude Design. It describes three connected
> surfaces, in priority order:
>
> 1. **Marketing site**: explains the product and turns visitors into trials or demo requests.
> 2. **Sign-up and onboarding**: gets a new customer from "Start free trial" to a working widget on their own site.
> 3. **Customer portal**: where a paying customer manages their knowledge base, widget, team, usage and subscription.
>
> A fourth, smaller surface, the **operator console** (our internal view of all customers), comes last.

---

## 0. Ground rules (read first)

**Every claim on the site must be true of the shipped product.** The product's
whole pitch is "it won't make things up", so the website can't either.

Allowed, because the product does these today:
- Answers come only from the customer's own documents (PDF, DOCX, TXT manuals).
- Every answer is checked against its source pages before it is shown. If the check fails, the answer is not shown, and the assistant says it doesn't know and offers a person instead.
- Every answer cites the manual and the exact page, with a one-click link to the original PDF.
- Visitors pick a product first, so each search runs against the right manual.
- Curated FAQ: the team approves answers to common questions, and those are served instantly, word for word.
- Unanswered questions are logged, so the team knows what's missing from the docs.
- Cross-product questions ("which validators work on 24V?") are answered from the catalogue and spec tables. Nothing in those answers is generated.
- Model choice: bring your own key for DeepSeek, OpenAI or Claude, **or** run fully offline on your own hardware with local models (Ollama). Customers see the same experience either way.
- Embeds on any website with one `<script>` tag. There is also a WordPress/PHP plugin.
- Admin accounts with roles (Owner / Admin / Viewer, which are `root` / `support` / `basic` internally), and sign-in with Microsoft (Entra ID) or Google Workspace.
- Usage is metered in credits: a standard answer costs 1 credit and a "deep" answer (stronger model, wider search) costs 4. There are per-visitor and per-session caps.
- Email alerts before an AI provider runs out of credit.
- Self-hosted install: a double-click Windows installer, or Docker.

**Do not invent:**
- Customer logos, testimonials, case-study numbers, user counts.
- Accuracy percentages or benchmark stats. Use a visible placeholder like `[STAT — to be verified]`.
- Certifications (SOC 2, ISO 27001, HIPAA) or uptime SLAs.
- Final prices. Use `$XX` placeholders; the price *structure* is in section 5.
- A domain. Use `groundedops.example` everywhere.

---

## 1. Brand

- **Name:** GroundedOps. **Mark:** "GO" monogram.
- **Tagline:** *Grounded, or it says nothing.*
- **Alt line:** *Your manuals already have the answers. GroundedOps makes them answer, and tells you honestly when they don't.*
- **Colours:** bronze `#C59055` (accent, CTAs), charcoal `#1A262C` (text, dark surfaces), clean white or very pale warm grey ground. Keep the accent rare so it means "act here".
- **Tone:** calm, precise, engineering-honest. Short sentences. No hype words ("revolutionary", "magic"). Talk like a senior support engineer who has been burned by chatbots that invent things.
- **Imagery:** real product screenshots, not stock photos. These exist and should be used as placeholders: launcher on a host page, choosing a product range, suggested FAQ questions, a grounded answer with sources expanded, and a refusal that offers a person. The refusal screenshot is the hero moment of the whole brand.
- Needs light **and** dark mode, WCAG AA contrast, and must work down to 360px wide.

## 2. Who we're selling to

| Persona | Cares about | What convinces them |
|---|---|---|
| **Head of Support / CX** (primary buyer) | Ticket volume, agents re-answering the same spec questions, bad answers reaching customers | Deflection, the refusal behaviour, the unanswered-questions report |
| **Product / technical documentation lead** | Manuals nobody reads; knowing where the docs are weak | Page-level citations, the gaps log |
| **IT / security** (blocker) | Data leaving the building, public attack surface, SSO | Offline mode, self-hosting, only the widget is public, SSO, roles |
| **Finance** | Unpredictable AI bills | Credits, hard caps, provider-credit alerts, bring-your-own-key |

Ideal customer: a manufacturer or distributor with many hardware products and long technical manuals full of spec tables, part numbers and fault codes. Examples: cash handling, industrial equipment, devices, biometrics.

## 3. Sitemap

```
Marketing
  /                     Home
  /how-it-works         The pipeline, explained for humans
  /features             Feature detail
  /security             Deployment options, data handling, access control
  /pricing              Plans + FAQ
  /demo                 Live sandbox widget + "book a demo"
  /docs                 Link out to documentation (install, widget, API)
  /about, /contact
  /legal/{terms,privacy,dpa,subprocessors,cookies}
  /status               (link to status page)

Auth
  /signup, /login, /login/sso, /verify-email, /forgot-password, /invite/:token

Onboarding
  /onboarding/1..6      (see section 6)

Customer portal  (app.groundedops.example)
  Overview · Knowledge · FAQ · Gaps · Widget · Usage · Team · Models & keys
  · Billing · Security & audit · Settings

Operator console (internal)
```

## 4. Marketing pages

### 4.1 Home: section by section

1. **Hero.** Headline: *"Answers from your own manuals. Checked before they're shown."* Sub: *"GroundedOps puts a support assistant on your website that cites the exact page, and says 'I don't know' instead of guessing."* CTAs: **Start free trial** (bronze) and **Try the live demo** (secondary). Visual: the widget open on a mock product page, mid-answer, with the source chip visible.
2. **The problem, as a 3-line story.** *"A customer asks what voltage your validator needs. A generic chatbot invents an answer. Confident. Fluent. Wrong."* Show a fake wrong answer next to the GroundedOps answer with its citation.
3. **Four pillars** (icon + one line each): *Scoped to the right manual* · *Reviewed answers first* · *Every answer cited to the page* · *Can't verify it? It won't say it.*
4. **The trust moment.** Large refusal screenshot. Copy: *"When the documents don't back it up, the answer never reaches your customer. They get an honest 'not in our documentation' and a route to a person."*
5. **Live demo block.** An embedded, working widget against a sample manual, with 3 suggested questions: one it answers, one cross-product question, and one it correctly refuses.
6. **How it works.** A 3-step strip: *Upload your manuals → we index, search and rerank → a verified, cited answer.* Links to /how-it-works.
7. **Gets better every week.** Split visual: the FAQ approval screen and the "unanswered questions" list. *"Approve answers to your top questions once. See exactly what your docs are missing."*
8. **Your models, your rules.** Three tiles: *Bring your own key* (DeepSeek, OpenAI, Claude) · *Included credits* (we handle it) · *Fully offline* (your hardware, nothing leaves).
9. **Security strip.** *Only the widget is public · Admin stays on your network · SSO with Microsoft or Google · Role-based access.* Links to /security.
10. **ROI calculator.** Inputs: monthly support tickets, % that are "it's in the manual" questions, cost per ticket. Output: an estimated monthly saving, with the assumption shown and labelled as an estimate.
11. **Pricing teaser** with 3 plan cards linking to /pricing.
12. **FAQ accordion.** 6 to 8 buyer objections (see 4.5).
13. **Final CTA band** in charcoal: tagline + **Start free trial**.

### 4.2 How it works
A vertical, scroll-animated pipeline diagram with a plain-English caption at each stage, plus a "for engineers" toggle that reveals the technical names:
Upload → parse page by page → chunk on step boundaries → **question comes in** → rewrite it to stand alone → check the approved FAQ first → hybrid search (keyword + semantic) → rerank → confidence gate (*refuse / ask to clarify / proceed*) → generate → **grounding check** → show the answer with page citations, or say it doesn't know.
Highlight the two points where the system *stops* rather than guesses.

### 4.3 Features
Grouped cards: **Answering** (scoped search, cross-product/spec-table answers, deep answers, clarifying questions, follow-up memory) · **Curation** (FAQ approval, gaps log, catalogue of categories → products → documents) · **Embedding** (script tag, WordPress plugin, branding, visitor tiers) · **Control** (credits, caps, provider alerts, model routing) · **Admin** (roles, SSO, audit).

### 4.4 Security & deployment
Comparison table of three deployment modes, with columns *Hosted by us*, *Self-hosted*, *Self-hosted offline*. Rows: where documents live, where the AI model runs, what's public, who holds the keys, internet required. Then sections on access control, what data is logged and for how long, and subprocessors per mode. Do not show certification badges.

### 4.5 Pricing page
Plan cards (section 5), a full feature comparison table, a monthly/annual toggle (annual shows "2 months free"), a credit explainer ("1 credit = 1 standard answer · approved FAQ answers are free · deep answers cost 4"), and a pricing FAQ:
- What happens if I run out of credits? (Choice of hard stop, or auto top-up.)
- Can I use my own OpenAI/Claude key? (Yes. BYO-key plans don't consume model credits, only a platform fee.)
- Are FAQ answers charged? (No.)
- Can I self-host? (Yes, on the Self-hosted plan.)
- Is there a free trial? Do I need a card? (14 days, no card.)
- How do I cancel? (From Billing, in two clicks. You can export your data.)

## 5. Plans and pricing structure

Prices are placeholders. The **meters** are what the product actually counts.

| | **Trial** | **Starter** | **Growth** | **Self-hosted / Enterprise** |
|---|---|---|---|---|
| Price | Free, 14 days, no card | `$XX`/mo | `$XX`/mo | Annual licence, "Talk to us" |
| Included answer credits / mo | 200 | `X,000` | `XX,000` | Unlimited (your models) |
| Products in catalogue | 3 | 25 | Unlimited | Unlimited |
| Document pages indexed | 500 | `X,000` | `XX,000` | Unlimited |
| Admin seats | 2 | 3 | 10 | Unlimited |
| Widget sites (allowed domains) | 1 | 1 | 5 | Unlimited |
| Bring your own model key | ✓ | ✓ | ✓ | ✓ |
| Deep answers | — | ✓ | ✓ | ✓ |
| SSO (Microsoft / Google) | — | — | ✓ | ✓ |
| Remove "Powered by GroundedOps" | — | — | ✓ | ✓ |
| Offline / on-prem models | — | — | — | ✓ |
| Support | Email | Email | Priority | Named contact |

Add-ons: extra credit packs, auto top-up, extra seats. Overage is **never automatic** unless the customer has turned on auto top-up.

## 6. Sign-up and onboarding flow

Goal: **a working, cited answer on the customer's own manual in under 10 minutes.**

1. **/signup.** Work email + password, or **Continue with Microsoft** / **Continue with Google**. Company name. Checkbox for terms + privacy (unticked). No card.
2. **Verify email.** A "check your inbox" screen with a resend button and a "wrong email?" link.
3. **Onboarding wizard.** Show progress along the top. Every step is skippable except step 1.
   1. **Workspace.** Name, workspace URL (`acme.groundedops.example`), industry dropdown.
   2. **Models.** Three cards: *Use included credits* (default) · *Bring my own key* (provider select + key field + "Test key" button) · *Offline / self-hosted* (routes to the Enterprise contact form).
   3. **Upload your first manual.** Drag and drop PDF/DOCX/TXT, with per-file progress (*uploading → reading pages → indexing*). Show the page count when done.
   4. **Build your catalogue.** Category → product tree. Suggest products from the uploaded file names, and let the customer drag documents onto products. Explain "shared" documents that belong to a whole category.
   5. **Try it.** A test chat, scoped to the product they just made, with suggested questions. Celebrate the first cited answer (subtle, not confetti).
   6. **Install the widget.** A copy-able script tag, a WordPress plugin download, an allowed-domains field, and a colour picker with live preview. Include a "Send these instructions to my developer" email action.
4. **Land on the Overview** with a checklist card ("Approve your first 5 FAQ answers", "Invite a teammate", "Add allowed domain") that stays until it's complete.

Also design: `/login` (email + SSO buttons), forgot password, reset password, accept invite, expired or invalid link, and "account disabled".

## 7. Customer portal

Left sidebar navigation, workspace switcher at the top, and user menu + help at the bottom. A persistent top banner slot for billing and usage states (see 8).

- **Overview.** KPI tiles for the chosen period: questions answered · answered from approved FAQ · refused (with an explanation that this is good) · credits used / remaining. Charts: questions per day, answered vs refused. Plus a "Top 10 unanswered questions" list with a **Fix this** action (add a document or write an FAQ), and the onboarding checklist.
- **Knowledge.** Document table (name, product, pages, indexed date, status), upload, re-index, delete (with confirmation), and a catalogue editor (categories → products → documents).
- **FAQ.** Per-product list of suggested and approved Q&A. Statuses: *suggested · approved · needs review*. An inline editor showing which source page each answer came from. Bulk approve.
- **Gaps.** Unanswered and refused questions, grouped and counted, with trend, CSV export, and the actions *Write FAQ answer* and *Upload missing doc*.
- **Widget.** Install code, allowed domains, branding (colour, logo, greeting, launcher position), live preview, and visitor-tier rules: anonymous vs signed-in member vs staff allowances, questions per session, and whether deep answers are allowed. The product enforces hard maximums on these, so show them.
- **Usage.** Credits used vs included, by day and by tier (anonymous / member / staff), plus standard vs deep. Projection line: "at this rate you'll use your credits by 18 Oct". Threshold alert settings (80% / 100%).
- **Team.** Members table with role (Owner / Admin / Viewer), last active and SSO status. Invite by email, change role, disable, resend invite. Explain the roles inline. Only Owners can manage the team.
- **Models & keys** (Owner only). Provider slots (DeepSeek / OpenAI / Claude), with key masked, *Test*, *Replace*, *Remove*. Show the remaining provider balance where the provider reports it, and say "provider doesn't report a balance" where it doesn't. Never fake a number. Low-balance alert recipients.
- **Billing & subscription** (Owner only). See section 8.
- **Security & audit.** SSO configuration (Microsoft tenant / Google domain), a "require SSO" toggle, session sign-out-everywhere, and an audit log (who changed what, when) with filter and export.
- **Settings.** Workspace name/URL, email settings, data export (all documents + FAQ + logs as a zip), and delete workspace (typed confirmation, 30-day grace).

Design empty, loading and error states for every page. In particular, an empty Knowledge page should lead straight into upload.

## 8. Subscription management

**Billing page:**
- Current plan card: name, price, renewal date, and credits included. Buttons: **Change plan**, **Cancel**.
- Plan-change modal: side-by-side comparison, the prorated charge or credit shown **before** confirming, and a downgrade warning if they're over the new plan's limits (e.g. "you have 31 products; Starter allows 25"), with what happens to the excess.
- Credit packs: buy once, or turn on auto top-up (threshold + pack size + monthly spending ceiling).
- Payment method (card / invoice for annual), billing email, company details + VAT/tax ID, and invoice history with PDF download. Assume Stripe Checkout and the Stripe Customer Portal handle payment entry. The design only needs our wrapper screens.

**Cancellation flow** (respectful, not a dark pattern): a reason picker → a single relevant alternative offer (downgrade, pause for 1–3 months, or switch to BYO-key to cut cost) → a confirmation that states exactly what happens and when (the widget stops on date X, data kept read-only for 30 days, export available) → a done screen with **Export data** and **Undo cancellation**.

**Lifecycle states:** each needs a banner and an email.

| State | Banner (in portal) | Widget behaviour |
|---|---|---|
| Trialing | "9 days left in your trial · Choose a plan" | Normal |
| Trial ended | Blocking modal: pick a plan | Shows "temporarily unavailable" + contact link |
| Active | none | Normal |
| Credits at 80% / 100% | Amber / red, with Buy credits · Upgrade | At 100%: approved FAQ answers still work, generated answers pause (unless auto top-up is on) |
| Past due | "Payment failed · Update card" + grace days left | Normal during 7-day grace, then paused |
| Paused | "Paused until DATE · Resume" | Off |
| Cancelled | "Ends on DATE · Reactivate" | Normal until period end |

## 9. Emails

Design one branded template and these variants: verify email · welcome (with the 3 next steps) · team invite · trial ending (7, 3 and 1 days before) · trial ended · receipt / invoice · payment failed (days 0, 3, 6) · plan changed · credits at 80% / 100% · auto top-up charged · AI provider balance low · **weekly digest** (questions answered, refusals, top 5 unanswered questions with **Fix this** links; this is the main retention email) · cancellation confirmed · data export ready · workspace scheduled for deletion.

## 10. Operator console (internal, lower priority)

Customers table (plan, status, MRR, credits used, last active, trial end), with filters for *trial ending this week* and *past due*. A customer detail page: plan history, usage, a **Grant credits** action, a **Extend trial** action, and a **View as customer** action (read-only, logged, and shown to the customer in their audit log). Top-line metrics: MRR, active trials, trial→paid conversion, churn, credits consumed vs model cost.

## 11. Deliverables and priority

1. Design tokens and components: buttons, inputs, cards, tables, banners, modals, plan cards, sidebar, empty states. Light + dark.
2. **Home**, **Pricing**, **Sign-up**, **Onboarding (all 6 steps)**.
3. Portal: **Overview**, **Knowledge**, **Widget**, **Billing** (including plan-change and cancel flows), **Usage**.
4. How it works, Security, Demo, Features.
5. Portal: FAQ, Gaps, Team, Models & keys, Security & audit, Settings.
6. Email template + variants, then the operator console.

Show each main screen at desktop (1440) and mobile (390). Mark every placeholder number and price visibly so none of them ship by accident.
