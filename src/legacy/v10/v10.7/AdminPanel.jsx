import React, { useEffect, useState } from "react";
import { api } from "../api.js";

/* v10.3: admin panel. Password-gated (default "admin" — TEMPORARY, see
   backend note). Lets the admin add/delete categories and products and
   upload documents directly into a product (the doc is ingested and
   tagged to that product/category for scoping). */
export default function AdminPanel({ onClose, onCatalogChanged }) {
  const [pw, setPw] = useState("");
  const [authed, setAuthed] = useState(false);
  const [err, setErr] = useState(null);
  const [cat, setCat] = useState({ categories: [] });
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  // v10.6: per-upload ingest provider (doc2query). "auto" uses API if a
  // key is configured, else local. Explicit choices force that path.
  const [ingestProvider, setIngestProvider] = useState("auto");
  // v10.7: ingested docs + their current tags, for the re-assign tool.
  const [sources, setSources] = useState([]);
  const refreshSources = () => api.adminSources(pw).then((r) => setSources(r.sources || [])).catch(() => {});

  // add-forms
  const [newCat, setNewCat] = useState({ key: "", name: "" });
  const [newProd, setNewProd] = useState({ category_key: "", key: "", name: "" });

  const refresh = () => api.catalog().then(setCat).catch(() => {});
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
  const delCategory = guard((k) => api.adminDeleteCategory(pw, k));
  const addProduct = guard(() =>
    api.adminAddProduct(pw, newProd.category_key, newProd.key.trim(), newProd.name.trim(), []));
  const delProduct = guard((ck, pk) => api.adminDeleteProduct(pw, ck, pk));

  const reassign = async (source, categoryKey, productKey) => {
    if (!categoryKey || !productKey) return;
    setErr(null); setBusy(true);
    try {
      await api.adminReassign(pw, source, categoryKey, productKey);
      await refreshSources();
      onCatalogChanged && onCatalogChanged();
    } catch (e) { setErr(String(e.message || e)); }
    finally { setBusy(false); }
  };

  const uploadToProduct = async (ck, pk, fileList) => {
    setErr(null); setBusy(true);
    try {
      for (const f of Array.from(fileList)) {
        setProgress({ file: f.name, pct: 0 });
        await api.adminUploadToProduct(pw, ck, pk, f,
          (s) => setProgress({ file: f.name, pct: s.pct || 0, stage: s.stage || s.status }),
          ingestProvider);
      }
      setProgress(null);
      await refresh();
      await refreshSources();
      onCatalogChanged && onCatalogChanged();
    } catch (e) { setErr(String(e.message || e)); }
    finally { setBusy(false); setProgress(null); }
  };

  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog admin-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="dialog-head">
          <h2>Admin — catalog & documents</h2>
          <button className="icon-btn" onClick={onClose}>✕</button>
        </div>

        {!authed ? (
          <>
            <p className="hint" style={{ marginTop: 0 }}>Enter the admin password to manage categories, products, and documents.</p>
            <div className="field">
              <input type="password" placeholder="Admin password" value={pw}
                     onChange={(e) => setPw(e.target.value)}
                     onKeyDown={(e) => e.key === "Enter" && login()} />
            </div>
            {err && <p className="hint" style={{ color: "#e57" }}>{err}</p>}
            <div className="dialog-actions">
              <button className="btn primary" onClick={login} disabled={!pw.trim()}>Unlock</button>
            </div>
          </>
        ) : (
          <>
            {err && <p className="hint" style={{ color: "#e57" }}>{err}</p>}
            {progress && (
              <div className="pull-progress">
                <div className="pull-row">
                  <span className="pull-name" title={progress.file}>{progress.file}</span>
                  <div className="pull-bar"><div className="pull-fill" style={{ width: `${progress.pct}%` }} /></div>
                  <span className="pull-pct">{Math.round(progress.pct)}%</span>
                </div>
              </div>
            )}

            <p className="section-label">Add category</p>
            <div className="admin-row">
              <input placeholder="key (e.g. note_validators)" value={newCat.key}
                     onChange={(e) => setNewCat({ ...newCat, key: e.target.value })} />
              <input placeholder="Name (e.g. Note Validators)" value={newCat.name}
                     onChange={(e) => setNewCat({ ...newCat, name: e.target.value })} />
              <button className="btn" onClick={addCategory} disabled={busy || !newCat.key || !newCat.name}>Add</button>
            </div>

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

            <p className="section-label">Document ingestion</p>
            <div className="admin-row">
              <label style={{ alignSelf: "center", fontSize: 13, opacity: 0.8 }}>
                Generate FAQ questions via:
              </label>
              <select value={ingestProvider} onChange={(e) => setIngestProvider(e.target.value)}>
                <option value="auto">Auto (API if key set, else local)</option>
                <option value="deepseek">DeepSeek API (fast)</option>
                <option value="openai">OpenAI API</option>
                <option value="anthropic">Claude API</option>
                <option value="local">Local model (slow, private)</option>
              </select>
            </div>
            <p className="hint">
              Applies to documents you upload below. API options are much faster
              for large docs; local keeps everything on-device.
            </p>

            <p className="section-label">Ingested documents — assign to a product</p>
            <p className="hint" style={{ marginTop: 0 }}>
              Docs uploaded via the plain Documents dialog are untagged and
              won&apos;t appear in any scoped chat until assigned here.
            </p>
            {sources.length === 0 && <p className="hint">No documents ingested yet.</p>}
            {sources.map((d) => (
              <div key={d.source} className="admin-doc-row">
                <div className="admin-doc-name" title={d.source}>{d.source}</div>
                <div className="admin-doc-tag">
                  {d.product ? `${d.category || "?"} › ${d.product}` : <span className="untagged">untagged</span>}
                </div>
                <select className="admin-doc-assign"
                        onChange={(e) => {
                          const [ck, pk] = e.target.value.split("|");
                          if (pk) reassign(d.source, ck, pk);
                        }}
                        value="">
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

            <p className="section-label">Catalog</p>
            {cat.categories.map((c) => (
              <div key={c.key} className="admin-cat">
                <div className="admin-cat-head">
                  <b>{c.name}</b>
                  <button className="icon-btn small" title="Delete category"
                          onClick={() => delCategory(c.key)}>✕</button>
                </div>
                {c.products.map((p) => (
                  <div key={p.key} className="admin-prod">
                    <span>{p.name}</span>
                    <div className="admin-prod-actions">
                      <label className="btn small" style={{ cursor: "pointer" }}>
                        + Docs
                        <input type="file" accept=".txt,.pdf,.docx" multiple style={{ display: "none" }}
                               disabled={busy}
                               onChange={(e) => e.target.files.length && uploadToProduct(c.key, p.key, e.target.files)} />
                      </label>
                      <button className="icon-btn small" title="Delete product"
                              onClick={() => delProduct(c.key, p.key)}>✕</button>
                    </div>
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
