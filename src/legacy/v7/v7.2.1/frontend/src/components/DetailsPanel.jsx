import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { SettingsIcon, CloseIcon } from "../icons.jsx";

export default function DetailsPanel({ message, index, onClose, onSettings, onRethink, busy }) {
  const meta = message.meta || {};
  const [openSource, setOpenSource] = useState(null);
  const [chunks, setChunks] = useState({});
  const [pick, setPick] = useState(null);
  const [options, setOptions] = useState([]);

  useEffect(() => {
    api.rethinkOptions().then((r) => setOptions(r.options || [])).catch(() => setOptions([]));
  }, []);

  // Reset transient UI when the selected response changes.
  useEffect(() => { setOpenSource(null); setPick(null); }, [index]);

  const toggleSource = async (src) => {
    if (openSource === src.source) { setOpenSource(null); return; }
    setOpenSource(src.source);
    if (!chunks[src.source]) {
      try {
        const r = await api.sourceChunks(src.chunk_ids || []);
        setChunks((c) => ({ ...c, [src.source]: r.chunks || [] }));
      } catch {
        setChunks((c) => ({ ...c, [src.source]: [] }));
      }
    }
  };

  const grounded = meta.grounding_score != null;
  const sources = meta.sources || [];
  const canRethink = message.query && meta.role !== "clarify" && meta.role !== "rejected";

  return (
    <div className="panel">
      <div className="panel-head">
        <button className="icon-btn" onClick={onSettings} aria-label="Settings" title="Settings">
          <SettingsIcon />
        </button>
        <button className="icon-btn" onClick={onClose} aria-label="Close panel" title="Close">
          <CloseIcon />
        </button>
      </div>

      <div className="chips">
        {grounded ? (
          <span className="chip grounded">grounded · {meta.grounding_score}</span>
        ) : (
          <span className="chip unverified">not verified</span>
        )}
        {meta.model && (
          <span className="chip provider">
            {meta.provider ? `${meta.provider}/` : ""}{meta.model}
          </span>
        )}
        {meta.escalated_to_deepseek && <span className="chip muted">escalated to DeepSeek</span>}
      </div>

      {meta.resolved_query && (
        <p className="section-label">Searched for: {meta.resolved_query}</p>
      )}

      <p className="section-label">Sources — click to see what was retrieved</p>
      {sources.length === 0 && <p className="section-label">No sources for this answer.</p>}
      <div className="source-grid">
        {sources.map((s) => (
          <button key={s.source} className="source-btn" onClick={() => toggleSource(s)}>
            {s.source}
          </button>
        ))}
      </div>
      {openSource && (chunks[openSource] || []).map((c, i) => (
        <div className="source-chunk" key={i}>{c.text || c.snippet || String(c)}</div>
      ))}

      {canRethink && (
        <div className="rethink">
          <p className="section-label">Re-answer with another model</p>
          {options.map((o) => {
            const id = `${o.provider}/${o.model}`;
            return (
              <label key={id}>
                <input
                  type="radio"
                  name="rethink"
                  checked={pick === id}
                  onChange={() => setPick(id)}
                />
                {id}
              </label>
            );
          })}
          <button
            className="btn full"
            disabled={!pick || busy}
            onClick={() => {
              const [provider, model] = pick.split("/");
              onRethink(message.query, provider, model);
            }}
          >
            {busy ? "Re-answering…" : "Re-answer"}
          </button>
        </div>
      )}

      <details className="meta">
        <summary>Details</summary>
        <pre>{JSON.stringify(meta, null, 2)}</pre>
      </details>
    </div>
  );
}
