import * as React from "react";

import type {
  BoardCheck,
  BoardCriterion,
  BoardOperation,
  BoardSnapshot,
  BoardSubTask,
  BoardTask,
} from "../../src/board-schema";
import { t, tf } from "../i18n";
import { canRun, checksOf, DEFAULT_TITLES, dependencyChoices, isLocked, STATUS_LABEL, SUB_ICON } from "./model";

type Edit = (operations: BoardOperation[]) => void;

/** A field that sends its value when it loses focus or on Enter, not per keystroke. */
function Field({
  value,
  onCommit,
  multiline = false,
  disabled = false,
  placeholder,
  className,
  label,
}: {
  value: string;
  onCommit: (value: string) => void;
  multiline?: boolean;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  label: string;
}): JSX.Element {
  const [draft, setDraft] = React.useState(value);
  React.useEffect(() => setDraft(value), [value]);
  const commit = () => {
    if (draft.trim() !== value.trim()) onCommit(draft);
  };
  if (multiline) {
    return (
      <textarea
        className={className}
        aria-label={label}
        value={draft}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
      />
    );
  }
  return (
    <input
      className={className}
      aria-label={label}
      value={draft}
      disabled={disabled}
      placeholder={placeholder}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") (event.target as HTMLInputElement).blur();
        if (event.key === "Escape") {
          setDraft(value);
          (event.target as HTMLInputElement).blur();
        }
      }}
    />
  );
}

/** One line to type a new entry into; Enter adds it and clears the line. */
function AddRow({
  placeholder,
  onAdd,
  disabled,
}: {
  placeholder: string;
  onAdd: (text: string) => void;
  disabled: boolean;
}): JSX.Element {
  const [text, setText] = React.useState("");
  const add = () => {
    if (!text.trim()) return;
    onAdd(text.trim());
    setText("");
  };
  return (
    <div className="add-row">
      <input
        aria-label={placeholder}
        placeholder={placeholder}
        value={text}
        disabled={disabled}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") add();
        }}
      />
      <button className="icon" title={t("Add")} disabled={disabled || !text.trim()} onClick={add}>
        <i className="codicon codicon-add" />
      </button>
    </div>
  );
}

function Checklist({
  items,
  locked,
  edit,
  task,
  subtask,
}: {
  items: BoardCheck[];
  locked: boolean;
  edit: Edit;
  task: string;
  subtask?: string;
}): JSX.Element {
  const where = { task, ...(subtask ? { subtask } : {}), list: "checklist" };
  return (
    <>
      {items.map((item) => (
        <div className="item-row" key={item.id}>
          <input
            type="checkbox"
            aria-label={item.text}
            checked={item.done}
            disabled={locked}
            onChange={(event) => edit([{ op: "update_item", ...where, item: item.id, done: event.target.checked }])}
          />
          <span className={`text ${item.done ? "done" : ""}`}>{item.text}</span>
          <button
            className="ghost icon remove"
            title={t("Remove")}
            disabled={locked}
            onClick={() => edit([{ op: "remove_item", ...where, item: item.id }])}
          >
            <i className="codicon codicon-close" />
          </button>
        </div>
      ))}
      <AddRow
        placeholder={t("Add a checklist item")}
        disabled={locked}
        onAdd={(text) => edit([{ op: "add_item", ...where, text }])}
      />
    </>
  );
}

const CRITERION_ICON: Record<BoardCriterion["status"], string> = {
  pending: "circle-large-outline",
  passed: "pass-filled",
  failed: "error",
};

function Criteria({
  items,
  locked,
  edit,
  task,
  subtask,
}: {
  items: BoardCriterion[];
  locked: boolean;
  edit: Edit;
  task: string;
  subtask?: string;
}): JSX.Element {
  const where = { task, ...(subtask ? { subtask } : {}), list: "criteria" };
  return (
    <>
      {items.map((item) => (
        <React.Fragment key={item.id}>
          <div className="item-row">
            <i
              className={`codicon codicon-${CRITERION_ICON[item.status]} ${item.status}`}
              title={t(item.status === "passed" ? "Passed" : item.status === "failed" ? "Failed" : "Not checked yet")}
            />
            <span className="text">{item.text}</span>
            <button
              className="ghost icon remove"
              title={t("Remove")}
              disabled={locked}
              onClick={() => edit([{ op: "remove_item", ...where, item: item.id }])}
            >
              <i className="codicon codicon-close" />
            </button>
          </div>
          {item.note && <div className="item-note">{item.note}</div>}
        </React.Fragment>
      ))}
      <AddRow
        placeholder={t("Add a validation criterion")}
        disabled={locked}
        onAdd={(text) => edit([{ op: "add_item", ...where, text }])}
      />
    </>
  );
}

