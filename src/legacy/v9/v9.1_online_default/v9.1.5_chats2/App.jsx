import React, { useEffect, useRef, useState } from "react";
import { api, hasProviderKey } from "./api.js";
import Rail from "./components/Rail.jsx";
import Message from "./components/Message.jsx";
import DetailsPanel from "./components/DetailsPanel.jsx";
import ChatsPage from "./components/ChatsPage.jsx";
import { SettingsDialog, DocumentsDialog, ModeDialog, StartupKeysDialog } from "./components/Dialogs.jsx";
import { SendIcon, RocketIcon, RunnerIcon } from "./icons.jsx";
import { LogoRingNoDot, LogoDot, LogoWordmark, LogoFull } from "./Logo.jsx";

const uuid = () =>
  (crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random()));

export default function App() {
  const [ready, setReady] = useState(false);
  const [readyMsg, setReadyMsg] = useState("Starting…");
  const [downSince, setDownSince] = useState(null); // first failed-poll time
  const [retryNonce, setRetryNonce] = useState(0);
  const [sessionId, setSessionId] = useState(uuid);
  // v9.1.1: persistent chat history (localStorage; survives restarts).
  // "New chat" archives instead of erasing. NOTE: backend follow-up
  // memory is per-process — a restored chat displays fully after a
  // backend restart, but its first follow-up starts from fresh
  // server-side context.
  const [chats, setChats] = useState(() => {
    try { return JSON.parse(localStorage.getItem("groundedops.chats") || "[]"); }
    catch { return []; }
  });
  // v9.1.4: "chats" shows the full-page chat history (opened by the
  // rail's New chat button); "chat" is the normal conversation.
  const [view, setView] = useState("chat");
  const restoringRef = useRef(false);

  // v9.1.5: resume the most recent chat on open (instead of a blank
  // conversation). The restore flag stops the persist effect from
  // bumping the chat's "updated" time just for being reopened.
  useEffect(() => {
    if (chats.length > 0) {
      restoringRef.current = true;
      setSessionId(chats[0].id);
      setMessages(chats[0].messages || []);
      setIntroDone(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
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

  const [startupKeys, setStartupKeys] = useState(false);

  useEffect(() => {
    if (!ready) return;
    api.settings()
      .then((s) => {
        const m = s.generation_mode || "api";
        setMode(m);
        setModelsLoaded(!!s.local_models_loaded);
        // v9.1.2: app opens in Online mode — if no key is saved for the
        // selected provider, show the startup key popup (same UI as the
        // sidebar Settings), with Free mode as the no-key path.
        if (m === "api" && !hasProviderKey(s.online_provider || "deepseek")) {
          setStartupKeys(true);
        }
      })
      .catch(() => {});
  }, [ready]);

  // v8.6.1: mode switching now goes through a proper dialog (ModeDialog)
  // with per-model load/unload selection, replacing window.confirm and
  // the inline warning text.
  const [modeDialog, setModeDialog] = useState(null); // "local" | "api" | null
  // v9.1.3: when the mode dialog is entered from the startup gate it is
  // REQUIRED — it cannot be dismissed without a decision, and Free mode
  // must load at least mistral (no silent cold-start path).
  const [modeDialogRequired, setModeDialogRequired] = useState(false);
  const [pullProgress, setPullProgress] = useState(null);

  const confirmModeSwitch = async (direction, models) => {
    setModelsBusy(true);
    try {
      if (direction === "local") {
        // v9.1.3: full path — check installed, pull missing (with live
        // progress), then warm. No silent cold-start.
        const st = await api.modelsStatus();
        if (!st.ollama_up) {
          alert("Ollama isn't running on this machine — start it, then try again.");
          return;
        }
        const missing = models.filter((m) => !st.installed[m]);
        if (missing.length) {
          await api.pullModels(missing);
          let pulling = true;
          while (pulling) {
            await new Promise((r) => setTimeout(r, 1500));
            const p = await api.pullStatus();
            setPullProgress(p);
            pulling = missing.some((m) => !(p[m] && p[m].done));
            const failed = missing.filter((m) => p[m] && p[m].status === "error");
            if (failed.length) {
              alert(`Download failed for: ${failed.join(", ")}. Check the backend log.`);
              setPullProgress(null);
              return;
            }
          }
          setPullProgress(null);
        }
        const s = await api.setMode("local");
        setMode(s.generation_mode || "local");
        if (models.length) {
          const r = await api.warmupModels(models);
          setModelsLoaded(!!r.loaded);
          if (!r.loaded) alert("Models installed but failed to load — check the backend log.");
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

  // v9.1.1: persist the active chat whenever messages change.
  useEffect(() => {
    if (messages.length === 0) return;
    if (restoringRef.current) { restoringRef.current = false; return; }
    setChats((prev) => {
      const title = (messages.find((m) => m.role === "user")?.content || "Chat").slice(0, 48);
      const entry = { id: sessionId, title, messages, updated: Date.now() };
      const next = [entry, ...prev.filter((c) => c.id !== sessionId)].slice(0, 50);
      try { localStorage.setItem("groundedops.chats", JSON.stringify(next)); } catch {}
      return next;
    });
  }, [messages, sessionId]);

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
    // Previous chat is already persisted — just start fresh.
    setSessionId(uuid());
    setMessages([]);
    setSelected(null);
    setIntroDone(true);
    setView("chat");
  };

  const openChat = (chat) => {
    restoringRef.current = true;
    setSessionId(chat.id);
    setMessages(chat.messages);
    setSelected(null);
    setIntroDone(true);
    setView("chat");
  };

  const deleteChats = (ids) => {
    setChats((prev) => {
      const next = prev.filter((c) => !ids.includes(c.id));
      try { localStorage.setItem("groundedops.chats", JSON.stringify(next)); } catch {}
      return next;
    });
    if (ids.includes(sessionId)) { setSessionId(uuid()); setMessages([]); }
  };

  const deleteChat = (id) => {
    setChats((prev) => {
      const next = prev.filter((c) => c.id !== id);
      try { localStorage.setItem("groundedops.chats", JSON.stringify(next)); } catch {}
      return next;
    });
    if (id === sessionId) { setSessionId(uuid()); setMessages([]); }
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
        onNewChat={() => setView("chats")}
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
              {/* v9.1.3: mode toggle — Rocket (Online) / Runner (Offline),
                  1.5x icon, rocket lit when online. Click opens the
                  ModeDialog for the opposite mode. */}
              {view !== "chats" && (
              <button
                className={`mode-switch ${mode === "api" ? "online" : ""}`}
                disabled={modelsBusy}
                title={mode === "api"
                  ? "Online — answers via API (fast, uses your key). Click to go Offline."
                  : "Offline — answers via local models (private, slower, uses RAM). Click to go Online."}
                onClick={() => { setModeDialogRequired(false); setModeDialog(mode === "api" ? "local" : "api"); }}
              >
                {mode === "api" ? <RocketIcon className="mode-ico" /> : <RunnerIcon className="mode-ico" />}
                <span>{mode === "api" ? "Online" : "Offline"}</span>
              </button>
              )}
              {view === "chats" && (
                <ChatsPage
                  chats={chats}
                  currentId={sessionId}
                  onOpen={openChat}
                  onNew={newChat}
                  onDeleteMany={deleteChats}
                  onClose={messages.length ? () => setView("chat") : null}
                />
              )}
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

      {startupKeys && (
        <StartupKeysDialog
          onClose={() => setStartupKeys(false)}
          onSwitchFree={() => { setStartupKeys(false); setModeDialogRequired(true); setModeDialog("local"); }}
        />
      )}

      {modeDialog && (
        <ModeDialog
          direction={modeDialog}
          modelsLoaded={modelsLoaded}
          busy={modelsBusy}
          required={modeDialogRequired}
          pullProgress={pullProgress}
          onConfirm={(models) => confirmModeSwitch(modeDialog, models)}
          onClose={() => {
            if (modelsBusy) return;
            setModeDialog(null);
            if (modeDialogRequired) { setModeDialogRequired(false); setStartupKeys(true); }
          }}
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
