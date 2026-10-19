import * as React from "react";

import type { FlowEdge } from "../../src/flow-schema";
import { chipsFor } from "./access";
import {
  Aims,
  Bounds,
  GRID,
  NodeLayout,
  PinLayout,
  Point,
  accentOf,
  aimAt,
  commentLook,
  commentOrder,
  edgeType,
  graphBounds,
  isAnnotation,
  isRound,
  layoutGraph,
  movedBy,
  nodesInRect,
  pinAnchor,
  pinPoint,
  resized,
  ResizeHandle,
  roundCenter,
  wireLength,
  wirePath,
} from "./model";
import { t } from "../i18n";
import { panelTop, presented } from "./focus";
import { useFollowRun } from "./follow-run";
import { NodeView, RoundNodeView } from "./node-view";
import { Box, CommentView, NoteBubble, NoteMark } from "./notes";
import type { Action, EditorState, NodeRunStatus } from "./store";
import { issuesByNode } from "./store";

// How long an edge stays lit after the flow walked it. The dot is a hint about
// where the run is, not a progress bar, so it fades on its own.
const EDGE_GLOW_MS = 2600;
export const MIN_ZOOM = 0.25;
export const MAX_ZOOM = 2.5;

/** The view after zooming to `next`, keeping `point` (screen space) where it is. */
export function zoomAround(
  view: { pan: [number, number]; zoom: number },
  point: Point,
  next: number
): { pan: [number, number]; zoom: number } {
  const zoom = clamp(next, MIN_ZOOM, MAX_ZOOM);
  const { pan } = view;
  return {
    zoom,
    pan: [
      point.x - ((point.x - pan[0]) / view.zoom) * zoom,
      point.y - ((point.y - pan[1]) / view.zoom) * zoom,
    ],
  };
}

interface DragState {
  kind: "node" | "pan" | "marquee" | "wire" | "resize";
  startScreen: Point;
  startWorld: Point;
  current: Point;
  /** What a node drag moves: the selection, and what its comments hold. */
  ids: string[];
  startPan: [number, number];
  pin?: { nodeId: string; pin: PinLayout; direction: "in" | "out" };
  handle?: ResizeHandle;
  startBox?: Box;
}

/** What the right-click landed on, so the menu can offer the right actions. */
export type ContextTarget =
  | { kind: "canvas"; at: Point; screen: Point }
  | { kind: "node"; id: string; at: Point; screen: Point }
  | { kind: "edge"; id: string; at: Point; screen: Point }
  | {
      kind: "pin";
      pin: { nodeId: string; pin: PinLayout; direction: "in" | "out" };
      at: Point;
      screen: Point;
    };

export interface CanvasProps {
  state: EditorState;
  dispatch(action: Action): void;
  onContext(target: ContextTarget): void;
  minimap: boolean;
}

