import React, { useEffect, useState } from "react";
import { api } from "../api.js";

/* Admin Control (v10.8)
   - Tabbed: Documents / Categories / Products
   - The ONLY document ingestion path (the plain Documents dialog is gone;
     the sidebar doc icon opens this panel).
   - Password on open AND again to delete a category.
   - After a doc ingests, its generated FAQ questions are shown with
     editable answers that save to the FAQ store.
   - The re-assign list shows UNTAGGED docs only (assigned docs drop off). */
export default function AdminPanel({ onClose, onCatalogChanged }) {
  const [pw, setPw] = useState("");
  const [authed, setAuthed] = useState(false);
  const [err, setErr] = useState(null);
  const [tab, setTab] = useState("documents");

  const [cat, setCat] = useState({ categories: [] });
  const [sources, setSources] = useState([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  const [ingestProvider, setIngestProvider] = useState("auto");

  const [newCat, setNewCat] = useState({ key: "", name: "" });
  const [newProd, setNewProd] = useState({ category_key: "", key: "", name: "" });

  const [upCat, setUpCat] = useState("");
  const [upProd, setUpProd] = useState("");

  const [faqReview, setFaqReview] = useState(null);
  const [faqDrafts, setFaqDrafts] = useState({});

  const refresh = () => api.catalog().then(setCat).catch(() => {});
  const refreshSources = () => api.adminSources(pw).then((r) => setSources(r.sources || [])).catch(() => {});
  useEffect(() => { if (authed) { refresh(); refreshSources(); } }, [authed]);

  const login = async () => {
    setErr(null);
    try { await api.adminLogin(pw); setAuthed(true); }
    catch { setErr("Wrong password."); }
  };

  const guard = (fn) => async (...a) => {
    setErr(null); setBusy(true);
    try { const c = await fn(...a); if (c && c.categories) setCat(c); onCatalogChanged && onCatalogChanged(); }
    catch (e) { setErr(String(e.message || e)); }
    finally { setBusy(false); }
  };

  const addCategory = guard(() => api.adminAddCategory(pw, newCat.key.trim(), newCat.name.trim()));
  const addProduct = guard(() =>
    api.adminAddProduct(pw, newProd.category_key, newProd.key.trim(), newProd.name.trim(), []));
  const delProduct = guard((ck, pk) => api.adminDeleteProduct(pw, ck, pk));

  // Category delete requires re-entering the password (point 4).
  const delCategory = async (key, name) => {
    const entered = window.prompt(`Deleting category "${name}" removes its products too.\nRe-enter the admin password to confirm:`);
    if (entered === null) return;
    setErr(null); setBusy(true);
    try {
      await api.adminLogin(entered);
      const c = await api.adminDeleteCategory(entered, key);
      if (c && c.categories) setCat(c);
      onCatalogChanged && onCatalogChanged();
    } catch (e) {
      setErr("Password incorrect — category not deleted.");
    } finally { setBusy(false); }
  };

  const doUpload = async (fileList) => {
    if (!upCat || !upProd) { setErr("Choose a category and product first."); return; }
    setErr(null); setBusy(true);
    try {
      for (const f of Array.from(fileList)) {
        setProgress({ file: f.name, pct: 0 });
        await api.adminUploadToProduct(pw, upCat, upProd, f,
          (s) => setProgress({ file: f.name, pct: s.pct || 0, stage: s.stage || s.status }),
          ingestProvider);
      }
      setProgress(null);
      await refresh();
      await refreshSources();
      onCatalogChanged && onCatalogChanged();
      const r = await api.faq(upProd);
      const items = r.faq || [];
      setFaqReview({ product: upProd, items });
      setFaqDrafts(Object.fromEntries(items.map((it) => [it.id, it.answer || ""])));
    } catch (e) { setErr(String(e.message || e)); }
    finally { setBusy(false); setProgress(null); }
  };

  const saveFaqAnswer = async (id) => {
    setErr(null);
    try {
      await api.faqEdit(pw, id, faqDrafts[id] ?? "");
      setFaqReview((fr) => fr && {
        ...fr, items: fr.items.map((it) => it.id === id ? { ...it, answer: faqDrafts[id], edited: true } : it),
      });
    } catch (e) { setErr(String(e.message || e)); }
  };

  const untagged = sources.filter((d) => !d.product);

  if (!authed) {
    return (
      <div className="overlay" onClick={onClose}>
        <div className="dialog admin-dialog" onClick={(e) => e.stopPropagation()}>
          <div className="dialog-head"><h2>Admin Control</h2>
            <button className="icon-btn" onClick={onClose}>✕</button></div>
          <p className="hint" style={{ marginTop: 0 }}>Enter the admin password to manage categories, products, and documents.</p>
          <div className="field">
            <input type="password" placeholder="Admin password" value={pw}
                   onChange={(e) => setPw(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && login()} autoFocus />
          </div>
          {err && <p className="hint" style={{ color: "#e57" }}>{err}</p>}
          <div className="dialog-actions">
            <button className="btn primary" onClick={login} disabled={!pw.trim()}>Unlock</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog admin-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="dialog-head"><h2>Admin Control</h2>
          <button className="icon-btn" onClick={onClose}>✕</button></div>

        <div className="admin-tabs">
          {["documents", "categories", "products"].map((t) => (
            <button key={t} className={`admin-tab ${tab === t ? "active" : ""}`}
                    onClick={() => { setTab(t); setFaqReview(null); }}>
              {t === "documents" ? "Documents" : t === "categories" ? "Categories" : "Products"}
            </button>
          ))}
        </div>

        {err && <p className="hint" style={{ color: "#e57" }}>{err}</p>}

        {tab === "documents" && !faqReview && (
          <>
            <p className="section-label">Ingest a document</p>
            <p className="hint" style={{ marginTop: 0 }}>
              All documents are ingested here. Choose where it belongs, then add the file.
            </p>
            <div className="admin-row">
              <select value={upCat} onChange={(e) => { setUpCat(e.target.value); setUpProd(""); }}>
                <option value="">Category…</option>
                {cat.categories.map((c) => <option key={c.key} value={c.key}>{c.name}</option>)}
              </select>
              <select value={upProd} onChange={(e) => setUpProd(e.target.value)} disabled={!upCat}>
                <option value="">Product…</option>
                {(cat.categories.find((c) => c.key === upCat)?.products || [])
                  .map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
              </select>
            </div>
            <div className="admin-row">
              <label style={{ alignSelf: "center", fontSize: 13, opacity: 0.8 }}>Generate FAQ via:</label>
              <select value={ingestProvider} onChange={(e) => setIngestProvider(e.target.value)}>
                <option value="auto">Auto (API if key set, else local)</option>
                <option value="deepseek">DeepSeek API (fast)</option>
                <option value="openai">OpenAI API</option>
                <option value="anthropic">Claude API</option>
                <option value="local">Local model (slow, private)</option>
              </select>
            </div>
            <label className={`uploader ${(!upCat || !upProd || busy) ? "disabled" : ""}`}
                   style={{ display: "block", cursor: (!upCat || !upProd || busy) ? "not-allowed" : "pointer" }}>
              {busy ? "Ingesting…" : (upCat && upProd ? "Click to add files — .txt / .pdf / .docx" : "Choose category & product first")}
              <input type="file" accept=".txt,.pdf,.docx" multiple style={{ display: "none" }}
                     disabled={!upCat || !upProd || busy}
                     onChange={(e) => e.target.files.length && doUpload(e.target.files)} />
            </label>
            {progress && (
              <div className="pull-progress">
                <div className="pull-row">
                  <span className="pull-name" title={progress.file}>{progress.file}</span>
                  <div className="pull-bar"><div className="pull-fill" style={{ width: `${progress.pct}%` }} /></div>
                  <span className="pull-pct">{Math.round(progress.pct)}%</span>
                </div>
                <p className="hint">{progress.stage === "doc2query" ? "Generating FAQ questions…" : progress.stage}</p>
              </div>
            )}

            <p className="section-label">Unassigned documents</p>
            {untagged.length === 0
              ? <p className="hint">All ingested documents are assigned to a product.</p>
              : untagged.map((d) => (
                <div key={d.source} className="admin-doc-row">
                  <div className="admin-doc-name" title={d.source}>{d.source}</div>
                  <div className="admin-doc-tag"><span className="untagged">untagged</span></div>
                  <select className="admin-doc-assign" value=""
                          onChange={(e) => {
                            const [ck, pk] = e.target.value.split("|");
                            if (pk) guard(() => api.adminReassign(pw, d.source, ck, pk).then(() => { refreshSources(); return null; }))();
                          }}>
                    <option value="">Assign to…</option>
                    {cat.categories.map((c) => (
                      <optgroup key={c.key} label={c.name}>
                        {c.products.map((p) => (
                          <option key={p.key} value={`${c.key}|${p.key}`}>{c.name} › {p.name}</option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </div>
              ))}
          </>
        )}

        {tab === "documents" && faqReview && (
          <>
            <p className="section-label">Review generated FAQ</p>
            <p className="hint" style={{ marginTop: 0 }}>
              These questions were generated from the document. Optionally refine the
              answers — they are used as conversation starters and cached responses.
            </p>
            {faqReview.items.length === 0 && <p className="hint">No FAQ questions were generated for this document.</p>}
            {faqReview.items.map((it) => (
              <div key={it.id} className="faq-item">
                <div className="faq-q">{it.question}{it.edited && <span className="faq-edited"> ✎</span>}</div>
                <textarea className="faq-editor" rows={3} value={faqDrafts[it.id] ?? ""}
                          onChange={(e) => setFaqDrafts({ ...faqDrafts, [it.id]: e.target.value })} />
                <div className="dialog-actions">
                  <button className="btn small primary" onClick={() => saveFaqAnswer(it.id)}>Save answer</button>
                </div>
              </div>
            ))}
            <div className="dialog-actions">
              <button className="btn" onClick={() => setFaqReview(null)}>Done</button>
            </div>
          </>
        )}

        {tab === "categories" && (
          <>
            <p className="section-label">Add category</p>
            <div className="admin-row">
              <input placeholder="key (e.g. note_validators)" value={newCat.key}
                     onChange={(e) => setNewCat({ ...newCat, key: e.target.value })} />
              <input placeholder="Name (e.g. Note Validators)" value={newCat.name}
                     onChange={(e) => setNewCat({ ...newCat, name: e.target.value })} />
              <button className="btn" onClick={addCategory} disabled={busy || !newCat.key || !newCat.name}>Add</button>
            </div>
            <p className="section-label">Categories</p>
            {cat.categories.map((c) => (
              <div key={c.key} className="admin-cat-line">
                <b style={{ flex: 1 }}>{c.name}</b>
                <span className="hint" style={{ margin: 0 }}>{c.products.length} product(s)</span>
                <button className="icon-btn small" title="Delete category (password required)"
                        onClick={() => delCategory(c.key, c.name)}>✕</button>
              </div>
            ))}
          </>
        )}

        {tab === "products" && (
          <>
            <p className="section-label">Add product to category</p>
            <div className="admin-row">
              <select value={newProd.category_key}
                      onChange={(e) => setNewProd({ ...newProd, category_key: e.target.value })}>
                <option value="">Choose category…</option>
                {cat.categories.map((c) => <option key={c.key} value={c.key}>{c.name}</option>)}
              </select>
              <input placeholder="product key" value={newProd.key}
                     onChange={(e) => setNewProd({ ...newProd, key: e.target.value })} />
              <input placeholder="Product name" value={newProd.name}
                     onChange={(e) => setNewProd({ ...newProd, name: e.target.value })} />
              <button className="btn" onClick={addProduct}
                      disabled={busy || !newProd.category_key || !newProd.key || !newProd.name}>Add</button>
            </div>
            <p className="section-label">Products by category</p>
            {cat.categories.map((c) => (
              <div key={c.key} className="admin-cat">
                <div className="admin-cat-head"><b>{c.name}</b></div>
                {c.products.map((p) => (
                  <div key={p.key} className="admin-prod">
                    <span>{p.name}</span>
                    <button className="icon-btn small" title="Delete product"
                            onClick={() => delProduct(c.key, p.key)}>✕</button>
                  </div>
                ))}
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
