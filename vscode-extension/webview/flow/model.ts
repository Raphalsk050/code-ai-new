// Geometry and catalog lookups: everything the canvas needs to know *where*
// something is. Kept apart from React so it can be unit-tested without a DOM.

import { t, tf } from "../i18n";
import type {
  FlowEdge,
  FlowGraph,
  FlowNode,
  GraphCatalog,
  NodeKindSpec,
  PinSpec,
} from "../../src/flow-schema";

/**
 * A node is a square with its glyph and nothing else, its name under it, as
 * n8n draws them. Pins sit on the edges, spread out, unlabelled until pointed
 * at; what a sub-node feeds (a model, a profession, tools) comes in from below.
 */
export const NODE_W = 100;
/** A node fed from below is wide, with its name inside, and its ports labelled under it. */
export const WIDE_W = 200;
const PIN_GAP = 20;
const PORT_GAP = 72;
/** Room under a square for its name and its one line of what it does. */
export const UNDER_H = 34;
/** Room under a wide node for its ports' names. */
export const PORT_LABEL_H = 18;
const BELOW_TYPES = new Set(["model", "profession", "tools"]);
export const GRID = 20;

/** Category -> header tint. Reads as "what kind of work is this node". */
export const CATEGORY_COLORS: Record<string, string> = {
  flow: "#475569",
  agent: "#2563eb",
  data: "#7e22ce",
  debug: "#a16207",
  math: "#4d7c0f",
  logic: "#9d174d",
  text: "#a21caf",
  convert: "#57534e",
  note: "#64748b",
};

/** How many copies of a dynamic pin a node asked for, as the backend counts them. */
export function dynamicCount(node: FlowNode, direction: "in" | "out"): number {
  const raw = direction === "out" ? node.data?.outputs : node.data?.inputs;
  return Math.max(1, Math.min(12, Number(raw ?? 2) || 2));
}

/** Kinds recognisable on sight get their own tint; the rest take the category's. */
export const KIND_COLORS: Record<string, string> = {
  start: "#15803d",
  end: "#b91c1c",
  model: "#0369a1",
  profession: "#2563eb",
  clean_agent: "#1e40af",
  custom_profession: "#c2410c",
  tools: "#4338ca",
  user_chat: "#0f766e",
  print: "#a16207",
};

/**
 * Kinds that hold a value rather than take a step: drawn as circles with the
 * name under them, as n8n draws its sub-nodes, and their one connector turns
 * around the circle to face where its wire goes.
 */
const ROUND_KINDS = new Set(["model", "var_get", "custom_profession", "tools"]);
export const ROUND_D = 72;
/** Room under the circle for its name and what it holds. */
export const ROUND_LABEL_H = 36;
export const ROUND_W = 136;

export function isRound(kind: string): boolean {
  return ROUND_KINDS.has(kind);
}

/** The middle of a round node's circle, in world space. */
export function roundCenter(layout: NodeLayout): Point {
  return { x: layout.node.pos[0] + layout.width / 2, y: layout.node.pos[1] + ROUND_D / 2 };
}

/** The angle a round node's connector faces: towards the middle of where its wires go. */
export function aimAt(center: Point, targets: Point[]): number {
  if (targets.length === 0) return 0;
  const x = targets.reduce((sum, point) => sum + point.x, 0) / targets.length;
  const y = targets.reduce((sum, point) => sum + point.y, 0) / targets.length;
  if (Math.abs(x - center.x) < 0.5 && Math.abs(y - center.y) < 0.5) return 0;
  return Math.atan2(y - center.y, x - center.x);
}

export function accentOf(spec: NodeKindSpec): string {
  return KIND_COLORS[spec.kind] ?? CATEGORY_COLORS[spec.category] ?? CATEGORY_COLORS.flow;
}

export interface PinLayout {
  pin: PinSpec;
  /** Local offset from the node's own origin. */
  x: number;
  y: number;
  /** The edge it sits on, which is the way its wire goes. */
  side: "left" | "right" | "bottom";
}

