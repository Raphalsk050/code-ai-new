import * as React from "react";

import { parseAnsi, stripAnsi } from "../ansi";
import { Codicon } from "../codicon";
import { t, tf } from "../i18n";
import { IconBrand } from "../icons";
import type { CommandBlock, TerminalState } from "../store";
import { formatDuration } from "../tool-row";

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
  const [menu, setMenu] = React.useState(false);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const menuRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (focusNonce > 0) inputRef.current?.focus();
  }, [focusNonce]);

  // Newest at the bottom, like a terminal.
  React.useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [terminal.screen, terminal.blocks]);

  React.useEffect(() => {
    if (!menu) return;
    const away = (e: PointerEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [menu]);

  if (!terminal.live) {
    return (
      <div className="rail-empty">
        {t("No terminal session. The same session serves you and the agent.")}
        <div className="panel-actions">
          <button className="panel-btn primary" onClick={() => onAction({ action: "start" })}>
            {t("Open a terminal")}
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
  const blocks = terminal.integrated ? terminal.blocks : [];
  const running = terminal.runningAuthor;
  const state = terminal.closed ? "closed" : running ? "running" : "idle";
  const copyOutput = () => {
    const last = blocks[blocks.length - 1];
    void navigator.clipboard?.writeText(last ? stripAnsi(last.output) : terminal.screen);
  };
  const item = (label: string, run: () => void, danger = false) => (
    <button
      className={`term-menu-item ${danger ? "danger" : ""}`}
      role="menuitem"
      onClick={() => {
        setMenu(false);
        run();
      }}
    >
      {label}
    </button>
  );

  return (
    <div className="term">
      <div className="term-bar">
        <span className={`term-dot ${state}`} title={t(state)} />
        <span className="term-where" title={terminal.cwd}>
          {terminal.shell && <span className="term-shell">{terminal.shell}</span>}
          {/* Left-to-right mark: the ellipsis cuts the start, the slashes stay put. */}
          {terminal.cwd && <span className="term-cwd">{"\u200e" + terminal.cwd}</span>}
        </span>
        {running === "user" && !terminal.closed && (
          <button className="btn-ghost btn-sm" onClick={() => onAction({ action: "ctrl", key: "c" })}>
            {t("Interrupt")} <kbd className="key">Ctrl+C</kbd>
          </button>
        )}
        <div className="term-more" ref={menuRef}>
          <button
            className="icon-btn"
            title={t("More actions")}
            aria-label={t("More actions")}
            aria-expanded={menu}
            onClick={() => setMenu(!menu)}
          >
            <Codicon name="kebab-vertical" />
          </button>
          {menu && (
            <div className="term-menu" role="menu">
              {item(t("Copy output"), copyOutput)}
              {item(t("Refresh the screen"), () => onAction({ action: "status" }))}
              {!terminal.closed && item(t("Send EOF (Ctrl+D)"), () => onAction({ action: "ctrl", key: "d" }))}
              {!terminal.closed && item(t("Clear (Ctrl+L)"), () => onAction({ action: "ctrl", key: "l" }))}
              <div className="term-menu-sep" />
              {terminal.closed
                ? item(t("New session"), () => onAction({ action: "start" }))
                : item(t("End the session"), () => onAction({ action: "kill" }), true)}
            </div>
          )}
        </div>
      </div>

      <div className="term-body" ref={scrollRef}>
        {blocks.length > 0 ? (
          <div className="term-blocks">
            {blocks.map((block) => (
              <TermBlock key={`${terminal.sessionId}-${block.id}`} block={block} />
            ))}
          </div>
        ) : (
          <div className="term-screen">{terminal.screen || t("(empty screen)")}</div>
        )}
      </div>

      {terminal.closed ? (
        <div className="term-ended">
          <span>{t("Session ended.")}</span>
          <button className="panel-btn primary" onClick={() => onAction({ action: "start" })}>
            {t("New session")}
          </button>
        </div>
      ) : running === "agent" ? (
        <div className="term-agent" role="status">
          <IconBrand size={14} />
          <span>{t("The agent is running a command here.")}</span>
          <button
            className="btn-ghost btn-sm"
            onClick={() => {
              onAction({ action: "ctrl", key: "c" });
              requestAnimationFrame(() => inputRef.current?.focus());
            }}
          >
            {t("Take over")}
          </button>
        </div>
      ) : (
        <div className="term-row">
          <span className="term-prompt">$</span>
          <label htmlFor="term-line" className="sr-only">
            {t("Line for the terminal")}
          </label>
          <input
            id="term-line"
            ref={inputRef}
            className="term-input"
            data-terminal-input="1"
            value={line}
            spellCheck={false}
            placeholder={t("type a command and press Enter")}
            onChange={(e) => setLine(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                send();
              }
            }}
          />
        </div>
      )}
      <div className="term-foot">
        {terminal.sessionId.slice(0, 8)} · {terminal.cols}×{terminal.rows}
      </div>
    </div>
  );
}

/** One command: who ran it, how long it took, how it ended; its output folds. */
function TermBlock({ block }: { block: CommandBlock }): JSX.Element {
  const failed = block.exitCode != null && block.exitCode !== 0;
  const [choice, setChoice] = React.useState<boolean | null>(null);
  const open = choice ?? (block.running || failed);
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    if (!block.running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [block.running]);
  const ms = (block.endedAt ?? now) - block.startedAt;
  const spans = React.useMemo(() => (open ? parseAnsi(block.output) : []), [open, block.output]);
  return (
    <div className={`term-block ${block.running ? "running" : failed ? "failed" : "done"}`}>
      <button className="term-block-head" aria-expanded={open} onClick={() => setChoice(!open)}>
        <span className={`term-author ${block.author}`} title={block.author === "agent" ? t("Run by the agent") : t("Run by you")}>
          {block.author === "agent" ? <IconBrand size={14} /> : <Codicon name="account" size={14} />}
        </span>
        <span className="term-command" title={block.command}>
          {block.command || t("(command)")}
        </span>
        <span className="term-meta">
          {ms > 0 && <span className="tool-time">{formatDuration(ms)}</span>}
          {block.running ? (
            <span className="spinner" />
          ) : failed ? (
            <span className="term-exit">{tf("exit {0}", block.exitCode ?? "")}</span>
          ) : block.exitCode === 0 ? (
            <Codicon name="check" size={14} className="term-ok" />
          ) : null}
        </span>
      </button>
      {open && (block.output || block.truncated) && (
        <pre className="term-output">
          {block.truncated && <span className="term-cut">{t("(earlier output cut)")}{"\n"}</span>}
          {spans.map((span, i) => (
            <span
              key={i}
              className={[span.color && `ansi-${span.color}`, span.bold && "ansi-bold", span.dim && "ansi-dim"]
                .filter(Boolean)
                .join(" ") || undefined}
            >
              {span.text}
            </span>
          ))}
        </pre>
      )}
    </div>
  );
}
