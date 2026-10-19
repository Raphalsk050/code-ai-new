import * as React from "react";

import { t } from "../i18n";
import type { Chip } from "./access";
import { glyphFor } from "./glyphs";
import { isWide, NodeLayout, PinLayout, ROUND_D } from "./model";
import { OutputPanel } from "./output-panel";
import type { AgentRun, NodeRunStatus, OutputPart } from "./store";

const RADIUS = 16;
/** How far out the dashed ring of a selected or running node sits. */
const RING = 5;

export interface NodeViewProps {
  layout: NodeLayout;
  offset: { dx: number; dy: number };
  selected: boolean;
  status: NodeRunStatus;
  hasError: boolean;
  hasWarning: boolean;
  badge: string;
  /** The node's colour. */
  accent: string;
  /** What an agent may touch, said when the node is pointed at. */
  chips: Chip[];
  /** The agent working inside this node, while one is. */
  activity?: AgentRun;
  /** The node is open: its output panel, under its body, at this height. */
  panel?: { top: number; height: number; parts: OutputPart[]; live: boolean; onClose?: () => void };
  color(type: string): string;
  /** The colour of the wire on a pin, when one is: the pin wears it too. */
  tint(pin: string, direction: "in" | "out"): string | undefined;
  connected(pin: string, direction: "in" | "out"): boolean;
  onNodeDown(nodeId: string, event: React.MouseEvent): void;
  onNodeContext(nodeId: string, event: React.MouseEvent): void;
  onPinDown(
    nodeId: string,
    pin: PinLayout,
    direction: "in" | "out",
    event: React.MouseEvent
  ): void;
  onPinUp(nodeId: string, pin: PinLayout, direction: "in" | "out"): void;
}

/**
 * One node, flat: a square in its colour holding its glyph, with its name and
 * one line of what it does under it. A node fed from below is wide and keeps
 * its name inside, its ports named under it. Pins carry no names until pointed
 * at or a wire is being pulled.
 */
