import React, { useEffect, useRef, useState } from "react";
import { api, hasKey } from "./api.js";
import Rail from "./components/Rail.jsx";
import Message from "./components/Message.jsx";
import DetailsPanel from "./components/DetailsPanel.jsx";
import { SettingsDialog, DocumentsDialog } from "./components/Dialogs.jsx";
import { SendIcon } from "./icons.jsx";

const uuid = () =>
  (crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random()));

export default function App() {
  const [ready, setReady] = useState(false);
  const [readyMsg, setReadyMsg] = useState("Starting…");
  const [sessionId, setSessionId] = useState(uuid);
  const [messages, setMessages] = useState([]);
  const [selected, setSelected] = useState(null);
  const [thinking, setThinking] = useState(false);
  const [input, setInput] = useState("");

  const [railExpanded, setRailExpanded] = useState(false);
  const [dark, setDark] = useState(() => localStorage.getItem("groundedops.dark") === "1");
  const [dialog, setDialog] = useState(null); // "settings" | "documents" | null

  const [logoOk, setLogoOk] = useState(true);
  const stageRef = useRef(null);

  // Apply theme to <html> so all CSS variables flip.
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    localStorage.setItem("groundedops.dark", dark ? "1" : "0");
  }, [dark]);

  // Poll /status until the backend reports ready.
  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const s = await api.status();
        setReadyMsg(s.message || "Loading…");
        if (s.ready || s.error) { setReady(true); return; }
      } catch {
        setReadyMsg("Waiting for backend…");
      }
      if (!stop) setTimeout(tick, 1000);
    };
    tick();
    return () => { stop = true; };
  }, []);

  // Preload the optional logo from /public.
  useEffect(() => {
    const img = new Image();
    img.onload = () => setLogoOk(true);
    img.onerror = () => setLogoOk(false);
    img.src = "/logo.png";
  }, []);

  // Auto-scroll to newest message.
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
  };

  const resetKB = async () => {
    if (!window.confirm("Reset the knowledge base? This permanently deletes all uploaded documents and their chunks. This cannot be undone.")) return;
    try {
      await api.reset();
      setMessages([]);
      setSessionId(uuid());
      setSelected(null);
    } catch (e) {
      alert(`Reset failed: ${e.message}`);
    }
  };

  const rethink = (q, provider, model) => runQuery(q, provider, model);

  if (!ready) {
    return (
      <div className="app">
        <div className="main">
          <div className="loader">
            <div className="ring" />
            <div>{readyMsg}</div>
          </div>
        </div>
      </div>
    );
  }

  const selMsg =
    selected != null && messages[selected] && messages[selected].role === "assistant"
      ? messages[selected]
      : null;

  return (
    <div className="app">
      <Rail
        expanded={railExpanded}
        onToggle={() => setRailExpanded((v) => !v)}
        dark={dark}
        logoUrl="/logo.png"
        logoOk={logoOk}
        onSettings={() => setDialog("settings")}
        onDocuments={() => setDialog("documents")}
        onNewChat={newChat}
        onReset={resetKB}
        onToggleTheme={() => setDark((v) => !v)}
      />

      <div className="main">
        <div className="stage" ref={stageRef}>
          <div className="stage-inner">
            <div className="conversation">
              {logoOk ? (
                <img className={`header-logo ${dark ? "white" : ""}`} src="/logo.png" alt="GroundedOps" />
              ) : (
                <div className="header-logo text">Grounded<span className="ops">Ops</span></div>
              )}

              {messages.length === 0 && !thinking && (
                <div className="empty">
                  <h3>Ask about your documents</h3>
                  <p>Answers are grounded in what you've uploaded — with sources, and nothing invented.</p>
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

            {selMsg && (
              <DetailsPanel
                message={selMsg}
                index={selected}
                busy={thinking}
                onClose={() => setSelected(null)}
                onSettings={() => setDialog("settings")}
                onRethink={rethink}
              />
            )}
          </div>
        </div>

        <div className="composer">
          <div className="composer-inner">
            <textarea
              rows={1}
              placeholder="Ask a question about your documents"
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

      {dialog === "settings" && (
        <SettingsDialog
          dark={dark}
          onToggleTheme={() => setDark((v) => !v)}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "documents" && (
        <DocumentsDialog onClose={() => setDialog(null)} onChanged={() => { /* stats refresh in dialog */ }} />
      )}
    </div>
  );
}
