import * as React from "react";

import { AgentAvatar } from "./agent-avatar";
import { t, tf } from "./i18n";

import { IconHistory, IconPlus, IconSettings, IconTrash } from "./icons";
import { HistoryEntry, relativeTime } from "./history";
import { VersionMark } from "./version-mark";

export function HomeScreen({
  entries,
  onNew,
  onOpen,
  onDelete,
  onSettings,
  update,
  docked = false,
  version,
}: {
  entries: HistoryEntry[];
  onNew: () => void;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  onSettings: () => void;
  /** The update (or sign-in) button: the panel opens here, so it shows here too. */
  update?: React.ReactNode;
  /** The floating avatar comes to sit where the mark is: the mark makes room. */
  docked?: boolean;
  /** The backend's version, once it has said. */
  version?: string;
}): JSX.Element {
  const list = entries;
  return (
    <div className="home">
      <div className="home-topbar">
        {update}
        <button className="icon-btn" title={t("Settings")} onClick={onSettings}>
          <IconSettings size={16} />
        </button>
      </div>
      <div className="home-hero">
        <div className={`spark${docked ? " spark-dock" : ""}`}>
          {!docked && <AgentAvatar pose="idle" size={30} />}
        </div>
        <h2>Code-AI</h2>
        <div className="home-sub">{t("Pick up a conversation or start another.")}</div>
        <button className="btn-primary home-new" onClick={onNew}>
          <IconPlus size={15} />
          {t("New conversation")}
        </button>
      </div>

      <div className="home-history">
        <div className="home-history-head">
          <IconHistory size={14} />
          <span>{t("History")}</span>
        </div>
        {list.length === 0 ? (
          <div className="home-empty">{t("No conversations yet.")}</div>
        ) : (
          <ul className="history-list">
            {list.map((c) => (
              <li key={c.id} className="history-item" onClick={() => onOpen(c.id)}>
                <div className="history-item-main">
                  <div className="history-title">{c.title}</div>
                  {c.summary && <div className="history-summary">{c.summary}</div>}
                  <div className="history-meta">
                    {relativeTime(c.updatedAt)} · {tf("{0} messages", c.count)}
                    {c.tokens ? ` · ${c.tokens}` : ""}
                  </div>
                </div>
                <button
                  className="history-del icon-btn"
                  title={t("Delete conversation")}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(c.id);
                  }}
                >
                  <IconTrash size={13} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="home-foot">
        <VersionMark version={version} />
      </div>
    </div>
  );
}
