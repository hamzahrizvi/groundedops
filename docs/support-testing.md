# Testing the assistant — a guide for support staff

The AI assistant answers customer questions from our product manuals. It is
running on a test server on the company network so you can try it before
customers do.

**What we need from you:** ask it the questions customers actually ask you,
and tell us where it gets things wrong. Section 5 is the part that matters.

---

## 1. Getting an account

Go to **http://10.10.15.170:8000/admin** and click **"Request one"** at the
bottom of the sign-in box.

You choose your own email, name and password (at least 12 characters). A
root user then approves it before you can sign in — you will not get in
immediately.

> **Ask for a Support-level account.** Say so in your request, or tell
> Hamza. This matters — see the warning in section 3. A Basic account
> cannot do the testing we need.

---

## 2. Getting in

**You must be on the company network.** Working from home means the VPN has
to be connected first.

> If the page will not load at all, **check the VPN before anything else.**
> That is almost always the reason.

Open **http://10.10.15.170:8000/admin** and sign in.

---

## 3. Always test as signed in — never as guest

This is the single most important setting, and it is easy to miss.

The assistant behaves **completely differently** depending on whether it
thinks it is talking to an anonymous website visitor or a signed-in
customer:

| | What it does |
|---|---|
| **Guest** | Only answers from a short list of pre-approved answers. For anything else it says *"I don't have a reviewed answer for that yet"* and offers to email support. **It does not read the manuals at all.** |
| **Signed in** | Searches the full product documentation and answers from it. **This is what we need you to test.** |

On the Test chat page there is a button at the top right of the chat that
reads **"Viewing as guest"**. **Click it so it reads "Viewing as
signed-in", then test.**

> **If the assistant tells you to "sign in" or offers to email support, you
> are still in guest mode.** Click the button and ask again. That is not a
> bug and is not worth reporting.

If you do not see that button at all, your account is Basic rather than
Support — ask for it to be upgraded, because you cannot do useful testing
without it.

---

## 4. Testing it

The chat opens with a few options — *Speak to sales*, *Request technical
support*, *Find a product*, *Get spare parts & accessories*.

For product testing, choose **Request technical support**, then:

1. Pick the **product range** — Coin Hoppers, Biometrics, Note Validator
2. Pick the **specific model** — NV200, NV4000, BV50, NV201 Spectral and so
   on, or **"Not sure — ask across the whole range"**
3. Ask your question

**Change** at the top of the chat switches product without starting over.

Then:

- Ask real questions, in the words customers use — not tidied-up versions
- Follow-up questions work; carry the conversation on like a customer would
- Try the awkward ones: the vague question, the wrong model number, the
  question the manual answers badly

**Give it a moment after a quiet spell.** The first question after the
server has been idle, or just after an update, can be slow or come back
asking you to clarify. Ask again and it will be fine. Not worth reporting.

---

## 5. Reporting problems — the important bit

When an answer is wrong, incomplete or odd:

1. Click **ⓘ Report** at the top right of the chat
2. Type what went wrong, or what it should have said
3. Click **Send report**

**The whole conversation goes with it automatically.** You do not need to
screenshot anything, copy the chat, or write a summary of what you asked.

### Write it like this

| Good | Why |
|---|---|
| "Wrong — the NV4000 clears from the top cover, not the cash box. Install manual page 14." | Says what is wrong *and* where the right answer is |
| "Didn't answer my question — it told me about the NV200 instead." | Names the actual failure |
| "Right answer, but it left out the power-isolation step." | Specific and checkable |

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

## 6. Worth reporting

- Wrong facts: figures, part numbers, settings, procedures
- Says it cannot find something that **is** in the manual *(while signed
  in — see section 3)*
- Answers about the wrong product or model
- Missing or wrong safety warnings ← urgent, see above
- Tone that would embarrass us in front of a customer

## 7. Not worth reporting

- A slow or clarifying first answer after an idle period (section 4)
- The "I don't have a reviewed answer" deflection **in guest mode** — that
  is the guest mode working as designed (section 3)
- Refusing to answer something that genuinely is not in the manuals — that
  is correct behaviour, not a failure
- Prices, stock levels, order status, lead times — it has no access to any
  of that and is not meant to

---

## 8. If something is broken

| Problem | First thing to check |
|---|---|
| Page will not load | VPN connected? Then the address in section 2 |
| Everything deflects to "email support" | You are in guest mode — section 3 |
| No "Viewing as guest" button | Your account is Basic, not Support — section 1 |
| Signed in but no chat appears | Reload the page |
| "Sign in failed" | Your request may not be approved yet — ask Hamza |
| Everything looks down | Email Hamza; the server may be mid-update |

---

**Where your reports go:** the console's Feedback page, where they are
reviewed against the manuals and turned into fixes. Common problems get
fixed first, which is why it is worth reporting the same fault even if you
think someone else already has.
