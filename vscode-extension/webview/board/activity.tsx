import * as React from "react";

import type { BoardStep } from "../../src/board-schema";
import { t } from "../i18n";
import { toolIcon, toolVerb } from "./live";
import { AgentChip } from "./subcard";

function clock(at: number): string {
  const date = new Date(at * 1000);
  return [date.getHours(), date.getMinutes(), date.getSeconds()].map((n) => String(n).padStart(2, "0")).join(":");
}

/** Everything the board's agents did, oldest first, following the newest while it is in view. */
export function ActivityPanel({
  feed,
  height,
  onClose,
  onOpen,
}: {
  feed: BoardStep[];
  height: number;
  onClose: () => void;
  onOpen: (task: string, subtask: string) => void;
}): JSX.Element {
  const list = React.useRef<HTMLDivElement>(null);
  const pinned = React.useRef(true);
  React.useLayoutEffect(() => {
    const element = list.current;
    if (element && pinned.current) element.scrollTop = element.scrollHeight;
  }, [feed]);
  return (
    <section className="activity" data-testid="board-activity" style={{ height, flexBasis: height }}>
      <div className="activity-head">
        <i className="codicon codicon-pulse" />
        <span className="title">{t("Activity")}</span>
        <span className="count">{feed.length}</span>
        <span className="spacer" />
        <button className="ghost icon" title={t("Close")} onClick={onClose}>
          <i className="codicon codicon-close" />
        </button>
      </div>
      <div
        className="activity-list"
        ref={list}
        onScroll={(event) => {
          const element = event.currentTarget;
          pinned.current = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
        }}
      >
        {feed.length === 0 && <div className="board-empty">{t("When the agents start, every step they take shows here.")}</div>}
        {feed.map((step, index) => (
          <div
            key={`${index}-${step.at}`}
            className={`activity-row k-${step.kind} s-${step.state || "none"}`}
            onClick={() => onOpen(step.task, step.subtask)}
          >
            <span className="when">{clock(step.at)}</span>
            <AgentChip agent={step.agent} />
            <span className="where">{step.subtask || step.task}</span>
            {step.kind === "tool" ? (
              <span className="what">
                <i className={`codicon codicon-${toolIcon(step.tool)}`} />
                <span className="verb">{t(toolVerb(step.tool))}</span>
                {step.target && <code>{step.target}</code>}
                {step.state === "started" && <i className="codicon codicon-loading state" />}
                {step.state === "done" && <i className="codicon codicon-check state ok" />}
                {step.state === "failed" && <i className="codicon codicon-error state bad" />}
              </span>
            ) : step.kind === "say" ? (
              <span className="what said">{step.text.replace(/^[^:]+:\s*/, "")}</span>
            ) : (
              <span className="what phase">{t(phaseLabel(step.text))}</span>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function phaseLabel(text: string): string {
  const phase = text.replace(/^[^:]+:\s*/, "");
  return phase ? phase[0].toUpperCase() + phase.slice(1) : phase;
}
