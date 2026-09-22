// The `*.flow.json` contract, mirrored from `code_ai/core/graph/models.py`.
//
// This side exists for editor ergonomics - drawing pins, refusing an illegal
// drag before the user drops it - and never as a second opinion on legality.
// `graphValidate` is the only authority; anything this file believes that the
// backend disagrees with is a bug here.

import type { EventEnvelope } from "./protocol";

export const FLOW_SCHEMA_VERSION = 1;
export const FLOW_EXTENSION = ".flow.json";

export type PinDirection = "in" | "out";
export type EdgeKind = "exec" | "data";

export interface FlowVariable {
  id: string;
  name: string;
  type: string;
  default: unknown;
}

export interface FlowNode {
  id: string;
  kind: string;
  pos: [number, number];
  data: Record<string, unknown>;
}

export interface FlowEdge {
  id: string;
  kind: EdgeKind;
  from: [string, string];
  to: [string, string];
}

export interface FlowView {
  pan: [number, number];
  zoom: number;
}

export interface FlowGraph {
  version: number;
  name: string;
  variables: FlowVariable[];
  nodes: FlowNode[];
  edges: FlowEdge[];
  view: FlowView;
}

// -- the catalog the backend serves --------------------------------------

export interface PinSpec {
  name: string;
  type: string;
  direction: PinDirection;
  label: string;
  required: boolean;
  dynamic: boolean;
  color: string;
}

export interface FieldSpec {
  name: string;
  kind: string;
  label: string;
  default: unknown;
  options: string[];
  help: string;
}

export interface NodeKindSpec {
  kind: string;
  title: string;
  description: string;
  category: string;
  pure: boolean;
  max_instances: number;
  pins: PinSpec[];
  fields: FieldSpec[];
}

export interface ProfessionSpec {
  name: string;
  title: string;
  description: string;
  writes: boolean;
  runs_processes: boolean;
  read_only: boolean;
  max_model_steps: number;
  capabilities: string[];
}

export interface GraphCatalog {
  schema_version: number;
  /** The language the user picked, so the panel can follow it. */
  language: string;
  node_kinds: NodeKindSpec[];
  pin_colors: Record<string, string>;
  variable_types: string[];
  professions: ProfessionSpec[];
  limits: {
    nodes: number;
    edges: number;
    variables: number;
    run?: { max_nodes_executed: number; max_seconds: number; max_loop_iterations: number };
  };
}

export interface ValidationIssue {
  message: string;
  node_id: string;
  pin: string;
  field: string;
  severity: "error" | "warning";
}

// -- webview <-> host ----------------------------------------------------

export type FlowHostToWebview =
  | { type: "flowDocument"; graph: FlowGraph; fileName: string; readOnly: boolean }
  | { type: "flowCatalog"; catalog: GraphCatalog }
  | { type: "flowEvent"; event: EventEnvelope }
  | { type: "flowValidation"; ok: boolean; issues: ValidationIssue[] }
  | { type: "flowRunState"; running: boolean; runId: string; message: string }
  | { type: "flowModels"; nodeId: string; ok: boolean; models: string[]; message: string }
  | { type: "flowNotice"; level: "info" | "warn" | "error"; text: string }
  | { type: "flowSecretStored"; name: string }
  | { type: "flowCommand"; command: "run" | "stop" };

export type FlowWebviewToHost =
  | { type: "flowReady" }
  | { type: "flowEdit"; graph: FlowGraph }
  | { type: "flowSave" }
  | { type: "flowValidate"; graph: FlowGraph }
  | { type: "flowRun"; graph: FlowGraph; variables: Record<string, unknown> }
  | { type: "flowStop" }
  | { type: "flowAnswer"; runId: string; callId: string; text: string }
  | {
      type: "flowListModels";
      nodeId: string;
      baseUrl: string;
      apiMode: string;
      apiKeyRef: string;
    }
  | { type: "flowStoreSecret"; name: string }
  | { type: "flowNotice"; level: "info" | "warn" | "error"; text: string };

// -- parsing and writing -------------------------------------------------

export function emptyFlow(name = "new-flow"): FlowGraph {
  return {
    version: FLOW_SCHEMA_VERSION,
    name,
    variables: [],
    nodes: [
      { id: "n1", kind: "start", pos: [-220, 0], data: {} },
      { id: "n2", kind: "end", pos: [220, 0], data: {} },
    ],
    edges: [{ id: "e1", kind: "exec", from: ["n1", "out"], to: ["n2", "in"] }],
    view: { pan: [0, 0], zoom: 1 },
  };
}

/**
 * Read a document's text. A file the user broke by hand must not take the
 * editor down with it, so anything unreadable comes back as an error the panel
 * can show next to a "start over" button.
 */