export interface NodeLayout {
  node: FlowNode;
  spec: NodeKindSpec;
  width: number;
  height: number;
  inputs: PinLayout[];
  outputs: PinLayout[];
  summary: string;
  /** Always 0: what an agent may touch is the inspector's to say, not the canvas's. */
  footer: number;
  /** The body's height before it opens to show what it wrote; the panel starts there. */
  body: number;
  /** How far under the body its text reaches: its name, or its ports' names. */
  under: number;
}

/** A wide node: one fed from below, with its name inside rather than under it. */
export function isWide(layout: NodeLayout): boolean {
  return layout.inputs.some((item) => item.side === "bottom");
}

/** The pins a node actually shows, after dynamic pins are expanded. */
export function pinsOf(spec: NodeKindSpec, node: FlowNode): PinSpec[] {
  const pins: PinSpec[] = [];
  // An int Math node is float in the catalog; its pins follow the node's type.
  const intMath = node.kind === "math" && String(node.data?.type ?? "float") === "int";
  for (const pin of spec.pins) {
    if (!pin.dynamic) {
      pins.push(intMath && pin.type === "float" ? { ...pin, type: "int" } : pin);
      continue;
    }
    // A dynamic declaration stands for however many the node asked for; the
    // first declaration expands and the rest are its siblings.
    if (!pin.name.endsWith("_0")) continue;
    const count = dynamicCount(node, pin.direction);
    const base = pin.name.slice(0, -2);
    for (let index = 0; index < count; index += 1) {
      const label = pin.label.includes("#")
        ? pin.label.split("#").join(String(index))
        : String(index);
      pins.push({ ...pin, name: `${base}_${index}`, label });
    }
  }
  return pins;
}

function execFirst(pins: PinSpec[]): PinSpec[] {
  return [...pins.filter((p) => p.type === "exec"), ...pins.filter((p) => p.type !== "exec")];
}

// -- comments --------------------------------------------------------------

/** A comment box's bounds and defaults; the backend states the same (models.py). */
export const COMMENT_MIN = 80;
export const COMMENT_MAX = 20000;
export const COMMENT_COLOR = "#64748b";
export const COMMENT_FONT = 18;
/** Room left around the nodes a new comment is drawn around. */
export const COMMENT_PAD = 30;
/** The swatches the inspector offers, Unreal's comment colours tuned for both themes. */
export const COMMENT_SWATCHES = [
  "#64748b",
  "#2563eb",
  "#0d9488",
  "#16a34a",
  "#ca8a04",
  "#ea580c",
  "#dc2626",
  "#9333ea",
];

export function isAnnotation(spec: NodeKindSpec | undefined): boolean {
  return Boolean(spec?.annotation);
}

function bounded(value: unknown, fallback: number, low: number, high: number): number {
  const number = Number(value);
  return Number.isFinite(number) ? Math.min(high, Math.max(low, number)) : fallback;
}

/** What a comment's text and size come to, read defensively from a hand-edited file. */
export function commentLook(node: FlowNode): {
  text: string;
  color: string;
  fontSize: number;
  width: number;
  height: number;
  groups: boolean;
} {
  const data = node.data ?? {};
  const color = String(data.color ?? "");
  return {
    text: String(data.title ?? ""),
    color: /^#[0-9a-fA-F]{6}$/.test(color) ? color : COMMENT_COLOR,
    fontSize: bounded(data.font_size, COMMENT_FONT, 10, 48),
    width: bounded(data.width, 400, COMMENT_MIN, COMMENT_MAX),
    height: bounded(data.height, 240, COMMENT_MIN, COMMENT_MAX),
    groups: String(data.move_mode ?? "group") !== "comment",
  };
}

/** The title bar's height for a comment's text, at the size it is drawn. */
export function commentHeader(text: string, fontSize: number): number {
  const lines = Math.max(1, text.split("\n").length);
  return Math.round(lines * fontSize * 1.3 + 14);
}

