import * as React from "react";
import { createRoot } from "react-dom/client";

import type {
  BoardHostToWebview,
  BoardLane,
  BoardOperation,
  BoardSnapshot,
  BoardTask,
  BoardWebviewToHost,
} from "../../src/board-schema";
import { setLanguage, t, tf } from "../i18n";
import { Detail } from "./detail";
import { flyCards, measureCards, Rects } from "./flight";
import {
  agentsAt,
  awaitingReview,
  checksOf,
  DEFAULT_TITLES,
  dropIndex,
  emptyBoard,
  isLocked,
  laneOfRole,
  movesBetween,
  playable,
  progressOf,
  STATUS_LABEL,
  tasksIn,
} from "./model";
import { BOARD_STYLE } from "./styles";

declare function acquireVsCodeApi(): {
  postMessage: (message: BoardWebviewToHost) => void;
  getState: () => unknown;
  setState: (state: unknown) => void;
};

const vscode = acquireVsCodeApi();
const send = (message: BoardWebviewToHost) => vscode.postMessage(message);

interface Notice {
  id: number;
  level: "info" | "warn" | "error";
  text: string;
}

interface Saved {
  selected?: string;
}

const laneTitle = (lane: BoardLane) =>
  lane.role && DEFAULT_TITLES.has(lane.title) ? t(lane.title, "lane") : lane.title;

