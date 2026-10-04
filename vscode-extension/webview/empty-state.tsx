import * as React from "react";

import { Codicon } from "./codicon";
import { HistoryEntry, relativeTime } from "./history";
import { t } from "./i18n";
import { AgentAvatar } from "./agent-avatar";

export interface EmptyAction {
  icon: string;
  label: string;
  hint: string;
  run: () => void;
}

/**
 * A new conversation: what can be done with what is selected right now, and
 * the conversations worth going back to.
 */
export function EmptyState({
  actions,
  recent,
  onOpen,
}: {
  actions: EmptyAction[];
  recent: HistoryEntry[];
  onOpen: (id: string) => void;
}): JSX.Element {
  return (
    <div className="empty">
      <div className="spark">
        <AgentAvatar pose="idle" size={30} />
      </div>
      <h2>Code-AI</h2>
      <div className="empty-sub">{t("Ask anything about this workspace, or start from here.")}</div>
      <div className="empty-actions">
        {actions.map((action) => (
          <button key={action.label} className="empty-action" onClick={action.run}>
            <Codicon name={action.icon} size={16} />
            <span className="empty-action-text">
              <span className="empty-action-label">{action.label}</span>
              <span className="empty-action-hint">{action.hint}</span>
            </span>
          </button>
        ))}
      </div>
      {recent.length > 0 && (
        <div className="empty-recent">
          <div className="empty-recent-head">{t("Recent")}</div>
          {recent.map((entry) => (
            <button key={entry.id} className="empty-recent-item" onClick={() => onOpen(entry.id)}>
              <Codicon name="comment-discussion" size={14} />
              <span className="empty-recent-title">{entry.title}</span>
              <span className="empty-recent-when">{relativeTime(entry.updatedAt)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
