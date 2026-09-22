// Editor state and every mutation that can happen to it.
//
// Undo is snapshot-based: each mutation that changes the graph pushes the
// previous graph onto a stack. For a document capped at a few hundred nodes a
// snapshot is a few kilobytes, and it makes "undo restores exactly what was
// there" true by construction rather than by getting every inverse right.

import {
  canConnect,
  emptyFlow,
  FlowEdge,
  FlowGraph,
  FlowNode,
  FlowVariable,
  GraphCatalog,
  nextId,
  ValidationIssue,
} from "../../src/flow-schema";
import { t, tf } from "../i18n";
import { Catalog, indexCatalog, pinsOf, snap } from "./model";

export const UNDO_LIMIT = 100;

export type NodeRunStatus = "idle" | "running" | "done" | "failed";

/**
 * A connection to make at the same time a node is created - what dragging a
 * wire off a pin and picking a node from the menu produces.
 */
export interface NewWire {
  /** The pin on the node being created. */
  newPin: string;
  kind: "exec" | "data";
  /** The pin that was already there. */
  other: [string, string];
  /** True when the existing pin is the output, i.e. the value flows from it. */
  otherIsSource: boolean;
}

export interface LogRow {
  id: number;
  kind: "print" | "info" | "agent" | "error" | "warn" | "decision";
  nodeId: string;
  text: string;
  at: string;
}

export interface PendingQuestion {
  nodeId: string;
  callId: string;
  prompt: string;
}

export interface RunState {
  running: boolean;
  runId: string;
  status: string;
  nodes: Record<string, NodeRunStatus>;
  /** Edges lit right now, each with the moment it was lit (for the dots). */
  active: Record<string, number>;
  traveled: string[];
  loops: Record<string, { index: number; item: string }>;
  variables: Record<string, string>;
  question: PendingQuestion | null;
  agents: Record<string, { name: string; profession: string; detail: string }>;
  startedAt: number;
  nodesExecuted: number;
}

export interface ModelListState {
  loading: boolean;
  models: string[];
  message: string;
}

export interface EditorState {
  graph: FlowGraph;
  catalog: Catalog | null;
  fileName: string;
  selection: string[];
  selectedEdge: string | null;
  undo: FlowGraph[];
  redo: FlowGraph[];
  issues: ValidationIssue[];
  valid: boolean;
  run: RunState;
  log: LogRow[];
  notice: { level: "info" | "warn" | "error"; text: string } | null;
  models: Record<string, ModelListState>;
  /** Variable overrides typed into the toolbar for the next run. */
  overrides: Record<string, string>;
  inspectorTab: "node" | "variables" | "run";
  logOpen: boolean;
  dirty: boolean;
}

export function initialRun(): RunState {
  return {
    running: false,
    runId: "",
    status: "",
    nodes: {},
    active: {},
    traveled: [],
    loops: {},
    variables: {},
    question: null,
    agents: {},
    startedAt: 0,
    nodesExecuted: 0,
  };
}

export function initialState(): EditorState {
  return {
    graph: emptyFlow(),
    catalog: null,
    fileName: "",
    selection: [],
    selectedEdge: null,
    undo: [],
    redo: [],
    issues: [],
    valid: true,
    run: initialRun(),
    log: [],
    notice: null,
    models: {},
    overrides: {},
    inspectorTab: "node",
    logOpen: true,
    dirty: false,
  };
}

function clone(graph: FlowGraph): FlowGraph {
  return JSON.parse(JSON.stringify(graph)) as FlowGraph;
}

/** Record the current graph so the next mutation can be undone. */
function withHistory(state: EditorState, graph: FlowGraph): EditorState {
  const undo = [...state.undo, clone(state.graph)].slice(-UNDO_LIMIT);
  return { ...state, graph, undo, redo: [], dirty: true };
}

