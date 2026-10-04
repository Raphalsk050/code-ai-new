import * as React from "react";

import { Codicon } from "./codicon";
import { t, tf } from "./i18n";
import { IconAgentsTwin } from "./icons";
import type { Item, ToolStatus } from "./store/transcript";
import { formatTokens } from "./store/usage";
import { ToolKind, toolKind } from "./tool-target";

type ToolItem = Extract<Item, { kind: "tool" }>;
type TurnItem = Extract<Item, { kind: "turn" }>;

/** The tools whose change the editor can show as a diff. */
export const DIFF_TOOLS = new Set(["write_file", "edit_code"]);

const KIND_ICON: Record<Exclude<ToolKind, "agent">, string> = {
  read: "file",
  write: "edit",
  run: "terminal",
  search: "search",
  web: "globe",
  plan: "checklist",
  tool: "tools",
};

function KindIcon({ name }: { name: string }): JSX.Element {
  const kind = toolKind(name);
  return kind === "agent" ? (
    <IconAgentsTwin size={14} className="tool-kind" />
  ) : (
    <Codicon name={KIND_ICON[kind]} size={14} className="tool-kind" />
  );
}

/** 420 -> "0.4s", 12400 -> "12s", 83000 -> "1:23", 3723000 -> "1:02:03". */
export function formatDuration(ms: number): string {
  const seconds = ms / 1000;
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const whole = Math.round(seconds);
  const pad = (n: number) => String(n).padStart(2, "0");
  if (whole < 3600) return `${Math.floor(whole / 60)}:${pad(whole % 60)}`;
  return `${Math.floor(whole / 3600)}:${pad(Math.floor(whole / 60) % 60)}:${pad(whole % 60)}`;
}

const BADGE: Partial<Record<ToolStatus, string>> = { running: "running", failed: "failed" };

function StatusGlyph({ status }: { status: ToolStatus }): JSX.Element {
  if (status === "running") return <span className="spinner" />;
  if (status === "failed") return <Codicon name="error" size={14} />;
  return <Codicon name="check" size={14} />;
}

export function LineDelta({ added, removed }: { added?: number; removed?: number }): JSX.Element | null {
  if (added == null || removed == null) return null;
  return (
    <span className="line-delta" aria-label={tf("{0} lines added, {1} removed", added, removed)}>
      <span className="delta-add">+{added}</span> <span className="delta-del">-{removed}</span>
    </span>
  );
}

/**
 * One card per call: status, kind and name on top, the target and the result
 * on the line below so they keep the card's width in a narrow panel. A badge
 * only when the call needs attention; the duration always.
 */
