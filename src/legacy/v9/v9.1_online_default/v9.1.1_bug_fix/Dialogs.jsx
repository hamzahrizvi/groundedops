import React, { useEffect, useState } from "react";
import { api, getKey, setKey, clearKey, hasKey } from "../api.js";
import { CloseIcon } from "../icons.jsx";

function Overlay({ children, onClose }) {
  // Close on backdrop click or Escape.
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      {children}
    </div>
  );
}

export function SettingsDialog({ dark, onToggleTheme, onClose, mode, modelsLoaded, modelsBusy, onLoadModels, onUnloadModels }) {
  const [keyInput, setKeyInput] = useState("");
  const [keySet, setKeySet] = useState(hasKey());

  // The key is never read back into the field — we only show status.
  const save = () => {
    if (!keyInput.trim()) return;
    setKey(keyInput.trim());
    setKeyInput("");
    setKeySet(true);
  };
  const clear = () => {
    clearKey();
    setKeyInput("");
    setKeySet(false);
  };

  return (
    <Overlay onClose={onClose}>
      <div className="dialog">
        <div className="dialog-head">
          <h2>Settings</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </button>
        </div>

        <p className="section-label">Appearance</p>
        <button className="btn full" onClick={onToggleTheme}>
          Switch to {dark ? "light" : "dark"} mode
        </button>

        <p className="section-label">Local models (Free mode)</p>
        <div className="status-line">
          <span className={`dot ${modelsLoaded ? "on" : "off"}`} />
          {modelsLoaded ? "mistral + phi loaded in memory" : "Not loaded"}
        </div>
        <p className="hint">
          Free mode runs entirely on this machine. Loading takes a minute or
          two and the models hold several GB of RAM while loaded; answers are
          also noticeably slower than Online mode. Nothing loads automatically —
          use the buttons below.
        </p>
        <div className="dialog-actions">
          <button className="btn primary" onClick={onLoadModels}
                  disabled={modelsBusy || modelsLoaded}>
            {modelsBusy ? "Working…" : "Load local models"}
          </button>
          <button className="btn" onClick={onUnloadModels}
                  disabled={modelsBusy || !modelsLoaded}>
            Unload (free RAM)
          </button>
        </div>
        {mode === "api" && modelsLoaded && (
          <p className="hint">
            You&apos;re in Online mode — the loaded local models aren&apos;t being
            used and can be unloaded to free memory.
          </p>
        )}

        <p className="section-label">DeepSeek API key</p>
        <div className="status-line">
          <span className={`dot ${keySet ? "on" : "off"}`} />
          {keySet ? "Key saved in this browser" : "No key set"}
        </div>
        <div className="field">
          <label>{keySet ? "Replace key" : "Enter key"}</label>
          <input
            type="password"
            placeholder="sk-..."
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && save()}
          />
        </div>
        <div className="dialog-actions">
          <button className="btn primary" onClick={save} disabled={!keyInput.trim()}>
            Save
          </button>
          <button className="btn" onClick={clear} disabled={!keySet}>
            Clear
          </button>
        </div>
        <p className="section-label" style={{ marginTop: 14 }}>
          Stored only in this browser and sent with each question. It's never shown again.
        </p>
      </div>
    </Overlay>
  );
}

/* v8.6.1: proper mode-switch dialog (replaces window.confirm + inline
   note). Two directions:
   - to "local" (Free): warn about time/RAM, let the user pick which
     models to load (or none — first answer then pays cold-load).
   - to "api" (Online): if local models are loaded, let the user pick
     which to shut down to free RAM (or keep them). */