export type Action =
  | { type: "catalog"; catalog: GraphCatalog }
  | { type: "document"; graph: FlowGraph; fileName: string }
  | { type: "validation"; ok: boolean; issues: ValidationIssue[] }
  | { type: "select"; ids: string[]; additive?: boolean }
  | { type: "selectEdge"; id: string | null }
  | {
      type: "addNode";
      kind: string;
      at: { x: number; y: number };
      data?: Record<string, unknown>;
      /** Wire the new node straight into the pin the drag came from. */
      wire?: NewWire;
    }
  | {
      type: "createVariableWithNode";
      varType: string;
      at: { x: number; y: number };
      mode: "get" | "set";
    }
  | { type: "moveNodes"; ids: string[]; dx: number; dy: number; commit: boolean }
  | { type: "setNodeData"; id: string; key: string; value: unknown }
  | { type: "deleteSelection" }
  | { type: "connect"; edge: Omit<FlowEdge, "id"> }
  | { type: "deleteEdge"; id: string }
  | { type: "addVariable"; varType: string }
  | { type: "setVariable"; id: string; patch: Partial<FlowVariable> }
  | { type: "deleteVariable"; id: string }
  | { type: "setOverride"; name: string; value: string }
  | { type: "paste"; nodes: FlowNode[]; edges: FlowEdge[] }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "setView"; pan?: [number, number]; zoom?: number }
  | { type: "rename"; name: string }
  | { type: "notice"; level: "info" | "warn" | "error"; text: string }
  | { type: "dismissNotice" }
  | { type: "runState"; running: boolean; runId: string; message: string }
  | { type: "graphEvent"; eventType: string; payload: Record<string, any> }
  | { type: "models"; nodeId: string; state: ModelListState }
  | { type: "inspectorTab"; tab: EditorState["inspectorTab"] }
  | { type: "toggleLog" }
  | { type: "clearLog" }
  | { type: "saved" };

