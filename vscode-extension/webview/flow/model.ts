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

export const NODE_W = 216;
export const HEADER_H = 40;
export const ROW_H = 20;
export const BODY_PAD = 8;
/** The strip under an agent's pins: what it may touch, or what it is doing. */
export const FOOTER_H = 24;
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

const FOOTER_KINDS = new Set(["profession", "clean_agent", "custom_profession", "tools"]);

export function accentOf(spec: NodeKindSpec): string {
  return KIND_COLORS[spec.kind] ?? CATEGORY_COLORS[spec.category] ?? CATEGORY_COLORS.flow;
}

export interface PinLayout {
  pin: PinSpec;
  /** Local offset from the node's own origin. */
  x: number;
  y: number;
}

export interface NodeLayout {
  node: FlowNode;
  spec: NodeKindSpec;
  width: number;
  height: number;
  inputs: PinLayout[];
  outputs: PinLayout[];
  summary: string;
  /** Height of the strip under the pins; 0 when the kind has none. */
  footer: number;
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
    };
  }
  const pins = pinsOf(spec, node);
  const inputs = execFirst(pins.filter((pin) => pin.direction === "in"));
  const outputs = execFirst(pins.filter((pin) => pin.direction === "out"));
  const summary = summaryOf(node, spec);
  const rows = Math.max(inputs.length, outputs.length);
  const footer = FOOTER_KINDS.has(node.kind) ? FOOTER_H : 0;
  const height = HEADER_H + BODY_PAD + rows * ROW_H + BODY_PAD + footer;
  const place = (list: PinSpec[], x: number): PinLayout[] =>
    list.map((pin, index) => ({
      pin,
      x,
      y: HEADER_H + BODY_PAD + index * ROW_H + ROW_H / 2,
    }));
  return {
    node,
    spec,
    width: NODE_W,
    height,
    inputs: place(inputs, 0),
    outputs: place(outputs, NODE_W),
    summary,
    footer,
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

/** Absolute position of one pin, for drawing wires. */
export function pinPoint(
  layouts: Map<string, NodeLayout>,
  nodeId: string,
  pinName: string,
  direction: "in" | "out"
): Point | null {
  const layout = layouts.get(nodeId);
  if (!layout) return null;
  const list = direction === "in" ? layout.inputs : layout.outputs;
  const found = list.find((item) => item.pin.name === pinName);
  if (!found) return null;
  return { x: layout.node.pos[0] + found.x, y: layout.node.pos[1] + found.y };
}

/** The cubic the editor draws between two pins, Blueprint-style. */
export function wirePath(from: Point, to: Point): string {
  const distance = Math.max(48, Math.abs(to.x - from.x) * 0.5);
  return `M ${from.x} ${from.y} C ${from.x + distance} ${from.y}, ${to.x - distance} ${to.y}, ${to.x} ${to.y}`;
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
