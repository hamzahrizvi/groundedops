import React from "react";
import {
  PanelIcon, KeyIcon, FolderIcon, ChatIcon, ResetIcon, SunIcon, MoonIcon,
} from "../icons.jsx";
import { LogoFull } from "../Logo.jsx";

function RailBtn({ icon, label, onClick }) {
  return (
    <button className="rail-btn" onClick={onClick}>
      <span className="icon">{icon}</span>
      <span className="label">{label}</span>
      <span className="tip">{label}</span>
    </button>
  );
}

export default function Rail({
  expanded, onToggle, dark,
  onSettings, onDocuments, onNewChat, onReset, onToggleTheme,
}) {
  return (
    <aside className={`rail ${expanded ? "expanded" : ""}`}>
      <div className="rail-top">
        {expanded && <LogoFull className="rail-logo" />}
        <button className="rail-btn rail-toggle-btn" onClick={onToggle}>
          <span className="icon"><PanelIcon /></span>
          <span className="tip">{expanded ? "Collapse" : "Expand"}</span>
        </button>
      </div>

      <hr className="rail-sep" />

      <RailBtn icon={<KeyIcon />} label="DeepSeek key" onClick={onSettings} />
      <RailBtn icon={<FolderIcon />} label="Documents" onClick={onDocuments} />

      <hr className="rail-sep" />

      <RailBtn icon={<ChatIcon />} label="Chats" onClick={onNewChat} />
      <RailBtn icon={<ResetIcon />} label="Reset knowledge base" onClick={onReset} />
      <RailBtn
        icon={dark ? <SunIcon /> : <MoonIcon />}
        label={dark ? "Light mode" : "Dark mode"}
        onClick={onToggleTheme}
      />
    </aside>
  );
}