export function reduce(state: EditorState, action: Action): EditorState {
  switch (action.type) {
    case "catalog":
      return { ...state, catalog: indexCatalog(action.catalog) };

    case "document": {
      // The document is the authority; an edit the editor did not make (an
      // external change, a revert, an undo in another tab) replaces the graph
      // and the history with it.
      const same = JSON.stringify(state.graph) === JSON.stringify(action.graph);
      if (same) return { ...state, fileName: action.fileName, dirty: false };
      return {
        ...state,
        graph: action.graph,
        fileName: action.fileName,
        selection: state.selection.filter((id) =>
          action.graph.nodes.some((node) => node.id === id)
        ),
        undo: [],
        redo: [],
        dirty: false,
      };
    }

    case "validation":
      return { ...state, valid: action.ok, issues: action.issues };

    case "select": {
      const ids = action.additive
        ? Array.from(new Set([...state.selection, ...action.ids]))
        : action.ids;
      return { ...state, selection: ids, selectedEdge: null, inspectorTab: "node" };
    }

    case "selectEdge":
      return { ...state, selectedEdge: action.id, selection: [] };

    case "addNode": {
      const spec = state.catalog?.kinds.get(action.kind);
      if (!spec) return state;
      const count = state.graph.nodes.filter((node) => node.kind === action.kind).length;
      if (spec.max_instances && count >= spec.max_instances) {
        return {
          ...state,
          notice: {
            level: "warn",
            text: tf(
              "A graph may hold only {0} {1} node(s).",
              spec.max_instances,
              spec.title
            ),
          },
        };
      }
      const id = nextId("n", state.graph.nodes.map((node) => node.id));
      const data: Record<string, unknown> = {};
      for (const field of spec.fields) {
        if (field.default !== null && field.default !== undefined) data[field.name] = field.default;
      }
      if (action.kind === "sequence") data.outputs = 2;
      Object.assign(data, action.data ?? {});
      const node: FlowNode = {
        id,
        kind: action.kind,
        pos: [snap(action.at.x), snap(action.at.y)],
        data,
      };
      let graph: FlowGraph = { ...state.graph, nodes: [...state.graph.nodes, node] };
      if (action.wire) {
        const { newPin, kind: wireKind, other, otherIsSource } = action.wire;
        graph = insertEdge(graph, {
          kind: wireKind,
          from: otherIsSource ? other : [id, newPin],
          to: otherIsSource ? [id, newPin] : other,
        });
      }
      return { ...withHistory(state, graph), selection: [id], selectedEdge: null };
    }

    case "moveNodes": {
      // A drag is one undo entry. The canvas holds the offset locally while the
      // mouse is down and dispatches the whole delta once, on mouse-up, so the
      // snapshot pushed here is the position the drag started from.
      if (action.dx === 0 && action.dy === 0) return state;
      const ids = new Set(action.ids);
      const nodes = state.graph.nodes.map((node) =>
        ids.has(node.id)
          ? {
              ...node,
              pos: [
                snap(node.pos[0] + action.dx),
                snap(node.pos[1] + action.dy),
              ] as [number, number],
            }
          : node
      );
      return withHistory(state, { ...state.graph, nodes });
    }

    case "setNodeData": {
      const nodes = state.graph.nodes.map((node) =>
        node.id === action.id
          ? { ...node, data: { ...node.data, [action.key]: action.value } }
          : node
      );
      let graph: FlowGraph = { ...state.graph, nodes };
      // Shrinking a Sequence drops the wires that hung off the pins it no
      // longer has, rather than leaving edges pointing at nothing.
      if (action.key === "outputs") {
        graph = { ...graph, edges: graph.edges.filter((edge) => livePin(graph, state, edge)) };
      }
      return withHistory(state, graph);
    }

    case "deleteSelection": {
      const ids = new Set(state.selection);
      if (state.selectedEdge) {
        const graph = {
          ...state.graph,
          edges: state.graph.edges.filter((edge) => edge.id !== state.selectedEdge),
        };
        return { ...withHistory(state, graph), selectedEdge: null };
      }
      if (ids.size === 0) return state;
      const graph: FlowGraph = {
        ...state.graph,
        nodes: state.graph.nodes.filter((node) => !ids.has(node.id)),
        edges: state.graph.edges.filter(
          (edge) => !ids.has(edge.from[0]) && !ids.has(edge.to[0])
        ),
      };
      return { ...withHistory(state, graph), selection: [] };
    }

    case "connect": {
      const rejection = connectionProblem(state, action.edge);
      if (rejection) return { ...state, notice: { level: "warn", text: rejection } };
      return withHistory(state, insertEdge(state.graph, action.edge));
    }

    case "deleteEdge": {
      const graph = {
        ...state.graph,
        edges: state.graph.edges.filter((edge) => edge.id !== action.id),
      };
      return { ...withHistory(state, graph), selectedEdge: null };
    }

    case "addVariable": {
      const variable: FlowVariable = {
        id: nextId("v", state.graph.variables.map((item) => item.id)),
        name: freshVariableName(state.graph.variables.map((item) => item.name)),
        type: action.varType,
        default: defaultFor(action.varType),
      };
      const graph = { ...state.graph, variables: [...state.graph.variables, variable] };
      return { ...withHistory(state, graph), inspectorTab: "variables" };
    }

    case "createVariableWithNode": {
      // One entry in the history: the user asked for "a new int", and undoing
      // that must not leave a node pointing at a variable that is gone.
      const variable: FlowVariable = {
        id: nextId("v", state.graph.variables.map((item) => item.id)),
        name: freshVariableName(state.graph.variables.map((item) => item.name)),
        type: action.varType,
        default: defaultFor(action.varType),
      };
      const kind = action.mode === "get" ? "var_get" : "var_set";
      const node: FlowNode = {
        id: nextId("n", state.graph.nodes.map((item) => item.id)),
        kind,
        pos: [snap(action.at.x), snap(action.at.y)],
        data: { variable: variable.name },
      };
      const graph: FlowGraph = {
        ...state.graph,
        variables: [...state.graph.variables, variable],
        nodes: [...state.graph.nodes, node],
      };
      return { ...withHistory(state, graph), selection: [node.id], selectedEdge: null };
    }

    case "setVariable": {
      const previous = state.graph.variables.find((item) => item.id === action.id);
      const variables = state.graph.variables.map((item) =>
        item.id === action.id ? { ...item, ...action.patch } : item
      );
      let graph: FlowGraph = { ...state.graph, variables };
      // Renaming a variable has to follow it into the nodes that read it, or
      // the rename silently breaks the graph.
      if (previous && action.patch.name && action.patch.name !== previous.name) {
        graph = {
          ...graph,
          nodes: graph.nodes.map((node) =>
            (node.kind === "var_get" || node.kind === "var_set") &&
            node.data?.variable === previous.name
              ? { ...node, data: { ...node.data, variable: action.patch.name } }
              : node
          ),
        };
      }
      return withHistory(state, graph);
    }

    case "deleteVariable": {
      const target = state.graph.variables.find((item) => item.id === action.id);
      const graph: FlowGraph = {
        ...state.graph,
        variables: state.graph.variables.filter((item) => item.id !== action.id),
      };
      if (target) {
        // The nodes that read it stay, with the field cleared: the compiler
        // then says exactly which ones need a new variable.
        graph.nodes = graph.nodes.map((node) =>
          (node.kind === "var_get" || node.kind === "var_set") &&
          node.data?.variable === target.name
            ? { ...node, data: { ...node.data, variable: "" } }
            : node
        );
      }
      return withHistory(state, graph);
    }

    case "setOverride":
      return { ...state, overrides: { ...state.overrides, [action.name]: action.value } };

    case "paste": {
      if (action.nodes.length === 0) return state;
      const graph: FlowGraph = {
        ...state.graph,
        nodes: [...state.graph.nodes, ...action.nodes],
        edges: [...state.graph.edges, ...action.edges],
      };
      return {
        ...withHistory(state, graph),
        selection: action.nodes.map((node) => node.id),
        selectedEdge: null,
      };
    }

    case "undo": {
      if (state.undo.length === 0) return state;
      const previous = state.undo[state.undo.length - 1];
      return {
        ...state,
        graph: previous,
        undo: state.undo.slice(0, -1),
        redo: [...state.redo, clone(state.graph)].slice(-UNDO_LIMIT),
        selection: state.selection.filter((id) => previous.nodes.some((node) => node.id === id)),
        dirty: true,
      };
    }

    case "redo": {
      if (state.redo.length === 0) return state;
      const next = state.redo[state.redo.length - 1];
      return {
        ...state,
        graph: next,
        redo: state.redo.slice(0, -1),
        undo: [...state.undo, clone(state.graph)].slice(-UNDO_LIMIT),
        dirty: true,
      };
    }

    case "setView": {
      // The viewport is cosmetic: it travels with the document but never goes
      // on the undo stack, because undoing a pan is not what Ctrl+Z means.
      const view = {
        pan: action.pan ?? state.graph.view.pan,
        zoom: action.zoom ?? state.graph.view.zoom,
      };
      return { ...state, graph: { ...state.graph, view } };
    }

    case "rename":
      return withHistory(state, { ...state.graph, name: action.name });

    case "notice":
      return { ...state, notice: { level: action.level, text: action.text } };

    case "dismissNotice":
      return { ...state, notice: null };

    case "runState": {
      if (action.running) {
        return {
          ...state,
          run: { ...initialRun(), running: true, runId: action.runId, startedAt: Date.now() },
        };
      }
      return {
        ...state,
        run: {
          ...state.run,
          running: false,
          status: action.message || state.run.status,
          active: {},
          question: null,
        },
      };
    }

    case "graphEvent":
      return applyGraphEvent(state, action.eventType, action.payload);

    case "models":
      return { ...state, models: { ...state.models, [action.nodeId]: action.state } };

    case "inspectorTab":
      return { ...state, inspectorTab: action.tab };

    case "toggleLog":
      return { ...state, logOpen: !state.logOpen };

    case "clearLog":
      return { ...state, log: [] };

    case "saved":
      return { ...state, dirty: false };

    default:
      return state;
  }
}

