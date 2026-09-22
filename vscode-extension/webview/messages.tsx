import * as React from "react";

import { t, tf } from "./i18n";

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
import { Item, ToolStatus, workingLabel } from "./store/transcript";

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
  running: "rodando",
  done: "ok",
  failed: "falhou",
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
        <em>{workingLabel(status)}</em>
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
