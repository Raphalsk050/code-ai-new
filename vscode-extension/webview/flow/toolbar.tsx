import * as React from "react";

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
          Orquestrador
          <span className="name">{state.fileName || state.graph.name || "sem nome"}</span>
          {state.dirty && <span className="name">•</span>}
        </span>

        <div className="flow-play">
          {running ? (
            <button className="danger" data-testid="stop" onClick={props.onStop}>
              ■ Parar
            </button>
          ) : (
            <button
              className="primary"
              data-testid="play"
              disabled={blocked || !state.catalog}
              title={blocked ? "Corrija os erros do grafo antes de executar." : "Executar (F5)"}
              onClick={props.onRun}
            >
              ▶ Executar
            </button>
          )}
          <span className="run-status" data-testid="run-status">
            {running
              ? `${state.run.nodesExecuted} nós`
              : blocked
                ? `${state.issues.filter((i) => i.severity === "error").length} erro(s)`
                : state.run.status || "pronto"}
          </span>
        </div>

        <span className="spacer" />
        <button
          data-testid="undo"
          disabled={state.undo.length === 0}
          title="Desfazer (Ctrl+Z)"
          onClick={() => dispatch({ type: "undo" })}
        >
          ↶
        </button>
        <button
          data-testid="redo"
          disabled={state.redo.length === 0}
          title="Refazer (Ctrl+Shift+Z)"
          onClick={() => dispatch({ type: "redo" })}
        >
          ↷
        </button>
        <button data-testid="frame" title="Enquadrar tudo (F)" onClick={props.onFrame}>
          ⤢
        </button>
        <button data-testid="save" title="Salvar (Ctrl+S)" onClick={props.onSave}>
          Salvar
        </button>
      </div>

    </>
  );
}
