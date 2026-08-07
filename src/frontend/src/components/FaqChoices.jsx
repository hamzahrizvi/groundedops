import React, { useState } from "react";

/**
 * FaqChoices — renders the FAQ disambiguation options returned by /query.
 *
 * When the backend can't confidently match a question to a curated FAQ, it
 * returns role="clarify" with a faq_candidates array instead of guessing.
 * This renders those as buttons so the user picks, which is the whole point:
 * judging whether two questions mean the same thing is something a person
 * does instantly and a model does badly.
 *
 * INTEGRATION (3 steps)
 *
 * 1. Keep the field when storing the assistant message — it's dropped today:
 *
 *      setMessages(prev => [...prev, {
 *        role: "assistant",
 *        text: data.answer,
 *        sources: data.sources,
 *        faqCandidates: data.faq_candidates || null,   // <— add
 *        forQuery: userText,                            // <— add
 *      }]);
 *
 * 2. Render it inside the assistant bubble:
 *
 *      {msg.faqCandidates?.length > 0 && (
 *        <FaqChoices
 *          candidates={msg.faqCandidates}
 *          originalQuery={msg.forQuery}
 *          onPick={(c) => sendQuery(c.question, { faqId: c.id })}
 *          onReject={(q) => sendQuery(q, { skipFaq: true })}
 *        />
 *      )}
 *
 *    And gate the sources affordance on there actually being sources —
 *    "View sources & options" currently renders on clarifications and
 *    refusals, where there is nothing behind it:
 *
 *      {msg.sources?.length > 0 && <SourcesToggle sources={msg.sources} />}
 *
 * 3. sendQuery must pass the two new fields through to POST /query:
 *
 *      body: JSON.stringify({
 *        q: text,
 *        session_id: sessionId,
 *        product: productKey,
 *        category: categoryKey,
 *        faq_id: opts.faqId || null,     // serves that exact entry by id
 *        skip_faq: !!opts.skipFaq,       // logs a gap, answers from docs
 *      })
 */

const styles = {
  wrap: {
    display: "flex",
    flexDirection: "column",
    gap: 8,
    marginTop: 12,
  },
  choice: {
    textAlign: "left",
    padding: "10px 14px",
    borderRadius: 10,
    border: "1px solid rgba(224,158,106,0.35)",
    background: "rgba(224,158,106,0.08)",
    color: "#e8dcd2",
    font: "inherit",
    fontSize: 14,
    lineHeight: 1.45,
    cursor: "pointer",
    transition: "background .12s, border-color .12s",
  },
  choiceHover: {
    background: "rgba(224,158,106,0.16)",
    borderColor: "rgba(224,158,106,0.7)",
  },
  alt: {
    background: "transparent",
    borderStyle: "dashed",
    borderColor: "rgba(169,162,156,0.4)",
    color: "#a9a29c",
  },
  altHover: {
    background: "rgba(169,162,156,0.08)",
    borderColor: "rgba(169,162,156,0.7)",
  },
  chosen: {
    fontSize: 13,
    color: "#a9a29c",
    marginTop: 10,
    fontStyle: "italic",
  },
};

function Choice({ label, alt, onClick }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onFocus={() => setHover(true)}
      onBlur={() => setHover(false)}
      style={{
        ...styles.choice,
        ...(alt ? styles.alt : null),
        ...(hover ? (alt ? styles.altHover : styles.choiceHover) : null),
      }}
    >
      {label}
    </button>
  );
}

export default function FaqChoices({
  candidates = [],
  originalQuery = "",
  onPick,
  onReject,
}) {
  // Once a choice is made the buttons collapse. Without this, stale options
  // stay clickable further up the transcript and re-fire old questions.
  const [chosen, setChosen] = useState(null);

  if (!candidates.length) return null;

  if (chosen) {
    return <div style={styles.chosen}>You selected: {chosen}</div>;
  }

  return (
    <div style={styles.wrap} role="group" aria-label="Matching questions">
      {candidates.map((c) => (
        <Choice
          key={c.id}
          label={c.question}
          onClick={() => {
            setChosen(c.question);
            onPick?.(c);
          }}
        />
      ))}
      <Choice
        alt
        label="None of these — search the documents"
        onClick={() => {
          setChosen("None of these");
          onReject?.(originalQuery);
        }}
      />
    </div>
  );
}
