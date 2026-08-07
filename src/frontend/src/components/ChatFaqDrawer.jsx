import React, { useEffect, useState } from "react";
import { api } from "../api.js";

/* v10.11: FAQ as a LEFT-side drawer, mirroring the right-side Details
   drawer. Docked to the left edge, full height, slides in from the left,
   backdrop-click closes. Content = FAQ for the current chat scope;
   grouped by product in a category/general chat. */
export default function ChatFaqDrawer({ scopeKey, scopeIsCategory, catalog, onAsk, onClose }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!scopeKey) { setItems([]); setLoading(false); return; }
    setLoading(true);
    api.faq(scopeKey)
      .then((r) => setItems(r.faq || []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, [scopeKey]);

  const prodName = (key) => {
    for (const c of (catalog?.categories || []))
      for (const p of c.products) if (p.key === key) return p.name;
    return key || "General";
  };

  let grouped = null;
  if (scopeIsCategory) {
    grouped = {};
    for (const it of items) {
      const k = (it.products || "").split(",").filter(Boolean)[0] || "general";
      (grouped[k] = grouped[k] || []).push(it);
    }
  }

  return (
    <div className="faq-drawer-panel">
      <div className="panel-head panel-head-left">
        <button className="icon-btn" onClick={onClose} title="Close">✕</button>
        <h3 style={{ margin: 0, flex: 1 }}>Frequently asked</h3>
      </div>

        {loading && <p className="hint">Loading…</p>}
        {!loading && items.length === 0 && (
          <p className="hint">No FAQ for this selection yet. Generate questions by
            ingesting this product’s documents through Admin Control.</p>
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
  );
}
