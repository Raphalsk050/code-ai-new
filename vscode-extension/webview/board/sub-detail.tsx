import * as React from "react";

import type { BoardOperation, BoardSubTask, BoardTask } from "../../src/board-schema";
import { t, tf } from "../i18n";
import { Checklist, Criteria, Field } from "./detail";
import { elapsed, LiveAgent } from "./live";
import { dependencyChoices, isLocked } from "./model";
import { AgentChip, LiveLine, useNow } from "./subcard";

const SUB_PILL: Record<BoardSubTask["status"], string> = {
  pending: "",
  queued: "queued",
  running: "running",
  done: "done",
  failed: "failed",
  stopped: "stopped",
  blocked: "failed",
};

/** One sub-task on its own: what a click on its sub-card opens. */
export function SubDetail({
  task,
  sub,
  live,
  edit,
  width,
  onClose,
  onOpenTask,
}: {
  task: BoardTask;
  sub: BoardSubTask;
  live?: LiveAgent;
  edit: (operations: BoardOperation[]) => void;
  width: number;
  onClose: () => void;
  onOpenTask: () => void;
}): JSX.Element {
  const locked = isLocked(task);
  const running = sub.status === "running";
  const now = useNow(running);
  const started = sub.started_at ?? 0;
  const ended = sub.finished_at ?? 0;
  const choices = dependencyChoices(task, sub);
  const [confirm, setConfirm] = React.useState(false);
  React.useEffect(() => {
    setConfirm(false);
  }, [sub.id]);
  const where = { task: task.id, subtask: sub.id };
  return (
    <aside className="detail" data-testid="board-sub-detail" style={{ width }}>
      <div className="detail-head">
        <button className="ghost link crumb" title={task.title} onClick={onOpenTask}>
          {task.id}
        </button>
        <i className="codicon codicon-chevron-right" />
        <span className="key">{sub.id}</span>
        <span className={`pill ${SUB_PILL[sub.status]}`}>{t(sub.status)}</span>
        <span className="spacer" />
        <button className="ghost icon" title={t("Close")} onClick={onClose}>
          <i className="codicon codicon-close" />
        </button>
      </div>
      <div className="detail-body">
        {locked && (
          <div className="locked">
            <i className="codicon codicon-loading" />
            {t("Agents are working on this task. Stop the board to change it.")}
          </div>
        )}
        <div className="parent-line">{tf("Part of {0}", task.title)}</div>
        <Field
          className="detail-title"
          label={t("Title")}
          value={sub.title}
          disabled={locked}
          onCommit={(title) => title.trim() && edit([{ op: "update_subtask", ...where, title }])}
        />
        <Field
          label={t("Description")}
          multiline
          value={sub.description}
          disabled={locked}
          placeholder={t("What this piece does and which files it owns")}
          onCommit={(description) => edit([{ op: "update_subtask", ...where, description }])}
        />
        {(sub.agent || running) && (
          <section className="sub-agent">
            <h4>{t("Agent")}</h4>
            <div className="row">
              {sub.agent && <AgentChip agent={sub.agent} live={running} />}
              {started > 0 && <span className="clock">{elapsed(started, running ? now : ended || now)}</span>}
              {live && live.steps > 0 && (
                <span className="steps">{live.steps === 1 ? t("1 step") : tf("{0} steps", live.steps)}</span>
              )}
            </div>
            {running && <LiveLine live={live} fallback={sub.activity} />}
            {running && live?.said && <div className="said">{live.said}</div>}
          </section>
        )}
        {sub.error && <div className="error">{sub.error}</div>}
        {sub.report && (
          <section>
            <h4>{t("Report")}</h4>
            <div className="report">{sub.report}</div>
          </section>
        )}
        {choices.length > 0 && (
          <section>
            <h4>{t("Waits for")}</h4>
            <div className="deps">
              {choices.map((other) => (
                <label key={other.id} title={other.title}>
                  <input
                    type="checkbox"
                    checked={sub.depends_on.includes(other.id)}
                    disabled={locked}
                    onChange={(event) => {
                      const next = event.target.checked
                        ? [...sub.depends_on, other.id]
                        : sub.depends_on.filter((id) => id !== other.id);
                      edit([{ op: "update_subtask", ...where, depends_on: next }]);
                    }}
                  />
                  {other.id}
                </label>
              ))}
            </div>
          </section>
        )}
        <section>
          <h4>
            {t("Checklist")}
            <span className="count">
              {sub.checklist.filter((item) => item.done).length}/{sub.checklist.length}
            </span>
          </h4>
          <Checklist items={sub.checklist} locked={locked} edit={edit} task={task.id} subtask={sub.id} />
        </section>
        <section>
          <h4>
            {t("Validation criteria")}
            <span className="count">
              {sub.criteria.filter((item) => item.status === "passed").length}/{sub.criteria.length}
            </span>
          </h4>
          <Criteria items={sub.criteria} locked={locked} edit={edit} task={task.id} subtask={sub.id} />
        </section>
        <div className="detail-actions">
          <button disabled={locked} onClick={() => edit([{ op: "update_subtask", ...where, done: sub.status !== "done" }])}>
            <i className={`codicon codicon-${sub.status === "done" ? "circle-large-outline" : "pass"}`} />
            {sub.status === "done" ? t("Mark as not done") : t("Mark as done")}
          </button>
          <span style={{ flex: "1 1 auto" }} />
          <button
            className={confirm ? "danger" : ""}
            disabled={locked}
            onClick={() => {
              if (!confirm) {
                setConfirm(true);
                return;
              }
              edit([{ op: "remove_subtask", ...where }]);
              onOpenTask();
            }}
          >
            <i className="codicon codicon-trash" />
            {confirm ? t("Click again to delete") : t("Remove sub-task")}
          </button>
        </div>
      </div>
    </aside>
  );
}
