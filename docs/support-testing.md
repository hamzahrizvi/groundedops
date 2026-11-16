# Testing the assistant — a guide for support staff

The AI assistant answers customer questions from our product manuals. It is
running on a test server on the company network so you can try it before
customers do.

**What we need from you:** ask it the questions customers actually ask you,
and tell us where it gets things wrong. Section 4 is the part that matters.

---

## 1. Getting an account

Email **hrizvi@innovative-technology.com** and say which products you
support. You will get a **Basic** account.

Basic means the test chat and nothing else — you cannot change documents,
answers or settings, even by accident. That is deliberate, not a limitation
to work around.

---

## 2. Getting in

**You must be on the company network.** Working from home means the VPN has
to be connected first.

> If the page will not load at all, **check the VPN before anything else.**
> That is almost always the reason.

1. Open **http://10.10.15.170:8000/admin**
2. Sign in with your work email and the password you were given
3. **Change your password straight away** — "Change password", top right.
   Minimum 12 characters.

---

## 3. Testing it

You land on one page: **Test chat**. It is the assistant exactly as a
customer sees it.

- Pick a product first
- Ask real questions, in the words customers use — not tidied-up versions
- Follow-up questions work; carry the conversation on like a customer would

**Give it a moment after a quiet spell.** The first question after the
server has been idle, or just after an update, can come back asking you to
clarify instead of answering. Ask again and it will be fine. That is not a
bug worth reporting.

---

## 4. Reporting problems — the important bit

When an answer is wrong, incomplete or odd:

1. Click **ⓘ Report** at the top right of the chat
2. Type what went wrong, or what it should have said
3. Click **Send report**

**The whole conversation goes with it automatically.** You do not need to
screenshot anything, copy the chat, or write a summary of what you asked.

### Write it like this

| Good | Why |
|---|---|
| "Wrong — the XR500 runs on 24V, not 12V. It's on page 14 of the install manual." | Says what is wrong *and* where the right answer is |
| "Didn't answer my question — it told me about the XR400 instead." | Names the actual failure |
| "Right answer, but it left out the isolation warning." | Specific and checkable |

"Doesn't work" or "bad answer" cannot be acted on. One line of detail is
enough.

### Please do not email or Teams these

A report sent through the button carries the conversation with it, lands
where the people fixing it are looking, and gets checked against the
manuals. An email carries none of that and gets lost.

**One exception:** anything unsafe — a missing warning, wrong voltage,
wrong torque figure, wrong isolation step — **report it in the tool AND
message Hamza directly.** Those are treated as urgent rather than waiting
for the weekly review.

---

## 5. Worth reporting

- Wrong facts: figures, part numbers, settings, procedures
- Says it cannot find something that **is** in the manual
- Answers about the wrong product
- Missing or wrong safety warnings ← urgent, see above
- Tone that would embarrass us in front of a customer

## 6. Not worth reporting

- A slow or clarifying first answer after an idle period (section 3)
- Refusing to answer something that genuinely is not in the manuals — that
  is correct behaviour, not a failure
- Prices, stock levels, order status, lead times — it has no access to any
  of that and is not meant to

---

## 7. If something is broken

| Problem | First thing to check |
|---|---|
| Page will not load | VPN connected? Then the address in section 2 |
| Signed in but no chat appears | Reload the page |
| "Sign in failed" | Password was changed, or the account is new and not set up yet — email Hamza |
| Everything looks down | Email Hamza; the server may be mid-update |

---

**Where your reports go:** the console's Feedback page, where they are
reviewed against the manuals and turned into fixes. Common problems get
fixed first, which is why it is worth reporting the same fault even if you
think someone else already has.
