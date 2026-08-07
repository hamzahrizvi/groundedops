import React, { useEffect, useState } from "react";
import { api } from "../api.js";

/* v10.10: openable FAQ side panel inside the chat view.
   - Shows FAQ relevant to the current chat scope.
   - Product chat -> that product's questions (flat).
   - General/category chat -> questions grouped by product.
   - Clicking a question asks it (onAsk). */
export default function ChatFaqPanel({ open, onToggle, scopeKey, scopeIsCategory, catalog, onAsk }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || !scopeKey) { setItems([]); return; }
    setLoading(true);
    api.faq(scopeKey)
      .then((r) => setItems(r.faq || []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, [open, scopeKey]);

  // product-name lookup for grouping labels
  const prodName = (key) => {
    for (const c of (catalog?.categories || []))
      for (const p of c.products) if (p.key === key) return p.name;
    return key || "General";
  };

  // group by product when this is a category-level chat
  let grouped = null;
  if (scopeIsCategory) {
    grouped = {};
    for (const it of items) {
      const keys = (it.products || "").split(",").filter(Boolean);
      const k = keys[0] || "general";
      (grouped[k] = grouped[k] || []).push(it);
    }
  }

  return (
    <div className={`faq-side ${open ? "open" : ""}`}>
      <button className="faq-side-tab" onClick={onToggle} title="Frequently asked questions">
        {open ? "‹" : "›"} <span className="faq-side-tab-label">FAQ</span>
      </button>
      {open && (
        <div className="faq-side-body">
          <h4>Frequently asked</h4>
          {loading && <p className="hint">Loading…</p>}
          {!loading && items.length === 0 && (
            <p className="hint">No FAQ for this selection yet.</p>
          )}
          {!loading && !scopeIsCategory && items.map((it) => (
            <button key={it.id} className="faq-side-q" onClick={() => onAsk(it.question)}>
              {it.question}
            </button>
          ))}
          {!loading && scopeIsCategory && grouped && Object.entries(grouped).map(([pk, qs]) => (
            <div key={pk} className="faq-side-group">
              <div className="faq-side-group-label">{prodName(pk)}</div>
              {qs.map((it) => (
                <button key={it.id} className="faq-side-q" onClick={() => onAsk(it.question)}>
                  {it.question}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
