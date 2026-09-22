import * as React from "react";

import { t, tf } from "../i18n";

import type { HistoryEntry } from "../history";
import { relativeTime } from "../history";
import { IconTrash } from "../icons";

export function HistoryPanel({
  entries,
  activeId,
  onOpen,
  onDelete,
  onNew,
  onSeeAll,
}: {
  entries: HistoryEntry[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  onNew: () => void;
  onSeeAll: () => void;
}): JSX.Element {
  if (entries.length === 0) {
    return (
      <div className="rail-empty">
        {t("No saved conversations yet.")}
        <div className="panel-actions">
          <button className="panel-btn primary" onClick={onNew}>
            {t("New conversation")}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="panel-card">
      {entries.slice(0, 8).map((entry) => (
        <div
          key={entry.id}
          className={`agent-card ${entry.id === activeId ? "running" : ""}`}
          onClick={() => onOpen(entry.id)}
          style={{ cursor: "pointer" }}
        >
          <div className="agent-head">
            <span className="agent-name">{entry.title}</span>
            <button
              className="rail-btn"
              title={t("Delete conversation")}
              onClick={(e) => {
                e.stopPropagation();
                onDelete(entry.id);
              }}
            >
              <IconTrash size={12} />
            </button>
          </div>
          {entry.summary ? (
            <div className="history-summary">{entry.summary}</div>
          ) : (
            <div className="agent-task">sem resumo ainda</div>
          )}
          <div className="history-tags">
            <span className="history-tag">{relativeTime(entry.updatedAt)}</span>
            <span className="history-tag">{entry.count} msgs</span>
            {entry.tokens ? <span className="history-tag">{entry.tokens}</span> : null}
          </div>
        </div>
      ))}
      <div className="panel-actions">
        <button className="panel-btn" onClick={onSeeAll}>
          Ver todas
        </button>
        <button className="panel-btn primary" onClick={onNew}>
          {t("New conversation")}
        </button>
      </div>
    </div>
  );
}
