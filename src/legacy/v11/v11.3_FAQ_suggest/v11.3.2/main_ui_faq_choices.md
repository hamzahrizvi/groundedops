Main chat UI — rendering FAQ candidates (v3.4.1)
================================================

The backend is already returning everything needed; the React app just
isn't rendering it, which is why you see the prompt text with no options.

The /query response for a disambiguation looks like:

    {
      "answer": "These FAQs match your query — please select the one you meant:",
      "role": "clarify",
      "needs_clarification": true,
      "faq_candidates": [
        { "id": "3f2a…", "question": "Does MyCheckr use cloud-based processing?",
          "score": 0.86, "semantic": 0.86, "lexical": 0.42 }
      ],
      "sources": []
    }

Two things to change.


------------------------------------------------------------------------
1. Keep faq_candidates on the message object
------------------------------------------------------------------------
Wherever the assistant message is pushed into state after a /query, carry
the field through (it's dropped today):

    setMessages(prev => [...prev, {
      role: "assistant",
      text: data.answer,
      sources: data.sources,
      // v3.4.1
      faqCandidates: data.faq_candidates || null,
      needsClarification: !!data.needs_clarification,
    }]);


------------------------------------------------------------------------
2. Render the options, and hide "View sources & options"
------------------------------------------------------------------------
A clarify message has no sources, so the "View sources & options" affordance
should not appear on it — that's the odd part of the current screenshot.

    {msg.faqCandidates?.length > 0 && (
      <FaqChoices
        candidates={msg.faqCandidates}
        originalQuery={msg.forQuery}      // the user text that produced this
        onPick={(c) => sendQuery(c.question, { faqId: c.id })}
        onReject={(q) => sendQuery(q, { skipFaq: true })}
      />
    )}

    {/* only show sources when there are some */}
    {msg.sources?.length > 0 && <SourcesToggle sources={msg.sources} />}


------------------------------------------------------------------------
The component
------------------------------------------------------------------------

    function FaqChoices({ candidates, originalQuery, onPick, onReject }) {
      const [used, setUsed] = React.useState(false);
      if (used) return null;                 // collapse once answered

      return (
        <div className="faq-choices" role="group"
             aria-label="Matching questions">
          {candidates.map((c) => (
            <button
              key={c.id}
              type="button"
              className="faq-choice"
              onClick={() => { setUsed(true); onPick(c); }}
            >
              {c.question}
            </button>
          ))}
          <button
            type="button"
            className="faq-choice faq-choice--alt"
            onClick={() => { setUsed(true); onReject(originalQuery); }}
          >
            None of these — search the documents
          </button>
        </div>
      );
    }


------------------------------------------------------------------------
sendQuery: pass the two new fields
------------------------------------------------------------------------

    async function sendQuery(text, opts = {}) {
      const res = await fetch(`${API}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          q: text,
          session_id: sessionId,
          product: productKey,
          category: categoryKey,
          // v3.4.1 — picking a suggestion serves that exact entry by id
          // (no re-matching, so no chance of a mismatch); rejecting logs a
          // FAQ gap and answers from the documents instead.
          faq_id: opts.faqId || null,
          skip_faq: !!opts.skipFaq,
        }),
      });
      return res.json();
    }


------------------------------------------------------------------------
Styling to match your dark theme
------------------------------------------------------------------------

    .faq-choices {
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin-top: 12px;
    }
    .faq-choice {
      text-align: left;
      padding: 10px 14px;
      border-radius: 10px;
      border: 1px solid rgba(224, 158, 106, 0.35);
      background: rgba(224, 158, 106, 0.08);
      color: #e8dcd2;
      font: inherit;
      font-size: 14px;
      cursor: pointer;
      transition: background 0.12s, border-color 0.12s;
    }
    .faq-choice:hover {
      background: rgba(224, 158, 106, 0.16);
      border-color: rgba(224, 158, 106, 0.7);
    }
    .faq-choice:focus-visible {
      outline: 2px solid #e09e6a;
      outline-offset: 2px;
    }
    .faq-choice--alt {
      background: transparent;
      border-style: dashed;
      color: #a9a29c;
    }


------------------------------------------------------------------------
Worth doing at the same time
------------------------------------------------------------------------
* Collapse the options once one is chosen (the `used` state above),
  otherwise stale buttons stay clickable further up the transcript.
* Don't render "View sources & options" when `sources` is empty — it
  currently shows on refusals and clarifications, where there is nothing
  behind it.
* If you show the match score anywhere, keep it in a tooltip rather than
  on the button; the score helps you tune thresholds but means nothing to
  a customer.
