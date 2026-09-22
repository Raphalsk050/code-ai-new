import * as React from "react";

import { t, tf } from "./i18n";

import type { HistoryEntry } from "./history";
import {
  IconAgents,
  IconChevron,
  IconGauge,
  IconHistory,
  IconList,
  IconPanelRight,
  IconSettings,
  IconTerminal,
} from "./icons";
import { ripple, useCollapseHeight, useMountTransition } from "./motion";
import {
  RAIL_DEFAULT_WIDTH,
  clampRailWidth,
  clampSectionHeight,
  readStoredRailWidth,
  readStoredSectionHeights,
  storeRailWidth,
  storeSectionHeights,
  widthFromDrag,
} from "./rail-layout";
import { AgentsPanel } from "./panels/agents-panel";
import { HistoryPanel } from "./panels/history-panel";
import { PlanPanel } from "./panels/plan-panel";
import { SessionPanel } from "./panels/session-panel";
import { TerminalAction, TerminalPanel } from "./panels/terminal-panel";
import type { AppState } from "./store";
import type { WidthClass } from "./use-container-width";

export type SectionId = "session" | "plan" | "agents" | "terminal" | "history";

export interface RailActions {
  onIndex: (full: boolean) => void;
  onCompact: () => void;
  onReplan: () => void;
  onPlannerMode: (mode: string) => void;
  onGoal: (action: "start" | "stop") => void;
  onTerminal: (action: TerminalAction) => void;
  onOpenConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  onNewConversation: () => void;
  onSeeAllHistory: () => void;
  onSettings: () => void;
}

interface RailProps {
  width: WidthClass;
  /** A section to reveal, bumped by the app (Esc opens the terminal). */
  reveal?: { section: SectionId; nonce: number } | null;
  focusNonce?: number;
  state: AppState;
  model: string;
  workspace: string;
  history: HistoryEntry[];
  activeId: string | null;
  connected: boolean;
  actions: RailActions;
}

const ICONS: Record<SectionId, (props: { size?: number }) => JSX.Element> = {
  session: IconGauge,
  plan: IconList,
  agents: IconAgents,
  terminal: IconTerminal,
  history: IconHistory,
};

const TITLES: Record<SectionId, string> = {
  session: "Session",
  plan: "Plano",
  agents: "Agentes",
  terminal: "Terminal",
  history: "History",
};

const ORDER: SectionId[] = ["session", "plan", "agents", "terminal", "history"];

/**
 * Everything that is not the conversation, in one place on the right.
 *
 * Three layouts, one component: docked column when the sidebar is wide, overlay
 * drawer when it is not, icon strip in both. Which one is live comes from the
 * measured width band, never from a media query.
 */