export function ModeDialog({ direction, modelsLoaded, busy, onConfirm, onClose }) {
  const toFree = direction === "local";
  const [sel, setSel] = useState({ mistral: true, phi: true });
  const toggle = (m) => setSel((s) => ({ ...s, [m]: !s[m] }));
  const chosen = Object.keys(sel).filter((m) => sel[m]);

  return (
    <Overlay onClose={onClose}>
      <div className="dialog">
        <div className="dialog-head">
          <h2>{toFree ? "Switch to Free (local) mode" : "Switch to Online mode"}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </button>
        </div>

        {toFree ? (
          <>
            <p className="hint" style={{ marginTop: 0 }}>
              Free mode answers entirely on this machine — private and no API
              cost, but <b>noticeably slower per answer</b> and the models hold
              <b> several GB of RAM</b> while loaded. Loading takes a minute or
              two the first time.
            </p>
            <p className="section-label">Load now (recommended):</p>
            <label className="check-row">
              <input type="checkbox" checked={sel.mistral} onChange={() => toggle("mistral")} />
              <span><b>mistral</b> — the answering model (~4 GB RAM). Required for answers.</span>
            </label>
            <label className="check-row">
              <input type="checkbox" checked={sel.phi} onChange={() => toggle("phi")} />
              <span><b>phi</b> — follow-up question resolver (~2 GB RAM). Without it,
              follow-ups use a simpler built-in fallback.</span>
            </label>
            <p className="hint">
              Loading nothing is allowed — the first answer will just be slow
              while models cold-load.
            </p>
            <div className="dialog-actions">
              <button className="btn primary" disabled={busy}
                      onClick={() => onConfirm(chosen)}>
                {busy ? "Working…" : chosen.length ? `Switch & load ${chosen.join(" + ")}` : "Switch without loading"}
              </button>
              <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
            </div>
          </>
        ) : (
          <>
            <p className="hint" style={{ marginTop: 0 }}>
              Online mode answers via the DeepSeek API — fast, but requires
              your API key (see Settings) and sends questions to the API.
            </p>
            {modelsLoaded ? (
              <>
                <p className="section-label">Local models are still holding RAM. Shut down:</p>
                <label className="check-row">
                  <input type="checkbox" checked={sel.mistral} onChange={() => toggle("mistral")} />
                  <span><b>mistral</b> (~4 GB)</span>
                </label>
                <label className="check-row">
                  <input type="checkbox" checked={sel.phi} onChange={() => toggle("phi")} />
                  <span><b>phi</b> (~2 GB)</span>
                </label>
                <p className="hint">
                  Keeping them loaded makes switching back to Free instant, at
                  the cost of the RAM they hold.
                </p>
              </>
            ) : (
              <p className="hint">No local models are loaded — nothing to shut down.</p>
            )}
            <div className="dialog-actions">
              <button className="btn primary" disabled={busy}
                      onClick={() => onConfirm(modelsLoaded ? chosen : [])}>
                {busy ? "Working…" : modelsLoaded && chosen.length
                  ? `Switch & unload ${chosen.join(" + ")}` : "Switch to Online"}
              </button>
              <button className="btn" onClick={onClose} disabled={busy}>Cancel</button>
            </div>
          </>
        )}
      </div>
    </Overlay>
  );
}

export function DocumentsDialog({ onClose, onChanged }) {
  const [stats, setStats] = useState({ sources: [], total_chunks: 0 });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const refresh = () => api.stats().then(setStats).catch((e) => setErr(String(e)));
  useEffect(() => { refresh(); }, []);

  const onFiles = async (fileList) => {
    setErr(null);
    setBusy(true);
    try {
      // Upload every selected file in one pass.
      for (const f of Array.from(fileList)) {
        await api.upload(f);
      }
      await refresh();
      onChanged && onChanged();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (src) => {
    setErr(null);
    try {
      await api.deleteSource(src);
      await refresh();
      onChanged && onChanged();
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <Overlay onClose={onClose}>
      <div className="dialog">
        <div className="dialog-head">
          <h2>Documents</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </button>
        </div>
        <p className="section-label">
          {stats.sources.length} document{stats.sources.length === 1 ? "" : "s"} · {stats.total_chunks} chunks
        </p>

        <label className="uploader" style={{ display: "block", cursor: "pointer" }}>
          {busy ? "Uploading…" : "Click to add files — .txt / .pdf / .docx"}
          <input
            type="file"
            accept=".txt,.pdf,.docx"
            multiple
            style={{ display: "none" }}
            disabled={busy}
            onChange={(e) => e.target.files.length && onFiles(e.target.files)}
          />
        </label>

        {err && <p style={{ color: "var(--amber)", fontSize: "0.85rem" }}>{err}</p>}

        <div className="doc-list">
          {stats.sources.length === 0 && <p className="section-label">No documents yet.</p>}
          {stats.sources.map((src) => (
            <div className="doc-row" key={src}>
              <span className="name">{src}</span>
              <button className="x" onClick={() => remove(src)} aria-label={`Remove ${src}`}>
                ✕
              </button>
            </div>
          ))}
        </div>
      </div>
    </Overlay>
  );
}
