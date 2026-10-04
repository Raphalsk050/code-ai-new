import { structuredPatch } from "diff";
import * as React from "react";
import { t, tf } from "./i18n";

/**
 * Colored, scrollable unified diff (Cline-style red/green line backgrounds),
 * built from the edit_code / write_file arguments before the tool ever touches
 * the filesystem. For a new file pass `oldText = ""`.
 */
/** Lines added and removed between two texts. */
export function countChanges(oldText: string, newText: string): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const hunk of structuredPatch("a", "b", oldText ?? "", newText ?? "", "", "", { context: 0 }).hunks) {
    for (const line of hunk.lines) {
      if (line[0] === "+") added++;
      else if (line[0] === "-") removed++;
    }
  }
  return { added, removed };
}

export function DiffView({
  oldText,
  newText,
  limit,
}: {
  oldText: string;
  newText: string;
  /** Show at most this many lines, then say how many were left out. */
  limit?: number;
}): JSX.Element {
  const hunks = React.useMemo(
    () => structuredPatch("a", "b", oldText ?? "", newText ?? "", "", "", { context: 3 }).hunks,
    [oldText, newText]
  );

  if (hunks.length === 0) {
    return <div className="diff diff-empty">{t("No changes.")}</div>;
  }

  let budget = limit ?? Infinity;
  let hidden = 0;
  return (
    <div className="diff">
      {hunks.map((hunk, hi) => {
        if (budget <= 0) {
          hidden += hunk.lines.length;
          return null;
        }
        let oldLn = hunk.oldStart;
        let newLn = hunk.newStart;
        return (
          <div key={hi} className="diff-hunk">
            <div className="diff-hunk-head">
              @@ -{hunk.oldStart},{hunk.oldLines} +{hunk.newStart},{hunk.newLines} @@
            </div>
            {hunk.lines.map((line, li) => {
              const sign = line[0];
              if (sign === "\\") return null; // "\ No newline at end of file"
              if (budget <= 0) {
                hidden++;
                return null;
              }
              budget--;
              const kind = sign === "+" ? "add" : sign === "-" ? "del" : "ctx";
              const oldNo = sign === "+" ? "" : String(oldLn++);
              const newNo = sign === "-" ? "" : String(newLn++);
              return (
                <div key={li} className={`diff-line diff-${kind}`}>
                  <span className="diff-gutter">{oldNo}</span>
                  <span className="diff-gutter">{newNo}</span>
                  <span className="diff-sign">{sign === " " ? "" : sign}</span>
                  <span className="diff-text">{line.slice(1) || " "}</span>
                </div>
              );
            })}
          </div>
        );
      })}
      {hidden > 0 && <div className="diff-more">{tf("{0} more lines", hidden)}</div>}
    </div>
  );
}