function contains(outer: Bounds, inner: Bounds): boolean {
  return (
    inner.minX >= outer.minX &&
    inner.minY >= outer.minY &&
    inner.maxX <= outer.maxX &&
    inner.maxY <= outer.maxY
  );
}

export function boundsOf(layout: NodeLayout): Bounds {
  const [x, y] = layout.node.pos;
  return { minX: x, minY: y, maxX: x + layout.width, maxY: y + layout.height };
}

/** Every node a comment holds: what lies wholly inside its box, nested comments included. */
export function nodesInside(layouts: Map<string, NodeLayout>, commentId: string): string[] {
  const comment = layouts.get(commentId);
  if (!comment || !isAnnotation(comment.spec)) return [];
  const box = boundsOf(comment);
  const inside: string[] = [];
  for (const [id, layout] of layouts) {
    if (id !== commentId && contains(box, boundsOf(layout))) inside.push(id);
  }
  return inside;
}

/**
 * What a drag of `ids` moves: the nodes themselves, plus what each comment
 * among them holds, unless that comment is set to move alone.
 */
export function movedBy(layouts: Map<string, NodeLayout>, ids: string[]): string[] {
  const moved = new Set(ids);
  for (const id of ids) {
    const layout = layouts.get(id);
    if (!layout || !isAnnotation(layout.spec) || !commentLook(layout.node).groups) continue;
    for (const inner of nodesInside(layouts, id)) moved.add(inner);
  }
  return [...moved];
}

/**
 * The order comments are drawn in, back to front.
 *
 * Of two that overlap, one that starts right of the other's middle is laid on
 * top of it, so it goes in front. Otherwise the bigger goes behind, which keeps
 * a comment nested in another reachable.
 */
export function commentOrder<T extends { id: string; box: Bounds }>(comments: T[]): T[] {
  const area = (item: T) => (item.box.maxX - item.box.minX) * (item.box.maxY - item.box.minY);
  const bySize = (a: T, b: T) =>
    area(b) - area(a) || a.box.minX - b.box.minX || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const behind = (a: T, b: T): boolean => {
    if (b.box.minX > (a.box.minX + a.box.maxX) / 2) return true;
    if (a.box.minX > (b.box.minX + b.box.maxX) / 2) return false;
    return bySize(a, b) < 0;
  };
  // Only overlapping pairs constrain each other; the rest keep the size order.
  const fallback = [...comments].sort(bySize);
  const waiting = new Map(fallback.map((item) => [item.id, 0]));
  const fronts = new Map<string, T[]>(fallback.map((item) => [item.id, []]));
  for (let i = 0; i < fallback.length; i += 1) {
    for (let j = i + 1; j < fallback.length; j += 1) {
      const a = fallback[i];
      const b = fallback[j];
      if (!overlaps(a.box, b.box)) continue;
      const [back, front] = behind(a, b) ? [a, b] : [b, a];
      fronts.get(back.id)!.push(front);
      waiting.set(front.id, waiting.get(front.id)! + 1);
    }
  }
  const out: T[] = [];
  const done = new Set<string>();
  while (out.length < fallback.length) {
    // Three comments can disagree in a circle; the size order breaks the tie.
    const next =
      fallback.find((item) => !done.has(item.id) && waiting.get(item.id) === 0) ??
      fallback.find((item) => !done.has(item.id))!;
    done.add(next.id);
    out.push(next);
    for (const front of fronts.get(next.id)!) waiting.set(front.id, waiting.get(front.id)! - 1);
  }
  return out;
}

function overlaps(a: Bounds, b: Bounds): boolean {
  return a.minX < b.maxX && b.minX < a.maxX && a.minY < b.maxY && b.minY < a.maxY;
}

