import * as React from "react";

import type { AppMode } from "../src/protocol";
import { IconBook, IconCI, IconWand } from "./icons";

const MODES: { mode: AppMode; label: string; icon: JSX.Element; title: string }[] = [
  { mode: "agent", label: "Agente", icon: <IconCI size={13} />, title: "Conversar com o agente" },
  { mode: "refactor", label: "Refatorar", icon: <IconWand size={13} />, title: "Sugerir melhorias de arquitetura para a seleção" },
  { mode: "explain", label: "Explicar", icon: <IconBook size={13} />, title: "Explicar o código selecionado ao passar o mouse" },
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
