import React, { useMemo, useState } from "react";

/* v9.1.4: full-page Chats view (modeled on the requested design).
   Opens when the user presses "New chat" in the rail. Rows show the
   chat title, a "RAG chat" tag, and a relative timestamp. Includes
   search, a time filter, and a select mode for bulk delete.

   WHERE CHATS LIVE: browser localStorage, key "groundedops.chats"
   (DevTools -> Application -> Local Storage). They are per-browser and
   survive frontend AND backend restarts, but do not sync across
   machines. Server-side persistence is a deployment-phase item. */

function timeAgo(ts) {
  if (!ts) return "";
  const s = Math.max(1, Math.floor((Date.now() - ts) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} minute${m === 1 ? "" : "s"} ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} hour${h === 1 ? "" : "s"} ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d} day${d === 1 ? "" : "s"} ago`;
  const mo = Math.floor(d / 30);
  return `${mo} month${mo === 1 ? "" : "s"} ago`;
}

const FILTERS = {
  All: () => true,
  Today: (c) => Date.now() - (c.updated || 0) < 24 * 3600 * 1000,
  "Last 7 days": (c) => Date.now() - (c.updated || 0) < 7 * 24 * 3600 * 1000,
};

export default function ChatsPage({ chats, currentId, onOpen, onNew, onDeleteMany, onClose }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All");
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState({});

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return chats
      .filter(FILTERS[filter] || FILTERS.All)
      .filter((c) => !q || (c.title || "").toLowerCase().includes(q));
  }, [chats, query, filter]);

  const chosen = Object.keys(selected).filter((id) => selected[id]);

  const toggleSelect = (id) =>
    setSelected((s) => ({ ...s, [id]: !s[id] }));

  const deleteChosen = () => {
    if (!chosen.length) return;
    if (!window.confirm(`Delete ${chosen.length} chat${chosen.length === 1 ? "" : "s"}? This cannot be undone.`)) return;
    onDeleteMany(chosen);
    setSelected({});
    setSelectMode(false);
  };

  return (
    <div className="chats-page">
      <div className="chats-page-head">
        <h1 className="chats-title">Chats</h1>
        <div className="chats-actions">
          <select
            className="chats-filter"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            aria-label="Filter chats"
          >
            {Object.keys(FILTERS).map((f) => (
              <option key={f} value={f}>Filter by {f}</option>
            ))}
          </select>
          {selectMode ? (
            <>
              <button className="btn" onClick={deleteChosen} disabled={!chosen.length}>
                Delete{chosen.length ? ` (${chosen.length})` : ""}
              </button>
              <button className="btn" onClick={() => { setSelectMode(false); setSelected({}); }}>
                Cancel
              </button>
            </>
          ) : (
            <button className="btn" onClick={() => setSelectMode(true)} disabled={!chats.length}>
              Select chats
            </button>
          )}
          <button className="btn primary chats-new" onClick={onNew}>New chat</button>
        </div>
      </div>

      <div className="chats-search">
        <span className="chats-search-ico">⌕</span>
        <input
          placeholder="Search chats…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {visible.length === 0 && (
        <p className="hint" style={{ marginTop: 24 }}>
          {chats.length === 0
            ? "No saved chats yet — start one with New chat."
            : "No chats match your search."}
        </p>
      )}

      <div className="chats-list">
        {visible.map((c) => (
          <div key={c.id} className={`chats-row ${c.id === currentId ? "current" : ""}`}>
            {selectMode && (
              <input
                type="checkbox"
                checked={!!selected[c.id]}
                onChange={() => toggleSelect(c.id)}
              />
            )}
            <button
              className="chats-row-title"
              onClick={() => (selectMode ? toggleSelect(c.id) : onOpen(c))}
              title={c.title}
            >
              {c.title || "Untitled chat"}
            </button>
            <span className="chats-row-tag">RAG chat</span>
            <span className="chats-row-time">{timeAgo(c.updated)}</span>
          </div>
        ))}
      </div>

      {onClose && (
        <button className="btn chats-back" onClick={onClose}>
          ← Back to current chat
        </button>
      )}
    </div>
  );
}
