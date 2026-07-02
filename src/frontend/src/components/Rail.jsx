import React from "react";
import {
  PanelIcon, KeyIcon, FolderIcon, PlusIcon, ResetIcon, SunIcon, MoonIcon,
} from "../icons.jsx";

function RailBtn({ icon, label, onClick, expanded }) {
  return (
    <button
      className="rail-btn"
      onClick={onClick}
      title={!expanded ? label : ""}
    >
      <span className="icon">{icon}</span>
      {expanded && <span className="label">{label}</span>}
      <span className="tip">{label}</span>
    </button>
  );
}

export default function Rail({
  expanded,
  onToggle,
  onSettings,
  onDocuments,
  onNewChat,
  onReset,
  onToggleTheme,
  dark,
}) {
  return (
    <aside className={`rail ${expanded ? "expanded" : ""}`}>
      <div className="rail-top">
        {expanded && (
          <span className="rail-title">GroundedOps</span>
        )}

        <button
          className="rail-btn rail-toggle-btn"
          onClick={onToggle}
          title={expanded ? "Collapse" : "Expand"}
        >
          <PanelIcon />
          <span className="tip">{expanded ? "Collapse" : "Expand"}</span>
        </button>
      </div>

      <hr className="rail-sep" />

      <RailBtn expanded={expanded} icon={<KeyIcon />} label="DeepSeek key" onClick={onSettings} />
      <RailBtn expanded={expanded} icon={<FolderIcon />} label="Documents" onClick={onDocuments} />

      <hr className="rail-sep" />

      <RailBtn expanded={expanded} icon={<PlusIcon />} label="New chat" onClick={onNewChat} />
      <RailBtn expanded={expanded} icon={<ResetIcon />} label="Reset knowledge base" onClick={onReset} />
      <RailBtn
        expanded={expanded}
        icon={dark ? <SunIcon /> : <MoonIcon />}
        label={dark ? "Light mode" : "Dark mode"}
        onClick={onToggleTheme}
      />
    </aside>
  );
}