import * as React from "react";

import { t, tf } from "./i18n";

import type { RefactorImprovement } from "../src/protocol";
import { IconCheck, IconWand } from "./icons";

export interface RefactorViewState {
  status: "idle" | "analyzing" | "ready" | "error";
  improvements: RefactorImprovement[];
  path?: string;
  language?: string;
  error?: string;
  planning: Record<string, boolean>;
  planned: Record<string, string>;
}

export const INITIAL_REFACTOR: RefactorViewState = {
  status: "idle",
  improvements: [],
  planning: {},
  planned: {},
};

const ALL = "__all__";

export function RefactorPanel({
  state,
  autoRun,
  onAnalyze,
  onPlan,
  onApply,
}: {
  state: RefactorViewState;
  autoRun: boolean;
  onAnalyze: () => void;
  onPlan: (id: string, improvements: RefactorImprovement[]) => void;
  onApply: (markdown: string) => void;
}): JSX.Element {
  const { status, improvements } = state;

  return (
    <div className="transcript">
      <div className="refactor">
        <div className="refactor-head">
          <div className="refactor-title">
            <IconWand size={16} />
            <span>{t("Architectural improvements")}</span>
          </div>
          <button className="btn-ghost refactor-analyze" onClick={onAnalyze} disabled={status === "analyzing"}>
            {status === "analyzing" ? t("Analysing…") : improvements.length ? t("Analyse again") : t("Analyse selection")}
          </button>
        </div>

        {state.path && (
          <div className="refactor-target">
            {state.path}
            {state.language ? ` · ${state.language}` : ""}
          </div>
        )}

        {status === "analyzing" && (
          <div className="refactor-status">
            <span className="spinner" /> {t("Analysing the selection…")}
          </div>
        )}

        {status === "error" && <div className="refactor-error">{state.error}</div>}

        {status === "idle" && (
          <div className="refactor-empty">
            {autoRun
              ? t("Select a snippet in the editor to see the suggested improvements.")
              : t("Select a snippet in the editor and run the analysis.")}
          </div>
        )}

        {status === "ready" && improvements.length === 0 && (
          <div className="refactor-empty">
            <IconCheck size={16} /> {t("No improvements found: this snippet looks clean.")}
          </div>
        )}

        {improvements.length > 0 && (
          <>
            <div className="refactor-cards">
              {improvements.map((imp) => (
                <Card
                  key={imp.id}
                  imp={imp}
                  planning={!!state.planning[imp.id]}
                  planned={state.planned[imp.id]}
                  onPlan={() => onPlan(imp.id, [imp])}
                  onApply={() => onApply(state.planned[imp.id]!)}
                />
              ))}
            </div>

            <div className="refactor-all">
              {state.planned[ALL] ? (
                <button className="btn-primary" onClick={() => onApply(state.planned[ALL]!)}>
                  {t("Apply all changes")}
                </button>
              ) : state.planning[ALL] ? (
                <button className="btn-ghost" disabled>
                  <span className="spinner" /> {t("Planning all…")}
                </button>
              ) : (
                <button className="btn-ghost" onClick={() => onPlan(ALL, improvements)}>
                  {t("Planning all")}
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Card({
  imp,
  planning,
  planned,
  onPlan,
  onApply,
}: {
  imp: RefactorImprovement;
  planning: boolean;
  planned?: string;
  onPlan: () => void;
  onApply: () => void;
}): JSX.Element {
  return (
    <div className="refactor-card">
      <div className="refactor-card-body">
        <div className="refactor-card-head">
          <span className="refactor-card-title">{imp.title}</span>
          <span className={`impact impact-${imp.impact}`}>{imp.impact}</span>
        </div>
        {imp.rationale && <div className="refactor-card-rationale">{imp.rationale}</div>}
      </div>
      <div className="refactor-card-action">
        {planned ? (
          <button className="btn-primary" onClick={onApply} title={t("Apply with the agent")}>
            {t("Apply changes")}
          </button>
        ) : planning ? (
          <button className="btn-ghost" disabled>
            <span className="spinner" /> {t("Planning…")}
          </button>
        ) : (
          <button className="btn-ghost" onClick={onPlan} title={t("Write a detailed plan")}>
            {t("Planning")}
          </button>
        )}
      </div>
    </div>
  );
}

export { ALL as REFACTOR_ALL };
