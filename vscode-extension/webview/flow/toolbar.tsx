import * as React from "react";

import { t, tf } from "../i18n";
import type { Action, EditorState } from "./store";
import { hasErrors } from "./store";

export interface ToolbarProps {
  state: EditorState;
  dispatch(action: Action): void;
  onRun(): void;
  onStop(): void;
  onSave(): void;
  onFrame(): void;
}

export function Toolbar(props: ToolbarProps): JSX.Element {
  const { state, dispatch } = props;
  const blocked = hasErrors(state.issues);
  const running = state.run.running;
  return (
    <>
      <div className="flow-toolbar">
        <span className="brand">
          <span className={`dot${running ? " running" : ""}`} />
          {t("Orchestrator")}
          <span className="name">{state.fileName || state.graph.name || t("unnamed")}</span>
          {state.dirty && <span className="name">•</span>}
        </span>

        <div className="flow-play">
          {running ? (
            <button className="danger" data-testid="stop" onClick={props.onStop}>
              ■ {t("Stop")}
            </button>
          ) : (
            <button
              className="primary"
              data-testid="play"
              disabled={blocked || !state.catalog}
              title={
                blocked ? t("Fix the graph errors before running it.") : `${t("Run")} (F5)`
              }
              onClick={props.onRun}
            >
              ▶ {t("Run")}
            </button>
          )}
          <span className="run-status" data-testid="run-status">
            {running
              ? tf("{0} nodes", state.run.nodesExecuted)
              : blocked
                ? tf("{0} error(s)", state.issues.filter((i) => i.severity === "error").length)
                : state.run.status || t("ready")}
          </span>
        </div>

        <span className="spacer" />
        <button
          data-testid="undo"
          disabled={state.undo.length === 0}
          title={`${t("Undo")} (Ctrl+Z)`}
          onClick={() => dispatch({ type: "undo" })}
        >
          ↶
        </button>
        <button
          data-testid="redo"
          disabled={state.redo.length === 0}
          title={`${t("Redo")} (Ctrl+Shift+Z)`}
          onClick={() => dispatch({ type: "redo" })}
        >
          ↷
        </button>
        <button data-testid="frame" title={`${t("Fit everything")} (F)`} onClick={props.onFrame}>
          ⤢
        </button>
        <button data-testid="save" title={`${t("Save")} (Ctrl+S)`} onClick={props.onSave}>
          {t("Save")}
        </button>
      </div>

    </>
  );
}
