import * as React from "react";

import { IconCI, IconHistory, IconPlus, IconSettings, IconTrash } from "./icons";
import { HistoryEntry, relativeTime } from "./history";

export function HomeScreen({
  entries,
  onNew,
  onOpen,
  onDelete,
  onSettings,
}: {
  entries: HistoryEntry[];
  onNew: () => void;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  onSettings: () => void;
}): JSX.Element {
  const list = entries;
  return (
    <div className="home">
      <div className="home-topbar">
        <button className="icon-btn" title="Settings" onClick={onSettings}>
          <IconSettings size={16} />
        </button>
      </div>
      <div className="home-hero">
        <div className="spark">
          <IconCI size={30} />
        </div>
        <h2>Code-AI</h2>
        <div className="home-sub">Pick up a past conversation or start a new one.</div>
        <button className="btn-primary home-new" onClick={onNew}>
          <IconPlus size={15} />
          New conversation
        </button>
      </div>

      <div className="home-history">
        <div className="home-history-head">
          <IconHistory size={14} />
          <span>History</span>
        </div>
        {list.length === 0 ? (
          <div className="home-empty">No conversations yet.</div>
        ) : (
          <ul className="history-list">
            {list.map((c) => (
              <li key={c.id} className="history-item" onClick={() => onOpen(c.id)}>
                <div className="history-item-main">
                  <div className="history-title">{c.title}</div>
                  <div className="history-meta">
                    {relativeTime(c.updatedAt)} · {c.count} messages
                  </div>
                </div>
                <button
                  className="history-del icon-btn"
                  title="Delete conversation"
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
    </div>
  );
}