export function Canvas({ state, dispatch, onContext, minimap }: CanvasProps): JSX.Element {
  const svgRef = React.useRef<SVGSVGElement | null>(null);
  const [drag, setDrag] = React.useState<DragState | null>(null);
  const [size, setSize] = React.useState({ width: 900, height: 600 });
  const lastSlide = React.useRef(-Infinity);
  // Repaint while edges are lit so the glow actually expires.
  const [, setTick] = React.useState(0);

  const catalog = state.catalog;
  const { pan, zoom } = state.graph.view;

  React.useEffect(() => {
    const element = svgRef.current;
    if (!element) return;
    const observer = new ResizeObserver(() => {
      const rect = element.getBoundingClientRect();
      setSize({ width: rect.width, height: rect.height });
    });
    observer.observe(element);
    const rect = element.getBoundingClientRect();
    setSize({ width: rect.width, height: rect.height });
    // React listens to the wheel passively, so its handler cannot stop a pinch
    // from zooming the whole page. This one can; the text panels keep theirs.
    const hold = (event: WheelEvent) => {
      if ((event.target as Element | null)?.closest?.(".flow-output")) return;
      event.preventDefault();
    };
    element.addEventListener("wheel", hold, { passive: false });
    return () => {
      observer.disconnect();
      element.removeEventListener("wheel", hold);
    };
  }, []);

  // Lit wires animate their dots; a running agent moves nothing on its own, and
  // its elapsed clock would freeze without a tick of its own.
  const lit = Object.keys(state.run.active).length > 0 || state.run.running;
  React.useEffect(() => {
    if (!lit) return;
    const timer = window.setInterval(() => setTick((value) => value + 1), 600);
    return () => window.clearInterval(timer);
  }, [lit]);

  // Keyed on the nodes, not the graph: a pan replaces the graph's view and
  // nothing else, and the camera following a run pans every frame.
  const graphNodes = state.graph.nodes;
  const layouts = React.useMemo(
    () => (catalog ? layoutGraph(state.graph, catalog) : new Map<string, NodeLayout>()),
    [graphNodes, catalog]
  );

  // A node opened by hand to read what it did; a new run takes the canvas back.
  const [pinned, setPinned] = React.useState<string | null>(null);
  React.useEffect(() => setPinned(null), [state.run.startedAt]);
  const follow = useFollowRun({
    run: state.run,
    enabled: catalog?.raw.follow_run !== false,
    layouts,
    size,
    view: state.graph.view,
    viewEdits: state.viewEdits,
    pinned,
    dispatch,
  });
  // The graph as drawn: the open node grown, the ones around it pushed aside.
  const shown = React.useMemo(
    () => presented(layouts, follow.focusId, follow.p),
    [layouts, follow.focusId, follow.p]
  );
  const problems = React.useMemo(() => issuesByNode(state.issues), [state.issues]);
  const chips = React.useMemo(() => {
    const byNode = new Map<string, ReturnType<typeof chipsFor>>();
    for (const node of state.graph.nodes) {
      byNode.set(node.id, chipsFor(catalog, state.graph, node));
    }
    return byNode;
  }, [state.graph, catalog]);
  const connected = React.useMemo(() => {
    const set = new Set<string>();
    for (const edge of state.graph.edges) {
      set.add(`${edge.from[0]}:${edge.from[1]}:out`);
      set.add(`${edge.to[0]}:${edge.to[1]}:in`);
    }
    return set;
  }, [state.graph.edges]);

  const toWorld = React.useCallback(
    (clientX: number, clientY: number): Point => {
      const rect = svgRef.current?.getBoundingClientRect();
      const left = rect?.left ?? 0;
      const top = rect?.top ?? 0;
      return {
        x: (clientX - left - pan[0]) / zoom,
        y: (clientY - top - pan[1]) / zoom,
      };
    },
    [pan, zoom]
  );

  // -- mouse ------------------------------------------------------------

  const onBackgroundDown = (event: React.MouseEvent) => {
    holdSelection(event);
    if (event.button === 1 || event.altKey || event.ctrlKey || event.metaKey) {
      setDrag({
        kind: "pan",
        startScreen: { x: event.clientX, y: event.clientY },
        startWorld: toWorld(event.clientX, event.clientY),
        current: { x: event.clientX, y: event.clientY },
        ids: [],
        startPan: [pan[0], pan[1]],
      });
      return;
    }
    if (event.button !== 0) return;
    const world = toWorld(event.clientX, event.clientY);
    setDrag({
      kind: "marquee",
      startScreen: { x: event.clientX, y: event.clientY },
      startWorld: world,
      current: world,
      ids: [],
      startPan: [pan[0], pan[1]],
    });
    if (!event.shiftKey) dispatch({ type: "select", ids: [] });
  };

  const onNodeDown = (nodeId: string, event: React.MouseEvent) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    holdSelection(event);
    const alreadySelected = state.selection.includes(nodeId);
    const ids = event.shiftKey
      ? Array.from(new Set([...state.selection, nodeId]))
      : alreadySelected
        ? state.selection
        : [nodeId];
    dispatch({ type: "select", ids });
    setDrag({
      kind: "node",
      startScreen: { x: event.clientX, y: event.clientY },
      startWorld: toWorld(event.clientX, event.clientY),
      current: toWorld(event.clientX, event.clientY),
      // A comment carries what it holds, worked out once as the drag starts.
      ids: movedBy(layouts, ids),
      startPan: [pan[0], pan[1]],
    });
  };

  const onHandleDown = (nodeId: string, handle: ResizeHandle, event: React.MouseEvent) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    holdSelection(event);
    const layout = layouts.get(nodeId);
    if (!layout) return;
    dispatch({ type: "select", ids: [nodeId] });
    const world = toWorld(event.clientX, event.clientY);
    setDrag({
      kind: "resize",
      startScreen: { x: event.clientX, y: event.clientY },
      startWorld: world,
      current: world,
      ids: [nodeId],
      startPan: [pan[0], pan[1]],
      handle,
      startBox: { x: layout.node.pos[0], y: layout.node.pos[1], width: layout.width, height: layout.height },
    });
  };

  const onPinDown = (
    nodeId: string,
    pin: PinLayout,
    direction: "in" | "out",
    event: React.MouseEvent
  ) => {
    if (event.button !== 0) return;
    holdSelection(event);
    const world = toWorld(event.clientX, event.clientY);
    setDrag({
      kind: "wire",
      startScreen: { x: event.clientX, y: event.clientY },
      startWorld: world,
      current: world,
      ids: [],
      startPan: [pan[0], pan[1]],
      pin: { nodeId, pin, direction },
    });
  };

  const onPinUp = (nodeId: string, pin: PinLayout, direction: "in" | "out") => {
    const source = drag?.pin;
    if (!drag || drag.kind !== "wire" || !source) return;
    setDrag(null);
    // Let go where it started: a click on the pin, not a connection.
    if (source.nodeId === nodeId) return;
    if (source.direction === direction) {
      dispatch({
        type: "notice",
        level: "warn",
        text: t("Connect an output to an input."),
      });
      return;
    }
    const from = source.direction === "out" ? source : { nodeId, pin };
    const to = source.direction === "out" ? { nodeId, pin } : source;
    dispatch({
      type: "connect",
      edge: {
        kind: from.pin.pin.type === "exec" ? "exec" : "data",
        from: [from.nodeId, from.pin.pin.name],
        to: [to.nodeId, to.pin.pin.name],
      },
    });
  };

  React.useEffect(() => {
    if (!drag) return;
    const onMove = (event: MouseEvent) => {
      if (drag.kind === "pan") {
        dispatch({
          type: "setView",
          pan: [
            drag.startPan[0] + (event.clientX - drag.startScreen.x),
            drag.startPan[1] + (event.clientY - drag.startScreen.y),
          ],
        });
        return;
      }
      setDrag({ ...drag, current: toWorld(event.clientX, event.clientY) });
    };
    const onUp = (event: MouseEvent) => {
      const world = toWorld(event.clientX, event.clientY);
      if (drag.kind === "node") {
        const dx = world.x - drag.startWorld.x;
        const dy = world.y - drag.startWorld.y;
        if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
          dispatch({ type: "moveNodes", ids: drag.ids, dx, dy, commit: true });
        }
      } else if (drag.kind === "resize" && drag.startBox && drag.handle) {
        const box = resized(drag.startBox, drag.handle, world.x - drag.startWorld.x, world.y - drag.startWorld.y);
        if (box.width !== drag.startBox.width || box.height !== drag.startBox.height || box.x !== drag.startBox.x || box.y !== drag.startBox.y) {
          dispatch({ type: "resizeNode", id: drag.ids[0], pos: [box.x, box.y], width: box.width, height: box.height });
        }
      } else if (drag.kind === "marquee") {
        const rect = rectOf(drag.startWorld, world);
        if (rect.maxX - rect.minX > 4 || rect.maxY - rect.minY > 4) {
          dispatch({
            type: "select",
            ids: nodesInRect(layouts, rect),
            additive: event.shiftKey,
          });
        }
      } else if (drag.kind === "wire" && drag.pin) {
        // A wire dropped on empty canvas is the Blueprint gesture for "make me
        // a node that fits here". Dropping it on a pin is handled by the pin
        // itself, so only a release over nothing opens the menu.
        const under = document.elementFromPoint(event.clientX, event.clientY);
        if (!under?.closest("[data-pin]")) {
          onContext({
            kind: "pin",
            pin: drag.pin,
            at: world,
            screen: { x: event.clientX, y: event.clientY },
          });
        }
      }
      setDrag(null);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [drag, dispatch, layouts, toWorld, onContext]);

  const onWheel = (event: React.WheelEvent) => {
    event.preventDefault();
    const rect = svgRef.current?.getBoundingClientRect();
    const px = event.clientX - (rect?.left ?? 0);
    const py = event.clientY - (rect?.top ?? 0);
    const now = performance.now();
    const gesture = wheelGesture(event.nativeEvent, now - lastSlide.current < 250);
    if (gesture === "pan") {
      lastSlide.current = now;
      dispatch({ type: "setView", pan: [pan[0] - event.deltaX, pan[1] - event.deltaY] });
      return;
    }
    // A pinch comes as small steps; a wheel notch as one big one.
    const factor =
      gesture === "pinch" ? Math.exp(-event.deltaY * 0.01) : event.deltaY < 0 ? 1.12 : 1 / 1.12;
    // Keep the point under the cursor fixed; zooming to the corner is the
    // fastest way to lose a graph.
    const view = zoomAround({ pan, zoom }, { x: px, y: py }, zoom * factor);
    dispatch({ type: "setView", zoom: view.zoom, pan: view.pan });
  };

  const offsetFor = (nodeId: string): { dx: number; dy: number } => {
    if (!drag || drag.kind !== "node" || !drag.ids.includes(nodeId)) return { dx: 0, dy: 0 };
    return { dx: drag.current.x - drag.startWorld.x, dy: drag.current.y - drag.startWorld.y };
  };

  /** Where a comment is drawn: its own box, or the one a drag or resize gives it now. */
  const boxFor = (layout: NodeLayout): Box => {
    if (drag?.kind === "resize" && drag.ids[0] === layout.node.id && drag.startBox && drag.handle) {
      return resized(
        drag.startBox,
        drag.handle,
        drag.current.x - drag.startWorld.x,
        drag.current.y - drag.startWorld.y
      );
    }
    const offset = offsetFor(layout.node.id);
    return {
      x: layout.node.pos[0] + offset.dx,
      y: layout.node.pos[1] + offset.dy,
      width: layout.width,
      height: layout.height,
    };
  };

  const nodeContext = (nodeId: string, event: React.MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    if (!state.selection.includes(nodeId)) {
      dispatch({ type: "select", ids: [nodeId] });
    }
    onContext({
      kind: "node",
      id: nodeId,
      at: toWorld(event.clientX, event.clientY),
      screen: { x: event.clientX, y: event.clientY },
    });
  };

  const stopEditing = () => dispatch({ type: "edit", target: null });

  // -- render -----------------------------------------------------------

  // Where each round node's connector faces: the middle of where its wires go.
  // One being pulled out of it follows the pointer around the rim instead, and
  // the wires it already has stay put.
  const aims: Aims = new Map();
  const pulled: Aims = new Map();
  for (const layout of shown.values()) {
    const id = layout.node.id;
    if (!isRound(layout.node.kind)) continue;
    const moved = offsetFor(id);
    const at = roundCenter(layout);
    const center = { x: at.x + moved.dx, y: at.y + moved.dy };
    const targets = state.graph.edges.flatMap((edge) => {
      if (edge.from[0] !== id) return [];
      const to = pinPoint(shown, edge.to[0], edge.to[1], "in");
      const shift = offsetFor(edge.to[0]);
      return to ? [{ x: to.x + shift.dx, y: to.y + shift.dy }] : [];
    });
    if (targets.length) aims.set(id, aimAt(center, targets));
    if (drag?.kind === "wire" && drag.pin?.nodeId === id && drag.pin.direction === "out") {
      pulled.set(id, aimAt(center, [drag.current]));
    }
  }

  const wires = state.graph.edges.flatMap((edge) => {
    const from = pinAnchor(shown, edge.from[0], edge.from[1], "out", aims);
    const to = pinAnchor(shown, edge.to[0], edge.to[1], "in", aims);
    if (!from || !to) return [];
    const dragOffsetFrom = offsetFor(edge.from[0]);
    const dragOffsetTo = offsetFor(edge.to[0]);
    const a = { ...from, x: from.x + dragOffsetFrom.dx, y: from.y + dragOffsetFrom.dy };
    const b = { ...to, x: to.x + dragOffsetTo.dx, y: to.y + dragOffsetTo.dy };
    const ends = [shown.get(edge.from[0]), shown.get(edge.to[0])].map((item) =>
      item ? accentOf(item.spec) : "#888"
    );
    return [{ edge, a, b, ends, type: catalog ? edgeType(state.graph, catalog, edge) : "any" }];
  });

  const now = Date.now();
  const marquee =
    drag?.kind === "marquee" ? rectOf(drag.startWorld, drag.current) : null;

  // Comments behind everything, in the order commentOrder settles between them.
  const all = Array.from(shown.values());
  const comments = commentOrder(
    all
      .filter((layout) => isAnnotation(layout.spec))
      .map((layout) => {
        const at = boxFor(layout);
        return {
          id: layout.node.id,
          layout,
          box: { minX: at.x, minY: at.y, maxX: at.x + at.width, maxY: at.y + at.height },
        };
      })
  ).map((item) => item.layout);
  // The open node last, so nothing it pushed aside is drawn over it.
  const nodes = all
    .filter((layout) => !isAnnotation(layout.spec))
    .sort((a, b) => Number(a.node.id === follow.focusId) - Number(b.node.id === follow.focusId));
  const editing = state.editing;
  const panelFor = (layout: NodeLayout) => {
    const id = layout.node.id;
    if (id !== follow.focusId) return undefined;
    const base = layouts.get(id);
    const top = panelTop(layout);
    return {
      top,
      height: base ? layout.height - base.height : 0,
      parts: state.run.output[id] ?? [],
      live: state.run.nodes[id] === "running",
      onClose: pinned === id ? () => setPinned(null) : undefined,
    };
  };

  return (
    <div className="flow-canvas-wrap">
      <svg
        ref={svgRef}
        className={`flow-canvas${drag?.kind === "pan" ? " panning" : ""}`}
        data-testid="flow-canvas"
        onMouseDown={onBackgroundDown}
        onWheel={onWheel}
        onContextMenu={(event) => {
          event.preventDefault();
          onContext({
            kind: "canvas",
            at: toWorld(event.clientX, event.clientY),
            screen: { x: event.clientX, y: event.clientY },
          });
        }}
      >
        <defs>
          <pattern
            id="flow-grid"
            width={GRID * zoom}
            height={GRID * zoom}
            patternUnits="userSpaceOnUse"
            x={pan[0]}
            y={pan[1]}
          >
            <circle className="flow-grid-dot" cx={1} cy={1} r={zoom < 0.5 ? 0.6 : 0.9} />
          </pattern>
          <pattern
            id="flow-grid-major"
            width={GRID * 5 * zoom}
            height={GRID * 5 * zoom}
            patternUnits="userSpaceOnUse"
            x={pan[0]}
            y={pan[1]}
          >
            <circle className="flow-grid-dot major" cx={1.2} cy={1.2} r={1.4} />
          </pattern>
          <linearGradient id="flow-sheen" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#ffffff" stopOpacity="0.22" />
            <stop offset="0.55" stopColor="#ffffff" stopOpacity="0.04" />
            <stop offset="1" stopColor="#000000" stopOpacity="0.18" />
          </linearGradient>
          <filter id="flow-shadow" x="-10%" y="-10%" width="120%" height="130%">
            <feDropShadow dx="0" dy="3" stdDeviation="4" floodColor="#000" floodOpacity="0.32" />
          </filter>
        </defs>
        <rect width="100%" height="100%" fill="url(#flow-grid)" />
        <rect width="100%" height="100%" fill="url(#flow-grid-major)" />

        <g transform={`translate(${pan[0]}, ${pan[1]}) scale(${zoom})`}>
          {comments.map((layout) => {
            const id = layout.node.id;
            const nodeIssues = problems[id] ?? [];
            const problem = nodeIssues.some((issue) => issue.severity === "error")
              ? "error"
              : nodeIssues.length > 0
                ? "warning"
                : null;
            return (
              <CommentView
                key={id}
                layout={layout}
                box={boxFor(layout)}
                zoom={zoom}
                selected={state.selection.includes(id)}
                problem={problem}
                editing={editing?.id === id && editing.what === "title"}
                onHeaderDown={(event) => onNodeDown(id, event)}
                onHandleDown={(handle, event) => onHandleDown(id, handle, event)}
                onContext={(event) => nodeContext(id, event)}
                onEdit={() => dispatch({ type: "edit", target: { id, what: "title" } })}
                onCommit={(text) => {
                  stopEditing();
                  if (text !== String(layout.node.data?.title ?? "")) {
                    dispatch({ type: "patchNodeData", id, patch: { title: text } });
                  }
                }}
                onCancel={stopEditing}
              />
            );
          })}

          {wires.map(({ edge, a, b, ends, type }) => {
            // The flow is drawn from one node's colour into the next one's; a
            // value goes dashed, in its type's colour.
            const gradient = `flow-wire-${edge.id.replace(/[^\w-]/g, "_")}`;
            const color = edge.kind === "exec" ? `url(#${gradient})` : catalog?.color(type) ?? "#888";
            const litAt = state.run.active[edge.id];
            const isLit = Boolean(litAt && now - litAt < EDGE_GLOW_MS);
            const traveled = state.run.traveled.includes(edge.id);
            const path = wirePath(a, b, a, b);
            const classes = ["flow-wire", edge.kind];
            if (state.selectedEdge === edge.id) classes.push("selected");
            if (traveled) classes.push("traveled");
            if (isLit) classes.push("lit");
            return (
              <g key={edge.id} data-testid={`edge-${edge.id}`} data-lit={isLit ? "1" : "0"}>
                {edge.kind === "exec" && (
                  <defs>
                    <linearGradient id={gradient} gradientUnits="userSpaceOnUse" x1={a.x} y1={a.y} x2={b.x} y2={b.y}>
                      <stop offset="0" stopColor={ends[0]} />
                      <stop offset="1" stopColor={ends[1]} />
                    </linearGradient>
                  </defs>
                )}
                {(isLit || state.selectedEdge === edge.id) && (
                  <path className={`flow-wire-glow ${edge.kind}`} d={path} stroke={color} />
                )}
                <path
                  className={classes.join(" ")}
                  d={path}
                  stroke={color}
                  strokeOpacity={isLit ? 1 : traveled ? 0.95 : 0.8}
                />
                <path
                  className="flow-wire-hit"
                  d={path}
                  onMouseDown={(event) => {
                    if (event.button !== 0) return;
                    event.stopPropagation();
                    dispatch({ type: "selectEdge", id: edge.id });
                  }}
                  onDoubleClick={(event) => {
                    event.stopPropagation();
                    dispatch({ type: "deleteEdge", id: edge.id });
                  }}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onContext({
                      kind: "edge",
                      id: edge.id,
                      at: toWorld(event.clientX, event.clientY),
                      screen: { x: event.clientX, y: event.clientY },
                    });
                  }}
                />
                {isLit && (
                  <circle
                    className={`flow-dot ${edge.kind}`}
                    r={4.5}
                    fill={edge.kind === "exec" ? ends[0] : color}
                    data-testid={`dot-${edge.id}`}
                  >
                    <animateMotion
                      dur={`${(wireLength(a, b) / 320).toFixed(2)}s`}
                      repeatCount="indefinite"
                      path={path}
                    />
                  </circle>
                )}
              </g>
            );
          })}

          {drag?.kind === "wire" && drag.pin && (
            <path
              className={`flow-wire pending ${drag.pin.pin.pin.type === "exec" ? "exec" : "data"}`}
              d={pendingPath(shown, drag, new Map([...aims, ...pulled]))}
              stroke={pendingStroke(shown, drag.pin, (type) => catalog?.color(type) ?? "#888")}
            />
          )}

          {nodes.map((layout) => {
            const nodeIssues = problems[layout.node.id] ?? [];
            if (isRound(layout.node.kind)) {
              return (
                <RoundNodeView
                  key={layout.node.id}
                  layout={layout}
                  offset={offsetFor(layout.node.id)}
                  selected={state.selection.includes(layout.node.id)}
                  status={(state.run.nodes[layout.node.id] ?? "idle") as NodeRunStatus}
                  hasError={nodeIssues.some((issue) => issue.severity === "error")}
                  hasWarning={nodeIssues.some((issue) => issue.severity === "warning")}
                  accent={accentOf(layout.spec)}
                  aim={pulled.get(layout.node.id) ?? aims.get(layout.node.id) ?? 0}
                  color={(type) => catalog?.color(type) ?? "#888"}
                  connected={(pin, direction) => connected.has(`${layout.node.id}:${pin}:${direction}`)}
                  onNodeDown={onNodeDown}
                  onNodeContext={nodeContext}
                  onPinDown={onPinDown}
                  onPinUp={onPinUp}
                />
              );
            }
            return (
              <NodeView
                key={layout.node.id}
                layout={layout}
                offset={offsetFor(layout.node.id)}
                selected={state.selection.includes(layout.node.id)}
                status={(state.run.nodes[layout.node.id] ?? "idle") as NodeRunStatus}
                hasError={nodeIssues.some((issue) => issue.severity === "error")}
                hasWarning={nodeIssues.some((issue) => issue.severity === "warning")}
                badge={badgeFor(state, layout.node.id)}
                accent={accentOf(layout.spec)}
                chips={chips.get(layout.node.id) ?? []}
                activity={state.run.agents[layout.node.id]}
                panel={panelFor(layout)}
                color={(type) => catalog?.color(type) ?? "#888"}
                connected={(pin, direction) =>
                  connected.has(`${layout.node.id}:${pin}:${direction}`)
                }
                onNodeDown={onNodeDown}
                onNodeContext={nodeContext}
                onPinDown={onPinDown}
                onPinUp={onPinUp}
              />
            );
          })}

          {nodes.map((layout) => {
            const id = layout.node.id;
            const note = String(layout.node.data?.note ?? "");
            const writing = editing?.id === id && editing.what === "note";
            const offset = offsetFor(id);
            const x = layout.node.pos[0] + offset.dx;
            const y = layout.node.pos[1] + offset.dy;
            if (writing || (note && layout.node.data?.note_shown !== false)) {
              return (
                <NoteBubble
                  key={`note-${id}`}
                  nodeId={id}
                  x={x}
                  y={y}
                  nodeWidth={layout.width}
                  text={note}
                  editing={writing}
                  onDown={(event) => onNodeDown(id, event)}
                  onEdit={() => dispatch({ type: "edit", target: { id, what: "note" } })}
                  onHide={() => dispatch({ type: "patchNodeData", id, patch: { note_shown: false } })}
                  onCommit={(text) => {
                    stopEditing();
                    const kept = text.trim() ? text : undefined;
                    if (kept === (note || undefined)) return;
                    dispatch({
                      type: "patchNodeData",
                      id,
                      patch: kept ? { note: kept, note_shown: true } : { note: undefined, note_shown: undefined },
                    });
                  }}
                  onCancel={stopEditing}
                />
              );
            }
            if (!note) return null;
            return (
              <NoteMark
                key={`note-${id}`}
                nodeId={id}
                x={x + layout.width - 14}
                y={y - 12}
                onShow={() => dispatch({ type: "patchNodeData", id, patch: { note_shown: true } })}
              />
            );
          })}

          {nodes.map((layout) => {
            // Folded, what a node did stays a click away, the way a finished thought does.
            const id = layout.node.id;
            if (id === follow.focusId || !(state.run.output[id] ?? []).length) return null;
            if (state.run.nodes[id] === "running") return null;
            const offset = offsetFor(id);
            return (
              <g
                key={`output-${id}`}
                className="flow-output-pill"
                data-testid={`output-pill-${id}`}
                transform={`translate(${layout.node.pos[0] + offset.dx + 12}, ${layout.node.pos[1] + offset.dy + layout.height + 5})`}
                onMouseDown={(event) => event.stopPropagation()}
                onClick={(event) => {
                  event.stopPropagation();
                  setPinned(pinned === id ? null : id);
                }}
              >
                <title>{t("Show the output")}</title>
                <rect width={74} height={18} rx={9} ry={9} />
                <text x={10} y={12.5}>
                  ▸ {t("Output")}
                </text>
              </g>
            );
          })}

          {marquee && (
            <rect
              className="flow-marquee"
              x={marquee.minX}
              y={marquee.minY}
              width={marquee.maxX - marquee.minX}
              height={marquee.maxY - marquee.minY}
            />
          )}
        </g>
      </svg>

      {state.graph.nodes.length === 0 && (
        <div className="flow-empty">
          {t("Empty graph. Right-click the canvas to create the first node.")}
        </div>
      )}
      {minimap && layouts.size > 0 && (
        <Minimap
          layouts={layouts}
          view={state.graph.view}
          size={size}
          selection={state.selection}
          running={state.run.nodes}
          onPan={(next) => dispatch({ type: "setView", pan: next })}
        />
      )}
    </div>
  );
}

