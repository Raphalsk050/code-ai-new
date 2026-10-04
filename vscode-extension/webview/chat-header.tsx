import * as React from "react";

import { Codicon } from "./codicon";
import { ContextMeter } from "./context-meter";
import { t, tf } from "./i18n";
import { IconBrand } from "./icons";
import { ripple } from "./motion";
import type { UsageState } from "./store";

/**
 * The chat's 40px bar: way back, the mark, the conversation, its branch, a
 * live status glyph (spinner while the agent works, its words in the tooltip),
 * the context meter and four actions.
 */
export function ChatHeader({
  title,
  branch,
  busy,
  offline = false,
  activity,
  narrow,
  usage,
  update,
  railOpen,
  onBack,
  onNew,
  onHistory,
  onToggleRail,
  onSettings,
  onCompact,
}: {
  title: string;
  branch: string;
  busy: boolean;
  /** No backend: the glyph turns red. */
  offline?: boolean;
  /** What the agent is doing, with the elapsed time; empty when idle. */
  activity: string;
  narrow: boolean;
  usage: UsageState;
  update?: React.ReactNode;
  railOpen: boolean;
  onBack: () => void;
  onNew: () => void;
  onHistory: () => void;
  onToggleRail: () => void;
  onSettings: () => void;
  onCompact: () => void;
}): JSX.Element {
  return (
    <div className={`statusbar ${narrow ? "narrow" : ""}`}>
      <button className="icon-btn" title={t("Back to the history")} onPointerDown={ripple} onClick={onBack}>
        <Codicon name="arrow-left" />
      </button>
      <span className="brand-mark" title="Code-AI">
        <IconBrand size={20} />
      </span>
      <span className="chat-title" title={title}>
        {title}
      </span>
      {branch && !narrow && (
        <span className="branch-chip" title={tf("On the {0} branch", branch)}>
          <Codicon name="git-branch" size={12} />
          <span>{branch}</span>
        </span>
      )}
      {!narrow && (
        <span
          className={`live-status ${offline ? "offline" : busy ? "busy" : "idle"}`}
          role="status"
          title={offline ? t("bridge offline") : busy ? activity : t("Ready")}
          aria-label={offline ? t("bridge offline") : busy ? activity : t("Ready")}
        >
          {busy && !offline ? <span className="spinner" /> : <span className="live-dot" />}
        </span>
      )}
      <span className="bar-spacer" />
      <ContextMeter usage={usage} onCompact={onCompact} compact={narrow} />
      {update}
      <div className="topbar-actions">
        <button className="icon-btn" title={t("New conversation")} onPointerDown={ripple} onClick={onNew}>
          <Codicon name="add" />
        </button>
        {!narrow && (
          <button className="icon-btn" title={t("History")} onPointerDown={ripple} onClick={onHistory}>
            <Codicon name="history" />
          </button>
        )}
        <button
          className={`icon-btn ${railOpen ? "on" : ""}`}
          title={railOpen ? t("Hide the task panel") : t("Show the task panel")}
          aria-pressed={railOpen}
          onPointerDown={ripple}
          onClick={onToggleRail}
        >
          <Codicon name="layout-sidebar-right" />
        </button>
        <button className="icon-btn" title={t("Settings")} onPointerDown={ripple} onClick={onSettings}>
          <Codicon name="gear" />
        </button>
      </div>
    </div>
  );
}
