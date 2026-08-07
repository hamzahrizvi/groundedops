import React, { useEffect, useState } from "react";
import { api, getKey, setKey, clearKey, hasKey, getProviderKey, setProviderKey, clearProviderKey, hasProviderKey } from "../api.js";
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

/* v9.1.2: shared Online-keys section — used by BOTH the Settings dialog
   and the startup popup, so the two are literally the same UI. Reports
   {provider, hasKey} upward via onState when provided. */
export function OnlineKeysSection({ onState }) {
  // v9.1.1: multi-provider keys (DeepSeek / OpenAI / Claude). Keys are
  // never read back into the fields — status dots only.
  const [keyInputs, setKeyInputs] = useState({ deepseek: "", openai: "", anthropic: "" });
  const [keyState, setKeyState] = useState({
    deepseek: hasProviderKey("deepseek"),
    openai: hasProviderKey("openai"),
    anthropic: hasProviderKey("anthropic"),
  });
  const [provider, setProvider] = useState("deepseek");

  useEffect(() => {
    api.settings().then((s) => setProvider(s.online_provider || "deepseek")).catch(() => {});
  }, []);

  const changeProvider = async (p) => {
    setProvider(p);
    try { await api.setOnlineProvider(p); } catch (e) { alert(`Provider switch failed: ${e.message}`); }
  };
  const saveKey = (p) => {
    const v = keyInputs[p].trim();
    if (!v) return;
    setProviderKey(p, v);
    setKeyInputs((k) => ({ ...k, [p]: "" }));
    setKeyState((k) => ({ ...k, [p]: true }));
  };
  const clearProviderKeyUI = (p) => {
    clearProviderKey(p);
    setKeyInputs((k) => ({ ...k, [p]: "" }));
    setKeyState((k) => ({ ...k, [p]: false }));
  };

  useEffect(() => {
    if (onState) onState({ provider, hasKey: !!keyState[provider] });
  }, [provider, keyState]);

  return (
    <>
        <p className="section-label">Online API keys</p>
        <p className="hint" style={{ marginTop: 0 }}>
          Online mode answers with the provider selected below. Keys are
          stored only in this browser and sent with each question. They&apos;re
          never shown again.
        </p>
        <div className="field">
          <label>Online provider</label>
          <select value={provider} onChange={(e) => changeProvider(e.target.value)}>
            <option value="deepseek">DeepSeek</option>
            <option value="openai">OpenAI</option>
            <option value="anthropic">Claude (Anthropic)</option>
          </select>
        </div>
        {["deepseek", "openai", "anthropic"].map((p) => (
          <div key={p}>
            <div className="status-line">
              <span className={`dot ${keyState[p] ? "on" : "off"}`} />
              {p === "anthropic" ? "Claude" : p === "openai" ? "OpenAI" : "DeepSeek"}:{" "}
              {keyState[p] ? "Key saved in this browser" : "No key set"}
            </div>
            <div className="field">
              <input
                type="password"
                placeholder={p === "anthropic" ? "sk-ant-..." : "sk-..."}
                value={keyInputs[p]}
                onChange={(e) => setKeyInputs((k) => ({ ...k, [p]: e.target.value }))}
                onKeyDown={(e) => e.key === "Enter" && saveKey(p)}
              />
            </div>
            <div className="dialog-actions">
              <button className="btn primary" onClick={() => saveKey(p)}
                      disabled={!keyInputs[p].trim()}>Save</button>
              <button className="btn" onClick={() => clearProviderKeyUI(p)}
                      disabled={!keyState[p]}>Clear</button>
            </div>
          </div>
        ))}
    </>
  );
}

/* v9.1.2: startup popup. The app now opens in Online mode; if no key is
   saved for the selected provider, this appears — same key UI as the
   sidebar Settings — and offers Free mode as the no-key path. */
