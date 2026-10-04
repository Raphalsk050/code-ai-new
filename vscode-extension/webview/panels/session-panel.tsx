import * as React from "react";

import { Codicon } from "../codicon";
import { meterLevel } from "../context-meter";
import { t, tf } from "../i18n";
import type { SessionState, UsageState } from "../store";
import { formatTokens } from "../store/usage";

/** The backend's state in words; enums never reach the screen raw. */
export function humanState(session: SessionState, waiting: boolean, connected: boolean): string {
  if (!connected) return session.status === "DISCONNECTED" ? t("Offline") : t("Connecting");
  if (waiting) return t("Waiting for you");
  switch (session.status) {
    case "EXECUTING_TOOL": {
      const tools = session.runningTools;
      if (tools.length === 1) return tf("Running {0}", tools[0].name);
      return tools.length > 1 ? tf("Running {0} tools", tools.length) : t("Running tools");
    }
    case "CALLING_MODEL":
      if (session.retry) return tf("Retrying the model call {0}", session.retry);
      return session.modelWaiting ? t("Queued at the provider") : t("Calling the model");
    case "COMPRESSING_CONTEXT":
      return t("Compacting context");
    case "CANCELLING":
      return t("Cancelling");
    case "CANCELLED":
      return t("Cancelled");
    case "ERROR":
    case "FAILED":
      return t("Failed");
    case "READY":
    case "IDLE":
      return t("Ready");
    default:
      return session.status.toLowerCase().replace(/_/g, " ");
  }
}

export function SessionPanel({
  session,
  usage,
  model,
  workspace,
  branch,
  waiting,
  connected,
  onIndex,
  onCompact,
}: {
  session: SessionState;
  usage: UsageState;
  model: string;
  workspace: string;
  branch: string;
  waiting: boolean;
  connected: boolean;
  onIndex: (full: boolean) => void;
  onCompact: () => void;
}): JSX.Element {
  const [details, setDetails] = React.useState(false);
  const indexing = session.indexTotal > 0;
  const indexPct = indexing ? Math.min(100, (session.indexDone / session.indexTotal) * 100) : 0;
  const level = meterLevel(usage);
  const ratio = usage.used != null && usage.budget ? Math.min(1, usage.used / usage.budget) : 0;
  const state = humanState(session, waiting, connected);
  const tone = !connected ? "bad" : waiting ? "warn" : session.status === "READY" || session.status === "IDLE" ? "ok" : "run";
  const verified = session.verification === "passed" ? true : session.verification === "not current" ? false : null;

  return (
    <div className="session-panel">
      <div className="session-meter">
        <div className="session-meter-head">
          <span className="k">{t("Context")}</span>
          <span className={`session-meter-value ${level}`}>
            {usage.used == null ? "-" : `${usage.estimated ? "~" : ""}${formatTokens(usage.used)}`}
            {usage.budget ? <span className="muted"> / {formatTokens(usage.budget)}</span> : null}
          </span>
        </div>
        <div className={`rail-meter ${level}`} aria-hidden="true">
          <span className="rail-meter-fill" style={{ width: `${level === "unknown" ? 0 : Math.max(1, ratio * 100)}%` }} />
          <span className="rail-meter-mark" style={{ left: `${usage.threshold * 100}%` }} title={t("Auto-compaction")} />
        </div>
      </div>

      <div className="kv">
        <span className="k">{t("State")}</span>
        <span className={`v state-${tone}`}>
          <span className="state-dot" /> {state}
        </span>
      </div>
      <div className="kv">
        <span className="k">{t("Spent")}</span>
        <span className="v">{tf("{0} tokens", formatTokens(usage.cumulative))}</span>
      </div>
      {verified != null && (
        <div className="kv">
          <span className="k">{t("Verification")}</span>
          <span className={`v ${verified ? "ok" : "warn"}`}>
            <Codicon name={verified ? "pass" : "warning"} size={12} /> {verified ? t("passed") : t("not current")}
          </span>
        </div>
      )}
      {indexing && (
        <>
          <div className="kv">
            <span className="k">{t("Index")}</span>
            <span className="v">
              {session.indexPhase} {session.indexDone}/{session.indexTotal}
            </span>
          </div>
          <div className="mini-track">
            <div className="mini-fill" style={{ width: `${indexPct}%` }} />
          </div>
        </>
      )}

      <div className="panel-actions">
        <button className="panel-btn" onClick={onCompact}>
          {t("Compact")}
        </button>
        <button className="panel-btn" onClick={() => onIndex(false)}>
          {t("Reindex")}
        </button>
        <button className="panel-btn" onClick={() => onIndex(true)}>
          {t("Full index")}
        </button>
      </div>

      <button className="details-toggle" aria-expanded={details} onClick={() => setDetails(!details)}>
        <Codicon name="chevron-right" size={12} className={`chev ${details ? "chev-open" : ""}`} />
        {t("Technical details")}
      </button>
      {details && (
        <div className="details">
          <div className="kv">
            <span className="k">{t("Model")}</span>
            <span className="v one-line" title={model}>{model || "-"}</span>
          </div>
          <div className="kv">
            <span className="k">{t("Workspace")}</span>
            <span className="v one-line" title={workspace}>{workspace ? workspace.split(/[\\/]/).pop() : "-"}</span>
          </div>
          <div className="kv">
            <span className="k">{t("Branch")}</span>
            <span className="v one-line" title={branch}>{branch || "-"}</span>
          </div>
          <div className="kv">
            <span className="k">{t("Status")}</span>
            <span className="v">{session.status.toLowerCase()}</span>
          </div>
          <div className="kv">
            <span className="k">{t("Phase")}</span>
            <span className="v">{session.phase}</span>
          </div>
          <div className="kv">
            <span className="k">{t("Planner")}</span>
            <span className="v">{session.plannerMode}</span>
          </div>
          <div className="kv">
            <span className="k">{t("Permission")}</span>
            <span className="v">{session.permissionMode}</span>
          </div>
        </div>
      )}
    </div>
  );
}
