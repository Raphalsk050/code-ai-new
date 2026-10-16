// What a graph says about itself, Unreal-style: comment boxes that name and
// group a part of the graph, and note bubbles pinned above single nodes.
// Neither runs; both are edited in place on the canvas.

import * as React from "react";

import { t } from "../i18n";
import { commentHeader, commentLook, NodeLayout, ResizeHandle, wrapText } from "./model";

const HANDLES: ResizeHandle[] = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];
// Rough advance of a character at 1px of font size.
const CHAR_EM = 0.56;
/** Below this size on screen, a comment's title is drawn larger so it still reads. */
const READABLE_PX = 12;

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CommentViewProps {
  layout: NodeLayout;
  /** Where the box is drawn: its own, or where a drag or resize has it. */
  box: Box;
  zoom: number;
  selected: boolean;
  problem: "error" | "warning" | null;
  editing: boolean;
  onHeaderDown(event: React.MouseEvent): void;
  onHandleDown(handle: ResizeHandle, event: React.MouseEvent): void;
  onContext(event: React.MouseEvent): void;
  onEdit(): void;
  onCommit(text: string): void;
  onCancel(): void;
}

/**
 * A comment box: a tinted body that lets clicks through to the canvas, and a
 * title bar that selects, drags and, double-clicked, edits it. Zoomed out, the
 * title grows so it stays readable; that is when a comment earns its keep.
 */
export function CommentView(props: CommentViewProps): JSX.Element {
  const { layout, box, zoom, selected, problem, editing } = props;
  const look = commentLook(layout.node);
  const fontSize = Math.max(look.fontSize, READABLE_PX / Math.max(zoom, 0.05));
  const header = Math.min(box.height, commentHeader(look.text, fontSize));
  const perLine = Math.max(1, Math.floor((box.width - 20) / (fontSize * CHAR_EM)));
  const lines = (look.text || " ").split("\n").map((line) => clip(line, perLine));
  // Grabbable at any zoom: a fixed size on screen, not in the graph.
  const grip = 8 / Math.max(zoom, 0.05);

  const classes = ["flow-comment"];
  if (selected) classes.push("selected");
  if (problem) classes.push(problem);

  return (
    <g
      className={classes.join(" ")}
      transform={`translate(${box.x}, ${box.y})`}
      data-node-id={layout.node.id}
      data-testid={`comment-${layout.node.id}`}
    >
      <rect
        className="flow-comment-halo"
        x={-3}
        y={-3}
        width={box.width + 6}
        height={box.height + 6}
        rx={9}
      />
      <rect
        className="flow-comment-body"
        width={box.width}
        height={box.height}
        rx={6}
        fill={look.color}
        stroke={look.color}
      />
      <path
        className="flow-comment-header"
        data-testid={`comment-header-${layout.node.id}`}
        d={headerPath(box.width, header)}
        fill={look.color}
        onMouseDown={props.onHeaderDown}
        onContextMenu={props.onContext}
        onDoubleClick={(event) => {
          event.stopPropagation();
          props.onEdit();
        }}
      />
      {editing ? (
        <InlineText
          x={8}
          y={4}
          width={box.width - 16}
          height={Math.max(header - 8, fontSize * 1.6)}
          fontSize={fontSize}
          value={look.text}
          testId="comment-edit"
          onCommit={props.onCommit}
          onCancel={props.onCancel}
        />
      ) : (
        <text className="flow-comment-title" fontSize={fontSize} x={10} y={7 + fontSize}>
          {lines.map((line, index) => (
            <tspan key={index} x={10} dy={index === 0 ? 0 : fontSize * 1.3}>
              {line}
            </tspan>
          ))}
        </text>
      )}
      {problem && (
        <g className={`flow-node-problem ${problem}`} transform={`translate(${box.width - 2}, 2)`}>
          <circle r={7} />
          <text y={3.5} textAnchor="middle">
            !
          </text>
        </g>
      )}
      {HANDLES.map((handle) => (
        <rect
          key={handle}
          className={`flow-comment-handle ${handle}`}
          data-testid={`comment-handle-${handle}`}
          {...handleRect(handle, box, grip)}
          onMouseDown={(event) => props.onHandleDown(handle, event)}
        />
      ))}
    </g>
  );
}

function handleRect(handle: ResizeHandle, box: Box, grip: number) {
  const corner = grip * 1.6;
  if (handle.length === 2) {
    return {
      x: handle.includes("e") ? box.width - corner / 2 : -corner / 2,
      y: handle.includes("s") ? box.height - corner / 2 : -corner / 2,
      width: corner,
      height: corner,
    };
  }
  if (handle === "n" || handle === "s") {
    return {
      x: corner / 2,
      y: (handle === "n" ? 0 : box.height) - grip / 2,
      width: Math.max(0, box.width - corner),
      height: grip,
    };
  }
  return {
    x: (handle === "w" ? 0 : box.width) - grip / 2,
    y: corner / 2,
    width: grip,
    height: Math.max(0, box.height - corner),
  };
}

function headerPath(width: number, height: number): string {
  const r = 6;
  return `M 0 ${height} L 0 ${r} Q 0 0 ${r} 0 L ${width - r} 0 Q ${width} 0 ${width} ${r} L ${width} ${height} Z`;
}

