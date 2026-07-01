import * as React from "react";

import type { ExplainTarget } from "../src/protocol";
import { IconBook, IconChevron } from "./icons";
import { Markdown } from "./markdown";

export interface ExplainViewState {
  status: "idle" | "analyzing" | "ready" | "error";
  markdown?: string;
  target?: ExplainTarget;
  error?: string;
}

export const INITIAL_EXPLAIN: ExplainViewState = { status: "idle" };

/** Format a target as `path:start-end`, matching the context chip. */
function targetLabel(target?: ExplainTarget): string {
  if (!target) return "";
  if (target.startLine && target.endLine) {
    return `${target.path}:${target.startLine}-${target.endLine}`;
  }
  return target.path;
}

/**
 * Collapsible side-panel view of the current explanation. This is the reliable
 * counterpart to the native editor hover: the hover can be finicky about
 * re-rendering when the async analysis resolves, but this panel always reflects
 * the latest result the moment it arrives.
 */
export function ExplainPanel({ state }: { state: ExplainViewState }): JSX.Element {
  const [collapsed, setCollapsed] = React.useState(false);
  const { status, target } = state;

  const idle = status === "idle";

  return (
    <div className="transcript">
      <div className="explain">
        <div className="explain-head">
          <button
            className="explain-toggle"
            title={collapsed ? "Expand" : "Collapse"}
            aria-expanded={!collapsed}
            onClick={() => setCollapsed((c) => !c)}
            disabled={idle}
          >
            <IconChevron size={14} className={`explain-chevron ${collapsed ? "" : "open"}`} />
          </button>
          <IconBook size={16} />
          <span className="explain-title">Explanation</span>
          {status === "analyzing" && <span className="spinner explain-spin" />}
        </div>

        {target && <div className="explain-target">{targetLabel(target)}</div>}

        {!collapsed && (
          <div className="explain-body">
            {idle && (
              <div className="explain-empty">
                Select code in the editor to see a detailed explanation here — and inline on hover.
              </div>
            )}
            {status === "analyzing" && !state.markdown && (
              <div className="explain-status">
                <span className="spinner" /> Reading the selection…
              </div>
            )}
            {status === "error" && <div className="explain-error">{state.error}</div>}
            {state.markdown && (status === "ready" || status === "analyzing") && (
              <Markdown text={state.markdown} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