function App(): JSX.Element {
  const saved = (vscode.getState() as Saved | undefined) ?? {};
  const [board, setBoard] = React.useState<BoardSnapshot>(emptyBoard);
  const [loaded, setLoaded] = React.useState(false);
  const [selected, setSelected] = React.useState<string | undefined>(saved.selected);
  const [notices, setNotices] = React.useState<Notice[]>([]);
  const [review, setReview] = React.useState<{ message: string } | null>(null);
  const [activity, setActivity] = React.useState<Record<string, string>>({});
  const [dragging, setDragging] = React.useState<string | null>(null);
  const [dropLane, setDropLane] = React.useState<string | null>(null);
  const [addingLane, setAddingLane] = React.useState(false);
  const boardRef = React.useRef(board);
  boardRef.current = board;
  // Where every card was before the board changed, and which moved: read by
  // the layout effect once the new board is on screen.
  const flight = React.useRef<{ rects: Rects; moved: string[]; landing: Set<string> } | null>(null);

  React.useEffect(() => vscode.setState({ selected } satisfies Saved), [selected]);

  const notify = React.useCallback((level: Notice["level"], text: string) => {
    const id = Date.now() + Math.random();
    setNotices((current) => [...current.slice(-3), { id, level, text }]);
    setTimeout(() => setNotices((current) => current.filter((item) => item.id !== id)), 6000);
  }, []);

  React.useEffect(() => {
    const onMessage = (event: MessageEvent<BoardHostToWebview>) => {
      const message = event.data;
      switch (message.type) {
        case "boardSnapshot": {
          if (message.language) setLanguage(message.language);
          const previous = boardRef.current;
          const moves = message.moved.length ? message.moved : movesBetween(previous, message.board);
          if (moves.length) {
            const done = laneOfRole(message.board, "done")?.id;
            flight.current = {
              rects: measureCards(),
              moved: moves.map((move) => move.task),
              landing: new Set(moves.filter((move) => move.to === done).map((move) => move.task)),
            };
          }
          setBoard(message.board);
          setLoaded(true);
          if (message.board.running) setReview(null);
          break;
        }
        case "boardActivity":
          setActivity((current) => ({ ...current, [message.task]: message.text }));
          break;
        case "boardReview":
          setReview({ message: message.message });
          break;
        case "boardNotice":
          notify(message.level, message.text);
          break;
        case "boardCreated":
          break;
        case "boardRunDone":
          if (message.failed.length) {
            notify("warn", tf("Not done: {0}. Open the card to see why.", message.failed.join(", ")));
          } else if (message.status === "completed" && message.done.length) {
            notify("info", tf("Done: {0}.", message.done.join(", ")));
          }
          setActivity({});
          break;
      }
    };
    window.addEventListener("message", onMessage);
    send({ type: "boardReady" });
    return () => window.removeEventListener("message", onMessage);
  }, [notify]);

  React.useLayoutEffect(() => {
    const pending = flight.current;
    if (!pending) return;
    flight.current = null;
    flyCards(pending.rects, pending.moved, pending.landing);
  }, [board]);

  const edit = React.useCallback((operations: BoardOperation[]) => send({ type: "boardEdit", operations }), []);
  const selectedTask = board.tasks.find((task) => task.id === selected);
  const { run, drafts } = playable(board);
  const waiting = awaitingReview(board);
  const agents = agentsAt(board);

  const play = (tasks: string[] = []) => send({ type: "boardPlay", tasks });
  const plan = (tasks: string[] = []) => send({ type: "boardPlan", tasks });

  const state = board.running
    ? tf("Running · {0} agent(s)", agents)
    : board.planning
      ? tf("Planning · {0} agent(s)", agents)
      : waiting.length
        ? t("Waiting for your confirmation")
        : "";

  // -- drag and drop -----------------------------------------------------

  const onDrop = (lane: BoardLane, event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const id = event.dataTransfer.getData("text/x-board-card") || dragging;
    setDragging(null);
    setDropLane(null);
    if (!id) return;
    const cards = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("[data-card]")).filter(
      (element) => element.dataset.card !== id
    );
    const middles = cards.map((element) => {
      const rect = element.getBoundingClientRect();
      return rect.top + rect.height / 2;
    });
    const index = dropIndex(middles, event.clientY);
    const task = board.tasks.find((item) => item.id === id);
    if (!task) return;
    // Counted without the card itself, as the backend places it.
    const place = tasksIn(board, lane.id).findIndex((item) => item.id === id);
    if (task.lane === lane.id && place === index) return;
    edit([{ op: "move_task", task: id, lane: lane.id, index }]);
  };

  return (
    <div className="board-app" data-testid="board">
      <style>{BOARD_STYLE}</style>
      <div className="board-toolbar">
        <span className="brand">
          <span className={`dot ${board.running || board.planning ? "live" : ""}`} />
          {t("Task board")}
        </span>
        {state && (
          <span className="state" data-testid="board-state">
            {state}
          </span>
        )}
        <span className="spacer" />
        <button onClick={() => setAddingLane(true)} disabled={addingLane}>
          <i className="codicon codicon-add" />
          {t("Lane")}
        </button>
        <button
          onClick={() => plan()}
          disabled={drafts.length === 0 || board.running}
          title={t("Break every draft in To Do into sub-tasks, one planner agent per task")}
        >
          <i className="codicon codicon-sparkle" />
          {t("Plan", "board")}
        </button>
        {board.running ? (
          <button className="danger" data-testid="board-stop" onClick={() => send({ type: "boardStop" })}>
            <i className="codicon codicon-debug-stop" />
            {t("Stop")}
          </button>
        ) : (
          <button
            className="primary"
            data-testid="board-play"
            disabled={run.length === 0 && drafts.length === 0}
            title={
              run.length
                ? tf("Implement {0} task(s), each sub-task by its own agent", run.length)
                : t("Plan the drafts first: nothing runs before you have seen its sub-tasks")
            }
            onClick={() => play()}
          >
            <i className="codicon codicon-play" />
            {t("Play")}
          </button>
        )}
      </div>

      {!board.running && waiting.length > 0 && (
        <div className="board-review" data-testid="board-review">
          <i className="codicon codicon-checklist" />
          <span className="text">
            <b>{tf("{0} task(s) waiting for your confirmation.", waiting.length)}</b>{" "}
            {t("Check the tasks and sub-tasks, change or add what you need, then press Play.")}
            {review?.message && <span className="agent-note">{review.message}</span>}
          </span>
          <button className="primary" onClick={() => play()}>
            <i className="codicon codicon-play" />
            {t("Play")}
          </button>
        </div>
      )}

      <div className="board-body">
        <div className="board-lanes">
          {board.lanes.map((lane) => (
            <Lane
              key={lane.id}
              lane={lane}
              board={board}
              selected={selected}
              activity={activity}
              dragging={dragging}
              drop={dropLane === lane.id}
              edit={edit}
              onSelect={setSelected}
              onDragStart={setDragging}
              onDragEnd={() => {
                setDragging(null);
                setDropLane(null);
              }}
              onDragOver={(event) => {
                if (!dragging) return;
                event.preventDefault();
                if (dropLane !== lane.id) setDropLane(lane.id);
              }}
              onDrop={(event) => onDrop(lane, event)}
            />
          ))}
          {addingLane && (
            <NewLane
              onCancel={() => setAddingLane(false)}
              onAdd={(title) => {
                edit([{ op: "add_lane", title }]);
                setAddingLane(false);
              }}
            />
          )}
        </div>
        {selectedTask && (
          <Detail
            task={selectedTask}
            board={board}
            edit={edit}
            onClose={() => setSelected(undefined)}
            onPlan={(id) => plan([id])}
            onRun={(id) => play([id])}
          />
        )}
      </div>

      {loaded && board.tasks.length === 0 && (
        <div className="board-empty" style={{ position: "absolute", top: "40%", left: 0, right: 0, pointerEvents: "none" }}>
          {t("The board is empty. Add a task in To Do, or ask the agent in the chat to plan the work here.")}
        </div>
      )}

      <div className="board-notices">
        {notices.map((notice) => (
          <div key={notice.id} className={`board-notice ${notice.level}`}>
            {notice.text}
          </div>
        ))}
      </div>
    </div>
  );
}

