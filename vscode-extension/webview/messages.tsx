import * as React from "react";

import { t, tf } from "./i18n";

import type { Block } from "./blocks";
import {
  IconAgents,
  IconBrain,
  IconBrand,
  IconCheck,
  IconChevron,
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

/** The tools whose change the editor can show as a diff. */
const DIFF_TOOLS = new Set(["write_file", "edit_code"]);

/**
 * One box per run of messages from the same side: the user's hug the right edge
 * like a chat, the agent's take the width its work needs. `children` is what is
 * still live at the end of the agent's turn - the typing line, an approval.
 */
export function BlockView({
  block,
  avatar,
  avatarProblem,
  avatarPending,
  onAvatarSignIn,
  done,
  onOpenDiff,
  children,
}: {
  block: Block;
  /** The user's picture, as a data: URI; the icon stands in without one. */
  avatar?: string;
  /** Why there is no picture, shown on the icon's hover. */
  avatarProblem?: string;
  /** A browser window to sign in is up, fetching the picture. */
  avatarPending?: boolean;
  /** Set when a sign-in in a browser can fetch the picture: the icon's click. */
  onAvatarSignIn?: () => void;
  /** The turn is over and this box holds its answer: it turns green. */
  done?: boolean;
  onOpenDiff?: (path: string) => void;
  children?: React.ReactNode;
}): JSX.Element {
  const user = block.speaker === "user";
  const last = block.items[block.items.length - 1];
  return (
    <section
      className={`block block-${block.speaker}${done ? " block-done" : ""}`}
      data-user-block={user ? block.key : undefined}
      data-last-message={user ? last?.id : undefined}
    >
      <div className="block-head">
        {user ? (
          <UserAvatar
            src={avatar}
            problem={avatarProblem}
            pending={avatarPending}
            onSignIn={onAvatarSignIn}
          />
        ) : (
          <span className="avatar avatar-assistant">
            <IconBrand size={14} />
          </span>
        )}
        <span className="block-name">{user ? t("You") : "Code-AI"}</span>
      </div>
      <div className="block-body">
        {block.items.map((item) => (
          <ItemView key={item.id} item={item} onOpenDiff={onOpenDiff} />
        ))}
        {children}
      </div>
    </section>
  );
}

/**
 * The user's GitHub picture when the backend found one, the icon otherwise -
 * and then the icon says why on hover, since a missing picture is otherwise
 * indistinguishable from one never asked for.
 */
export function UserAvatar({
  src,
  problem,
  pending,
  onSignIn,
  size = 20,
}: {
  src?: string;
  problem?: string;
  pending?: boolean;
  onSignIn?: () => void;
  size?: number;
}): JSX.Element {
  const [broken, setBroken] = React.useState(false);
  React.useEffect(() => setBroken(false), [src]);
  const style = { width: size, height: size };
  if (src && !broken) {
    return (
      <img
        className="avatar avatar-user avatar-photo"
        style={style}
        src={src}
        alt=""
        onError={() => setBroken(true)}
      />
    );
  }
  const why = broken ? t("the picture did not decode") : problem;
  const icon = <IconUser size={Math.round(size * 0.6)} />;
  if (onSignIn && !broken) {
    // Only a browser session may see the picture: one click opens that browser.
    return (
      <button
        type="button"
        className={`avatar avatar-user avatar-sign-in${pending ? " avatar-pending" : ""}`}
        style={style}
        disabled={pending}
        title={
          pending
            ? t("Signing in to GitHub in the browser window...")
            : `${tf("No GitHub picture: {0}", why ?? "")}. ${t("Click to sign in to GitHub and show it.")}`
        }
        onClick={onSignIn}
      >
        {icon}
      </button>
    );
  }
  return (
    <span
      className="avatar avatar-user"
      style={style}
      title={why ? tf("No GitHub picture: {0}", why) : undefined}
    >
      {icon}
    </span>
  );
}

export function ItemView({
  item,
  onOpenDiff,
}: {
  item: Item;
  /** Set when edits are reviewed in the editor's diff view. */
  onOpenDiff?: (path: string) => void;
}): JSX.Element | null {
  switch (item.kind) {
    case "user":
      // Each send keeps its own line: the pin finds it, and a click lands on it.
      return (
        <div className="user-text" data-user-msg={item.id}>
          {item.text}
        </div>
      );
    case "assistant":
      return (
        <div className="assistant-text">
          <Markdown text={item.text} />
        </div>
      );
    case "working":
      return <WorkingRow text={item.text} />;
    case "thinking":
      return <ThinkingRow text={item.text} streaming={item.streaming} />;
    case "tool":
      return (
        <ToolRow
          name={item.name}
          target={item.target}
          status={item.status}
          detail={item.detail}
          onOpen={
            onOpenDiff && item.path && DIFF_TOOLS.has(item.name)
              ? () => onOpenDiff(item.path)
              : undefined
          }
        />
      );
    case "notice":
      return <NoticeRow level={item.level} text={item.text} />;
    default:
      return null;
  }
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

function ToolRow(props: {
  name: string;
  target: string;
  status: ToolStatus;
  detail: string;
  onOpen?: () => void;
}) {
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
          {target &&
            (props.onOpen ? (
              <button className="tool-target tool-target-link" title={t("Show the change in the editor")} onClick={props.onOpen}>
                {target}
              </button>
            ) : (
              <span className="tool-target" title={target}>
                {target}
              </span>
            ))}
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
    <div className="typing">
      <span />
      <span />
      <span />
      <em>{label}</em>
      {heartbeat ? <em className="typing-clock">· {formatElapsed(heartbeat)}</em> : null}
    </div>
  );
}

/** Seconds -> "14s" or "1:23". */
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
