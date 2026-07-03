import React from "react";
import { ChevronRight } from "../icons.jsx";

// One conversation turn. User messages are right-aligned bubbles; assistant
// messages are selectable cards (clicking opens the details panel).
export default function Message({ msg, index, selected, onSelect }) {
  if (msg.role === "user") {
    return (
      <div className="row user">
        <div className="bubble-user">{msg.content}</div>
      </div>
    );
  }

  return (
    <div className="row bot">
      <div
        className={`card ${selected ? "selected" : ""}`}
        onClick={() => onSelect(index)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onSelect(index)}
      >
        {/* Answer text. Backend returns plain text; render line breaks. */}
        {String(msg.content).split("\n").map((line, i) => <p key={i}>{line}</p>)}
        <div className="card-foot">
          {selected ? "Showing details →" : "View sources & options"}
          <ChevronRight />
        </div>
      </div>
    </div>
  );
}
