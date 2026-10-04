import * as React from "react";

import type { ChangeSummary } from "../../src/protocol";
import { Codicon } from "../codicon";
import { t } from "../i18n";
import { LineDelta } from "../tool-row";

/** The files the agent changed in this conversation; a click shows the change. */
export function ChangesPanel({
  files,
  onOpen,
  onRevert,
  onReviewAll,
}: {
  files: ChangeSummary[];
  onOpen: (path: string) => void;
  onRevert: (path: string) => void;
  onReviewAll: () => void;
}): JSX.Element {
  if (files.length === 0) {
    return <div className="rail-empty">{t("Nothing changed yet.")}</div>;
  }
  return (
    <div className="changes">
      <ul className="change-list">
        {files.map((file) => {
          const slash = file.path.lastIndexOf("/");
          const name = file.path.slice(slash + 1);
          const dir = slash > 0 ? file.path.slice(0, slash) : "";
          return (
            <li key={file.path} className="change-row">
              <button className="change-open" title={file.path} onClick={() => onOpen(file.path)}>
                <Codicon name={file.created ? "new-file" : "file"} size={14} />
                <span className="change-name">{name}</span>
                {dir && <span className="change-dir">{"\u200e" + dir}</span>}
              </button>
              {file.revertable ? (
                <LineDelta added={file.added} removed={file.removed} />
              ) : (
                <span className="change-unknown" title={t("Too large or not text: no line count")}>
                  ?
                </span>
              )}
              {file.revertable && (
                <button
                  className="icon-btn change-revert"
                  title={file.created ? t("Delete this new file") : t("Revert to before the agent changed it")}
                  aria-label={t("Revert")}
                  onClick={() => onRevert(file.path)}
                >
                  <Codicon name="discard" size={14} />
                </button>
              )}
            </li>
          );
        })}
      </ul>
      <div className="panel-actions">
        <button className="panel-btn" onClick={onReviewAll}>
          <Codicon name="diff" size={14} /> {t("Review all")}
        </button>
      </div>
    </div>
  );
}