/** Where a comment drawn around `ids` goes: their bounds, padded, with room for the title. */
export function commentAround(
  layouts: Map<string, NodeLayout>,
  ids: string[],
  fontSize = COMMENT_FONT
): { pos: [number, number]; width: number; height: number } | null {
  const picked = ids.map((id) => layouts.get(id)).filter((item): item is NodeLayout => !!item);
  if (picked.length === 0) return null;
  // An open note bubble sits above its node: the title must clear it.
  const withNote = (layout: NodeLayout): Bounds => {
    const bounds = boundsOf(layout);
    return { ...bounds, minY: bounds.minY - noteHeight(layout.node) };
  };
  const box = picked.map(withNote).reduce((a, b) => ({
    minX: Math.min(a.minX, b.minX),
    minY: Math.min(a.minY, b.minY),
    maxX: Math.max(a.maxX, b.maxX),
    maxY: Math.max(a.maxY, b.maxY),
  }));
  const header = commentHeader("", fontSize);
  const x = snap(box.minX - COMMENT_PAD);
  const y = snap(box.minY - COMMENT_PAD - header);
  return {
    pos: [x, y],
    width: Math.max(COMMENT_MIN, Math.ceil(box.maxX + COMMENT_PAD - x)),
    height: Math.max(COMMENT_MIN, Math.ceil(box.maxY + COMMENT_PAD - y)),
  };
}

/** Bubble geometry, shared with notes.tsx so a comment can make room for it. */
export const BUBBLE_W = 240;
export const BUBBLE_FONT = 11;
export const BUBBLE_LINE = 15;
export const BUBBLE_TAIL = 9;
const BUBBLE_CHARS = Math.floor((BUBBLE_W - 20) / (BUBBLE_FONT * 0.56));

/** How far a node's open note bubble reaches above it; 0 when it has none showing. */
export function noteHeight(node: FlowNode): number {
  const note = String(node.data?.note ?? "");
  if (!note.trim() || node.data?.note_shown === false) return 0;
  return wrapText(note, BUBBLE_CHARS, 12).length * BUBBLE_LINE + 14 + BUBBLE_TAIL + 4;
}

export type ResizeHandle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

/** A comment's box after dragging `handle` by (dx, dy), never smaller than the minimum. */
export function resized(
  start: { x: number; y: number; width: number; height: number },
  handle: ResizeHandle,
  dx: number,
  dy: number
): { x: number; y: number; width: number; height: number } {
  let { x, y, width, height } = start;
  if (handle.includes("e")) width = start.width + dx;
  if (handle.includes("s")) height = start.height + dy;
  if (handle.includes("w")) {
    width = start.width - dx;
    x = start.x + dx;
  }
  if (handle.includes("n")) {
    height = start.height - dy;
    y = start.y + dy;
  }
  if (width < COMMENT_MIN) {
    if (handle.includes("w")) x -= COMMENT_MIN - width;
    width = COMMENT_MIN;
  }
  if (height < COMMENT_MIN) {
    if (handle.includes("n")) y -= COMMENT_MIN - height;
    height = COMMENT_MIN;
  }
  return { x, y, width: Math.min(width, COMMENT_MAX), height: Math.min(height, COMMENT_MAX) };
}

/** Lines of `text` at most `perLine` characters wide, breaking at spaces where it can. */
export function wrapText(text: string, perLine: number, maxLines: number): string[] {
  const out: string[] = [];
  for (const paragraph of text.split("\n")) {
    let line = "";
    for (const word of paragraph.split(/\s+/)) {
      if (!word) continue;
      let rest = word;
      while (rest.length > perLine) {
        if (line) {
          out.push(line);
          line = "";
        }
        out.push(rest.slice(0, perLine));
        rest = rest.slice(perLine);
      }
      if (!line) line = rest;
      else if (line.length + 1 + rest.length <= perLine) line += ` ${rest}`;
      else {
        out.push(line);
        line = rest;
      }
    }
    out.push(line);
  }
  if (out.length <= maxLines) return out;
  const kept = out.slice(0, maxLines);
  kept[maxLines - 1] = `${kept[maxLines - 1].slice(0, Math.max(0, perLine - 1))}…`;
  return kept;
}

