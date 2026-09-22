// Geometry and catalog lookups: everything the canvas needs to know *where*
// something is. Kept apart from React so it can be unit-tested without a DOM.

import { t } from "../i18n";
import type {
  FlowEdge,
  FlowGraph,
  FlowNode,
  GraphCatalog,
  NodeKindSpec,
  PinSpec,
} from "../../src/flow-schema";

export const NODE_W = 216;
export const HEADER_H = 30;
export const ROW_H = 20;
export const BODY_PAD = 8;
export const GRID = 20;

/** Category -> header tint. Reads as "what kind of work is this node". */
export const CATEGORY_COLORS: Record<string, string> = {
  flow: "#5a6472",
  agent: "#1f6feb",
  data: "#6f42c1",
  debug: "#8a6d1f",
};

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
}

/** The pins a node actually shows, after dynamic pins are expanded. */
export function pinsOf(spec: NodeKindSpec, node: FlowNode): PinSpec[] {
  const pins: PinSpec[] = [];
  for (const pin of spec.pins) {
    if (!pin.dynamic) {
      pins.push(pin);
      continue;
    }
    // A dynamic declaration stands for however many the node asked for; the
    // first declaration expands and the rest are its siblings.
    if (!pin.name.endsWith("_0")) continue;
    const count = Math.max(1, Math.min(12, Number(node.data?.outputs ?? 2) || 2));
    const base = pin.name.slice(0, -2);
    for (let index = 0; index < count; index += 1) {
      pins.push({ ...pin, name: `${base}_${index}`, label: String(index) });
    }
  }
  return pins;
}

function execFirst(pins: PinSpec[]): PinSpec[] {
  return [...pins.filter((p) => p.type === "exec"), ...pins.filter((p) => p.type !== "exec")];
}

export function layoutNode(node: FlowNode, spec: NodeKindSpec): NodeLayout {
  const pins = pinsOf(spec, node);
  const inputs = execFirst(pins.filter((pin) => pin.direction === "in"));
  const outputs = execFirst(pins.filter((pin) => pin.direction === "out"));
  const summary = summaryOf(node, spec);
  const rows = Math.max(inputs.length, outputs.length);
  const height = HEADER_H + BODY_PAD + rows * ROW_H + (summary ? 18 : 0) + BODY_PAD;
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
      const profession = String(data.profession ?? "").trim();
      return profession ? profession.replace(/_/g, " ") : t("choose a profession");
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
    default:
      return "";
  }
}

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

export function nodesInRect(layouts: Map<string, NodeLayout>, rect: Bounds): string[] {
  const hits: string[] = [];
  for (const [id, layout] of layouts) {
    const [x, y] = layout.node.pos;
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
