import * as React from "react";

import type { TerminalState } from "../store";

export type TerminalAction =
  | { action: "start"; command?: string }
  | { action: "write"; text: string }
  | { action: "ctrl"; key: string }
  | { action: "kill" }
  | { action: "status" };

export function TerminalPanel({
  terminal,
  onAction,
  focusNonce = 0,
}: {
  terminal: TerminalState;
  onAction: (action: TerminalAction) => void;
  /** Bumped when Esc hands the keyboard to the terminal. */
  focusNonce?: number;
}): JSX.Element {
  const [line, setLine] = React.useState("");
  const screenRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (focusNonce > 0) inputRef.current?.focus();
  }, [focusNonce]);

  // The PTY screen is a viewport, not a log: keep it pinned to the newest rows.
  React.useLayoutEffect(() => {
    const el = screenRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [terminal.screen]);

  if (!terminal.live) {
    return (
      <div className="rail-empty">
        Nenhuma sessão de terminal. A mesma sessão serve você e o agente.
        <div className="panel-actions">
          <button className="panel-btn primary" onClick={() => onAction({ action: "start" })}>
            Abrir terminal
          </button>
        </div>
      </div>
    );
  }

  const send = () => {
    const text = line.trim();
    if (!text) return;
    onAction({ action: "write", text });
    setLine("");
  };

  return (
    <div className="panel-card">
      <div className="rail-section-note">
        {terminal.sessionId.slice(0, 8)} · {terminal.cols}×{terminal.rows}
        {terminal.closed ? " · encerrada" : ""}
      </div>
      <div className="term-screen" ref={screenRef}>
        {terminal.screen || "(tela vazia)"}
      </div>
      <div className="term-row">
        <span style={{ fontFamily: "var(--mono)", color: "var(--ok)" }}>$</span>
        <label htmlFor="term-line" className="sr-only">
          Linha para o terminal
        </label>
        <input
          id="term-line"
          ref={inputRef}
          className="term-input"
          data-terminal-input="1"
          value={line}
          disabled={terminal.closed}
          placeholder={terminal.closed ? "sessão encerrada" : "digite e Enter"}
          onChange={(e) => setLine(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              send();
            }
          }}
        />
      </div>
      <div className="panel-actions">
        <button className="panel-btn" onClick={() => onAction({ action: "ctrl", key: "c" })}>
          Ctrl+C
        </button>
        <button className="panel-btn" onClick={() => onAction({ action: "ctrl", key: "d" })}>
          Ctrl+D
        </button>
        <button className="panel-btn" onClick={() => onAction({ action: "status" })}>
          Atualizar
        </button>
        {terminal.closed ? (
          <button className="panel-btn primary" onClick={() => onAction({ action: "start" })}>
            Nova sessão
          </button>
        ) : (
          <button className="panel-btn danger" onClick={() => onAction({ action: "kill" })}>
            Encerrar
          </button>
        )}
      </div>
    </div>
  );
}
