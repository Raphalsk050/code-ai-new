import * as React from "react";

import { useFlipList } from "../motion";
import type { AgentsState } from "../store";

export function AgentsPanel({ agents }: { agents: AgentsState }): JSX.Element {
  const register = useFlipList();

  if (agents.rows.length === 0) {
    return (
      <div className="rail-empty">
        Nenhum sub-agente neste turno. Eles aparecem aqui assim que o agente delega.
      </div>
    );
  }

  return (
    <div className="panel-card">
      {agents.rows.map((row) => (
        <div key={row.id} ref={register(row.id)} className={`agent-card ${row.status}`}>
          <div className="agent-head">
            <span className="agent-state" />
            <span className="agent-name">{row.name || row.type}</span>
            <span className="rail-section-note">{row.status}</span>
          </div>
          {row.task && <div className="agent-task">{row.task.slice(0, 160)}</div>}
          <div className="agent-detail">{row.detail}</div>
        </div>
      ))}
      {agents.circuitOpen.length > 0 && (
        <div className="plan-reject">
          circuito aberto: {agents.circuitOpen.join(", ")} (falhas repetidas, despacho suspenso)
        </div>
      )}
    </div>
  );
}