/**
 * What a wheel event is. A Magic Mouse has no middle button: sliding a finger
 * on it, like two fingers on a trackpad, sends a stream of small wheel steps,
 * and those move the camera. A pinch (or Ctrl+wheel) zooms. A wheel with
 * notches sends one large step per notch and keeps zooming, as it always has.
 */
export function wheelGesture(
  event: { deltaX: number; deltaY: number; deltaMode: number; ctrlKey: boolean; wheelDeltaY?: number },
  /** A slide was seen a moment ago: its momentum tail is the same slide. */
  sliding = false
): "pan" | "pinch" | "wheel" {
  if (event.ctrlKey) return "pinch";
  if (event.deltaMode !== 0) return "wheel";
  if (sliding || event.deltaX !== 0) return "pan";
  // Chromium reports a notch as a multiple of 120; a surface, as whatever it moved.
  if (typeof event.wheelDeltaY === "number" && event.wheelDeltaY !== 0) {
    return event.wheelDeltaY % 120 === 0 ? "wheel" : "pan";
  }
  return Number.isInteger(event.deltaY) && Math.abs(event.deltaY) >= 50 ? "wheel" : "pan";
}

const MAP_W = 200;
const MAP_H = 128;
const MAP_PAD = 8;
// World units kept around the graph, so its edge nodes are not flush with the frame.
const MAP_MARGIN = 160;

