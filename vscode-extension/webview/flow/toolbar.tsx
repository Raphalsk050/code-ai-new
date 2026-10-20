import * as React from "react";

import { t, tf } from "../i18n";
import {
  IconCheck,
  IconFrame,
  IconMap,
  IconMinus,
  IconPlay,
  IconPlus,
  IconRedo,
  IconSave,
  IconStop,
  IconUndo,
  IconWarn,
  IconX,
} from "../icons";
import { ZOOM_STEP } from "./canvas";
import type { Action, EditorState } from "./store";
import { hasErrors } from "./store";

export interface ToolbarProps {
  state: EditorState;
  dispatch(action: Action): void;
  onRun(): void;
  onStop(): void;
  onSave(): void;
  onFrame(): void;
  onZoom(factor: number): void;
  onZoomReset(): void;
  minimap: boolean;
  onToggleMinimap(): void;
}

export function Toolbar(props: ToolbarProps): JSX.Element {
  const { state, dispatch } = props;
  const blocked = hasErrors(state.issues);
  const running = state.run.running;
  const zoom = Math.round(state.graph.view.zoom * 100);
  return (
    <div className="flow-toolbar">
      <span className="brand">
        <span className={`dot${running ? " running" : ""}`} />
        {t("Orchestrator")}
        <span className="name">{state.fileName || state.graph.name || t("unnamed")}</span>
        {state.dirty && (
          <span className="dirty" title={t("Unsaved changes")}>
            •
          </span>
        )}
      </span>

      <div className="flow-play">
        {running ? (
          <button className="danger flow-run" data-testid="stop" onClick={props.onStop}>
            <IconStop size={12} /> {t("Stop")}
          </button>
        ) : (
          <button
            className="primary flow-run"
            data-testid="play"
            disabled={blocked || !state.catalog}
            title={blocked ? t("Fix the graph errors before running it.") : `${t("Run")} (F5)`}
            onClick={props.onRun}
          >
            <IconPlay size={12} /> {t("Run")}
          </button>
        )}
        <StatusPill state={state} />
      </div>

      <span className="spacer" />
      <div className="flow-btn-group">
        <button
          className="icon"
          data-testid="undo"
          disabled={state.undo.length === 0}
          title={`${t("Undo")} (Ctrl+Z)`}
          onClick={() => dispatch({ type: "undo" })}
        >
          <IconUndo size={15} />
        </button>
        <button
          className="icon"
          data-testid="redo"
          disabled={state.redo.length === 0}
          title={`${t("Redo")} (Ctrl+Shift+Z)`}
          onClick={() => dispatch({ type: "redo" })}
        >
          <IconRedo size={15} />
        </button>
      </div>
      <div className="flow-btn-group">
        <button
          className="icon"
          data-testid="zoom-out"
          title={t("Zoom out")}
          onClick={() => props.onZoom(1 / ZOOM_STEP)}
        >
          <IconMinus size={15} />
        </button>
        <button
          className="flow-zoom"
          data-testid="zoom-reset"
          title={t("Back to 100%")}
          onClick={props.onZoomReset}
        >
          {zoom}%
        </button>
        <button
          className="icon"
          data-testid="zoom-in"
          title={t("Zoom in")}
          onClick={() => props.onZoom(ZOOM_STEP)}
        >
          <IconPlus size={15} />
        </button>
        <button
          className="icon"
          data-testid="frame"
          title={`${t("Fit everything")} (F)`}
          onClick={props.onFrame}
        >
          <IconFrame size={15} />
        </button>
        <button
          className={`icon${props.minimap ? " on" : ""}`}
          data-testid="minimap-toggle"
          title={t("Minimap: shows when part of the graph is off screen")}
          aria-pressed={props.minimap}
          onClick={props.onToggleMinimap}
        >
          <IconMap size={15} />
        </button>
      </div>
      <button
        className="flow-save"
        data-testid="save"
        title={`${t("Save")} (Ctrl+S)`}
        onClick={props.onSave}
      >
        <IconSave size={14} /> {t("Save")}
      </button>
    </div>
  );
}

/** One line on how the graph stands: running, broken, or how it last ended. */
function StatusPill({ state }: { state: EditorState }): JSX.Element {
  const errors = state.issues.filter((issue) => issue.severity === "error").length;
  const warnings = state.issues.filter((issue) => issue.severity === "warning").length;
  const status = state.run.status;
  let tone = "ok";
  let icon: JSX.Element | null = <IconCheck size={12} />;
  let text = t("ready");
  if (state.run.running) {
    tone = "live";
    icon = <span className="pulse" />;
    text = tf("{0} nodes", state.run.nodesExecuted);
  } else if (errors > 0) {
    tone = "bad";
    icon = <IconX size={12} />;
    text = tf("{0} error(s)", errors);
  } else if (status && status !== "completed") {
    tone = "bad";
    icon = <IconX size={12} />;
    text = t(status);
  } else if (status) {
    text = t(status);
  } else if (warnings > 0) {
    tone = "warn";
    icon = <IconWarn size={12} />;
    text = tf("ready · {0} warning(s)", warnings);
  }
  return (
    <span className={`flow-pill ${tone}`} data-testid="run-status">
      {icon}
      {text}
    </span>
  );
}
