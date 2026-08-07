import React, { useEffect, useState } from "react";
import { api } from "../api.js";

/* v10.4: FAQ page. Lists the doc2query questions generated at ingest for
   a product, showing each with its answer. An admin (password) can edit
   or delete answers. Read-only for everyone else. */
export default function FaqPage({ catalog, onClose }) {
  const [product, setProduct] = useState("");   // "" = all
  const [items, setItems] = useState([]);
  const [pw, setPw] = useState("");
  const [adminMode, setAdminMode] = useState(false);
  const [editing, setEditing] = useState(null);  // id being edited
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState(null);

  const refresh = () =>
    api.faq(product || null).then((r) => setItems(r.faq || [])).catch(() => {});
  useEffect(() => { refresh(); }, [product]);

  const enableAdmin = async () => {
    setErr(null);
    try { await api.adminLogin(pw); setAdminMode(true); }
    catch { setErr("Wrong password."); }
  };

  const save = async (id) => {
    try {
      await api.faqEdit(pw, id, draft);
      setEditing(null);
      refresh();
    } catch (e) { setErr(String(e.message || e)); }
  };

  const del = async (id) => {
    if (!window.confirm("Delete this FAQ entry?")) return;
    try { await api.faqDelete(pw, id); refresh(); }
    catch (e) { setErr(String(e.message || e)); }
  };

  const allProducts = (catalog?.categories || []).flatMap((c) =>
    c.products.map((p) => ({ ...p, cat: c.name })));

  return (
    <div className="chats-page">
      <div className="chats-page-head">
        <h1 className="chats-title">FAQ</h1>
        <div className="chats-actions">
          <select className="chats-filter" value={product}
                  onChange={(e) => setProduct(e.target.value)}>
            <option value="">All products</option>
            {allProducts.map((p) => (
              <option key={p.key} value={p.key}>{p.cat} › {p.name}</option>
            ))}
          </select>
          {!adminMode ? (
            <>
              <input className="chats-filter" type="password" placeholder="Admin password"
                     value={pw} onChange={(e) => setPw(e.target.value)}
                     onKeyDown={(e) => e.key === "Enter" && enableAdmin()} />
              <button className="btn" onClick={enableAdmin} disabled={!pw.trim()}>Edit mode</button>
            </>
          ) : (
            <span className="product-badge">Admin edit mode</span>
          )}
          {onClose && <button className="btn" onClick={onClose}>← Back</button>}
        </div>
      </div>

      {err && <p className="hint" style={{ color: "#e57" }}>{err}</p>}
      {items.length === 0 && (
        <p className="hint" style={{ marginTop: 20 }}>
          No FAQ entries yet — these are generated when documents are ingested.
        </p>
      )}

      <div className="faq-list">
        {items.map((it) => (
          <div key={it.id} className="faq-item">
            <div className="faq-q">
              {it.question}
              {it.edited && <span className="faq-edited" title="Answer edited by admin"> ✎</span>}
            </div>
            {editing === it.id ? (
              <div>
                <textarea className="faq-editor" value={draft}
                          onChange={(e) => setDraft(e.target.value)} rows={4} />
                <div className="dialog-actions">
                  <button className="btn primary" onClick={() => save(it.id)}>Save</button>
                  <button className="btn" onClick={() => setEditing(null)}>Cancel</button>
                </div>
              </div>
            ) : (
              <div className="faq-a">
                {it.answer}
                {adminMode && (
                  <div className="faq-actions">
                    <button className="btn small"
                            onClick={() => { setEditing(it.id); setDraft(it.answer); }}>Edit</button>
                    <button className="icon-btn small" onClick={() => del(it.id)}>✕</button>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