export function NodeView(props: NodeViewProps): JSX.Element {
  const { layout, offset, selected, status, hasError, hasWarning, badge, accent } = props;
  const { width, height, body } = layout;
  // An agent can sit on one step for minutes: while it does, its line says
  // what it is doing instead of what it is.
  const activity = status === "running" ? props.activity : undefined;
  const summary = summaryLine(layout, props.connected("profession", "in"));
  const line = activity ? activityLabel(activity) : summary;
  const glyph = glyphFor(layout.spec.kind);
  const inside = isWide(layout) || Boolean(props.panel);
  const [x, y] = layout.node.pos;
  const tip = [layout.spec.title, summary, ...props.chips.map((chip) => chip.label)].filter(Boolean).join(" · ");

  const classes = ["flow-node"];
  if (selected) classes.push("selected");
  if (hasError) classes.push("error");
  else if (hasWarning) classes.push("warning");
  if (status !== "idle") classes.push(status);

  const lineProps = {
    className: "flow-node-summary",
    "data-testid": activity ? `activity-${layout.node.id}` : undefined,
  };
  const room = Math.floor((width - 70) / 6.6);

  return (
    <g
      className={classes.join(" ")}
      transform={`translate(${x + offset.dx}, ${y + offset.dy})`}
      data-node-id={layout.node.id}
      data-testid={`node-${layout.node.id}`}
      data-status={status}
      onMouseDown={(event) => props.onNodeDown(layout.node.id, event)}
      onContextMenu={(event) => props.onNodeContext(layout.node.id, event)}
    >
      <title>{tip}</title>
      <rect
        className="flow-node-halo"
        x={-RING}
        y={-RING}
        width={width + RING * 2}
        height={height + RING * 2}
        rx={RADIUS + RING}
        stroke={accent}
      />
      <rect className="flow-node-box" width={width} height={height} rx={RADIUS} ry={RADIUS} fill={accent} />

      {inside ? (
        <>
          <Glyph glyph={glyph} x={18} y={body / 2 - 14} scale={1.75} />
          <text className="flow-node-title" x={60} y={line ? body / 2 - 2 : body / 2 + 4}>
            {truncate(layout.spec.title, room)}
          </text>
          {line && (
            <text {...lineProps} x={60} y={body / 2 + 13}>
              {truncate(line, Math.floor((width - 70) / 5.6))}
            </text>
          )}
        </>
      ) : (
        <>
          <Glyph glyph={glyph} x={width / 2 - 16} y={body / 2 - 16} scale={2} />
          <text className="flow-node-title under" x={width / 2} y={height + 16} textAnchor="middle">
            {truncate(layout.spec.title, 20)}
          </text>
          {line && (
            <text {...lineProps} className="flow-node-summary under" x={width / 2} y={height + 30} textAnchor="middle">
              {truncate(line, 26)}
            </text>
          )}
        </>
      )}

      {badge && <Badge text={badge} x={width / 2} />}
      {status === "running" && <Spinner x={width - 14} y={14} />}
      {status === "done" && <Mark x={width - 14} y={14} kind="done" />}
      {status === "failed" && <Mark x={width - 14} y={14} kind="failed" />}

      {layout.inputs.map((item) => (
        <Pin
          key={`in-${item.pin.name}`}
          item={item}
          direction="in"
          color={props.color(item.pin.type)}
          tint={props.tint(item.pin.name, "in")}
          filled={props.connected(item.pin.name, "in")}
          required={item.pin.required}
          onDown={(event) => props.onPinDown(layout.node.id, item, "in", event)}
          onUp={() => props.onPinUp(layout.node.id, item, "in")}
        />
      ))}
      {layout.outputs.map((item) => (
        <Pin
          key={`out-${item.pin.name}`}
          item={item}
          direction="out"
          color={props.color(item.pin.type)}
          tint={props.tint(item.pin.name, "out")}
          filled={props.connected(item.pin.name, "out")}
          required={false}
          onDown={(event) => props.onPinDown(layout.node.id, item, "out", event)}
          onUp={() => props.onPinUp(layout.node.id, item, "out")}
        />
      ))}

      {props.panel && props.panel.height > 12 && (
        <foreignObject
          className="flow-output-frame"
          x={8}
          y={props.panel.top}
          width={Math.max(0, width - 16)}
          height={props.panel.height - 6}
        >
          <OutputPanel
            nodeId={layout.node.id}
            parts={props.panel.parts}
            live={props.panel.live}
            onClose={props.panel.onClose}
          />
        </foreignObject>
      )}

      {(hasError || hasWarning) && (
        <g
          className={`flow-node-problem ${hasError ? "error" : "warning"}`}
          transform={`translate(${width - 2}, 2)`}
        >
          <circle r={7} />
          <text y={3.5} textAnchor="middle">
            !
          </text>
        </g>
      )}
    </g>
  );
}

export type RoundNodeViewProps = Omit<NodeViewProps, "chips" | "activity" | "panel" | "badge"> & {
  /** Which way its connector faces, in radians. */
  aim: number;
};

/**
 * A node that holds a value - a model, a variable, a profession, tools - as a
 * circle with its name underneath. Its connector rides the rim, facing wherever
 * its wires go, or the pointer while one is being pulled out of it. A wire
 * dropped anywhere on the circle lands on that connector.
 */
