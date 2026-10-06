import * as React from "react";

import { t, tf } from "./i18n";

import type { Block } from "./blocks";
import { Codicon } from "./codicon";
import { useFollow } from "./follow";
import { IconBrand, IconUser } from "./icons";
import { Markdown } from "./markdown";
import type { SessionState } from "./store/session";
import { Item, workingLabel } from "./store/transcript";
import { DIFF_TOOLS, foldTools, ToolGroup, ToolRow, TurnFooter } from "./tool-row";
import { toolKind } from "./tool-target";

interface ItemActions {
  /** Set when edits are reviewed in the editor's diff view. */
  onOpenDiff?: (path: string) => void;
  /** Brings up the terminal, offered on a command that failed. */
  onTerminal?: () => void;
}

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
  onTerminal,
  children,
}: ItemActions & {
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
        {foldTools(block.items).map((entry) =>
          "group" in entry ? (
            <ToolGroup key={entry.group[0].id} items={entry.group}>
              {entry.group.map((item) => (
                <ItemView key={item.id} item={item} onOpenDiff={onOpenDiff} onTerminal={onTerminal} />
              ))}
            </ToolGroup>
          ) : (
            <ItemView key={entry.item.id} item={entry.item} onOpenDiff={onOpenDiff} onTerminal={onTerminal} />
          )
        )}
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
  // Keyed by the source that failed: an error that lands before an effect
  // could reset a flag would otherwise leave a new picture marked broken, or
  // a broken one shown as a blank image.
  const [brokenSrc, setBrokenSrc] = React.useState("");
  const broken = !!src && brokenSrc === src;
  const style = { width: size, height: size };
  if (src && !broken) {
    return (
      <img
        className="avatar avatar-user avatar-photo"
        style={style}
        src={src}
        alt=""
        onError={() => setBrokenSrc(src)}
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
  onTerminal,
}: ItemActions & { item: Item }): JSX.Element | null {
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
          <Markdown text={item.text} streaming={item.streaming} />
        </div>
      );
    case "working":
      return <WorkingRow text={item.text} />;
    case "thinking":
      return <ThinkingRow text={item.text} streaming={item.streaming} />;
    case "tool":
      return (
        <ToolRow
          item={item}
          onOpen={
            onOpenDiff && item.path && DIFF_TOOLS.has(item.name)
              ? () => onOpenDiff(item.path)
              : undefined
          }
          onTerminal={onTerminal && toolKind(item.name) === "run" ? onTerminal : undefined}
        />
      );
    case "notice":
      return <NoticeRow level={item.level} text={item.text} />;
    case "turn":
      return <TurnFooter item={item} onOpen={onOpenDiff} />;
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
  const follow = useFollow(body, { active: open && !!streaming });

  return (
    <div className={`thinking ${open ? "open" : ""} ${streaming ? "live" : ""}`}>
      <button className="thinking-head" onClick={() => setChoice(!open)}>
        <Codicon name="chevron-right" size={14} className="chev" />
        <Codicon name="lightbulb" size={14} />
        <span>{t("Reasoning")}</span>
        {streaming && <span className="thinking-live" aria-label={t("in progress")} />}
      </button>
      {open && (
        <div ref={body} className="thinking-body" {...follow.handlers}>
          {text}
        </div>
      )}
    </div>
  );
}

function NoticeRow({ level, text }: { level: string; text: string }) {
  const icon =
    level === "error" ? <Codicon name="error" size={14} /> : level === "warning" ? <Codicon name="warning" size={14} /> : null;
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