function Lane({
  lane,
  board,
  selected,
  activity,
  dragging,
  drop,
  edit,
  onSelect,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
}: {
  lane: BoardLane;
  board: BoardSnapshot;
  selected?: string;
  activity: Record<string, string>;
  dragging: string | null;
  drop: boolean;
  edit: (operations: BoardOperation[]) => void;
  onSelect: (id: string) => void;
  onDragStart: (id: string) => void;
  onDragEnd: () => void;
  onDragOver: (event: React.DragEvent<HTMLDivElement>) => void;
  onDrop: (event: React.DragEvent<HTMLDivElement>) => void;
}): JSX.Element {
  const cards = tasksIn(board, lane.id);
  const [renaming, setRenaming] = React.useState(false);
  const [menu, setMenu] = React.useState(false);
  const [confirm, setConfirm] = React.useState<"" | "remove" | "clear">("");
  const [adding, setAdding] = React.useState(false);
  const [title, setTitle] = React.useState("");
  const canAdd = lane.role !== "done" && lane.role !== "in_progress";

  React.useEffect(() => {
    if (!menu) setConfirm("");
  }, [menu]);

  const addTask = () => {
    if (!title.trim()) return;
    edit([{ op: "add_task", title: title.trim(), lane: lane.id }]);
    setTitle("");
  };

  return (
    <div
      className={`lane ${drop ? "drop" : ""}`}
      data-lane={lane.id}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <div className="lane-head" style={{ position: "relative" }}>
        {renaming ? (
          <input
            autoFocus
            aria-label={t("Lane name")}
            defaultValue={laneTitle(lane)}
            onBlur={(event) => {
              const value = event.target.value.trim();
              if (value && value !== laneTitle(lane)) edit([{ op: "rename_lane", lane: lane.id, title: value }]);
              setRenaming(false);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") (event.target as HTMLInputElement).blur();
              if (event.key === "Escape") setRenaming(false);
            }}
          />
        ) : (
          <span className="lane-title" title={t("Double-click to rename")} onDoubleClick={() => setRenaming(true)}>
            {laneTitle(lane)}
          </span>
        )}
        <span className="lane-count">{cards.length}</span>
        <span className="spacer" />
        <button className="ghost icon" title={t("Lane actions")} aria-label={t("Lane actions")} onClick={() => setMenu((open) => !open)}>
          <i className="codicon codicon-ellipsis" />
        </button>
        {menu && (
          <div className="lane-menu" style={{ right: 6, top: 30 }} onMouseLeave={() => setMenu(false)}>
            <button
              onClick={() => {
                setMenu(false);
                setRenaming(true);
              }}
            >
              <i className="codicon codicon-edit" />
              {t("Rename")}
            </button>
            {!lane.role && (
              <button
                className={confirm === "remove" ? "confirm" : ""}
                onClick={() => {
                  if (confirm !== "remove") return setConfirm("remove");
                  edit([{ op: "remove_lane", lane: lane.id }]);
                  setMenu(false);
                }}
              >
                <i className="codicon codicon-trash" />
                {confirm === "remove" ? t("Click again to remove") : t("Remove lane")}
              </button>
            )}
            <button
              className={confirm === "clear" ? "confirm" : ""}
              disabled={cards.length === 0 || cards.some(isLocked)}
              onClick={() => {
                if (confirm !== "clear") return setConfirm("clear");
                edit([{ op: "clear_lane", lane: lane.id }]);
                setMenu(false);
              }}
            >
              <i className="codicon codicon-clear-all" />
              {confirm === "clear" ? t("Click again to delete its tasks") : t("Delete every task here")}
            </button>
          </div>
        )}
      </div>
      <div className="lane-cards">
        {cards.map((task) => (
          <Card
            key={task.id}
            task={task}
            selected={selected === task.id}
            dragging={dragging === task.id}
            activity={activity[task.id] || task.activity}
            onSelect={() => onSelect(task.id)}
            onDragStart={(event) => {
              event.dataTransfer.setData("text/x-board-card", task.id);
              event.dataTransfer.effectAllowed = "move";
              onDragStart(task.id);
            }}
            onDragEnd={onDragEnd}
          />
        ))}
      </div>
      {canAdd &&
        (adding ? (
          <div className="lane-add-form">
            <input
              autoFocus
              aria-label={t("Task title")}
              placeholder={t("What needs to be done?")}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") addTask();
                if (event.key === "Escape") setAdding(false);
              }}
            />
            <div className="row">
              <button className="primary" disabled={!title.trim()} onClick={addTask}>
                {t("Add")}
              </button>
              <button className="ghost" onClick={() => setAdding(false)}>
                {t("Cancel")}
              </button>
            </div>
          </div>
        ) : (
          <button className="ghost lane-add" data-testid={`add-task-${lane.id}`} onClick={() => setAdding(true)}>
            <i className="codicon codicon-add" />
            {t("Add task")}
          </button>
        ))}
    </div>
  );
}

