import * as React from "react";

import type { AppMode } from "../src/protocol";
import { IconBook, IconCI, IconWand } from "./icons";

const MODES: { mode: AppMode; label: string; icon: JSX.Element; title: string }[] = [
  { mode: "agent", label: "Agent", icon: <IconCI size={13} />, title: "Chat with the agent" },
  { mode: "refactor", label: "Refactor", icon: <IconWand size={13} />, title: "Suggest architectural improvements for a selection" },
  { mode: "explain", label: "Explain", icon: <IconBook size={13} />, title: "Explain the selected code on hover" },
];

/** Segmented control that switches the extension's operating mode. */
export function ModeSwitch({ mode, onChange }: { mode: AppMode; onChange: (m: AppMode) => void }): JSX.Element {
  return (
    <div className="mode-switch" role="tablist">
      {MODES.map((m) => (
        <button
          key={m.mode}
          role="tab"
          aria-selected={mode === m.mode}
          className={`mode-opt ${mode === m.mode ? "active" : ""}`}
          title={m.title}
          onClick={() => onChange(m.mode)}
        >
          {m.icon}
          <span>{m.label}</span>
        </button>
      ))}
    </div>
  );
}
