import * as React from "react";

import { useFlipList } from "../motion";
import type { GoalState, PlanState } from "../store";

const MARK: Record<string, string> = {
  done: "✓",
  running: "▸",
  failed: "✗",
  paused: "◌",
  pending: "○",
};

export function PlanPanel({
  plan,
  goal,
  plannerMode,
  onReplan,
  onPlannerMode,
  onGoal,
}: {
  plan: PlanState;
  goal: GoalState;
  plannerMode: string;
  onReplan: () => void;
  onPlannerMode: (mode: string) => void;
  onGoal: (action: "start" | "stop") => void;
}): JSX.Element {
  const register = useFlipList();

  if (plan.steps.length === 0 && goal.phase === "none") {
    return (
      <div className="rail-empty">
        Sem plano neste turno. Em <code>plan</code> o agente escreve os passos antes de agir.
        <div className="panel-actions">
          {["auto", "plan", "act"].map((m) => (
            <button
              key={m}
              className={`panel-btn ${plannerMode === m ? "primary" : ""}`}
              onClick={() => onPlannerMode(m)}
            >
              {m}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="panel-card">
      {plan.steps.map((step, i) => (
        <div
          key={`${i}-${step.title}`}
          ref={register(`${i}-${step.title}`)}
          className={`plan-step ${step.status}`}
        >
          <span className="mark">{MARK[step.status] ?? "○"}</span>
          <span className="title">{step.title}</span>
        </div>
      ))}

      {plan.evidence.length > 0 && (
        <div className="plan-evidence">
          {plan.evidence.slice(-3).map((e, i) => (
            <div key={i}>
              {e.type}: {e.summary}
            </div>
          ))}
        </div>
      )}

      {plan.rejection && <div className="plan-reject">conclusão rejeitada: {plan.rejection}</div>}

      {goal.phase !== "none" && (
        <div className="plan-reject" style={{ borderColor: "var(--border)" }}>
          <div>
            <strong>objetivo</strong> · {goal.phase}
            {goal.maxIterations ? ` · ${goal.iteration}/${goal.maxIterations}` : ""}
          </div>
          <div style={{ marginTop: 3 }}>{goal.objective}</div>
          {goal.criteria.length > 0 && (
            <div style={{ marginTop: 3 }}>
              critérios: {goal.met}/{goal.criteria.length}
            </div>
          )}
          {goal.note && <div style={{ marginTop: 3 }}>{goal.note}</div>}
          <div className="panel-actions">
            {(goal.phase === "proposed" || goal.phase === "blocked") && (
              <button className="panel-btn primary" onClick={() => onGoal("start")}>
                {goal.phase === "blocked" ? "Retomar" : "Iniciar loop"}
              </button>
            )}
            {goal.phase === "running" && (
              <button className="panel-btn danger" onClick={() => onGoal("stop")}>
                Parar
              </button>
            )}
          </div>
        </div>
      )}

      <div className="panel-actions">
        <button className="panel-btn" onClick={onReplan}>
          Replanejar
        </button>
        {["auto", "plan", "act"].map((m) => (
          <button
            key={m}
            className={`panel-btn ${plannerMode === m ? "primary" : ""}`}
            onClick={() => onPlannerMode(m)}
          >
            {m}
          </button>
        ))}
      </div>
    </div>
  );
}
