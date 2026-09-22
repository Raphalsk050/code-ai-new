import * as React from "react";

import { t, tf } from "../i18n";
import type { Action, EditorState } from "./store";

export interface LogPanelProps {
  state: EditorState;
  dispatch(action: Action): void;
}

/** The run log: everything a Print node wrote, plus what the agents did. */
export function LogPanel({ state, dispatch }: LogPanelProps): JSX.Element {
  const rowsRef = React.useRef<HTMLDivElement | null>(null);
  const count = state.log.length;

  React.useEffect(() => {
    const element = rowsRef.current;
    if (!element) return;
    // Follow the tail: a run the user is watching should not need scrolling.
    element.scrollTop = element.scrollHeight;
  }, [count]);

  return (
    <div className="flow-log" data-testid="log">
      <div className="flow-log-head">
        <button data-testid="log-toggle" onClick={() => dispatch({ type: "toggleLog" })}>
          {state.logOpen ? "▾" : "▸"} {t("Log")}
        </button>
        <span className="flow-hint" data-testid="log-count">
          {tf("{0} line(s)", count)}
        </span>
        <span className="spacer" />
        {state.run.runId && (
          <span className="flow-hint">
            {state.run.running ? t("running") : state.run.status}
          </span>
        )}
        <button data-testid="log-clear" onClick={() => dispatch({ type: "clearLog" })}>
          {t("Clear")}
        </button>
      </div>
      {state.logOpen && (
        <div className="flow-log-rows" ref={rowsRef}>
          {count === 0 && (
            <span className="flow-hint">
              {t(
                "Nothing yet. A Print node writes here, and that is how a graph gets debugged."
              )}
            </span>
          )}
          {state.log.map((row) => (
            <div key={row.id} className={`flow-log-row ${row.kind}`} data-testid="log-row">
              <span className="at">{row.at}</span>
              {row.nodeId && <span className="who">{row.nodeId}</span>}
              <span className="text">{row.text}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