export function layoutNode(node: FlowNode, spec: NodeKindSpec): NodeLayout {
  if (isAnnotation(spec)) {
    const look = commentLook(node);
    return {
      node,
      spec,
      width: look.width,
      height: look.height,
      inputs: [],
      outputs: [],
      summary: "",
      footer: 0,
      body: look.height,
      under: 0,
    };
  }
  const pins = pinsOf(spec, node);
  const inputs = execFirst(pins.filter((pin) => pin.direction === "in"));
  const outputs = execFirst(pins.filter((pin) => pin.direction === "out"));
  const summary = summaryOf(node, spec);
  if (isRound(node.kind)) {
    // Where the connectors rest; pinAnchor turns an output to face its wires.
    const middle = ROUND_W / 2;
    const height = ROUND_D + ROUND_LABEL_H;
    return {
      node,
      spec,
      width: ROUND_W,
      height,
      inputs: inputs.map((pin) => ({ pin, x: middle - ROUND_D / 2, y: ROUND_D / 2, side: "left" })),
      outputs: outputs.map((pin) => ({ pin, x: middle + ROUND_D / 2, y: ROUND_D / 2, side: "right" })),
      summary,
      footer: 0,
      body: height,
      under: 0,
    };
  }
  const below = inputs.filter((pin) => BELOW_TYPES.has(pin.type));
  const left = inputs.filter((pin) => !BELOW_TYPES.has(pin.type));
  const width = below.length ? Math.max(WIDE_W, below.length * PORT_GAP + 24) : NODE_W;
  const height = Math.max(NODE_W, Math.max(left.length, outputs.length) * PIN_GAP + 24);
  // Down the middle of an edge, the flow first.
  const spread = (list: PinSpec[], x: number, side: "left" | "right"): PinLayout[] =>
    list.map((pin, index) => ({ pin, x, y: height / 2 + (index - (list.length - 1) / 2) * PIN_GAP, side }));
  return {
    node,
    spec,
    width,
    height,
    inputs: [
      ...spread(left, 0, "left"),
      ...below.map((pin, index): PinLayout => ({
        pin,
        x: (width * (index + 0.5)) / below.length,
        y: height,
        side: "bottom",
      })),
    ],
    outputs: spread(outputs, width, "right"),
    summary,
    footer: 0,
    body: height,
    under: below.length ? PORT_LABEL_H : UNDER_H,
  };
}

/** The one line a node shows under its title: what it is set to do. */
export function summaryOf(node: FlowNode, spec: NodeKindSpec): string {
  const data = node.data ?? {};
  switch (node.kind) {
    case "model": {
      const model = String(data.model ?? "").trim();
      const url = String(data.base_url ?? "").trim();
      if (model && url) return `${model} @ ${shortUrl(url)}`;
      return model || (url ? shortUrl(url) : t("session model"));
    }
    case "profession": {
      const name = String(data.name ?? "").trim();
      if (name) return name;
      const profession = String(data.profession ?? "").trim();
      return profession ? profession.replace(/_/g, " ") : t("custom agent");
    }
    case "clean_agent":
      return String(data.name ?? "").trim() || t("clean agent");
    case "tools": {
      const name = String(data.name ?? "").trim();
      const count = Array.isArray(data.tools) ? data.tools.length : 0;
      return name ? `${name} · ${count}` : tf("{0} tools", count);
    }
    case "custom_profession": {
      const name = String(data.name ?? "").trim();
      return name || t("name this profession");
    }
    case "loop": {
      const kind = String(data.kind ?? "count");
      if (kind === "count") return `count x${Number(data.count ?? 0) || 0}`;
      return kind;
    }
    case "decision":
      return String(data.mode ?? "bool");
    case "var_set":
    case "var_get":
      return String(data.variable ?? "") || t("choose a variable");
    case "print":
      return String(data.label ?? "");
    case "user_chat":
      return String(data.title ?? "") || t("ask the user");
    case "math":
      return `a ${MATH_SIGNS[String(data.op ?? "add")] ?? "?"} b${
        String(data.type ?? "float") === "int" ? " · int" : ""
      }`;
    case "compare":
      return `a ${COMPARE_SIGNS[String(data.op ?? "equal")] ?? "?"} b`;
    case "logic":
      return String(data.op ?? "and") === "not" ? "NOT a" : `a ${String(data.op ?? "and").toUpperCase()} b`;
    case "select":
      return t("condition ? true : false");
    case "append":
      return tf("{0} parts", dynamicCount(node, "in"));
    case "format":
      return String(data.template ?? "").split("\n")[0] || t("write a template");
    case "text_case":
    case "text_check":
      return t(String(data.op ?? "").replace(/_/g, " "));
    case "text_replace":
      return `${String(data.find ?? "") || "…"} → ${String(data.replace_with ?? "")}`;
    case "split":
      return `${t("at")} "${String(data.separator ?? ",")}"`;
    case "to_int":
      return t(String(data.mode ?? "truncate"));
    default:
      return "";
  }
}