export function ToolRow({
  item,
  onOpen,
  onTerminal,
}: {
  item: ToolItem;
  onOpen?: () => void;
  onTerminal?: () => void;
}): JSX.Element {
  const { name, target, status } = item;
  const failed = status === "failed";
  const [choice, setChoice] = React.useState<boolean | null>(null);
  const [copied, setCopied] = React.useState(false);
  // A write reports its path, which the target already shows.
  const detail = item.detail === target ? "" : item.detail;
  const long = detail.length > 80 || detail.includes("\n");
  const expandable = long || (failed && !!detail);
  const open = choice ?? failed;
  const badge = BADGE[status];
  const copy = () => {
    void navigator.clipboard?.writeText(detail).then(
      () => setCopied(true),
      () => setCopied(false)
    );
  };
  return (
    <div className={`tool tool-${status}`}>
      <div
        className={`tool-head ${expandable ? "expandable" : ""}`}
        onClick={() => expandable && setChoice(!open)}
      >
        <span className="tool-icon">
          <StatusGlyph status={status} />
        </span>
        <KindIcon name={name} />
        <span className="tool-name">{name}</span>
        {badge && <span className={`tool-badge badge-${status}`}>{t(badge)}</span>}
        <span className="tool-end">
          {item.ms != null && status !== "running" && <span className="tool-time">{formatDuration(item.ms)}</span>}
          {expandable && <Codicon name="chevron-right" size={14} className={`chev ${open ? "chev-open" : ""}`} />}
        </span>
      </div>
      {(target || (detail && !expandable)) && (
        <div className="tool-sub">
          {target &&
            (onOpen ? (
              <button className="tool-target tool-target-link" title={t("Show the change in the editor")} onClick={onOpen}>
                {target}
              </button>
            ) : (
              <span className="tool-target" title={target}>
                {target}
              </span>
            ))}
          <LineDelta added={item.added} removed={item.removed} />
          {detail && !expandable && <span className="tool-detail-inline">{detail}</span>}
        </div>
      )}
      {expandable && open && (
        <div className="tool-detail">
          <pre className="tool-detail-block">{detail}</pre>
          {failed && (
            <div className="tool-actions">
              {onTerminal && (
                <button className="btn-ghost btn-sm" onClick={onTerminal}>
                  <Codicon name="terminal" size={14} /> {t("Open the terminal")}
                </button>
              )}
              <button className="btn-ghost btn-sm" onClick={copy}>
                <Codicon name={copied ? "check" : "copy"} size={14} /> {copied ? t("Copied") : t("Copy")}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Calls that went through without a word, folded into one line until asked. */
export function ToolGroup({ items, children }: { items: ToolItem[]; children: React.ReactNode }): JSX.Element {
  const [open, setOpen] = React.useState(false);
  const timed = items.filter((item) => item.ms != null);
  const total = timed.reduce((sum, item) => sum + (item.ms ?? 0), 0);
  const names = [...new Set(items.map((item) => item.name))];
  return (
    <div className={`tool-group ${open ? "open" : ""}`}>
      <button className="tool-group-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="tool-icon">
          <Codicon name="check-all" size={14} />
        </span>
        <span className="tool-group-label">{tf("{0} tools", items.length)}</span>
        <span className="tool-group-names">{names.join(", ")}</span>
        <span className="tool-end">
          {timed.length > 0 && <span className="tool-time">{formatDuration(total)}</span>}
          <Codicon name="chevron-right" size={14} className={`chev ${open ? "chev-open" : ""}`} />
        </span>
      </button>
      {open && <div className="tool-group-body">{children}</div>}
    </div>
  );
}

/** The turn in one line: how long, how much, what it touched, whether it passed. */
export function TurnFooter({ item, onOpen }: { item: TurnItem; onOpen?: (path: string) => void }): JSX.Element {
  const parts: React.ReactNode[] = [];
  if (item.ms != null)
    parts.push(
      <span key="t">
        <Codicon name="clock" size={12} /> {formatDuration(item.ms)}
      </span>
    );
  if (item.tokens)
    parts.push(<span key="k">{tf("{0} tokens", formatTokens(item.tokens))}</span>);
  if (item.files.length > 0)
    parts.push(
      <span key="f" className="turn-files" title={item.files.join("\n")}>
        <Codicon name="files" size={12} />{" "}
        {item.files.length === 1 && onOpen ? (
          <button className="turn-file" onClick={() => onOpen(item.files[0])}>
            {item.files[0].split(/[\\/]/).pop()}
          </button>
        ) : item.files.length === 1 ? (
          item.files[0].split(/[\\/]/).pop()
        ) : (
          tf("{0} files", item.files.length)
        )}
      </span>
    );
  if (item.verified != null)
    parts.push(
      <span key="v" className={item.verified ? "turn-ok" : "turn-bad"}>
        <Codicon name={item.verified ? "pass" : "error"} size={12} />{" "}
        {item.verified ? t("verification passed") : t("verification failed")}
      </span>
    );
  return <div className="turn-footer">{parts}</div>;
}

export type Entry = { item: Item } | { group: ToolItem[] };

/** Runs of at least `min` calls that all went through become one group. */
export function foldTools(items: readonly Item[], min = 3): Entry[] {
  const out: Entry[] = [];
  let run: ToolItem[] = [];
  const flush = () => {
    if (run.length >= min) out.push({ group: run });
    else for (const item of run) out.push({ item });
    run = [];
  };
  for (const item of items) {
    if (item.kind === "tool" && item.status === "done") run.push(item);
    else {
      flush();
      out.push({ item });
    }
  }
  flush();
  return out;
}