export function parseFlow(text: string): { graph?: FlowGraph; error?: string } {
  const trimmed = text.trim();
  if (!trimmed) return { graph: emptyFlow() };
  let raw: unknown;
  try {
    raw = JSON.parse(trimmed);
  } catch (err) {
    return { error: `This file is not valid JSON: ${(err as Error).message}` };
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { error: "A graph must be a JSON object." };
  }
  const doc = raw as Record<string, unknown>;
  const version = typeof doc.version === "number" ? doc.version : FLOW_SCHEMA_VERSION;
  if (version > FLOW_SCHEMA_VERSION) {
    return {
      error: `This graph was written for format ${version}; this build reads up to ${FLOW_SCHEMA_VERSION}.`,
    };
  }
  return {
    graph: {
      version,
      name: typeof doc.name === "string" ? doc.name : "",
      variables: asVariables(doc.variables),
      nodes: asNodes(doc.nodes),
      edges: asEdges(doc.edges),
      view: asView(doc.view),
    },
  };
}

/**
 * Write a graph back as text.
 *
 * Fixed key order, integer positions, 2-space indent, trailing newline: a node
 * nudged four pixels has to produce a four-pixel diff, or every save becomes
 * unreviewable and the file stops being worth keeping in git.
 */
export function serializeFlow(graph: FlowGraph): string {
  const doc = {
    version: graph.version || FLOW_SCHEMA_VERSION,
    name: graph.name ?? "",
    variables: graph.variables.map((variable) => ({
      id: variable.id,
      name: variable.name,
      type: variable.type,
      default: variable.default ?? null,
    })),
    nodes: graph.nodes.map((node) => ({
      id: node.id,
      kind: node.kind,
      pos: [Math.round(node.pos[0]), Math.round(node.pos[1])],
      data: sortedKeys(node.data),
    })),
    edges: graph.edges.map((edge) => ({
      id: edge.id,
      kind: edge.kind,
      from: [edge.from[0], edge.from[1]],
      to: [edge.to[0], edge.to[1]],
    })),
    view: {
      pan: [round(graph.view.pan[0]), round(graph.view.pan[1])],
      zoom: round(graph.view.zoom),
    },
  };
  return JSON.stringify(doc, null, 2) + "\n";
}

function round(value: number): number {
  return Math.round((Number.isFinite(value) ? value : 0) * 1000) / 1000;
}

function sortedKeys(data: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(data ?? {}).sort()) {
    const value = data[key];
    if (value === undefined) continue;
    out[key] = value;
  }
  return out;
}

function asVariables(raw: unknown): FlowVariable[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    return [
      {
        id: String(row.id ?? ""),
        name: String(row.name ?? ""),
        type: String(row.type ?? "string"),
        default: row.default ?? null,
      },
    ];
  });
}

function asNodes(raw: unknown): FlowNode[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    const pos = Array.isArray(row.pos) ? row.pos : [0, 0];
    return [
      {
        id: String(row.id ?? ""),
        kind: String(row.kind ?? ""),
        pos: [Number(pos[0]) || 0, Number(pos[1]) || 0] as [number, number],
        data:
          row.data && typeof row.data === "object" && !Array.isArray(row.data)
            ? { ...(row.data as Record<string, unknown>) }
            : {},
      },
    ];
  });
}

function asEdges(raw: unknown): FlowEdge[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    const from = Array.isArray(row.from) ? row.from : [];
    const to = Array.isArray(row.to) ? row.to : [];
    if (from.length < 2 || to.length < 2) return [];
    return [
      {
        id: String(row.id ?? ""),
        kind: row.kind === "data" ? "data" : "exec",
        from: [String(from[0]), String(from[1])] as [string, string],
        to: [String(to[0]), String(to[1])] as [string, string],
      },
    ];
  });
}

function asView(raw: unknown): FlowView {
  if (!raw || typeof raw !== "object") return { pan: [0, 0], zoom: 1 };
  const row = raw as Record<string, unknown>;
  const pan = Array.isArray(row.pan) ? row.pan : [0, 0];
  const zoom = Number(row.zoom);
  return {
    pan: [Number(pan[0]) || 0, Number(pan[1]) || 0],
    zoom: Number.isFinite(zoom) && zoom > 0 ? zoom : 1,
  };
}

// -- pin rules (the editor's half) ---------------------------------------

/** Whether a data pin of type `source` may feed one of type `target`. */
export function canConnect(source: string, target: string): boolean {
  if (source === "exec" || target === "exec") return source === target;
  if (source === target) return true;
  if (source === "any" || target === "any") return true;
  if (source === "int" && target === "float") return true;
  if (target === "string" && (source === "int" || source === "float" || source === "bool")) {
    return true;
  }
  return false;
}

/** The next free id with the given prefix, so ids stay short and stable. */
export function nextId(prefix: string, used: Iterable<string>): string {
  const taken = new Set(used);
  for (let index = 1; index < 100000; index += 1) {
    const candidate = `${prefix}${index}`;
    if (!taken.has(candidate)) return candidate;
  }
  return `${prefix}${Date.now()}`;
}
