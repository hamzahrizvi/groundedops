import React, { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import Rail from "./components/Rail.jsx";
import Message from "./components/Message.jsx";
import DetailsPanel from "./components/DetailsPanel.jsx";
import { SettingsDialog, DocumentsDialog, ModeDialog } from "./components/Dialogs.jsx";
import { SendIcon } from "./icons.jsx";
import { LogoRingNoDot, LogoDot, LogoWordmark, LogoFull } from "./Logo.jsx";

const uuid = () =>
  (crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random()));

export default function App() {
  const [ready, setReady] = useState(false);
  const [readyMsg, setReadyMsg] = useState("Starting…");
  const [downSince, setDownSince] = useState(null); // first failed-poll time
  const [retryNonce, setRetryNonce] = useState(0);
  const [sessionId, setSessionId] = useState(uuid);
  const [messages, setMessages] = useState([]);
  const [selected, setSelected] = useState(null);
  const [thinking, setThinking] = useState(false);
  const [input, setInput] = useState("");

  const [railExpanded, setRailExpanded] = useState(false);
  const [dark, setDark] = useState(() => localStorage.getItem("groundedops.dark") === "1");
  const [dialog, setDialog] = useState(null);
  const [introDone, setIntroDone] = useState(false);

  // v8.6: online (DeepSeek API) vs free (local Ollama) mode, live-switched
  // via the top-right toggle. Local models are NOT loaded at startup —
  // "modelsLoaded" tracks whether the user has loaded them from settings.
  const [mode, setMode] = useState("local");
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [modelsBusy, setModelsBusy] = useState(false);

  useEffect(() => {
    api.settings()
      .then((s) => {
        setMode(s.generation_mode || "local");
        setModelsLoaded(!!s.local_models_loaded);
      })
      .catch(() => {});
  }, [ready]);

  // v8.6.1: mode switching now goes through a proper dialog (ModeDialog)
  // with per-model load/unload selection, replacing window.confirm and
  // the inline warning text.
  const [modeDialog, setModeDialog] = useState(null); // "local" | "api" | null

  const confirmModeSwitch = async (direction, models) => {
    setModelsBusy(true);
    try {
      if (direction === "local") {
        const s = await api.setMode("local");
        setMode(s.generation_mode || "local");
        if (models.length) {
          const r = await api.warmupModels(models);
          setModelsLoaded(!!r.loaded);
          if (!r.loaded) alert("Some models failed to load — is Ollama running? (`ollama list`)");
        }
      } else {
        const s = await api.setMode("api");
        setMode(s.generation_mode || "api");
        if (models.length) {
          await api.unloadModels(models);
          if (models.includes("mistral") && models.includes("phi")) setModelsLoaded(false);
        }
      }
      setModeDialog(null);
    } catch (e) {
      alert(`Mode switch failed: ${e.message}`);
    } finally {
      setModelsBusy(false);
    }
  };

  const loadModels = async () => {
    setModelsBusy(true);
    try {
      const r = await api.warmupModels();
      setModelsLoaded(!!r.loaded);
      if (!r.loaded) alert("Some models failed to load — check that Ollama is running (`ollama list`).");
    } catch (e) {
      alert(`Load failed: ${e.message}`);
    } finally {
      setModelsBusy(false);
    }
  };

  const stageRef = useRef(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    localStorage.setItem("groundedops.dark", dark ? "1" : "0");
  }, [dark]);

  // Poll /status until the backend reports ready. Track consecutive
  // failures so we can show a clear "unreachable" message + Retry, and
  // back off the polling so we don't hammer (or spam the console).
  useEffect(() => {
    let stop = false;
    let fails = 0;
    const tick = async () => {
      try {
        const s = await api.status();
        fails = 0;
        setDownSince(null);
        setReadyMsg(s.message || "Loading…");
        if (s.ready || s.error) { setReady(true); return; }
      } catch {
        fails += 1;
        setDownSince((d) => d ?? Date.now());
        setReadyMsg("Waiting for backend…");
      }
      if (!stop) setTimeout(tick, fails > 3 ? 3000 : 1000);
    };
    tick();
    return () => { stop = true; };
  }, [retryNonce]);

  // The splash stays until the intro animation finishes OR the backend is
  // ready — whichever is later gives the animation at least ~2.2s to play.
  useEffect(() => {
    if (introDone || messages.length > 0) return;
    const t = setTimeout(() => setIntroDone(true), 2200);
    return () => clearTimeout(t);
  }, [introDone, messages.length]);

  useEffect(() => {
    if (messages.length > 0 && !introDone) setIntroDone(true);
  }, [messages.length, introDone]);

  useEffect(() => {
    if (stageRef.current) stageRef.current.scrollTop = stageRef.current.scrollHeight;
  }, [messages, thinking]);

  const runQuery = async (q, forceProvider, forceModel) => {
    if (!q.trim() || thinking) return;
    setMessages((m) => [...m, { role: "user", content: q }]);
    setThinking(true);
    try {
      const data = await api.query({ q, sessionId, forceProvider, forceModel });
      setMessages((m) => [...m, { role: "assistant", content: data.answer, meta: data, query: q }]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: "assistant", content: `Something went wrong: ${e.message}`, meta: {}, query: q },
      ]);
    } finally {
      setThinking(false);
    }
  };

  const submit = () => {
    const q = input.trim();
    if (!q) return;
    setInput("");
    runQuery(q);
  };

  const newChat = async () => {
    try { await api.clearSession(sessionId); } catch { /* ignore */ }
    setSessionId(uuid());
    setMessages([]);
    setSelected(null);
    setIntroDone(true);
  };

  const resetKB = async () => {
    if (!window.confirm("Reset the knowledge base? This permanently deletes all uploaded documents and their chunks. This cannot be undone.")) return;
    try {
      await api.reset();
      setMessages([]);
      setSessionId(uuid());
      setSelected(null);
      setIntroDone(true);
    } catch (e) {
      alert(`Reset failed: ${e.message}`);
    }
  };

  const rethink = (q, provider, model) => runQuery(q, provider, model);

  if (!ready && !introDone) {
    // Loading + intro share the same splash (animation plays during load).
  }

  const selMsg =
    selected != null && messages[selected] && messages[selected].role === "assistant"
      ? messages[selected]
      : null;

  // Show the splash until the backend is ready AND the intro has had time
  // to play. Previously this advanced on the timer alone, which made the
  // app look loaded while the backend was still unreachable.
  const showSplash = !ready || !introDone;

  return (
    <div className="app">
      <Rail
        expanded={railExpanded}
        onToggle={() => setRailExpanded((v) => !v)}
        dark={dark}
        onSettings={() => setDialog("settings")}
        onDocuments={() => setDialog("documents")}
        onNewChat={newChat}
        onReset={resetKB}
        onToggleTheme={() => setDark((v) => !v)}
      />

      <div className="main">
        <div className="stage" ref={stageRef}>
          {showSplash ? (
            <div className="splash">
              {/* Faithful GroundedOps mark animation. Same paths as the
                  original; the dot fades in, the ring reveals clockwise,
                  a bronze highlight sweeps, then the wordmark fades in. */}
              <div className="splash-mark-wrap">
                <LogoRingNoDot className="splash-ring" />
                <LogoDot className="splash-dot-svg" />
              </div>
              <LogoWordmark className="splash-word" />
              {!ready && downSince && Date.now() - downSince > 6000 ? (
                <div className="splash-status">
                  <div>Can't reach the backend on <code>:8000</code>.</div>
                  <div style={{ marginTop: 4 }}>Make sure it's running, then</div>
                  <button
                    className="btn"
                    style={{ marginTop: 10 }}
                    onClick={() => { setDownSince(null); setRetryNonce((n) => n + 1); }}
                  >
                    Retry
                  </button>
                </div>
              ) : (
                !ready && <div className="splash-status">{readyMsg}</div>
              )}
            </div>
          ) : (
            <div className="stage-inner">
              {/* v8.6.1: answer-mode segmented control — opens ModeDialog */}
              <div className="mode-toggle" role="group" aria-label="Answer mode">
                <span className="mode-label">Answers</span>
                <button
                  className={`mode-btn ${mode === "api" ? "active" : ""}`}
                  disabled={modelsBusy}
                  title="DeepSeek API — fast, needs API key"
                  onClick={() => mode !== "api" && setModeDialog("api")}
                >⚡ Online</button>
                <button
                  className={`mode-btn ${mode === "local" ? "active" : ""}`}
                  disabled={modelsBusy}
                  title="Local models — private, slower, uses RAM"
                  onClick={() => mode !== "local" && setModeDialog("local")}
                >🔒 Free</button>
              </div>
              <div className="conversation">
                <LogoFull className="header-logo" />

                {messages.length === 0 && !thinking && (
                  <div className="empty">
                    <h3>What&apos;s on your mind today?</h3>
                  </div>
                )}

                {messages.map((m, i) => (
                  <Message
                    key={i}
                    msg={m}
                    index={i}
                    selected={selected === i}
                    onSelect={setSelected}
                  />
                ))}

                {thinking && (
                  <div className="row bot">
                    <div className="thinking">
                      <span className="d" /><span className="d" /><span className="d" />
                      <span style={{ marginLeft: 6 }}>Thinking…</span>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="composer">
          <div className="composer-inner">
            <textarea
              rows={1}
              placeholder="Ask anything"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
              }}
            />
            <button className="send-btn" onClick={submit} disabled={!input.trim() || thinking} aria-label="Send">
              <SendIcon />
            </button>
          </div>
        </div>
      </div>

      {/* Details as a right-side DRAWER that overlays the chat, same overlay
          pattern as the dialogs. Backdrop click closes it. */}
      {selMsg && (
        <div className="drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && setSelected(null)}>
          <div className="drawer">
            <DetailsPanel
              message={selMsg}
              index={selected}
              busy={thinking}
              onClose={() => setSelected(null)}
              onSettings={() => setDialog("settings")}
              onRethink={rethink}
            />
          </div>
        </div>
      )}

      {modeDialog && (
        <ModeDialog
          direction={modeDialog}
          modelsLoaded={modelsLoaded}
          busy={modelsBusy}
          onConfirm={(models) => confirmModeSwitch(modeDialog, models)}
          onClose={() => !modelsBusy && setModeDialog(null)}
        />
      )}

      {dialog === "settings" && (
        <SettingsDialog
          dark={dark}
          onToggleTheme={() => setDark((v) => !v)}
          mode={mode}
          modelsLoaded={modelsLoaded}
          modelsBusy={modelsBusy}
          onLoadModels={loadModels}
          onUnloadModels={async () => {
            setModelsBusy(true);
            try { await api.unloadModels(); setModelsLoaded(false); }
            catch (e) { alert(`Unload failed: ${e.message}`); }
            finally { setModelsBusy(false); }
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "documents" && (
        <DocumentsDialog onClose={() => setDialog(null)} onChanged={() => { /* stats refresh in dialog */ }} />
      )}
    </div>
  );
}