/**
 * Add an edge, dropping whatever occupied the slot it needs.
 *
 * An exec output drives exactly one wire and a data input takes exactly one
 * value, so connecting to an occupied pin replaces rather than stacks - the
 * alternative is a graph that looks connected twice and runs once.
 */
export function insertEdge(graph: FlowGraph, edge: Omit<FlowEdge, "id">): FlowGraph {
  const id = nextId("e", graph.edges.map((item) => item.id));
  const edges = graph.edges.filter((item) => {
    if (edge.kind === "exec") {
      return !(item.from[0] === edge.from[0] && item.from[1] === edge.from[1]);
    }
    return !(item.to[0] === edge.to[0] && item.to[1] === edge.to[1]);
  });
  return { ...graph, edges: [...edges, { ...edge, id }] };
}

/** The type a pin really carries, after a variable node's own type is resolved. */
export function pinTypeOf(
  state: EditorState,
  nodeId: string,
  pinName: string,
  direction: "in" | "out"
): string {
  const node = state.graph.nodes.find((item) => item.id === nodeId);
  const spec = node ? state.catalog?.kinds.get(node.kind) : undefined;
  if (!node || !spec) return "any";
  const pin = pinsOf(spec, node).find(
    (item) => item.name === pinName && item.direction === direction
  );
  if (!pin) return "any";
  return resolvedPinType(state, nodeId, pin.type);
}