interface MinimapProps {
  layouts: Map<string, NodeLayout>;
  view: { pan: [number, number]; zoom: number };
  size: { width: number; height: number };
  selection: string[];
  running: Record<string, NodeRunStatus>;
  onPan(pan: [number, number]): void;
}

/**
 * The whole graph in a corner, with the visible part outlined. Clicking or
 * dragging on it centres the view there. The frame follows the graph alone -
 * including the viewport would shift the mapping under a drag.
 */
function Minimap({ layouts, view, size, selection, running, onPan }: MinimapProps) {
  const ref = React.useRef<SVGSVGElement | null>(null);
  const bounds = graphBounds(layouts);
  if (!bounds) return null;
  const minX = bounds.minX - MAP_MARGIN;
  const minY = bounds.minY - MAP_MARGIN;
  const spanX = bounds.maxX - bounds.minX + MAP_MARGIN * 2;
  const spanY = bounds.maxY - bounds.minY + MAP_MARGIN * 2;
  const scale = Math.min((MAP_W - MAP_PAD * 2) / spanX, (MAP_H - MAP_PAD * 2) / spanY);
  const ox = MAP_PAD + (MAP_W - MAP_PAD * 2 - spanX * scale) / 2;
  const oy = MAP_PAD + (MAP_H - MAP_PAD * 2 - spanY * scale) / 2;
  const toMap = (x: number, y: number) => ({ x: ox + (x - minX) * scale, y: oy + (y - minY) * scale });

  const { pan, zoom } = view;
  const topLeft = toMap(-pan[0] / zoom, -pan[1] / zoom);
  const viewW = (size.width / zoom) * scale;
  const viewH = (size.height / zoom) * scale;

  const centreOn = (clientX: number, clientY: number) => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    const worldX = (clientX - rect.left - ox) / scale + minX;
    const worldY = (clientY - rect.top - oy) / scale + minY;
    onPan([size.width / 2 - worldX * zoom, size.height / 2 - worldY * zoom]);
  };

  const onDown = (event: React.MouseEvent) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    centreOn(event.clientX, event.clientY);
    const move = (moved: MouseEvent) => centreOn(moved.clientX, moved.clientY);
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  return (
    <svg
      ref={ref}
      className="flow-minimap"
      width={MAP_W}
      height={MAP_H}
      data-testid="minimap"
      onMouseDown={onDown}
      onContextMenu={(event) => event.preventDefault()}
    >
      <rect className="flow-minimap-bg" width={MAP_W} height={MAP_H} rx={8} ry={8} />
      {Array.from(layouts.values())
        .filter((layout) => isAnnotation(layout.spec))
        .map((layout) => {
          const at = toMap(layout.node.pos[0], layout.node.pos[1]);
          const color = commentLook(layout.node).color;
          return (
            <rect
              key={layout.node.id}
              className="flow-minimap-comment"
              x={at.x}
              y={at.y}
              width={Math.max(2, layout.width * scale)}
              height={Math.max(2, layout.height * scale)}
              rx={1.5}
              fill={color}
              stroke={color}
            />
          );
        })}
      {Array.from(layouts.values()).filter((layout) => !isAnnotation(layout.spec)).map((layout) => {
        const at = toMap(layout.node.pos[0], layout.node.pos[1]);
        const status = running[layout.node.id];
        const classes = ["flow-minimap-node"];
        if (selection.includes(layout.node.id)) classes.push("selected");
        if (status && status !== "idle") classes.push(status);
        return (
          <rect
            key={layout.node.id}
            className={classes.join(" ")}
            x={at.x}
            y={at.y}
            width={Math.max(2, layout.width * scale)}
            height={Math.max(2, layout.height * scale)}
            rx={isRound(layout.node.kind) ? Math.max(1, (layout.height * scale) / 2) : 1.5}
            fill={accentOf(layout.spec)}
          />
        );
      })}
      <rect
        className="flow-minimap-view"
        x={topLeft.x}
        y={topLeft.y}
        width={viewW}
        height={viewH}
        rx={2}
      />
    </svg>
  );
}

