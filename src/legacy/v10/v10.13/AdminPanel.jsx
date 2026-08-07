import React, { useEffect, useState } from "react";
import { api } from "../api.js";

/* Admin Control (v10.10)
   Tabs: Documents · FAQ · Categories · Products
   - Documents: ingest here, AND a persistent list of every ingested doc
     with its assignment + an always-available reassign dropdown.
   - FAQ: pick a document, view/edit/answer its generated questions.
   - Password on open; category delete re-prompts for password. */
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

  // FAQ tab
  const [faqSource, setFaqSource] = useState("");
  const [faqItems, setFaqItems] = useState([]);
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
  const addProduct = guard(() => api.adminAddProduct(pw, newProd.category_key, newProd.key.trim(), newProd.name.trim(), []));
  const delProduct = guard((ck, pk) => api.adminDeleteProduct(pw, ck, pk));

  const delCategory = async (key, name) => {
    const entered = window.prompt(`Deleting category "${name}" removes its products too.\nRe-enter the admin password to confirm:`);
    if (entered === null) return;
    setErr(null); setBusy(true);
    try {
      await api.adminLogin(entered);
      const c = await api.adminDeleteCategory(entered, key);
      if (c && c.categories) setCat(c);
      onCatalogChanged && onCatalogChanged();
    } catch { setErr("Password incorrect — category not deleted."); }
    finally { setBusy(false); }
  };

  const reassign = async (source, ck, pk) => {
    if (!ck || !pk) return;
    setErr(null); setBusy(true);
    try { await api.adminReassign(pw, source, ck, pk); await refreshSources(); onCatalogChanged && onCatalogChanged(); }
    catch (e) { setErr(String(e.message || e)); }
    finally { setBusy(false); }
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
      await new Promise((r) => setTimeout(r, 700));
      await refresh(); await refreshSources();
      onCatalogChanged && onCatalogChanged();
    } catch (e) { setErr(String(e.message || e)); }
    finally { setBusy(false); setProgress(null); }
  };

  // FAQ tab: load all, filter by chosen source
  const loadFaq = async (source) => {
    setFaqSource(source);
    if (!source) { setFaqItems([]); return; }
    try {
      const r = await api.faq(null);          // all entries
      const items = (r.faq || []).filter((f) => f.source === source);
      setFaqItems(items);
      setFaqDrafts(Object.fromEntries(items.map((it) => [it.id, it.answer || ""])));
    } catch (e) { setErr(String(e.message || e)); }
  };
  const saveFaq = async (id) => {
    try {
      await api.faqEdit(pw, id, faqDrafts[id] ?? "");
      setFaqItems((its) => its.map((it) => it.id === id ? { ...it, answer: faqDrafts[id], edited: true } : it));
    } catch (e) { setErr(String(e.message || e)); }
  };

  // v10.13: generate FAQ from the BROWSER (no backend key). Sample the
  // doc's text, call the provider directly, store the questions.
  const [faqGenProvider, setFaqGenProvider] = useState("deepseek");
  const [genBusy, setGenBusy] = useState(false);
  const generateFaq = async () => {
    if (!faqSource) { setErr("Choose a document first."); return; }
    setErr(null); setGenBusy(true);
    try {
      const meta = sources.find((d) => d.source === faqSource) || {};
      const { sample } = await api.sourceSample(pw, faqSource);
      const questions = await api.generateFaqQuestions(faqGenProvider, sample, 6);
      if (!questions.length) throw new Error("The model returned no questions — try again or a different provider.");
      await api.storeFaq(pw, faqSource, meta.product || "", meta.category || "",
                         questions.map((q) => ({ question: q, answer: "" })));
      await loadFaq(faqSource);
    } catch (e) { setErr(String(e.message || e)); }
    finally { setGenBusy(false); }
  };
  const delFaq = async (id) => {
    try { await api.faqDelete(pw, id); setFaqItems((its) => its.filter((it) => it.id !== id)); }
    catch (e) { setErr(String(e.message || e)); }
  };

  const tagLabel = (d) => d.product
    ? `${d.category || "?"} › ${d.product}`
    : null;

  if (!authed) {
    return (
      <div className="overlay" onClick={onClose}>
        <div className="dialog admin-dialog" onClick={(e) => e.stopPropagation()}>
          <div className="dialog-head"><h2>Admin Control</h2>
            <button className="icon-btn" onClick={onClose}>✕</button></div>
          <p className="hint" style={{ marginTop: 0 }}>Enter the admin password to manage categories, products, documents and FAQ.</p>
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
          {[["documents", "Documents"], ["faq", "FAQ"], ["categories", "Categories"], ["products", "Products"]].map(([t, label]) => (
            <button key={t} className={`admin-tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>{label}</button>
          ))}
        </div>

        {err && <p className="hint" style={{ color: "#e57" }}>{err}</p>}

        {/* -------- DOCUMENTS: ingest + persistent assign list -------- */}
        {tab === "documents" && (
          <>
            <p className="section-label">Ingest a document</p>
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

            <p className="section-label">All documents — assignment</p>
            <p className="hint" style={{ marginTop: 0 }}>Reassign any document at any time. Untagged docs won&apos;t appear in scoped chats until assigned.</p>
            {sources.length === 0 && <p className="hint">No documents ingested yet.</p>}
            {sources.map((d) => (
              <div key={d.source} className="admin-doc-row">
                <div className="admin-doc-name" title={d.source}>{d.source}</div>
                <div className="admin-doc-tag">
                  {tagLabel(d) || <span className="untagged">untagged</span>}
                </div>
                <select className="admin-doc-assign" value=""
                        onChange={(e) => { const [ck, pk] = e.target.value.split("|"); if (pk) reassign(d.source, ck, pk); }}>
                  <option value="">{tagLabel(d) ? "Reassign…" : "Assign to…"}</option>
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

        {/* -------- FAQ: per-document view/edit/answer -------- */}
        {tab === "faq" && (
          <>
            <p className="section-label">FAQ by document</p>
            <div className="admin-row">
              <select value={faqSource} onChange={(e) => loadFaq(e.target.value)}>
                <option value="">Choose a document…</option>
                {sources.map((d) => <option key={d.source} value={d.source}>{d.source}</option>)}
              </select>
            </div>
            {faqSource && (
              <div className="admin-row">
                <label style={{ alignSelf: "center", fontSize: 13, opacity: 0.8 }}>Generate using:</label>
                <select value={faqGenProvider} onChange={(e) => setFaqGenProvider(e.target.value)}>
                  <option value="deepseek">DeepSeek</option>
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Claude</option>
                </select>
                <button className="btn primary" onClick={generateFaq} disabled={genBusy}>
                  {genBusy ? "Generating…" : "Generate FAQ"}
                </button>
              </div>
            )}
            {faqSource && (
              <p className="hint" style={{ marginTop: 0 }}>
                Generation runs in your browser using the API key you set in Settings —
                no key is needed on the server. Click Generate to (re)create questions
                for this document, then optionally write answers below.
              </p>
            )}
            {faqSource && faqItems.length === 0 && (
              <p className="hint">No FAQ questions stored yet — click “Generate FAQ” above.</p>
            )}
            {faqItems.map((it) => (
              <div key={it.id} className="faq-item">
                <div className="faq-q">{it.question}{it.edited && <span className="faq-edited"> ✎</span>}</div>
                <textarea className="faq-editor" rows={3} value={faqDrafts[it.id] ?? ""}
                          onChange={(e) => setFaqDrafts({ ...faqDrafts, [it.id]: e.target.value })} />
                <div className="dialog-actions">
                  <button className="btn small primary" onClick={() => saveFaq(it.id)}>Save answer</button>
                  <button className="btn small" onClick={() => delFaq(it.id)}>Delete</button>
                </div>
              </div>
            ))}
          </>
        )}

        {/* -------- CATEGORIES -------- */}
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

        {/* -------- PRODUCTS -------- */}
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
                    <button className="icon-btn small" title="Delete product" onClick={() => delProduct(c.key, p.key)}>✕</button>
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