export function RoundNodeView(props: RoundNodeViewProps): JSX.Element {
  const { layout, offset, selected, status, hasError, hasWarning, accent, aim } = props;
  const r = ROUND_D / 2;
  const cx = layout.width / 2;
  const cy = r;
  const glyph = glyphFor(layout.spec.kind);
  const [x, y] = layout.node.pos;
  const port = layout.outputs[0];
  // Which model, not where it is served: the inspector has the address.
  const line = layout.summary.split(" @ ")[0];

  const classes = ["flow-node", "round"];
  if (selected) classes.push("selected");
  if (hasError) classes.push("error");
  else if (hasWarning) classes.push("warning");
  if (status !== "idle") classes.push(status);

  return (
    <g
      className={classes.join(" ")}
      transform={`translate(${x + offset.dx}, ${y + offset.dy})`}
      data-node-id={layout.node.id}
      data-testid={`node-${layout.node.id}`}
      data-status={status}
      onMouseDown={(event) => props.onNodeDown(layout.node.id, event)}
      onContextMenu={(event) => props.onNodeContext(layout.node.id, event)}
    >
      <title>{[layout.spec.title, layout.summary].filter(Boolean).join(" · ")}</title>
      <circle className="flow-node-halo" cx={cx} cy={cy} r={r + RING} stroke={accent} />
      <circle
        className="flow-node-box"
        cx={cx}
        cy={cy}
        r={r}
        fill={accent}
        data-pin={port?.pin.name}
        // Not stopped: a drag of the node itself ends on the window's mouseup.
        onMouseUp={() => port && props.onPinUp(layout.node.id, port, "out")}
      />
      <Glyph glyph={glyph} x={cx - 14} y={cy - 14} scale={1.75} />
      <text className="flow-node-title under" x={cx} y={ROUND_D + 15} textAnchor="middle">
        {truncate(layout.spec.title, 20)}
      </text>
      {line && (
        <text className="flow-node-summary under" x={cx} y={ROUND_D + 29} textAnchor="middle">
          {truncate(line, 24)}
        </text>
      )}
      {port && (
        <Pin
          item={{ ...port, x: cx + Math.cos(aim) * r, y: cy + Math.sin(aim) * r }}
          direction="out"
          color={props.color(port.pin.type)}
          tint={props.tint(port.pin.name, "out")}
          filled={props.connected(port.pin.name, "out")}
          required={false}
          bare
          onDown={(event) => props.onPinDown(layout.node.id, port, "out", event)}
          onUp={() => props.onPinUp(layout.node.id, port, "out")}
        />
      )}
      {(hasError || hasWarning) && (
        <g
          className={`flow-node-problem ${hasError ? "error" : "warning"}`}
          transform={`translate(${cx + r * 0.72}, ${cy - r * 0.72})`}
        >
          <circle r={7} />
          <text y={3.5} textAnchor="middle">
            !
          </text>
        </g>
      )}
    </g>
  );
}

function Glyph({ glyph, x, y, scale }: { glyph: ReturnType<typeof glyphFor>; x: number; y: number; scale: number }) {
  return (
    <g className="flow-node-glyph" transform={`translate(${x}, ${y}) scale(${scale})`}>
      {glyph.d && <path className="stroke" d={glyph.d} />}
      {glyph.fill && <path className="fill" d={glyph.fill} />}
    </g>
  );
}

/** A loop's turn or an agent's name, on the node's top edge. */
function Badge({ text, x }: { text: string; x: number }) {
  const width = Math.min(90, text.length * 5.8 + 14);
  return (
    <g transform={`translate(${x - width / 2}, -8)`}>
      <rect className="flow-badge-pill" width={width} height={16} rx={8} ry={8} />
      <text className="flow-badge" x={width / 2} y={11.5} textAnchor="middle">
        {truncate(text, Math.floor((width - 10) / 5.8))}
      </text>
    </g>
  );
}

/** The second line: what the node is set to do. */
function summaryLine(layout: NodeLayout, professionWired: boolean): string {
  if (layout.spec.kind !== "profession" || !professionWired) return layout.summary;
  // A wired Profession node answers the dropdown, so the node stops naming one.
  return String(layout.node.data?.name ?? "").trim() || t("wired profession");
}

interface PinProps {
  item: PinLayout;
  direction: "in" | "out";
  color: string;
  /** Its wire's colour, which it takes over its type's. */
  tint?: string;
  filled: boolean;
  required: boolean;
  /** No name at all: a round node's connector is the only one it has. */
  bare?: boolean;
  onDown(event: React.MouseEvent): void;
  onUp(): void;
}

/**
 * A pin on the node's edge. A side pin is named only while pointed at or while
 * a wire is being pulled; one on the bottom edge, which a sub-node feeds, is
 * always named under it, as n8n names those.
 */
