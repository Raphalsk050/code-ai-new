import * as React from "react";

import {
  IconBrain,
  IconCheck,
  IconChevron,
  IconCI,
  IconTool,
  IconUser,
  IconWarn,
  IconX,
} from "./icons";
import { Markdown } from "./markdown";
import type { Item, ToolStatus } from "./reducer";

export function ItemView({ item }: { item: Item }): JSX.Element | null {
  switch (item.kind) {
    case "user":
      return <UserRow text={item.text} />;
    case "assistant":
      return <AssistantRow text={item.text} streaming={item.streaming} />;
    case "working":
      return <WorkingRow text={item.text} />;
    case "thinking":
      return <ThinkingRow text={item.text} />;
    case "tool":
      return <ToolRow name={item.name} status={item.status} detail={item.detail} />;
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
    <Row role="user" name="You" avatar={<IconUser size={15} />}>
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

function ThinkingRow({ text }: { text: string }) {
  const [open, setOpen] = React.useState(false);
  return (
    <div className={`thinking ${open ? "open" : ""}`}>
      <button className="thinking-head" onClick={() => setOpen((v) => !v)}>
        <IconChevron size={13} className="chev" />
        <IconBrain size={13} />
        <span>Thinking</span>
      </button>
      {open && <div className="thinking-body">{text}</div>}
    </div>
  );
}

const STATUS_LABEL: Record<ToolStatus, string> = {
  running: "running",
  done: "done",
  failed: "failed",
};

function ToolRow({ name, status, detail }: { name: string; status: ToolStatus; detail: string }) {
  const [open, setOpen] = React.useState(false);
  const long = detail.length > 80 || detail.includes("\n");
  return (
    <div className={`tool tool-${status}`}>
      <div className="tool-head" onClick={() => long && setOpen((v) => !v)} style={{ cursor: long ? "pointer" : "default" }}>
        <span className="tool-icon">
          {status === "running" ? <span className="spinner" /> : status === "done" ? <IconCheck /> : <IconX />}
        </span>
        <IconTool size={13} className="tool-wrench" />
        <span className="tool-name">{name}</span>
        <span className={`tool-badge badge-${status}`}>{STATUS_LABEL[status]}</span>
        {detail && !long && <span className="tool-detail-inline">{detail}</span>}
        {long && <IconChevron size={13} className={`chev ${open ? "chev-open" : ""}`} />}
      </div>
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

export function TypingIndicator({ status, heartbeat }: { status: string; heartbeat?: number }) {
  return (
    <Row role="assistant" name="Code-AI" avatar={<IconCI size={15} />}>
      <div className="typing">
        <span />
        <span />
        <span />
        <em>{status.toLowerCase().replace(/_/g, " ")}</em>
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
