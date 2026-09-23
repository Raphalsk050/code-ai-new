import * as React from "react";

import type { FlowEdge } from "../../src/flow-schema";
import { chipsFor } from "./access";
import {
  Bounds,
  GRID,
  NodeLayout,
  PinLayout,
  Point,
  accentOf,
  edgeType,
  graphBounds,
  layoutGraph,
  nodesInRect,
  pinPoint,
  wireLength,
  wirePath,
} from "./model";
import { t } from "../i18n";
import { NodeView } from "./node-view";
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
  kind: "node" | "pan" | "marquee" | "wire";
  startScreen: Point;
  startWorld: Point;
  current: Point;
  ids: string[];
  startPan: [number, number];
  pin?: { nodeId: string; pin: PinLayout; direction: "in" | "out" };
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
    return () => observer.disconnect();
  }, []);

  // Lit wires animate their dots; a running agent moves nothing on its own, and
  // its elapsed clock would freeze without a tick of its own.
  const lit = Object.keys(state.run.active).length > 0 || state.run.running;
  React.useEffect(() => {
    if (!lit) return;
    const timer = window.setInterval(() => setTick((value) => value + 1), 600);
    return () => window.clearInterval(timer);
  }, [lit]);

  const layouts = React.useMemo(
    () => (catalog ? layoutGraph(state.graph, catalog) : new Map<string, NodeLayout>()),
    [state.graph, catalog]
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
      ids,
      startPan: [pan[0], pan[1]],
    });
  };

  const onPinDown = (
    nodeId: string,
    pin: PinLayout,
    direction: "in" | "out",
    event: React.MouseEvent
  ) => {
    if (event.button !== 0) return;
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
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
    // Keep the point under the cursor fixed; zooming to the corner is the
    // fastest way to lose a graph.
    const view = zoomAround({ pan, zoom }, { x: px, y: py }, zoom * factor);
    dispatch({ type: "setView", zoom: view.zoom, pan: view.pan });
  };

  const offsetFor = (nodeId: string): { dx: number; dy: number } => {
    if (!drag || drag.kind !== "node" || !drag.ids.includes(nodeId)) return { dx: 0, dy: 0 };
    return { dx: drag.current.x - drag.startWorld.x, dy: drag.current.y - drag.startWorld.y };
  };

  // -- render -----------------------------------------------------------

  const wires = state.graph.edges.flatMap((edge) => {
    const from = pinPoint(layouts, edge.from[0], edge.from[1], "out");
    const to = pinPoint(layouts, edge.to[0], edge.to[1], "in");
    if (!from || !to) return [];
    const dragOffsetFrom = offsetFor(edge.from[0]);
    const dragOffsetTo = offsetFor(edge.to[0]);
    const a = { x: from.x + dragOffsetFrom.dx, y: from.y + dragOffsetFrom.dy };
    const b = { x: to.x + dragOffsetTo.dx, y: to.y + dragOffsetTo.dy };
    return [{ edge, a, b, type: catalog ? edgeType(state.graph, catalog, edge) : "any" }];
  });

  const now = Date.now();
  const marquee =
    drag?.kind === "marquee" ? rectOf(drag.startWorld, drag.current) : null;

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
          {wires.map(({ edge, a, b, type }) => {
            const color = catalog?.color(type) ?? "#888";
            const litAt = state.run.active[edge.id];
            const isLit = Boolean(litAt && now - litAt < EDGE_GLOW_MS);
            const traveled = state.run.traveled.includes(edge.id);
            const path = wirePath(a, b);
            const classes = ["flow-wire", edge.kind];
            if (state.selectedEdge === edge.id) classes.push("selected");
            if (traveled) classes.push("traveled");
            if (isLit) classes.push("lit");
            return (
              <g key={edge.id} data-testid={`edge-${edge.id}`} data-lit={isLit ? "1" : "0"}>
                {(isLit || state.selectedEdge === edge.id) && (
                  <path className={`flow-wire-glow ${edge.kind}`} d={path} stroke={color} />
                )}
                <path
                  className={classes.join(" ")}
                  d={path}
                  stroke={color}
                  strokeOpacity={isLit ? 1 : traveled ? 0.9 : 0.62}
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
                    fill={color}
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
              className={`flow-wire ${drag.pin.pin.pin.type === "exec" ? "exec" : "data"}`}
              d={pendingPath(layouts, drag)}
              stroke={catalog?.color(drag.pin.pin.pin.type) ?? "#fff"}
              strokeDasharray="5 4"
            />
          )}

          {Array.from(layouts.values()).map((layout) => {
            const nodeIssues = problems[layout.node.id] ?? [];
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
                color={(type) => catalog?.color(type) ?? "#888"}
                connected={(pin, direction) =>
                  connected.has(`${layout.node.id}:${pin}:${direction}`)
                }
                onNodeDown={onNodeDown}
                onNodeContext={(nodeId, event) => {
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
                }}
                onPinDown={onPinDown}
                onPinUp={onPinUp}
              />
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
      {minimap && offScreen(layouts, state.graph.view, size) && (
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

/** Whether any node sits outside the visible canvas, which is when a map helps. */
function offScreen(
  layouts: Map<string, NodeLayout>,
  view: { pan: [number, number]; zoom: number },
  size: { width: number; height: number }
): boolean {
  const bounds = graphBounds(layouts);
  if (!bounds) return false;
  const { pan, zoom } = view;
  return (
    bounds.minX * zoom + pan[0] < 0 ||
    bounds.minY * zoom + pan[1] < 0 ||
    bounds.maxX * zoom + pan[0] > size.width ||
    bounds.maxY * zoom + pan[1] > size.height
  );
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
      {Array.from(layouts.values()).map((layout) => {
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
            rx={1.5}
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

function badgeFor(state: EditorState, nodeId: string): string {
  const loop = state.run.loops[nodeId];
  if (loop) return `#${loop.index + 1}`;
  const agent = state.run.agents[nodeId];
  if (agent) return agent.name;
  return "";
}

function pendingPath(layouts: Map<string, NodeLayout>, drag: DragState): string {
  const source = drag.pin;
  if (!source) return "";
  const anchor = pinPoint(layouts, source.nodeId, source.pin.pin.name, source.direction);
  if (!anchor) return "";
  return source.direction === "out"
    ? wirePath(anchor, drag.current)
    : wirePath(drag.current, anchor);
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
