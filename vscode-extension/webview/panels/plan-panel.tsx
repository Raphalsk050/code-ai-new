import * as React from "react";

import { Codicon } from "../codicon";
import { t, tf } from "../i18n";
import { useFlipList } from "../motion";
import type { GoalState, PlanState, StepStatus } from "../store";

function StepMark({ status }: { status: StepStatus }): JSX.Element {
  switch (status) {
    case "done":
      return <Codicon name="check" size={14} />;
    case "running":
      return <span className="spinner" />;
    case "failed":
      return <Codicon name="error" size={14} />;
    case "paused":
      return <Codicon name="debug-pause" size={14} />;
    default:
      return <Codicon name="circle-large-outline" size={14} />;
  }
}

export function PlanPanel({
  plan,
  goal,
  onReplan,
  onGoal,
}: {
  plan: PlanState;
  goal: GoalState;
  onReplan: () => void;
  onGoal: (action: "start" | "stop") => void;
}): JSX.Element {
  const register = useFlipList();

  if (plan.steps.length === 0 && goal.phase === "none") {
    return <div className="rail-empty">{t("No plan in this turn.")}</div>;
  }

  return (
    <div className="panel-card">
      <ol className="plan-steps">
        {plan.steps.map((step, i) => (
          <li
            key={`${i}-${step.title}`}
            ref={register(`${i}-${step.title}`)}
            className={`plan-step ${step.status}`}
          >
            <span className="mark">
              <StepMark status={step.status} />
            </span>
            <span className="title">{step.title}</span>
          </li>
        ))}
      </ol>

      {plan.evidence.length > 0 && (
        <div className="plan-evidence">
          {plan.evidence.slice(-3).map((e, i) => (
            <div key={i}>
              {e.type}: {e.summary}
            </div>
          ))}
        </div>
      )}

      {plan.rejection && <div className="plan-reject">{tf("completion rejected: {0}", plan.rejection)}</div>}

      {goal.phase !== "none" && (
        <div className="plan-goal">
          <div>
            <strong>{t("goal")}</strong> · {goal.phase}
            {goal.maxIterations ? ` · ${goal.iteration}/${goal.maxIterations}` : ""}
          </div>
          <div className="plan-goal-line">{goal.objective}</div>
          {goal.criteria.length > 0 && (
            <div className="plan-goal-line">{tf("criteria: {0}/{1}", goal.met, goal.criteria.length)}</div>
          )}
          {goal.note && <div className="plan-goal-line">{goal.note}</div>}
          <div className="panel-actions">
            {(goal.phase === "proposed" || goal.phase === "blocked") && (
              <button className="panel-btn primary" onClick={() => onGoal("start")}>
                {goal.phase === "blocked" ? t("Resume") : t("Start loop")}
              </button>
            )}
            {goal.phase === "running" && (
              <button className="panel-btn danger" onClick={() => onGoal("stop")}>
                {t("Stop")}
              </button>
            )}
          </div>
        </div>
      )}

      {plan.steps.length > 0 && (
        <div className="panel-actions">
          <button className="panel-btn" onClick={onReplan}>
            {t("Replan")}
          </button>
        </div>
      )}
    </div>
  );
}