function Card({
  task,
  selected,
  dragging,
  activity,
  onSelect,
  onDragStart,
  onDragEnd,
}: {
  task: BoardTask;
  selected: boolean;
  dragging: boolean;
  activity: string;
  onSelect: () => void;
  onDragStart: (event: React.DragEvent<HTMLDivElement>) => void;
  onDragEnd: () => void;
}): JSX.Element {
  const progress = progressOf(task);
  const counts = checksOf(task);
  const locked = isLocked(task);
  const live = task.status === "running" || task.status === "verifying" || task.status === "planning";
  const share = progress.total ? Math.round((progress.done / progress.total) * 100) : task.status === "done" ? 100 : 0;
  return (
    <div
      className={`card s-${task.status} ${selected ? "selected" : ""} ${dragging ? "dragging" : ""}`}
      data-card={task.id}
      draggable={!locked}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={onSelect}
    >
      <i className="codicon codicon-pass-filled card-check" />
      <div className="card-top">
        <span className="card-key">{task.id}</span>
        {task.status !== "done" && <span className={`pill ${task.status}`}>{t(STATUS_LABEL[task.status], "status")}</span>}
      </div>
      <div className="card-title">{task.title}</div>
      {(progress.total > 0 || counts.checks > 0 || counts.criteria > 0) && (
        <div className="card-meta">
          {progress.total > 0 && (
            <span className="item" title={t("Sub-tasks done")}>
              <i className="codicon codicon-list-tree" />
              {progress.done}/{progress.total}
              {progress.failed > 0 && <span className="bad"> · {progress.failed}!</span>}
            </span>
          )}
          {counts.checks > 0 && (
            <span className={`item ${counts.checked === counts.checks ? "ok" : ""}`} title={t("Checklist")}>
              <i className="codicon codicon-checklist" />
              {counts.checked}/{counts.checks}
            </span>
          )}
          {counts.criteria > 0 && (
            <span className={`item ${counts.passed === counts.criteria ? "ok" : ""}`} title={t("Validation criteria")}>
              <i className="codicon codicon-verified" />
              {counts.passed}/{counts.criteria}
            </span>
          )}
          {progress.running > 0 && (
            <span className="item" title={t("Agents working")}>
              <i className="codicon codicon-loading" />
              {progress.running}
            </span>
          )}
        </div>
      )}
      {progress.total > 0 && (
        <div className="card-bar">
          <span style={{ width: `${share}%` }} />
        </div>
      )}
      {task.depends_on.length > 0 && task.status !== "done" && (
        <div className="card-after">{tf("after {0}", task.depends_on.join(", "))}</div>
      )}
      {live && activity && <div className="card-activity">{activity}</div>}
    </div>
  );
}

function NewLane({ onAdd, onCancel }: { onAdd: (title: string) => void; onCancel: () => void }): JSX.Element {
  const [title, setTitle] = React.useState("");
  return (
    <div className="lane new">
      <input
        autoFocus
        aria-label={t("Lane name")}
        placeholder={t("Lane name, e.g. Review")}
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && title.trim()) onAdd(title.trim());
          if (event.key === "Escape") onCancel();
        }}
      />
      <div className="row">
        <button className="primary" disabled={!title.trim()} onClick={() => onAdd(title.trim())}>
          {t("Add lane")}
        </button>
        <button className="ghost" onClick={onCancel}>
          {t("Cancel")}
        </button>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