const BUBBLE_W = 240;
const BUBBLE_FONT = 11;
const BUBBLE_LINE = 15;
const BUBBLE_TAIL = 9;

export interface NoteBubbleProps {
  nodeId: string;
  /** The node's top-left corner and width, where it is drawn right now. */
  x: number;
  y: number;
  nodeWidth: number;
  text: string;
  editing: boolean;
  onDown(event: React.MouseEvent): void;
  onEdit(): void;
  onHide(): void;
  onCommit(text: string): void;
  onCancel(): void;
}

/** A node's note, in a speech bubble above it. Double-click edits it. */
export function NoteBubble(props: NoteBubbleProps): JSX.Element {
  const { x, y, text, editing } = props;
  const lines = wrapText(text, Math.floor((BUBBLE_W - 20) / (BUBBLE_FONT * CHAR_EM)), 12);
  const longest = Math.max(...lines.map((line) => line.length), 8);
  const width = editing ? BUBBLE_W : Math.min(BUBBLE_W, Math.ceil(longest * BUBBLE_FONT * CHAR_EM + 34));
  const height = editing ? 76 : lines.length * BUBBLE_LINE + 14;
  const top = y - height - BUBBLE_TAIL - 4;
  return (
    <g
      className="flow-bubble"
      transform={`translate(${x}, ${top})`}
      data-testid={`note-${props.nodeId}`}
      onMouseDown={props.onDown}
      onDoubleClick={(event) => {
        event.stopPropagation();
        props.onEdit();
      }}
    >
      <path className="flow-bubble-shape" d={bubblePath(width, height)} />
      {editing ? (
        <InlineText
          x={8}
          y={6}
          width={width - 16}
          height={height - 12}
          fontSize={BUBBLE_FONT}
          value={text}
          testId="note-edit"
          onCommit={props.onCommit}
          onCancel={props.onCancel}
        />
      ) : (
        <>
          <text className="flow-bubble-text" x={10} y={7 + BUBBLE_FONT}>
            {lines.map((line, index) => (
              <tspan key={index} x={10} dy={index === 0 ? 0 : BUBBLE_LINE}>
                {line}
              </tspan>
            ))}
          </text>
          <g
            className="flow-bubble-close"
            data-testid={`note-hide-${props.nodeId}`}
            transform={`translate(${width - 9}, 9)`}
            onMouseDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              props.onHide();
            }}
          >
            <title>{t("Hide the note")}</title>
            <circle r={6} />
            <path d="M -2.4 -2.4 L 2.4 2.4 M 2.4 -2.4 L -2.4 2.4" />
          </g>
        </>
      )}
    </g>
  );
}

function bubblePath(width: number, height: number): string {
  const r = 7;
  const tail = 22;
  return (
    `M ${r} 0 H ${width - r} Q ${width} 0 ${width} ${r} V ${height - r} ` +
    `Q ${width} ${height} ${width - r} ${height} H ${tail + 8} L ${tail} ${height + BUBBLE_TAIL} ` +
    `L ${tail - 2} ${height} H ${r} Q 0 ${height} 0 ${height - r} V ${r} Q 0 0 ${r} 0 Z`
  );
}

/** The mark above a node whose note is hidden: a click brings the bubble back. */
export function NoteMark({
  nodeId,
  x,
  y,
  onShow,
}: {
  nodeId: string;
  x: number;
  y: number;
  onShow(): void;
}): JSX.Element {
  return (
    <g
      className="flow-note-mark"
      data-testid={`note-show-${nodeId}`}
      transform={`translate(${x}, ${y})`}
      onMouseDown={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.stopPropagation();
        onShow();
      }}
    >
      <title>{t("Show the note")}</title>
      <path d="M -6 -5 H 6 V 3 H 0 L -3 6 V 3 H -6 Z" />
    </g>
  );
}

export interface InlineTextProps {
  x: number;
  y: number;
  width: number;
  height: number;
  fontSize: number;
  value: string;
  testId: string;
  onCommit(text: string): void;
  onCancel(): void;
}

/**
 * A text field drawn on the canvas, at the zoom the canvas has. Enter keeps
 * the text, Shift+Enter starts a new line, Escape leaves it as it was, and
 * clicking elsewhere keeps it.
 */
export function InlineText(props: InlineTextProps): JSX.Element {
  const [text, setText] = React.useState(props.value);
  const ref = React.useRef<HTMLTextAreaElement | null>(null);
  const settled = React.useRef(false);
  React.useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);
  const commit = () => {
    if (settled.current) return;
    settled.current = true;
    props.onCommit(text);
  };
  return (
    <foreignObject x={props.x} y={props.y} width={Math.max(40, props.width)} height={props.height}>
      <textarea
        ref={ref}
        className="flow-inline-edit"
        data-testid={props.testId}
        style={{ fontSize: props.fontSize }}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onBlur={commit}
        onMouseDown={(event) => event.stopPropagation()}
        onDoubleClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          event.stopPropagation();
          if (event.key === "Escape") {
            event.preventDefault();
            settled.current = true;
            props.onCancel();
          } else if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            commit();
          }
        }}
      />
    </foreignObject>
  );
}

function clip(text: string, max: number): string {
  if (max <= 1) return "";
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}
