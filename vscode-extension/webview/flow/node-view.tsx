import * as React from "react";

import { CATEGORY_COLORS, HEADER_H, NodeLayout, PinLayout } from "./model";
import type { NodeRunStatus } from "./store";

export interface NodeViewProps {
  layout: NodeLayout;
  offset: { dx: number; dy: number };
  selected: boolean;
  status: NodeRunStatus;
  hasError: boolean;
  hasWarning: boolean;
  badge: string;
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

/** One node: header, summary line, and its pins down both sides. */
export function NodeView(props: NodeViewProps): JSX.Element {
  const { layout, offset, selected, status, hasError, badge } = props;
  const [x, y] = layout.node.pos;
  const classes = ["flow-node"];
  if (selected) classes.push("selected");
  if (hasError) classes.push("error");
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
      <rect
        className="flow-node-box"
        width={layout.width}
        height={layout.height}
        rx={7}
        ry={7}
      />
      <path
        d={headerPath(layout.width)}
        fill={CATEGORY_COLORS[layout.spec.category] ?? CATEGORY_COLORS.flow}
        opacity={0.92}
      />
      <text className="flow-node-title" x={10} y={19}>
        {layout.spec.title}
      </text>
      {badge && (
        <text className="flow-badge" x={layout.width - 8} y={19} textAnchor="end">
          {badge}
        </text>
      )}
      {layout.summary && (
        <text className="flow-node-summary" x={10} y={layout.height - 8}>
          {truncate(layout.summary, 30)}
        </text>
      )}

      {layout.inputs.map((item) => (
        <Pin
          key={`in-${item.pin.name}`}
          item={item}
          direction="in"
          width={layout.width}
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
          width={layout.width}
          color={props.color(item.pin.type)}
          filled={props.connected(item.pin.name, "out")}
          required={false}
          onDown={(event) => props.onPinDown(layout.node.id, item, "out", event)}
          onUp={() => props.onPinUp(layout.node.id, item, "out")}
        />
      ))}
    </g>
  );
}

interface PinProps {
  item: PinLayout;
  direction: "in" | "out";
  width: number;
  color: string;
  filled: boolean;
  required: boolean;
  onDown(event: React.MouseEvent): void;
  onUp(): void;
}

function Pin({ item, direction, width, color, filled, required, onDown, onUp }: PinProps) {
  const isExec = item.pin.type === "exec";
  const label = item.pin.label || item.pin.name;
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
      {isExec ? (
        // Exec pins take their colour from CSS, not from the catalog: "white"
        // is invisible on a light theme.
        <path
          className={`flow-pin-shape exec${filled ? " filled" : ""}`}
          d={execPin(item.x, item.y)}
          strokeWidth={1.6}
        />
      ) : (
        <circle
          className="flow-pin-shape"
          cx={item.x}
          cy={item.y}
          r={4.6}
          fill={filled ? color : "none"}
          stroke={color}
          strokeWidth={1.6}
        />
      )}
      {label && !(isExec && (label === "in" || label === "out")) && (
        <text
          className="flow-pin-label"
          x={labelX}
          y={item.y + 3.4}
          textAnchor={direction === "in" ? "start" : "end"}
        >
          {label}
          {required ? "*" : ""}
        </text>
      )}
    </g>
  );
}

/** A rounded top edge, so the header tint follows the box's corners. */
function headerPath(width: number): string {
  const r = 7;
  return (
    `M 0 ${HEADER_H} L 0 ${r} Q 0 0 ${r} 0 L ${width - r} 0 ` +
    `Q ${width} 0 ${width} ${r} L ${width} ${HEADER_H} Z`
  );
}

/** Exec pins are arrows, the way a Blueprint draws flow. */
function execPin(x: number, y: number): string {
  const w = 5;
  const h = 6;
  return `M ${x - w} ${y - h} L ${x + w} ${y} L ${x - w} ${y + h} Z`;
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}