function SubTaskCard({
  task,
  sub,
  open,
  onToggle,
  locked,
  edit,
}: {
  task: BoardTask;
  sub: BoardSubTask;
  open: boolean;
  onToggle: () => void;
  locked: boolean;
  edit: Edit;
}): JSX.Element {
  const choices = dependencyChoices(task, sub);
  return (
    <div className="sub" data-sub={sub.id}>
      <div className="sub-head">
        <input
          type="checkbox"
          title={t("Done")}
          aria-label={tf("{0} done", sub.id)}
          checked={sub.status === "done"}
          disabled={locked}
          onChange={(event) =>
            edit([{ op: "update_subtask", task: task.id, subtask: sub.id, done: event.target.checked }])
          }
        />
        <i className={`codicon codicon-${SUB_ICON[sub.status]} st-${sub.status}`} title={t(sub.status)} />
        <span className="id">{sub.id}</span>
        <span className="title" onClick={onToggle}>
          {sub.title}
        </span>
        {sub.agent && <span className="agent">{sub.agent}</span>}
        <button className="ghost icon" title={open ? t("Fold") : t("Open")} onClick={onToggle}>
          <i className={`codicon codicon-chevron-${open ? "up" : "down"}`} />
        </button>
      </div>
      {sub.depends_on.length > 0 && !open && (
        <div className="card-after">{tf("after {0}", sub.depends_on.join(", "))}</div>
      )}
      {open && (
        <div className="sub-body">
          <Field
            label={t("Title")}
            value={sub.title}
            disabled={locked}
            onCommit={(title) => title.trim() && edit([{ op: "update_subtask", task: task.id, subtask: sub.id, title }])}
          />
          <Field
            label={t("Description")}
            multiline
            value={sub.description}
            disabled={locked}
            placeholder={t("What this piece does and which files it owns")}
            onCommit={(description) => edit([{ op: "update_subtask", task: task.id, subtask: sub.id, description }])}
          />
          {choices.length > 0 && (
            <div>
              <h4>{t("Waits for")}</h4>
              <div className="deps">
                {choices.map((other) => (
                  <label key={other.id}>
                    <input
                      type="checkbox"
                      checked={sub.depends_on.includes(other.id)}
                      disabled={locked}
                      onChange={(event) => {
                        const next = event.target.checked
                          ? [...sub.depends_on, other.id]
                          : sub.depends_on.filter((id) => id !== other.id);
                        edit([{ op: "update_subtask", task: task.id, subtask: sub.id, depends_on: next }]);
                      }}
                    />
                    {other.id}
                  </label>
                ))}
              </div>
            </div>
          )}
          <div>
            <h4>{t("Checklist")}</h4>
            <Checklist items={sub.checklist} locked={locked} edit={edit} task={task.id} subtask={sub.id} />
          </div>
          <div>
            <h4>{t("Validation criteria")}</h4>
            <Criteria items={sub.criteria} locked={locked} edit={edit} task={task.id} subtask={sub.id} />
          </div>
          {sub.error && <div className="error">{sub.error}</div>}
          {sub.report && <div className="report">{sub.report}</div>}
          <div className="row">
            <span className="grow" />
            <button
              className="danger"
              disabled={locked}
              onClick={() => edit([{ op: "remove_subtask", task: task.id, subtask: sub.id }])}
            >
              <i className="codicon codicon-trash" />
              {t("Remove sub-task")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function Detail({
  task,
  board,
  edit,
  onClose,
  onPlan,
  onRun,
}: {
  task: BoardTask;
  board: BoardSnapshot;
  edit: Edit;
  onClose: () => void;
  onPlan: (id: string) => void;
  onRun: (id: string) => void;
}): JSX.Element {
  const locked = isLocked(task);
  const [open, setOpen] = React.useState<Set<string>>(new Set());
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  React.useEffect(() => setConfirmDelete(false), [task.id]);
  const counts = checksOf(task);
  const others = board.tasks.filter((other) => other.id !== task.id);
  const toggle = (id: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const laneTitle = (title: string) => (DEFAULT_TITLES.has(title) ? t(title, "lane") : title);

  return (
    <aside className="detail" data-testid="board-detail">
      <div className="detail-head">
        <span className="key">{task.id}</span>
        <span className={`pill ${task.status}`}>{t(STATUS_LABEL[task.status], "status")}</span>
        <span className="spacer" />
        <select
          aria-label={t("Lane")}
          value={task.lane}
          disabled={locked}
          style={{ width: "auto" }}
          onChange={(event) => edit([{ op: "move_task", task: task.id, lane: event.target.value }])}
        >
          {board.lanes.map((lane) => (
            <option key={lane.id} value={lane.id}>
              {laneTitle(lane.title)}
            </option>
          ))}
        </select>
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
        <Field
          className="detail-title"
          label={t("Title")}
          value={task.title}
          disabled={locked}
          onCommit={(title) => title.trim() && edit([{ op: "update_task", task: task.id, title }])}
        />
        <Field
          label={t("Description")}
          multiline
          value={task.description}
          disabled={locked}
          placeholder={t("What the feature is, and what done looks like")}
          onCommit={(description) => edit([{ op: "update_task", task: task.id, description }])}
        />
        <div className="detail-actions">
          <button disabled={locked || board.running} onClick={() => onPlan(task.id)}>
            <i className="codicon codicon-sparkle" />
            {task.subtasks.length ? t("Plan again") : t("Break into sub-tasks")}
          </button>
          <button className="primary" disabled={!canRun(task, board)} onClick={() => onRun(task.id)}>
            <i className="codicon codicon-play" />
            {t("Run this task")}
          </button>
          <span style={{ flex: "1 1 auto" }} />
          <button
            className={confirmDelete ? "danger" : ""}
            disabled={locked}
            onClick={() => {
              if (!confirmDelete) {
                setConfirmDelete(true);
                return;
              }
              edit([{ op: "remove_task", task: task.id }]);
              onClose();
            }}
          >
            <i className="codicon codicon-trash" />
            {confirmDelete ? t("Click again to delete") : t("Delete")}
          </button>
        </div>
        {task.error && <div className="error">{task.error}</div>}

        <section>
          <h4>
            {t("Sub-tasks")}
            <span className="count">
              {task.subtasks.filter((sub) => sub.status === "done").length}/{task.subtasks.length}
            </span>
          </h4>
          {task.subtasks.map((sub) => (
            <SubTaskCard
              key={sub.id}
              task={task}
              sub={sub}
              open={open.has(sub.id)}
              onToggle={() => toggle(sub.id)}
              locked={locked}
              edit={edit}
            />
          ))}
          <AddRow
            placeholder={t("Add a sub-task")}
            disabled={locked}
            onAdd={(title) => edit([{ op: "add_subtask", task: task.id, title }])}
          />
        </section>

        <section>
          <h4>
            {t("Checklist")}
            <span className="count">
              {counts.checked}/{counts.checks}
            </span>
          </h4>
          <Checklist items={task.checklist} locked={locked} edit={edit} task={task.id} />
        </section>

        <section>
          <h4>
            {t("Validation criteria")}
            <span className="count">
              {counts.passed}/{counts.criteria}
            </span>
          </h4>
          <Criteria items={task.criteria} locked={locked} edit={edit} task={task.id} />
        </section>

        {others.length > 0 && (
          <section>
            <h4>{t("Starts after")}</h4>
            <div className="deps">
              {others.map((other) => (
                <label key={other.id} title={other.title}>
                  <input
                    type="checkbox"
                    checked={task.depends_on.includes(other.id)}
                    disabled={locked}
                    onChange={(event) => {
                      const next = event.target.checked
                        ? [...task.depends_on, other.id]
                        : task.depends_on.filter((id) => id !== other.id);
                      edit([{ op: "update_task", task: task.id, depends_on: next }]);
                    }}
                  />
                  {other.id}
                </label>
              ))}
            </div>
          </section>
        )}

        {task.report && (
          <section>
            <h4>{t("Report")}</h4>
            <div className="report">{task.report}</div>
          </section>
        )}
      </div>
    </aside>
  );
}