/**
 * A drag on the canvas must not start a text selection: it would sweep node
 * titles and inspector text along with it. The field that had focus lets go
 * too, so Delete and the other shortcuts reach the graph again.
 */
function holdSelection(event: React.MouseEvent): void {
  event.preventDefault();
  const active = document.activeElement as HTMLElement | null;
  if (active && active !== document.body) active.blur();
  window.getSelection()?.removeAllRanges();
}

function badgeFor(state: EditorState, nodeId: string): string {
  const loop = state.run.loops[nodeId];
  if (loop) return `#${loop.index + 1}`;
  const agent = state.run.agents[nodeId];
  if (agent) return agent.name;
  return "";
}

function pendingPath(layouts: Map<string, NodeLayout>, drag: DragState, aims: Aims): string {
  const source = drag.pin;
  if (!source) return "";
  const anchor = pinAnchor(layouts, source.nodeId, source.pin.pin.name, source.direction, aims);
  if (!anchor) return "";
  return source.direction === "out"
    ? wirePath(anchor, drag.current, anchor)
    : wirePath(drag.current, anchor, undefined, anchor);
}

/** A wire being pulled: in its node's colour for the flow, its type's for a value. */
function pendingStroke(
  layouts: Map<string, NodeLayout>,
  source: NonNullable<DragState["pin"]>,
  color: (type: string) => string
): string {
  if (source.pin.pin.type !== "exec") return color(source.pin.pin.type);
  const layout = layouts.get(source.nodeId);
  return layout ? accentOf(layout.spec) : color("exec");
}

function rectOf(a: Point, b: Point): Bounds {
  return {
    minX: Math.min(a.x, b.x),
    minY: Math.min(a.y, b.y),
    maxX: Math.max(a.x, b.x),
    maxY: Math.max(a.y, b.y),
  };
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** Pan and zoom so the whole graph fits the viewport, with a margin. */
export function frameAll(
  layouts: Map<string, NodeLayout>,
  width: number,
  height: number
): { pan: [number, number]; zoom: number } | null {
  const bounds = graphBounds(layouts);
  if (!bounds) return null;
  const margin = 60;
  const graphWidth = Math.max(1, bounds.maxX - bounds.minX);
  const graphHeight = Math.max(1, bounds.maxY - bounds.minY);
  const zoom = clamp(
    Math.min((width - margin * 2) / graphWidth, (height - margin * 2) / graphHeight),
    MIN_ZOOM,
    1.2
  );
  return {
    zoom,
    pan: [
      width / 2 - ((bounds.minX + bounds.maxX) / 2) * zoom,
      height / 2 - ((bounds.minY + bounds.maxY) / 2) * zoom,
    ],
  };
}

export function edgesOf(state: EditorState): FlowEdge[] {
  return state.graph.edges;
}