function Pin({ item, direction, color: own, tint, filled, required, bare, onDown, onUp }: PinProps) {
  const isExec = item.pin.type === "exec";
  const color = tint ?? own;
  // An exec pin's colour comes from CSS until a wire gives it one: inline wins.
  const worn = isExec && tint && filled ? { fill: tint } : undefined;
  const label = bare ? "" : item.pin.label || item.pin.name;
  const below = item.side === "bottom";
  const named = label && !(isExec && (label === "in" || label === "out"));
  return (
    // The name is beside the pin, not part of it: what takes the pointer is the dot alone.
    <>
      <g
        className={`flow-pin ${item.side}`}
        data-testid={`pin-${direction}-${item.pin.name}`}
        data-pin={item.pin.name}
        data-direction={direction}
        onMouseDown={(event) => {
          event.stopPropagation();
          onDown(event);
        }}
        onMouseUp={(event) => {
          event.stopPropagation();
          onUp();
        }}
      >
        <circle className="flow-pin-hit" cx={item.x} cy={item.y} r={9} fill={color} opacity={0} />
        {isExec && direction === "in" ? (
          // Where the flow comes in is a bar on the edge, the way n8n draws it.
          // Exec pins take their colour from CSS, not from the catalog: "white"
          // is invisible on a light theme.
          <rect
            className={`flow-pin-shape exec bar${filled ? " filled" : ""}`}
            x={item.x - 3.5}
            y={item.y - 8}
            width={7}
            height={16}
            rx={3.5}
            style={worn}
          />
        ) : isExec ? (
          <circle
            className={`flow-pin-shape exec${filled ? " filled" : ""}`}
            cx={item.x}
            cy={item.y}
            r={5.6}
            style={worn}
          />
        ) : below ? (
          // What a sub-node feeds is a diamond, as in n8n.
          <path
            className={`flow-pin-shape${filled ? " filled" : ""}`}
            d={`M ${item.x} ${item.y - 6} L ${item.x + 6} ${item.y} L ${item.x} ${item.y + 6} L ${item.x - 6} ${item.y} Z`}
            fill={filled ? color : undefined}
            stroke={color}
          />
        ) : item.pin.multi ? (
          // A pin that stacks wires is square, the way a Blueprint draws arrays.
          <rect
            className={`flow-pin-shape multi${filled ? " filled" : ""}`}
            x={item.x - 4.8}
            y={item.y - 4.8}
            width={9.6}
            height={9.6}
            rx={2.4}
            fill={filled ? color : undefined}
            stroke={color}
          />
        ) : (
          <circle
            className={`flow-pin-shape${filled ? " filled" : ""}`}
            cx={item.x}
            cy={item.y}
            r={5}
            fill={filled ? color : undefined}
            stroke={color}
          />
        )}
      </g>
      {named && (
        <text
          className={`flow-pin-label${below ? " port" : ""}`}
          x={below ? item.x : direction === "in" ? item.x - 11 : item.x + 11}
          y={below ? item.y + 18 : item.y + 3.6}
          textAnchor={below ? "middle" : direction === "in" ? "end" : "start"}
        >
          {label}
          {required ? "*" : ""}
        </text>
      )}
    </>
  );
}

/** A rotating arc: the one thing on the node that says "still alive". */
function Spinner({ x, y }: { x: number; y: number }) {
  return (
    <g className="flow-spinner" transform={`translate(${x}, ${y})`}>
      <circle className="flow-spinner-track" r={6} />
      <path className="flow-spinner-arc" d="M 0 -6 A 6 6 0 0 1 6 0" />
    </g>
  );
}

/** Done or failed, drawn where the spinner was. */
function Mark({ x, y, kind }: { x: number; y: number; kind: "done" | "failed" }) {
  return (
    <g className={`flow-mark ${kind}`} transform={`translate(${x}, ${y})`}>
      <circle r={7} />
      {kind === "done" ? (
        <path d="M -3.2 0.2 L -0.9 2.5 L 3.4 -2.4" />
      ) : (
        <path d="M -2.6 -2.6 L 2.6 2.6 M 2.6 -2.6 L -2.6 2.6" />
      )}
    </g>
  );
}

/**
 * What the node says while its agent works: the tool it is in, how many calls
 * deep it is, and how long it has been going.
 *
 * The step count is the part that matters - a detail line that never changes
 * looks identical to a hung run, and a number that keeps moving does not.
 */
export function activityLabel(agent: AgentRun): string {
  const parts = [agent.detail || "working"];
  if (agent.steps > 0) parts.push(`step ${agent.steps}`);
  if (agent.since > 0) parts.push(elapsed(Date.now() - agent.since));
  return parts.join(" · ");
}

/** Wall-clock since the agent was hired, short enough to sit on a node. */
export function elapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  return `${minutes}m ${String(total % 60).padStart(2, "0")}s`;
}

function truncate(text: string, max: number): string {
  if (max <= 1) return "";
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}