/** Why a connection cannot be made, or `null` if it can. */
export function connectionProblem(state: EditorState, edge: Omit<FlowEdge, "id">): string | null {
  const catalog = state.catalog;
  if (!catalog) return t("The node catalog has not loaded yet.");
  const [fromId, fromPin] = edge.from;
  const [toId, toPin] = edge.to;
  if (fromId === toId) return t("A node cannot connect to itself.");
  const fromNode = state.graph.nodes.find((node) => node.id === fromId);
  const toNode = state.graph.nodes.find((node) => node.id === toId);
  if (!fromNode || !toNode) return t("One of the nodes is gone.");
  const fromSpec = catalog.kinds.get(fromNode.kind);
  const toSpec = catalog.kinds.get(toNode.kind);
  if (!fromSpec || !toSpec) return t("Unknown node kind.");
  const out = pinsOf(fromSpec, fromNode).find(
    (pin) => pin.name === fromPin && pin.direction === "out"
  );
  const into = pinsOf(toSpec, toNode).find((pin) => pin.name === toPin && pin.direction === "in");
  if (!out || !into) return t("That pin does not exist.");
  if ((out.type === "exec") !== (into.type === "exec")) {
    return t("An exec pin does not connect to a data pin.");
  }
  if (out.type !== "exec") {
    const sourceType = resolvedPinType(state, fromNode.id, out.type);
    const targetType = resolvedPinType(state, toNode.id, into.type);
    if (!canConnect(sourceType, targetType)) {
      return tf("A {0} value cannot feed a {1} pin.", sourceType, targetType);
    }
  }
  const duplicate = state.graph.edges.some(
    (item) =>
      item.from[0] === fromId &&
      item.from[1] === fromPin &&
      item.to[0] === toId &&
      item.to[1] === toPin
  );
  if (duplicate) return t("That connection already exists.");
  return null;
}

/** A variable node reports the type of the variable it points at. */
function resolvedPinType(state: EditorState, nodeId: string, declared: string): string {
  const node = state.graph.nodes.find((item) => item.id === nodeId);
  if (!node || (node.kind !== "var_get" && node.kind !== "var_set")) return declared;
  const variable = state.graph.variables.find(
    (item) => item.name === String(node.data?.variable ?? "")
  );
  return variable?.type ?? declared;
}

function livePin(graph: FlowGraph, state: EditorState, edge: FlowEdge): boolean {
  const catalog = state.catalog;
  if (!catalog) return true;
  const node = graph.nodes.find((item) => item.id === edge.from[0]);
  if (!node) return false;
  const spec = catalog.kinds.get(node.kind);
  if (!spec) return true;
  return pinsOf(spec, node).some(
    (pin) => pin.name === edge.from[1] && pin.direction === "out"
  );
}

/** A name no other variable has, so two + clicks do not collide. */
export function freshVariableName(used: string[]): string {
  const taken = new Set(used);
  if (!taken.has("newVar")) return "newVar";
  for (let index = 2; index < 1000; index += 1) {
    const candidate = `newVar${index}`;
    if (!taken.has(candidate)) return candidate;
  }
  return `newVar${Date.now()}`;
}

export function defaultFor(type: string): unknown {
  switch (type) {
    case "int":
      return 0;
    case "float":
      return 0;
    case "bool":
      return false;
    default:
      return "";
  }
}

let logSeq = 1;

function log(state: EditorState, row: Omit<LogRow, "id" | "at">): EditorState {
  const entry: LogRow = {
    ...row,
    id: logSeq++,
    at: new Date().toLocaleTimeString(),
  };
  // Bounded: a graph in a loop can print thousands of lines, and the panel has
  // to stay responsive while it does.
  return { ...state, log: [...state.log, entry].slice(-400) };
}

