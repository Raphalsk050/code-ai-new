import * as React from "react";

import type { BoardSubTask, BoardTask } from "../../src/board-schema";
import { t, tf } from "../i18n";
import { agentHue, elapsed, LiveAgent, toolIcon, toolVerb } from "./live";
import { SUB_ICON } from "./model";

/** An agent's name in its own colour, the same on every card and in the feed. */
export function AgentChip({ agent, live = false }: { agent: string; live?: boolean }): JSX.Element {
  return (
    <span className={`agent-chip ${live ? "live" : ""}`} style={{ "--agent-hue": agentHue(agent) } as React.CSSProperties}>
      <span className="agent-dot" />
      {agent}
    </span>
  );
}

/** What an agent is doing now, in one line: "Editing src/api.py", or the phase it is in. */
export function LiveLine({ live, fallback }: { live?: LiveAgent; fallback?: string }): JSX.Element | null {
  if (live?.phase) {
    return (
      <div className="live-line">
        <i className="codicon codicon-loading" />
        <span className="verb">{t(capitalize(live.phase))}</span>
      </div>
    );
  }
  if (live?.tool) {
    return (
      <div className={`live-line ${live.state === "failed" ? "bad" : ""}`}>
        <i className={`codicon codicon-${toolIcon(live.tool)}`} />
        <span className="verb">{t(toolVerb(live.tool))}</span>
        {live.target && <code className="target">{live.target}</code>}
      </div>
    );
  }
  if (fallback) {
    // What the board saved of the step: "<tool> <target>", read the same way.
    const [tool, ...rest] = fallback.split(" ");
    return <LiveLine live={{ agent: "", tool, target: rest.join(" "), state: "started", phase: "", said: "", steps: 0, at: 0 }} />;
  }
  return null;
}

function capitalize(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

/** A clock that ticks while it is on screen. */
export function useNow(active: boolean): number {
  const [now, setNow] = React.useState(() => Date.now() / 1000);
  React.useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

/** One sub-task as a card of its own, under its task: it moves across the lanes as its agent works. */
export function SubCard({
  task,
  sub,
  live,
  onOpen,
}: {
  task: BoardTask;
  sub: BoardSubTask;
  live?: LiveAgent;
  onOpen: () => void;
}): JSX.Element {
  const running = sub.status === "running";
  const now = useNow(running);
  const started = sub.started_at ?? 0;
  const ended = sub.finished_at ?? 0;
  return (
    <div className={`subcard st-${sub.status}`} data-card={sub.id} data-parent={task.id} onClick={onOpen}>
      <div className="sub-top">
        <i className={`codicon codicon-${SUB_ICON[sub.status]} st-${sub.status}`} title={t(sub.status)} />
        <span className="sub-key">{sub.id}</span>
        <span className="sub-title">{sub.title}</span>
      </div>
      {running && (
        <div className="sub-live">
          <div className="row">
            {sub.agent && <AgentChip agent={sub.agent} live />}
            <span className="grow" />
            {started > 0 && <span className="clock">{elapsed(started, now)}</span>}
            {live && live.steps > 0 && (
              <span className="steps">{live.steps === 1 ? t("1 step") : tf("{0} steps", live.steps)}</span>
            )}
          </div>
          <LiveLine live={live} fallback={sub.activity} />
          {live?.said && <div className="said">{live.said}</div>}
        </div>
      )}
      {sub.status === "done" && sub.agent && (
        <div className="sub-meta">
          <AgentChip agent={sub.agent} />
          {started > 0 && ended > 0 && <span className="clock">{elapsed(started, ended)}</span>}
        </div>
      )}
      {(sub.status === "failed" || sub.status === "blocked") && sub.error && <div className="sub-error">{sub.error}</div>}
      {sub.status === "pending" && sub.depends_on.length > 0 && (
        <div className="sub-after">{tf("after {0}", sub.depends_on.join(", "))}</div>
      )}
    </div>
  );
}

/** A task named in a lane it is not in, to group its sub-cards that are. */
export function TaskStub({ task, onOpen }: { task: BoardTask; onOpen: () => void }): JSX.Element {
  return (
    <div className="card-stub" data-stub={task.id} onClick={onOpen} title={t("Open the task")}>
      <i className="codicon codicon-symbol-class" />
      <span className="card-key">{task.id}</span>
      <span className="stub-title">{task.title}</span>
    </div>
  );
}
