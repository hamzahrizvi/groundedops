import React from "react";
import {
  PanelIcon, KeyIcon, FolderIcon, PlusIcon, ResetIcon, SunIcon, MoonIcon,
} from "../icons.jsx";

// Small helper: a rail button with an icon, a label (expanded) and a
// right-side tooltip (collapsed only — handled purely in CSS).
function RailBtn({ icon, label, onClick }) {
  return (
    <button className="rail-btn" onClick={onClick}>
      {icon}
      <span className="label">{label}</span>
      <span className="tip">{label}</span>
    </button>
  );
}

export default function Rail({
  expanded, onToggle, dark, logoUrl, logoOk,
  onSettings, onDocuments, onNewChat, onReset, onToggleTheme,
}) {
  return (
    <aside className={`rail ${expanded ? "expanded" : ""}`}>
      <div className="rail-top">
        {expanded && (logoOk
          ? <img className={`rail-logo ${dark ? "white" : ""}`} src={logoUrl} alt="GroundedOps" />
          : <span style={{ fontFamily: "var(--serif)", fontWeight: 600 }}>GroundedOps</span>)}
        <button className="rail-btn" style={{ width: 44, height: 44 }} onClick={onToggle}>
          <PanelIcon />
          <span className="tip">{expanded ? "Collapse" : "Expand"}</span>
        </button>
      </div>

      <hr className="rail-sep" />

      <RailBtn icon={<KeyIcon />} label="DeepSeek key" onClick={onSettings} />
      <RailBtn icon={<FolderIcon />} label="Documents" onClick={onDocuments} />

      <hr className="rail-sep" />

      <RailBtn icon={<PlusIcon />} label="New chat" onClick={onNewChat} />
      <RailBtn icon={<ResetIcon />} label="Reset knowledge base" onClick={onReset} />
      <RailBtn
        icon={dark ? <SunIcon /> : <MoonIcon />}
        label={dark ? "Light mode" : "Dark mode"}
        onClick={onToggleTheme}
      />
    </aside>
  );
}
