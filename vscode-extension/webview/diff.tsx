import { structuredPatch } from "diff";
import * as React from "react";

/**
 * Colored, scrollable unified diff (Cline-style red/green line backgrounds),
 * built from the edit_code / write_file arguments before the tool ever touches
 * the filesystem. For a new file pass `oldText = ""`.
 */
export function DiffView({ oldText, newText }: { oldText: string; newText: string }): JSX.Element {
  const hunks = React.useMemo(
    () => structuredPatch("a", "b", oldText ?? "", newText ?? "", "", "", { context: 3 }).hunks,
    [oldText, newText]
  );

  if (hunks.length === 0) {
    return <div className="diff diff-empty">No changes.</div>;
  }

  return (
    <div className="diff">
      {hunks.map((hunk, hi) => {
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
    </div>
  );
}