export function StartupKeysDialog({ onClose, onSwitchFree }) {
  const [state, setState] = useState({ provider: "deepseek", hasKey: false });
  return (
    <Overlay onClose={() => { /* v9.1.3: not dismissable */ }}>
      <div className="dialog">
        <div className="dialog-head">
          <h2>Online mode — API key needed</h2>
          {/* v9.1.3: no close button — this gate requires a decision:
              save a key and continue online, or switch to Free mode. */}
        </div>
        <p className="hint" style={{ marginTop: 0 }}>
          GroundedOps starts in Online mode — answers via API (fast, uses
          your key). Enter or update a key below, or switch to Free mode to
          run entirely on this machine (private, slower, uses RAM).
        </p>
        <OnlineKeysSection onState={setState} />
        {!state.hasKey && (
          <p className="hint">
            No key saved for the selected provider yet — save one to continue
            online, or switch to Free mode.
          </p>
        )}
        <div className="dialog-actions">
          <button className="btn primary" disabled={!state.hasKey} onClick={onClose}>
            Continue online
          </button>
          <button className="btn" onClick={onSwitchFree}>
            Switch to Free mode
          </button>
        </div>
      </div>
    </Overlay>
  );
}

export function SettingsDialog({ dark, onToggleTheme, onClose, mode, modelsLoaded, modelsBusy, onLoadModels, onUnloadModels }) {


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

        <OnlineKeysSection />
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
export function ModeDialog({ direction, modelsLoaded, busy, required, pullProgress, onConfirm, onClose }) {
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
            {required ? (
              <p className="hint">
                To use Free mode, at least <b>mistral</b> must be loaded — this
                is what answers your questions. If a model isn&apos;t installed
                yet it will be downloaded first (progress shown below).
              </p>
            ) : (
              <p className="hint">
                Loading nothing is allowed — the first answer will just be slow
                while models cold-load.
              </p>
            )}
            {pullProgress && (
              <div className="pull-progress">
                {Object.entries(pullProgress).map(([m, p]) => (
                  <div key={m} className="pull-row">
                    <span className="pull-name">{m}</span>
                    <div className="pull-bar">
                      <div className="pull-fill" style={{ width: `${p.pct || 0}%` }} />
                    </div>
                    <span className="pull-pct">
                      {p.status === "error" ? "failed" : `${Math.round(p.pct || 0)}%`}
                    </span>
                  </div>
                ))}
                <p className="hint">Downloading models — this can take several minutes on a slow connection.</p>
              </div>
            )}
            <div className="dialog-actions">
              <button className="btn primary"
                      disabled={busy || (required && !sel.mistral)}
                      onClick={() => onConfirm(chosen)}>
                {busy ? "Working…"
                  : chosen.length ? `Switch & load ${chosen.join(" + ")}`
                  : required ? "Select mistral to continue" : "Switch without loading"}
              </button>
              <button className="btn" onClick={onClose} disabled={busy}>{required ? "Back to key entry" : "Cancel"}</button>
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

  const [progress, setProgress] = useState(null);

  const onFiles = async (fileList) => {
    setErr(null);
    setBusy(true);
    try {
      for (const f of Array.from(fileList)) {
        setProgress({ file: f.name, pct: 0, stage: "starting" });
        await api.upload(f, (s) =>
          setProgress({ file: f.name, pct: s.pct || 0, stage: s.stage || s.status }));
      }
      setProgress(null);
      await refresh();
      onChanged && onChanged();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
      setProgress(null);
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

        {progress && (
          <div className="pull-progress">
            <div className="pull-row">
              <span className="pull-name" title={progress.file}>{progress.file}</span>
              <div className="pull-bar"><div className="pull-fill" style={{ width: `${progress.pct}%` }} /></div>
              <span className="pull-pct">{Math.round(progress.pct)}%</span>
            </div>
            <p className="hint">{progress.stage === "doc2query" ? "Generating search hints…" : progress.stage}</p>
          </div>
        )}
        <label className="uploader" style={{ display: "block", cursor: "pointer" }}>
          {busy ? "Ingesting…" : "Click to add files — .txt / .pdf / .docx"}
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
