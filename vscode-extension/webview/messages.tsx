import * as React from "react";

import { t, tf } from "./i18n";

import {
  IconAgents,
  IconBrain,
  IconCheck,
  IconChevron,
  IconCI,
  IconFile,
  IconGlobe,
  IconList,
  IconPencil,
  IconSearch,
  IconTerminal,
  IconTool,
  IconUser,
  IconWarn,
  IconX,
} from "./icons";
import { Markdown } from "./markdown";
import type { SessionState } from "./store/session";
import { Item, ToolStatus, workingLabel } from "./store/transcript";
import { ToolKind, toolKind } from "./tool-target";

export function ItemView({ item }: { item: Item }): JSX.Element | null {
  switch (item.kind) {
    case "user":
      return <UserRow text={item.text} />;
    case "assistant":
      return <AssistantRow text={item.text} streaming={item.streaming} />;
    case "working":
      return <WorkingRow text={item.text} />;
    case "thinking":
      return <ThinkingRow text={item.text} streaming={item.streaming} />;
    case "tool":
      return (
        <ToolRow name={item.name} target={item.target} status={item.status} detail={item.detail} />
      );
    case "notice":
      return <NoticeRow level={item.level} text={item.text} />;
    default:
      return null;
  }
}

function Row(props: { role: string; avatar: React.ReactNode; name: string; children: React.ReactNode }) {
  return (
    <div className={`row row-${props.role}`}>
      <div className={`avatar avatar-${props.role}`}>{props.avatar}</div>
      <div className="row-body">
        <div className="row-name">{props.name}</div>
        <div className="row-content">{props.children}</div>
      </div>
    </div>
  );
}

function UserRow({ text }: { text: string }) {
  return (
    <Row role="user" name={t("You")} avatar={<IconUser size={15} />}>
      <div className="user-text">{text}</div>
    </Row>
  );
}

function AssistantRow({ text }: { text: string; streaming: boolean }) {
  return (
    <Row role="assistant" name="Code-AI" avatar={<IconCI size={15} />}>
      <Markdown text={text} />
    </Row>
  );
}

function WorkingRow({ text }: { text: string }) {
  return <div className="working-note">{text}</div>;
}

function ThinkingRow({ text, streaming }: { text: string; streaming?: boolean }) {
  // Open while the model is reasoning, collapsed once it stops - unless the
  // reader has said otherwise, in which case their click wins for good.
  const [choice, setChoice] = React.useState<boolean | null>(null);
  const open = choice ?? !!streaming;
  const body = React.useRef<HTMLDivElement>(null);

  // Follow the text as it arrives; stop the moment the reader scrolls up, so
  // reading something is never interrupted by the next delta.
  const [follow, setFollow] = React.useState(true);
  React.useEffect(() => {
    const el = body.current;
    if (!el || !open || !streaming || !follow) return;
    el.scrollTop = el.scrollHeight;
  }, [text, open, streaming, follow]);

  return (
    <div className={`thinking ${open ? "open" : ""} ${streaming ? "live" : ""}`}>
      <button className="thinking-head" onClick={() => setChoice(!open)}>
        <IconChevron size={13} className="chev" />
        <IconBrain size={13} />
        <span>{t("Reasoning")}</span>
        {streaming && <span className="thinking-live" aria-label="em andamento" />}
      </button>
      {open && (
        <div
          ref={body}
          className="thinking-body"
          onScroll={(e) => {
            const el = e.currentTarget;
            setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 24);
          }}
        >
          {text}
        </div>
      )}
    </div>
  );
}

const STATUS_LABEL: Record<ToolStatus, string> = {
  running: "running",
  done: "ok",
  failed: "failed",
};

const KIND_ICON: Record<ToolKind, (props: { size?: number; className?: string }) => JSX.Element> = {
  read: IconFile,
  write: IconPencil,
  run: IconTerminal,
  search: IconSearch,
  web: IconGlobe,
  agent: IconAgents,
  plan: IconList,
  tool: IconTool,
};

function ToolRow(props: { name: string; target: string; status: ToolStatus; detail: string }) {
  const { name, target, status } = props;
  const [open, setOpen] = React.useState(false);
  // A write reports its path, which the target already shows.
  const detail = props.detail === target ? "" : props.detail;
  const long = detail.length > 80 || detail.includes("\n");
  const Kind = KIND_ICON[toolKind(name)];
  return (
    <div className={`tool tool-${status}`}>
      <div className="tool-head" onClick={() => long && setOpen((v) => !v)} style={{ cursor: long ? "pointer" : "default" }}>
        <span className="tool-icon">
          {status === "running" ? <span className="spinner" /> : status === "done" ? <IconCheck /> : <IconX />}
        </span>
        <Kind size={13} className="tool-kind" />
        <span className="tool-name">{name}</span>
        <span className={`tool-badge badge-${status}`}>{t(STATUS_LABEL[status])}</span>
        {long && <IconChevron size={13} className={`chev ${open ? "chev-open" : ""}`} />}
      </div>
      {/* Its own row, so the file or command keeps the card's full width
          instead of being squeezed to a few letters in a narrow panel. */}
      {(target || (detail && !long)) && (
        <div className="tool-sub">
          {target && (
            <span className="tool-target" title={target}>
              {target}
            </span>
          )}
          {detail && !long && <span className="tool-detail-inline">{detail}</span>}
        </div>
      )}
      {long && open && <pre className="tool-detail-block">{detail}</pre>}
    </div>
  );
}

function NoticeRow({ level, text }: { level: string; text: string }) {
  const icon = level === "error" || level === "warning" ? <IconWarn size={13} /> : null;
  return (
    <div className={`notice notice-${level}`}>
      {icon}
      <span>{text}</span>
    </div>
  );
}

/**
 * What the agent is doing, in words: the tool in hand and what it works on, a
 * retry in progress, or the plain state.
 */
export function activityLabel(session: SessionState): string {
  if (session.modelWaiting) return t("queued at the provider");
  if (session.retry && session.status === "CALLING_MODEL") return tf("retrying {0}", session.retry);
  const tools = session.runningTools;
  if (session.status === "EXECUTING_TOOL" && tools.length === 1) {
    const [tool] = tools;
    return tf("running {0}", tool.name) + (tool.target ? ` · ${tool.target}` : "");
  }
  if (session.status === "EXECUTING_TOOL" && tools.length > 1) {
    const names = [...new Set(tools.map((tool) => tool.name))].join(", ");
    return `${tf("running {0} tools", tools.length)} · ${names}`;
  }
  return t(workingLabel(session.status));
}

export function TypingIndicator({ label, heartbeat }: { label: string; heartbeat?: number }) {
  return (
    <Row role="assistant" name="Code-AI" avatar={<IconCI size={15} />}>
      <div className="typing">
        <span />
        <span />
        <span />
        <em>{label}</em>
        {heartbeat ? <em className="typing-clock">· {formatElapsed(heartbeat)}</em> : null}
      </div>
    </Row>
  );
}

/** Seconds -> "14s" or "1:23". */
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