const MATH_SIGNS: Record<string, string> = {
  add: "+",
  subtract: "−",
  multiply: "×",
  divide: "÷",
  modulo: "%",
  power: "^",
  min: "min",
  max: "max",
};

const COMPARE_SIGNS: Record<string, string> = {
  equal: "==",
  not_equal: "!=",
  less: "<",
  less_equal: "<=",
  greater: ">",
  greater_equal: ">=",
};

function shortUrl(url: string): string {
  return url.replace(/^https?:\/\//, "").replace(/\/v1\/?$/, "");
}

export interface Catalog {
  raw: GraphCatalog;
  kinds: Map<string, NodeKindSpec>;
  color(type: string): string;
}

export function indexCatalog(raw: GraphCatalog): Catalog {
  const kinds = new Map(raw.node_kinds.map((spec) => [spec.kind, spec]));
  return {
    raw,
    kinds,
    color: (type: string) => raw.pin_colors[type] ?? raw.pin_colors.any ?? "#A0A0A0",
  };
}

export function layoutGraph(graph: FlowGraph, catalog: Catalog): Map<string, NodeLayout> {
  const layouts = new Map<string, NodeLayout>();
  for (const node of graph.nodes) {
    const spec = catalog.kinds.get(node.kind);
    if (!spec) continue;
    layouts.set(node.id, layoutNode(node, spec));
  }
  return layouts;
}

export interface Point {
  x: number;
  y: number;
}

/** The way a wire leaves or enters a pin, as a unit vector. */
export interface Anchor extends Point {
  dx: number;
  dy: number;
}

/** Each round node's connector angle, in radians; one that is missing faces right. */
export type Aims = Map<string, number>;

/**
 * Where one pin is in world space, and which way its wire goes. A round node's
 * output sits on its circle at the angle `aims` gives it, and leaves outwards.
 */
export function pinAnchor(
  layouts: Map<string, NodeLayout>,
  nodeId: string,
  pinName: string,
  direction: "in" | "out",
  aims?: Aims
): Anchor | null {
  const layout = layouts.get(nodeId);
  if (!layout) return null;
  const list = direction === "in" ? layout.inputs : layout.outputs;
  const found = list.find((item) => item.pin.name === pinName);
  if (!found) return null;
  if (isRound(layout.node.kind) && direction === "out") {
    const angle = aims?.get(nodeId) ?? 0;
    const center = roundCenter(layout);
    const dx = Math.cos(angle);
    const dy = Math.sin(angle);
    return { x: center.x + dx * (ROUND_D / 2), y: center.y + dy * (ROUND_D / 2), dx, dy };
  }
  const way = found.side === "bottom" ? { dx: 0, dy: 1 } : found.side === "left" ? LEFT : RIGHT;
  return { x: layout.node.pos[0] + found.x, y: layout.node.pos[1] + found.y, ...way };
}

/** Absolute position of one pin, for drawing wires. */
export function pinPoint(
  layouts: Map<string, NodeLayout>,
  nodeId: string,
  pinName: string,
  direction: "in" | "out",
  aims?: Aims
): Point | null {
  const anchor = pinAnchor(layouts, nodeId, pinName, direction, aims);
  return anchor && { x: anchor.x, y: anchor.y };
}

const RIGHT = { dx: 1, dy: 0 };
const LEFT = { dx: -1, dy: 0 };

/**
 * The cubic the editor draws between two pins: out of the one along its way
 * out, into the other along its way in. Side pins go straight across, as a
 * Blueprint wire does; a round node's wire leaves along the circle's radius.
 */
export function wirePath(
  from: Point,
  to: Point,
  out: { dx: number; dy: number } = RIGHT,
  into: { dx: number; dy: number } = LEFT
): string {
  const across = Math.max(48, Math.abs(to.x - from.x) * 0.5);
  const radial = Math.max(48, Math.hypot(to.x - from.x, to.y - from.y) * 0.4);
  const reach = (way: { dx: number; dy: number }) => (way.dy === 0 ? across : radial);
  const a = reach(out);
  const b = reach(into);
  const c1 = `${round2(from.x + out.dx * a)} ${round2(from.y + out.dy * a)}`;
  const c2 = `${round2(to.x + into.dx * b)} ${round2(to.y + into.dy * b)}`;
  return `M ${from.x} ${from.y} C ${c1}, ${c2}, ${to.x} ${to.y}`;
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

/** Rough length of that cubic, used to keep the running dot at one speed. */
export function wireLength(from: Point, to: Point): number {
  const dx = Math.abs(to.x - from.x);
  const dy = Math.abs(to.y - from.y);
  return Math.max(60, dx + dy * 0.6);
}

export function edgeType(
  graph: FlowGraph,
  catalog: Catalog,
  edge: FlowEdge
): string {
  if (edge.kind === "exec") return "exec";
  const node = graph.nodes.find((item) => item.id === edge.from[0]);
  if (!node) return "any";
  const spec = catalog.kinds.get(node.kind);
  if (!spec) return "any";
  // A variable node's real type is the variable it reads, not the catalog's
  // placeholder - which is what makes the wire the right colour.
  if (node.kind === "var_get" || node.kind === "var_set") {
    const declared = graph.variables.find((item) => item.name === String(node.data?.variable ?? ""));
    if (declared) return declared.type;
  }
  const pin = pinsOf(spec, node).find(
    (item) => item.name === edge.from[1] && item.direction === "out"
  );
  return pin?.type ?? "any";
}

export function snap(value: number): number {
  return Math.round(value / (GRID / 2)) * (GRID / 2);
}

export interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export function graphBounds(layouts: Map<string, NodeLayout>): Bounds | null {
  if (layouts.size === 0) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const layout of layouts.values()) {
    minX = Math.min(minX, layout.node.pos[0]);
    minY = Math.min(minY, layout.node.pos[1]);
    maxX = Math.max(maxX, layout.node.pos[0] + layout.width);
    maxY = Math.max(maxY, layout.node.pos[1] + layout.height);
  }
  return { minX, minY, maxX, maxY };
}

/**
 * What a marquee over `rect` selects: the nodes it touches, and the comments
 * it wholly encloses - a marquee drawn inside a comment is about its nodes,
 * not about the comment.
 */
export function nodesInRect(layouts: Map<string, NodeLayout>, rect: Bounds): string[] {
  const hits: string[] = [];
  for (const [id, layout] of layouts) {
    const [x, y] = layout.node.pos;
    if (isAnnotation(layout.spec)) {
      if (contains(rect, boundsOf(layout))) hits.push(id);
      continue;
    }
    if (
      x + layout.width >= rect.minX &&
      x <= rect.maxX &&
      y + layout.height >= rect.minY &&
      y <= rect.maxY
    ) {
      hits.push(id);
    }
  }
  return hits;
}
