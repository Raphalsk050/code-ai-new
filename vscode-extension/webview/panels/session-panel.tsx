import * as React from "react";

import { t, tf } from "../i18n";

import type { SessionState } from "../store";
import type { UsageState } from "../store";
import { formatTokens } from "../store/usage";

export function SessionPanel({
  session,
  usage,
  model,
  workspace,
  onIndex,
  onCompact,
}: {
  session: SessionState;
  usage: UsageState;
  model: string;
  workspace: string;
  onIndex: (full: boolean) => void;
  onCompact: () => void;
}): JSX.Element {
  const indexing = session.indexTotal > 0;
  const pct = indexing ? Math.min(100, (session.indexDone / session.indexTotal) * 100) : 0;
  return (
    <div className="panel-card">
      <div className="kv">
        <span className="k">model</span>
        <span className="v">{model || "-"}</span>
      </div>
      <div className="kv">
        <span className="k">workspace</span>
        <span className="v">{workspace ? workspace.split(/[\\/]/).pop() : "-"}</span>
      </div>
      <div className="kv">
        <span className="k">estado</span>
        <span className="v">{session.status.toLowerCase()}</span>
      </div>
      <div className="kv">
        <span className="k">fase</span>
        <span className="v">{session.phase}</span>
      </div>
      <div className="kv">
        <span className="k">planner</span>
        <span className="v">{session.plannerMode}</span>
      </div>
      <div className="kv">
        <span className="k">permission</span>
        <span className="v">{session.permissionMode}</span>
      </div>
      <div className="kv">
        <span className="k">verification</span>
        <span className={`v ${session.verification === "passed" ? "ok" : "warn"}`}>
          {session.verification}
        </span>
      </div>
      <div className="kv">
        <span className="k">contexto</span>
        <span className="v">
          {usage.used == null ? "-" : `${usage.estimated ? "~" : ""}${formatTokens(usage.used)}`}
          {usage.budget ? ` / ${formatTokens(usage.budget)}` : ""}
        </span>
      </div>
      <div className="kv">
        <span className="k">acumulado</span>
        <span className="v">{formatTokens(usage.cumulative)}</span>
      </div>

      {indexing && (
        <>
          <div className="kv">
            <span className="k">index</span>
            <span className="v">
              {session.indexPhase} {session.indexDone}/{session.indexTotal}
            </span>
          </div>
          <div className="mini-track">
            <div className="mini-fill" style={{ width: `${pct}%` }} />
          </div>
        </>
      )}

      <div className="panel-actions">
        <button className="panel-btn" onClick={() => onIndex(false)}>
          Reindexar
        </button>
        <button className="panel-btn" onClick={() => onIndex(true)}>
          Full index
        </button>
        <button className="panel-btn" onClick={onCompact}>
          Compactar
        </button>
      </div>
    </div>
  );
}