export function TaskRail(props: RailProps): JSX.Element {
  const { width, state } = props;
  const wide = width === "md";
  const [docked, setDocked] = React.useState(true);
  // Both splitters. The width is one number; the heights are per section and
  // absent until the user drags one, so an untouched rail keeps sizing itself.
  const [railWidth, setRailWidth] = React.useState(() => readStoredRailWidth() ?? RAIL_DEFAULT_WIDTH);
  const [heights, setHeights] = React.useState<Record<string, number>>(() => readStoredSectionHeights());
  const dragFromWidth = React.useRef(railWidth);
  const [open, setOpen] = React.useState<Set<SectionId>>(() => new Set(["session", "plan"]));
  const [drawer, setDrawer] = React.useState<SectionId | null>(null);

  // Collapsing the drawer when the rail becomes wide (and vice versa) keeps one
  // mental model: a section is either in the column or in the sheet, never both.
  React.useEffect(() => {
    if (wide) setDrawer(null);
  }, [wide]);

  // Something outside the rail asked for a section (Esc reaching for the
  // terminal): open it where this width keeps sections.
  const revealNonce = props.reveal?.nonce ?? 0;
  React.useEffect(() => {
    const section = props.reveal?.section;
    if (!section || revealNonce === 0) return;
    if (wide) {
      setDocked(true);
      setOpen((prev) => (prev.has(section) ? prev : new Set(prev).add(section)));
    } else {
      setDrawer(section);
    }
    // Only the nonce means "asked again"; the section alone would re-open a
    // drawer the user just closed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealNonce]);

  const notes = sectionNotes(state);
  const showDock = wide && docked;

  const toggleSection = (id: SectionId) => {
    if (showDock) {
      setOpen((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
      return;
    }
    setDrawer((prev) => (prev === id ? null : id));
    if (wide) setDocked(true);
  };

  const body = (id: SectionId) => <SectionBody id={id} {...props} />;

  return (
    <>
      {!showDock && (
        <div className="rail-strip">
          <button
            className="rail-btn"
            title={wide ? t("Dock the panel") : t("Task panel")}
            aria-label={t("Task panel")}
            onPointerDown={ripple}
            onClick={() => (wide ? setDocked(true) : setDrawer(drawer ? null : "plan"))}
          >
            <IconPanelRight size={14} />
          </button>
          <div className="rail-sep" />
          {ORDER.map((id) => {
            const Icon = ICONS[id];
            const note = notes[id];
            return (
              <button
                key={id}
                className={`rail-btn ${drawer === id ? "active" : ""}`}
                title={t(TITLES[id])}
                aria-label={t(TITLES[id])}
                onPointerDown={ripple}
                onClick={() => toggleSection(id)}
              >
                <Icon size={14} />
                {note.badge ? <span className="rail-badge">{note.badge}</span> : null}
                {note.dot ? <span className="rail-dot" /> : null}
              </button>
            );
          })}
          <div style={{ flex: 1 }} />
          <button
            className="rail-btn"
            title={t("Settings")}
            aria-label={t("Settings")}
            onPointerDown={ripple}
            onClick={props.actions.onSettings}
          >
            <IconSettings size={14} />
          </button>
        </div>
      )}

      {showDock ? (
        <>
          <RailGrip
            axis="x"
            label={t("Resize the task panel")}
            onStart={() => {
              dragFromWidth.current = railWidth;
            }}
            onDrag={(dx, el) =>
              setRailWidth(
                widthFromDrag(
                  dragFromWidth.current,
                  dx,
                  // The row this grip sits in, which is all the space there is
                  // to share with the conversation.
                  el.parentElement?.getBoundingClientRect().width ?? 0
                )
              )
            }
            onEnd={() => storeRailWidth(railWidth)}
            onReset={() => {
              setRailWidth(RAIL_DEFAULT_WIDTH);
              storeRailWidth(RAIL_DEFAULT_WIDTH);
            }}
          />
        <div className="rail-dock" style={{ width: clampRailWidth(railWidth, Infinity) }}>
          <div className="rail-dock-head">
            <span className="rail-section-title rail-section-head" style={{ padding: 0 }}>
              Tarefas
            </span>
            <button
              className="rail-btn"
              title={t("Collapse the panel")}
              aria-label={t("Collapse the task panel")}
              onPointerDown={ripple}
              onClick={() => setDocked(false)}
            >
              <IconChevron size={13} />
            </button>
          </div>
          <div className="rail-dock-body">
            {ORDER.map((id) => (
              <RailSection
                key={id}
                id={id}
                open={open.has(id)}
                note={notes[id].text}
                height={heights[id]}
                onResize={(next) =>
                  setHeights((prev) => {
                    const copy = { ...prev };
                    if (next === null) delete copy[id];
                    else copy[id] = next;
                    return copy;
                  })
                }
                onResizeCommit={() => storeSectionHeights(heights)}
                onToggle={() => toggleSection(id)}
              >
                {body(id)}
              </RailSection>
            ))}
          </div>
          <RailFooter connected={props.connected} state={state} />
          </div>
        </>
      ) : (
        <RailDrawer section={drawer} onClose={() => setDrawer(null)} title={drawer ? t(TITLES[drawer]) : ""}>
          {drawer ? body(drawer) : null}
        </RailDrawer>
      )}
    </>
  );
}

/**
 * A splitter the user drags. Pointer capture rather than window listeners: the
 * pointer keeps reporting to this element once the drag starts, so a fast drag
 * that leaves the 5px strip - or leaves the webview entirely - still tracks.
 *
 * Keyboard-operable because it is the only way to resize without a mouse, and
 * `role="separator"` with a tabindex is what a screen reader expects to find.
 */
function RailGrip({
  axis,
  label,
  onStart,
  onDrag,
  onEnd,
  onReset,
}: {
  axis: "x" | "y";
  label: string;
  onStart: () => void;
  onDrag: (delta: number, el: HTMLElement) => void;
  onEnd: () => void;
  onReset: () => void;
}): JSX.Element {
  const origin = React.useRef<{ x: number; y: number } | null>(null);
  const step = (delta: number, el: HTMLElement) => {
    onStart();
    onDrag(delta, el);
    onEnd();
  };
  return (
    <div
      className={`rail-grip rail-grip-${axis}`}
      role="separator"
      aria-orientation={axis === "x" ? "vertical" : "horizontal"}
      aria-label={label}
      title={`${label} (duplo clique para restaurar)`}
      tabIndex={0}
      onPointerDown={(e) => {
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        origin.current = { x: e.clientX, y: e.clientY };
        onStart();
      }}
      onPointerMove={(e) => {
        const from = origin.current;
        if (!from) return;
        onDrag(axis === "x" ? e.clientX - from.x : e.clientY - from.y, e.currentTarget);
      }}
      onPointerUp={(e) => {
        if (!origin.current) return;
        origin.current = null;
        e.currentTarget.releasePointerCapture(e.pointerId);
        onEnd();
      }}
      onDoubleClick={onReset}
      onKeyDown={(e) => {
        const el = e.currentTarget;
        const big = e.shiftKey ? 48 : 16;
        if (axis === "x" && e.key === "ArrowLeft") step(-big, el);
        else if (axis === "x" && e.key === "ArrowRight") step(big, el);
        else if (axis === "y" && e.key === "ArrowUp") step(-big, el);
        else if (axis === "y" && e.key === "ArrowDown") step(big, el);
        else if (e.key === "Home" || e.key === "Escape") onReset();
        else return;
        e.preventDefault();
      }}
    />
  );
}

function RailSection({
  id,
  open,
  note,
  onToggle,
  height,
  onResize,
  onResizeCommit,
  children,
}: {
  id: SectionId;
  open: boolean;
  note: string;
  onToggle: () => void;
  /** Dragged height in px, or undefined to size to the content. */
  height?: number;
  /** null restores the content height. */
  onResize: (next: number | null) => void;
  /** The drag is over: persist whatever it settled on. */
  onResizeCommit: () => void;
  children: React.ReactNode;
}): JSX.Element {
  const ref = useCollapseHeight(open);
  const bodyRef = React.useRef<HTMLDivElement>(null);
  // The height the current drag started from: the content height when the
  // section has never been dragged, so the first pull does not jump.
  const dragFrom = React.useRef(0);
  return (
    <div className={`rail-section ${open ? "open" : ""}`}>
      <button
        className="rail-section-head"
        aria-expanded={open}
        aria-controls={`rail-body-${id}`}
        onPointerDown={ripple}
        onClick={onToggle}
      >
        <IconChevron size={12} className="chev" />
        <span className="rail-section-title">{t(TITLES[id])}</span>
        {note && <span className="rail-section-note">{note}</span>}
      </button>
      <div ref={ref} id={`rail-body-${id}`}>
        <div
          ref={bodyRef}
          className={`rail-section-body ${height ? "sized" : ""}`}
          style={height ? { height, overflowY: "auto" } : undefined}
        >
          {children}
        </div>
      </div>
      {/* Outside the collapse wrapper on purpose: in it the grip was clipped
          while the section animated, and its overhang sat *under* the next
          section's header button, which swallowed the drag. */}
      {open && (
        <RailGrip
          axis="y"
          label={tf("Resize {0}", t(TITLES[id]))}
          onStart={() => {
            dragFrom.current = height ?? bodyRef.current?.getBoundingClientRect().height ?? 0;
          }}
          onDrag={(dy) => onResize(clampSectionHeight(dragFrom.current + dy))}
          onEnd={onResizeCommit}
          onReset={() => {
            onResize(null);
            onResizeCommit();
          }}
        />
      )}
    </div>
  );
}

function RailDrawer({
  section,
  title,
  onClose,
  children,
}: {
  section: SectionId | null;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}): JSX.Element | null {
  const { mounted, phase } = useMountTransition(section !== null);
  if (!mounted) return null;
  return (
    <>
      <div className={`rail-scrim ${phase}`} onClick={onClose} />
      <div className={`rail-drawer ${phase}`} role="dialog" aria-label={title}>
        <div className="rail-dock-head">
          <span className="rail-section-title rail-section-head" style={{ padding: 0 }}>
            {title}
          </span>
          <button className="rail-btn" aria-label={t("Close")} onPointerDown={ripple} onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="rail-dock-body">
          <div className="rail-section-body">{children}</div>
        </div>
      </div>
    </>
  );
}

function RailFooter({ connected, state }: { connected: boolean; state: AppState }): JSX.Element {
  return (
    <div className="rail-foot">
      <span className={`dot ${connected ? "" : "off"}`} />
      <span>{connected ? "bridge conectada" : "bridge offline"}</span>
      <span style={{ flex: 1 }} />
      <span>{state.session.plannerMode}</span>
    </div>
  );
}

function SectionBody(props: RailProps & { id: SectionId }): JSX.Element {
  const { id, state, actions } = props;
  switch (id) {
    case "session":
      return (
        <SessionPanel
          session={state.session}
          usage={state.usage}
          model={props.model}
          workspace={props.workspace}
          onIndex={actions.onIndex}
          onCompact={actions.onCompact}
        />
      );
    case "plan":
      return (
        <PlanPanel
          plan={state.plan}
          goal={state.goal}
          plannerMode={state.session.plannerMode}
          onReplan={actions.onReplan}
          onPlannerMode={actions.onPlannerMode}
          onGoal={actions.onGoal}
        />
      );
    case "agents":
      return <AgentsPanel agents={state.agents} />;
    case "terminal":
      return (
        <TerminalPanel
          terminal={state.terminal}
          onAction={actions.onTerminal}
          focusNonce={props.focusNonce ?? 0}
        />
      );
    case "history":
      return (
        <HistoryPanel
          entries={props.history}
          activeId={props.activeId}
          onOpen={actions.onOpenConversation}
          onDelete={actions.onDeleteConversation}
          onNew={actions.onNewConversation}
          onSeeAll={actions.onSeeAllHistory}
        />
      );
  }
}

function sectionNotes(state: AppState): Record<SectionId, { text: string; badge?: number; dot?: boolean }> {
  const running = state.agents.rows.filter((r) => r.status === "running").length;
  const doneSteps = state.plan.steps.filter((s) => s.status === "done").length;
  return {
    session: { text: state.session.phase },
    plan: {
      text: state.plan.steps.length ? `${doneSteps}/${state.plan.steps.length}` : "",
      badge: state.plan.steps.length ? undefined : undefined,
    },
    agents: { text: running ? `${running} rodando` : "", badge: running || undefined },
    terminal: {
      text: state.terminal.live ? (state.terminal.closed ? "encerrada" : "ativa") : "",
      dot: state.terminal.live && !state.terminal.closed,
    },
    history: { text: "" },
  };
}