function applyGraphEvent(
  state: EditorState,
  eventType: string,
  payload: Record<string, any>
): EditorState {
  const run = state.run;
  switch (eventType) {
    case "graph.run.started":
      return {
        ...state,
        run: {
          ...initialRun(),
          running: true,
          runId: String(payload.run_id ?? ""),
          startedAt: Date.now(),
          variables: payload.variables ?? {},
        },
        log: [],
      };

    case "graph.run.completed": {
      const status = String(payload.status ?? "");
      const next = log(
        {
          ...state,
          run: {
            ...run,
            running: false,
            status,
            active: {},
            question: null,
            nodesExecuted: Number(payload.nodes_executed ?? run.nodesExecuted),
            variables: payload.variables ?? run.variables,
          },
        },
        {
          kind: status === "completed" ? "info" : "error",
          nodeId: "",
          text:
            status === "completed"
              ? tf(
                  "Run finished in {0}s ({1} nodes).",
                  payload.seconds,
                  payload.nodes_executed
                )
              : tf("Run {0}: {1}", status, payload.reason || payload.error || ""),
        }
      );
      return next;
    }

    case "graph.node.started":
      return {
        ...state,
        run: {
          ...run,
          nodes: { ...run.nodes, [String(payload.node_id)]: "running" },
          nodesExecuted: Number(payload.index ?? run.nodesExecuted),
        },
      };

    case "graph.node.completed": {
      const nodeId = String(payload.node_id);
      return {
        ...state,
        run: { ...run, nodes: { ...run.nodes, [nodeId]: "done" } },
      };
    }

    case "graph.node.failed":
      return log(
        {
          ...state,
          run: { ...run, nodes: { ...run.nodes, [String(payload.node_id)]: "failed" } },
        },
        { kind: "error", nodeId: String(payload.node_id), text: String(payload.error ?? "") }
      );

    case "graph.edge.active": {
      const edgeId = String(payload.edge_id ?? "");
      if (!edgeId) return state;
      return {
        ...state,
        run: {
          ...run,
          active: { ...run.active, [edgeId]: Date.now() },
          traveled: run.traveled.includes(edgeId) ? run.traveled : [...run.traveled, edgeId],
        },
      };
    }

    case "graph.print":
      return log(state, {
        kind: "print",
        nodeId: String(payload.node_id ?? ""),
        text: payload.label ? `${payload.label}: ${payload.text}` : String(payload.text ?? ""),
      });

    case "graph.agent.started":
      return log(
        {
          ...state,
          run: {
            ...run,
            agents: {
              ...run.agents,
              [String(payload.node_id)]: {
                name: String(payload.agent_name ?? ""),
                profession: String(payload.profession ?? ""),
                detail: "",
              },
            },
          },
        },
        {
          kind: "agent",
          nodeId: String(payload.node_id ?? ""),
          text: `${payload.agent_name} (${payload.profession}) on ${payload.model}`,
        }
      );

    case "graph.node.progress": {
      const nodeId = String(payload.node_id ?? "");
      const agent = run.agents[nodeId];
      if (!agent) return state;
      return {
        ...state,
        run: {
          ...run,
          agents: { ...run.agents, [nodeId]: { ...agent, detail: String(payload.detail ?? "") } },
        },
      };
    }

    case "graph.variable.changed":
      return {
        ...state,
        run: {
          ...run,
          variables: { ...run.variables, [String(payload.name)]: String(payload.value ?? "") },
        },
      };

    case "graph.loop.iteration":
      return {
        ...state,
        run: {
          ...run,
          loops: {
            ...run.loops,
            [String(payload.node_id)]: {
              index: Number(payload.index ?? 0),
              item: String(payload.item ?? ""),
            },
          },
        },
      };

    case "graph.loop.capped":
      return log(state, {
        kind: "warn",
        nodeId: String(payload.node_id ?? ""),
        text: tf("The loop stopped at its cap of {0} laps.", payload.cap),
      });

    case "graph.decision":
      return log(state, {
        kind: "decision",
        nodeId: String(payload.node_id ?? ""),
        text: tf("decision ({0}): {1}", payload.mode, payload.verdict ? "true" : "false"),
      });

    case "graph.user_chat.requested":
      return log(
        {
          ...state,
          run: {
            ...run,
            question: {
              nodeId: String(payload.node_id ?? ""),
              callId: String(payload.call_id ?? ""),
              prompt: String(payload.prompt ?? ""),
            },
          },
        },
        {
          kind: "info",
          nodeId: String(payload.node_id ?? ""),
          text: t("Waiting for your answer."),
        }
      );

    case "graph.user_chat.answered":
      return { ...state, run: { ...run, question: null } };

    case "graph.warning":
      return log(state, {
        kind: "warn",
        nodeId: String(payload.node_id ?? ""),
        text: String(payload.message ?? ""),
      });

    case "graph.validation.failed":
      return {
        ...state,
        valid: false,
        issues: (payload.issues ?? []) as ValidationIssue[],
        run: { ...run, running: false },
      };

    default:
      return state;
  }
}

export function issuesByNode(issues: ValidationIssue[]): Record<string, ValidationIssue[]> {
  const grouped: Record<string, ValidationIssue[]> = {};
  for (const issue of issues) {
    if (!issue.node_id) continue;
    grouped[issue.node_id] = [...(grouped[issue.node_id] ?? []), issue];
  }
  return grouped;
}

export function hasErrors(issues: ValidationIssue[]): boolean {
  return issues.some((issue) => issue.severity === "error");
}
