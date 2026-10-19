import * as React from "react";

import { t } from "../i18n";
import type { Chip } from "./access";
import { glyphFor } from "./glyphs";
import { HEADER_H, NodeLayout, PinLayout, ROUND_D } from "./model";
import { OutputPanel } from "./output-panel";
import type { AgentRun, NodeRunStatus, OutputPart } from "./store";

const RADIUS = 14;
/** How far out the dashed ring of a selected or running node sits. */
const RING = 6;
const CHIP_H = 15;
// Rough advance of the 9.5px chip font; only used to decide what fits.
const CHIP_CHAR_W = 5.8;
const CHIP_PAD = 7;

export interface NodeViewProps {
  layout: NodeLayout;
  offset: { dx: number; dy: number };
  selected: boolean;
  status: NodeRunStatus;
  hasError: boolean;
  hasWarning: boolean;
  badge: string;
  /** Header tint. */
  accent: string;
  /** What the footer says, for the kinds that have one. */
  chips: Chip[];
  /** The agent working inside this node, while one is. */
  activity?: AgentRun;
  /** The node is open: its output panel, under the pins, at this height. */
  panel?: { top: number; height: number; parts: OutputPart[]; live: boolean; onClose?: () => void };
  color(type: string): string;
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

/** One node: a card in its kind's colour with its glyph, the pins, and an access footer. */
export function NodeView(props: NodeViewProps): JSX.Element {
  const { layout, offset, selected, status, hasError, hasWarning, badge, accent } = props;
  const width = layout.width;
  const height = layout.height;
  // An agent node can sit on one step for minutes. While it does, the footer
  // stops showing what it may do and shows what it is doing.
  const activity = status === "running" ? props.activity : undefined;
  const summary = summaryLine(layout, props.connected("profession", "in"));
  const glyph = glyphFor(layout.spec.kind);
  const [x, y] = layout.node.pos;

  const classes = ["flow-node"];
  if (selected) classes.push("selected");
  if (hasError) classes.push("error");
  else if (hasWarning) classes.push("warning");
  if (status !== "idle") classes.push(status);

  // The right end of the header: a status mark, then the badge pill left of it.
  const markRoom = status === "idle" ? 0 : 22;
  const badgeWidth = badge ? Math.min(90, badge.length * 5.8 + 14) : 0;
  const textRoom = width - 38 - 10 - markRoom - (badgeWidth ? badgeWidth + 6 : 0);

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
      <rect
        className="flow-node-halo"
        x={-RING}
        y={-RING}
        width={width + RING * 2}
        height={height + RING * 2}
        rx={RADIUS + RING}
        stroke={accent}
        style={{ color: accent }}
      />
      <rect className="flow-node-box" width={width} height={height} rx={RADIUS} ry={RADIUS} fill={accent} />
      <rect className="flow-node-sheen" width={width} height={height} rx={RADIUS} ry={RADIUS} fill="url(#flow-sheen)" />
      <line className="flow-node-divider" x1={12} y1={HEADER_H} x2={width - 12} y2={HEADER_H} />

      <g className="flow-node-glyph" transform="translate(10, 10)">
        <rect width={20} height={20} rx={6} ry={6} />
        <g transform="translate(2, 2)">
          {glyph.d && <path className="stroke" d={glyph.d} />}
          {glyph.fill && <path className="fill" d={glyph.fill} />}
        </g>
      </g>
      <text className="flow-node-title" x={38} y={summary ? 18 : 24.5}>
        {truncate(layout.spec.title, Math.floor(textRoom / 6.6))}
      </text>
      {summary && (
        <text className="flow-node-summary" x={38} y={32}>
          {truncate(summary, Math.floor(textRoom / 5.6))}
        </text>
      )}

      {badge && (
        <g transform={`translate(${width - 10 - markRoom - badgeWidth}, 12)`}>
          <rect className="flow-badge-pill" width={badgeWidth} height={16} rx={8} ry={8} />
          <text className="flow-badge" x={badgeWidth / 2} y={11.5} textAnchor="middle">
            {truncate(badge, Math.floor((badgeWidth - 10) / 5.8))}
          </text>
        </g>
      )}
      {status === "running" && <Spinner x={width - 20} y={20} />}
      {status === "done" && <Mark x={width - 20} y={20} kind="done" />}
      {status === "failed" && <Mark x={width - 20} y={20} kind="failed" />}

      {layout.inputs.map((item) => (
        <Pin
          key={`in-${item.pin.name}`}
          item={item}
          direction="in"
          width={width}
          color={props.color(item.pin.type)}
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
          width={width}
          color={props.color(item.pin.type)}
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

      {layout.footer > 0 && (
        <g transform={`translate(0, ${height - layout.footer})`}>
          <path className="flow-node-footer" d={footerPath(width, layout.footer)} />
          {activity ? (
            <text
              className="flow-node-activity"
              x={10}
              y={layout.footer / 2 + 3.5}
              data-testid={`activity-${layout.node.id}`}
            >
              {truncate(activityLabel(activity), 36)}
            </text>
          ) : (
            <Chips chips={props.chips} width={width - 16} y={(layout.footer - CHIP_H) / 2} />
          )}
        </g>
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
 * A node that holds a value - a model, a variable - as a circle with its name
 * underneath. Its connector rides the rim, facing wherever its wires go, or the
 * pointer while one is being pulled out of it. A wire dropped anywhere on the
 * circle lands on that connector.
 */
export function RoundNodeView(props: RoundNodeViewProps): JSX.Element {
  const { layout, offset, selected, status, hasError, hasWarning, accent, aim } = props;
  const r = ROUND_D / 2;
  const cx = layout.width / 2;
  const cy = r;
  const glyph = glyphFor(layout.spec.kind);
  const [x, y] = layout.node.pos;
  const port = layout.outputs[0];

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
      <circle className="flow-node-halo" cx={cx} cy={cy} r={r + RING} stroke={accent} style={{ color: accent }} />
      <g
        className="flow-round-body"
        data-pin={port?.pin.name}
        // Not stopped: a drag of the node itself ends on the window's mouseup.
        onMouseUp={() => port && props.onPinUp(layout.node.id, port, "out")}
      >
        <circle className="flow-node-box" cx={cx} cy={cy} r={r} fill={accent} />
        <circle className="flow-node-sheen" cx={cx} cy={cy} r={r} fill="url(#flow-sheen)" />
      </g>
      <g className="flow-node-glyph big" transform={`translate(${cx - 14}, ${cy - 14}) scale(1.75)`}>
        {glyph.d && <path className="stroke" d={glyph.d} />}
        {glyph.fill && <path className="fill" d={glyph.fill} />}
      </g>
      <text className="flow-round-title" x={cx} y={ROUND_D + 15} textAnchor="middle">
        {truncate(layout.spec.title, 20)}
      </text>
      {layout.summary && (
        <text className="flow-round-summary" x={cx} y={ROUND_D + 29} textAnchor="middle">
          {truncate(layout.summary, 24)}
        </text>
      )}
      {port && (
        <Pin
          item={{ ...port, x: cx + Math.cos(aim) * r, y: cy + Math.sin(aim) * r }}
          direction="out"
          width={layout.width}
          color={props.color(port.pin.type)}
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

/** The header's second line: what the node is set to do. */
function summaryLine(layout: NodeLayout, professionWired: boolean): string {
  if (layout.spec.kind !== "profession" || !professionWired) return layout.summary;
  // A wired Profession node answers the dropdown, so the node stops naming one.
  return String(layout.node.data?.name ?? "").trim() || t("wired profession");
}

function Chips({ chips, width, y }: { chips: Chip[]; width: number; y: number }) {
  let cursor = 8;
  return (
    <g className="flow-chips-row">
      {fitChips(chips, width).map((chip, index) => {
        const chipWidth = widthOf(chip.label);
        const at = cursor;
        cursor += chipWidth + 4;
        return (
          <g key={`${chip.label}-${index}`} transform={`translate(${at}, ${y})`}>
            {chip.title && <title>{chip.title}</title>}
            <rect
              className={`flow-node-chip ${chip.tone}`}
              width={chipWidth}
              height={CHIP_H}
              rx={CHIP_H / 2}
              ry={CHIP_H / 2}
            />
            <text className={`flow-node-chip-text ${chip.tone}`} x={chipWidth / 2} y={10.6} textAnchor="middle">
              {chip.label}
            </text>
          </g>
        );
      })}
    </g>
  );
}

function widthOf(label: string): number {
  return Math.ceil(label.length * CHIP_CHAR_W + CHIP_PAD * 2);
}

/** As many chips as fit, then one "+N" for the rest. */
export function fitChips(chips: Chip[], width: number): Chip[] {
  const out: Chip[] = [];
  let used = 0;
  for (let index = 0; index < chips.length; index += 1) {
    const rest = chips.length - index - 1;
    const need = widthOf(chips[index].label) + (rest > 0 ? widthOf(`+${rest}`) + 8 : 0);
    if (used + need > width) {
      out.push({ label: `+${chips.length - index}`, tone: "muted" });
      return out;
    }
    out.push(chips[index]);
    used += widthOf(chips[index].label) + 4;
  }
  return out;
}

interface PinProps {
  item: PinLayout;
  direction: "in" | "out";
  width: number;
  color: string;
  filled: boolean;
  required: boolean;
  /** No label: a round node's connector is the only one it has. */
  bare?: boolean;
  onDown(event: React.MouseEvent): void;
  onUp(): void;
}

function Pin({ item, direction, width, color, filled, required, bare, onDown, onUp }: PinProps) {
  const isExec = item.pin.type === "exec";
  const label = bare ? "" : item.pin.label || item.pin.name;
  const labelX = direction === "in" ? 13 : width - 13;
  return (
    <g
      className="flow-pin"
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
        />
      ) : isExec ? (
        <circle className={`flow-pin-shape exec${filled ? " filled" : ""}`} cx={item.x} cy={item.y} r={5.6} />
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
      {label && !(isExec && (label === "in" || label === "out")) && (
        <text
          className="flow-pin-label"
          x={labelX}
          y={item.y + 3.6}
          textAnchor={direction === "in" ? "start" : "end"}
        >
          {label}
          {required ? "*" : ""}
        </text>
      )}
    </g>
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

/** The footer strip, rounded at the bottom to match the box. */
function footerPath(width: number, height: number): string {
  const r = RADIUS;
  return (
    `M 0 0 L ${width} 0 L ${width} ${height - r} Q ${width} ${height} ${width - r} ${height} ` +
    `L ${r} ${height} Q 0 ${height} 0 ${height - r} Z`
  );
}

function truncate(text: string, max: number): string {
  if (max <= 1) return "";
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}
