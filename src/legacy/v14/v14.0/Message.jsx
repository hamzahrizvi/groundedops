import React from "react";
import { ChevronRight } from "../icons.jsx";
import FaqChoices from "./FaqChoices.jsx";

// One conversation turn. User messages are right-aligned bubbles; assistant
// messages are selectable cards (clicking opens the details panel).
export default function Message({ msg, index, selected, onSelect, onFaqPick, onFaqReject }) {
  if (msg.role === "user") {
    return (
      <div className="row user">
        <div className="bubble-user">{msg.content}</div>
      </div>
    );
  }

  const candidates = msg.meta?.faq_candidates || [];
  const hasSources = (msg.meta?.sources?.length || 0) > 0;

  return (
    <div className="row bot">
      <div
        className={`card ${selected ? "selected" : ""}`}
        onClick={() => hasSources && onSelect(index)}
        role={hasSources ? "button" : undefined}
        tabIndex={hasSources ? 0 : undefined}
        onKeyDown={(e) => hasSources && (e.key === "Enter" || e.key === " ") && onSelect(index)}
      >
        {/* Answer text. Backend returns plain text; render line breaks. */}
        {String(msg.content).split("\n").map((line, i) => <p key={i}>{line}</p>)}

        {candidates.length > 0 && (
          // stopPropagation: this card's onClick opens the details panel —
          // picking/rejecting a suggestion shouldn't also trigger that.
          <div onClick={(e) => e.stopPropagation()}>
            <FaqChoices
              candidates={candidates}
              originalQuery={msg.query || ""}
              onPick={onFaqPick}
              onReject={onFaqReject}
            />
          </div>
        )}

        {/* A clarification or refusal has no sources/candidates behind it —
            offering the toggle there opens a panel with nothing in it. */}
        {hasSources && (
          <div className="card-foot">
            {selected ? "Showing details →" : "View sources & options"}
            <ChevronRight />
          </div>
        )}
      </div>
    </div>
  );
}
