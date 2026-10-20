// Functions, Blueprint-style: graphs of their own inside the document.
//
// The canvas edits one graph at a time - the main one or a function's - as a
// plain FlowGraph, a *view*. Everything here moves between that view and the
// whole document, and keeps the pins a function's signature puts on its Entry,
// its Returns and its Call nodes in step with the signature.

import type { FlowEdge, FlowFunction, FlowGraph, FlowNode, FlowParam } from "../../src/flow-schema";
import { nextId } from "../../src/flow-schema";

/** The kinds whose pins come from a signature, and which side of it they show. */
export const SIGNATURE_KINDS: Record<string, string> = {
  function_entry: "params_out",
  function_return: "results_in",
  call_function: "call",
};

/** The graph the canvas shows: the main one, or one function's with what all graphs share. */
export function project(doc: FlowGraph, scope: string | null): FlowGraph {
  const found = scope ? doc.functions?.find((item) => item.id === scope) : undefined;
  if (!found) return doc;
  return {
    version: doc.version,
    name: doc.name,
    variables: doc.variables,
    nodes: found.nodes,
    edges: found.edges,
    view: found.view,
    functions: doc.functions,
    scope: found.id,
  };
}

/**
 * A view written back into its document. The camera goes back only when asked:
 * a pan is not an edit, and following a run pans every frame.
 */
export function embed(doc: FlowGraph, view: FlowGraph, camera = false): FlowGraph {
  if (!view.scope) return view;
  const functions = doc.functions ?? [];
  const current = functions.find((item) => item.id === view.scope);
  if (!current) return doc;
  const same =
    current.nodes === view.nodes &&
    current.edges === view.edges &&
    doc.variables === view.variables &&
    doc.name === view.name;
  if (same && (!camera || current.view === view.view)) return doc;
  return {
    ...doc,
    name: view.name,
    variables: view.variables,
    functions: functions.map((item) =>
      item.id === view.scope
        ? { ...item, nodes: view.nodes, edges: view.edges, view: camera ? view.view : item.view }
        : item
    ),
  };
}

/** Ids made in a function's graph carry its id, so no two graphs ever share one. */
export function idPrefix(graph: FlowGraph, letter: string): string {
  return graph.scope ? `${graph.scope}.${letter}` : letter;
}

/**
 * The ids a new node may not take. The main graph also steers clear of every
 * function's: a file written by hand need not follow the prefixes.
 */
export function takenNodeIds(graph: FlowGraph): string[] {
  const own = graph.nodes.map((node) => node.id);
  if (graph.scope) return own;
  return [...own, ...(graph.functions ?? []).flatMap((item) => item.nodes.map((node) => node.id))];
}

export function takenEdgeIds(graph: FlowGraph): string[] {
  const own = graph.edges.map((edge) => edge.id);
  if (graph.scope) return own;
  return [...own, ...(graph.functions ?? []).flatMap((item) => item.edges.map((edge) => edge.id))];
}

/** Every graph in the document: the main one first, then each function's. */
export function graphsOf(doc: FlowGraph): { scope: string | null; nodes: FlowNode[]; edges: FlowEdge[] }[] {
  return [
    { scope: null, nodes: doc.nodes, edges: doc.edges },
    ...(doc.functions ?? []).map((item) => ({ scope: item.id, nodes: item.nodes, edges: item.edges })),
  ];
}

/** What a node of a function carries of it: its name, for the node's line, and its pins. */
export function signatureOf(found: FlowFunction | undefined): {
  name: string;
  inputs: FlowParam[];
  outputs: FlowParam[];
} {
  return { name: found?.name ?? "", inputs: found?.inputs ?? [], outputs: found?.outputs ?? [] };
}

/**
 * Whether the add-node menu offers a kind in this graph. An Entry comes with
 * its function, and a call is offered once per function rather than blank.
 */
export function offeredHere(kind: string, scope: string | null | undefined): boolean {
  if (kind === "function_entry" || kind === "call_function") return false;
  if (kind === "function_return") return Boolean(scope);
  if (kind === "start" || kind === "end") return !scope;
  return true;
}

/** The functions a graph may call: not itself, and not a collapsed one made for one place. */
export function callable(doc: FlowGraph, scope: string | null | undefined): FlowFunction[] {
  return (doc.functions ?? []).filter((item) => item.id !== scope && !item.collapsed);
}

/**
 * Every Entry, Return and Call carrying its function's signature as it now is,
 * and no wire left on a pin a signature no longer has. The same document comes
 * back when nothing needed changing.
 */
export function withSignatures(doc: FlowGraph): FlowGraph {
  const byId = new Map((doc.functions ?? []).map((item) => [item.id, item]));
  const fix = (nodes: FlowNode[], edges: FlowEdge[], owner: string | null) => {
    let changed = false;
    const valid = new Map<string, Set<string>>();
    const next = nodes.map((node) => {
      if (!SIGNATURE_KINDS[node.kind]) return node;
      const found =
        node.kind === "call_function" ? byId.get(String(node.data?.function ?? "")) : owner ? byId.get(owner) : undefined;
      const signature = signatureOf(found);
      valid.set(node.id, new Set([...signature.inputs, ...signature.outputs].map((param) => param.id)));
      if (JSON.stringify(node.data?.signature ?? null) === JSON.stringify(signature)) return node;
      changed = true;
      return { ...node, data: { ...node.data, signature } };
    });
    const kept = edges.filter((edge) =>
      [edge.from, edge.to].every(([nodeId, pin]) => {
        const pins = valid.get(nodeId);
        return !pins || pin === "in" || pin === "out" || pins.has(pin);
      })
    );
    if (kept.length !== edges.length) changed = true;
    return changed ? { nodes: next, edges: kept } : null;
  };

  let result = doc;
  const main = fix(doc.nodes, doc.edges, null);
  if (main) result = { ...result, ...main };
  if (doc.functions?.length) {
    let touched = false;
    const functions = doc.functions.map((item) => {
      const fixed = fix(item.nodes, item.edges, item.id);
      if (!fixed) return item;
      touched = true;
      return { ...item, ...fixed };
    });
    if (touched) result = { ...result, functions };
  }
  return result;
}

/** A name no other function has: "Function", "Function 2", ... */
export function freshFunctionName(doc: FlowGraph, base: string): string {
  const taken = new Set((doc.functions ?? []).map((item) => item.name));
  if (!taken.has(base)) return base;
  for (let index = 2; ; index += 1) {
    if (!taken.has(`${base} ${index}`)) return `${base} ${index}`;
  }
}

/** A new function, its Entry already wired into one Return. */
export function newFunction(doc: FlowGraph, name: string, collapsed = false): FlowFunction {
  const id = nextId("f", (doc.functions ?? []).map((item) => item.id));
  const entry: FlowNode = { id: `${id}.n1`, kind: "function_entry", pos: [0, 0], data: {} };
  const exit: FlowNode = { id: `${id}.n2`, kind: "function_return", pos: [480, 0], data: {} };
  const made: FlowFunction = {
    id,
    name,
    inputs: [],
    outputs: [],
    nodes: [entry, exit],
    edges: [{ id: `${id}.e1`, kind: "exec", from: [entry.id, "out"], to: [exit.id, "in"] }],
    view: { pan: [160, 200], zoom: 1 },
  };
  if (collapsed) made.collapsed = true;
  return made;
}

/** A parameter id not yet used on either side of a function. */
export function freshParamId(found: FlowFunction): string {
  return nextId("p", [...found.inputs, ...found.outputs].map((param) => param.id));
}

/** Kinds a selection must not carry into a function: each belongs where it is. */
const STAYS_PUT = new Set(["start", "end", "function_entry", "function_return"]);

export type PinTypeOf = (nodeId: string, pin: string, direction: "in" | "out") => string;

/**
 * Unreal's "Collapse to Function": the selected nodes move into a new function,
 * and one Call takes their place. Wires crossing the selection's edge become
 * the function's inputs and outputs; the flow goes in through the Entry and
 * out through the Return, so the selection may have one way in and one way out.
 */
export function collapse(
  doc: FlowGraph,
  view: FlowGraph,
  ids: string[],
  typeOf: PinTypeOf,
  name: string
): { doc: FlowGraph; callId: string } | { problem: string } {
  const inside = new Set(ids.filter((id) => view.nodes.some((node) => node.id === id)));
  if (inside.size === 0) return { problem: "Select the nodes to collapse." };
  if (view.nodes.some((node) => inside.has(node.id) && STAYS_PUT.has(node.kind))) {
    return { problem: "Start, End, Entry and Return stay where they are; leave them out of the selection." };
  }
  const crossing = (edge: FlowEdge) => inside.has(edge.from[0]) !== inside.has(edge.to[0]);
  const execIn = view.edges.filter((edge) => edge.kind === "exec" && crossing(edge) && inside.has(edge.to[0]));
  const execOut = view.edges.filter((edge) => edge.kind === "exec" && crossing(edge) && inside.has(edge.from[0]));
  const key = (end: [string, string]) => end.join(":");
  if (new Set(execIn.map((edge) => key(edge.to))).size > 1) {
    return { problem: "A function has one way in, and the flow enters this selection in more than one place." };
  }
  if (execOut.length > 1) {
    return { problem: "A function has one way out, and the flow leaves this selection in more than one place." };
  }

  const made = newFunction(doc, name, true);
  const fid = made.id;
  const entry = made.nodes[0];
  const exit = made.nodes[1];
  const moved = view.nodes.filter((node) => inside.has(node.id));
  const minX = Math.min(...moved.map((node) => node.pos[0]));
  const maxX = Math.max(...moved.map((node) => node.pos[0]));
  const midY = Math.round(moved.reduce((sum, node) => sum + node.pos[1], 0) / moved.length);
  entry.pos = [minX - 320, midY];
  exit.pos = [maxX + 400, midY];

  // What moves in takes the function's ids, so the graph it left can never hand them out again.
  const renamed = new Map<string, string>();
  const nodeIds = [entry.id, exit.id];
  for (const node of moved) {
    const id = nextId(`${fid}.n`, nodeIds);
    nodeIds.push(id);
    renamed.set(node.id, id);
  }
  const there = (end: [string, string]): [string, string] => [renamed.get(end[0]) ?? end[0], end[1]];

  const inner: FlowEdge[] = [];
  const innerEdge = (kind: "exec" | "data", from: [string, string], to: [string, string]) =>
    inner.push({ id: nextId(`${fid}.e`, inner.map((edge) => edge.id)), kind, from: there(from), to: there(to) });
  for (const edge of view.edges) {
    if (inside.has(edge.from[0]) && inside.has(edge.to[0])) innerEdge(edge.kind, edge.from, edge.to);
  }
  const outer: FlowEdge[] = view.edges.filter((edge) => !inside.has(edge.from[0]) && !inside.has(edge.to[0]));
  const outerTaken = takenEdgeIds(view);
  const callId = nextId(idPrefix(view, "n"), takenNodeIds(view));
  const outerEdge = (kind: "exec" | "data", from: [string, string], to: [string, string]) => {
    const id = nextId(idPrefix(view, "e"), outerTaken);
    outerTaken.push(id);
    outer.push({ id, kind, from, to });
  };

  const params = (): FlowParam[] => [...made.inputs, ...made.outputs];
  const param = (side: "inputs" | "outputs", label: string, type: string): FlowParam => {
    const taken = new Set(made[side].map((item) => item.name));
    let unique = label || (side === "inputs" ? "input" : "output");
    for (let index = 2; taken.has(unique); index += 1) unique = `${label}${index}`;
    const created = { id: nextId("p", params().map((item) => item.id)), name: unique, type };
    made[side].push(created);
    return created;
  };

  // The flow: in through the Entry, out through the Return.
  if (execIn.length) {
    innerEdge("exec", [entry.id, "out"], execIn[0].to);
    for (const edge of execIn) outerEdge("exec", edge.from, [callId, "in"]);
  }
  if (execOut.length) {
    innerEdge("exec", execOut[0].from, [exit.id, "in"]);
    outerEdge("exec", [callId, "out"], execOut[0].to);
  }
  if (!execIn.length && !execOut.length) innerEdge("exec", [entry.id, "out"], [exit.id, "in"]);
  // The template's own Entry -> Return wire is replaced by what the selection needs.
  made.edges = [];

  // A value coming in is one input per source, however many nodes inside read it.
  const inputs = new Map<string, FlowParam>();
  for (const edge of view.edges.filter((item) => item.kind === "data" && crossing(item) && inside.has(item.to[0]))) {
    const source = key(edge.from);
    let slot = inputs.get(source);
    if (!slot) {
      slot = param("inputs", edge.to[1], typeOf(edge.from[0], edge.from[1], "out"));
      inputs.set(source, slot);
      outerEdge("data", edge.from, [callId, slot.id]);
    }
    innerEdge("data", [entry.id, slot.id], edge.to);
  }
  // A value going out is one output per source inside, however many nodes outside read it.
  const outputs = new Map<string, FlowParam>();
  for (const edge of view.edges.filter((item) => item.kind === "data" && crossing(item) && inside.has(item.from[0]))) {
    const source = key(edge.from);
    let slot = outputs.get(source);
    if (!slot) {
      slot = param("outputs", edge.from[1], typeOf(edge.from[0], edge.from[1], "out"));
      outputs.set(source, slot);
      innerEdge("data", edge.from, [exit.id, slot.id]);
    }
    outerEdge("data", [callId, slot.id], edge.to);
  }

  made.nodes = [entry, ...moved.map((node) => ({ ...node, id: renamed.get(node.id)! })), exit];
  made.edges = inner;
  const call: FlowNode = {
    id: callId,
    kind: "call_function",
    pos: [Math.round((minX + maxX) / 2), midY],
    data: { function: fid },
  };
  const parent: FlowGraph = {
    ...view,
    nodes: [...view.nodes.filter((node) => !inside.has(node.id)), call],
    edges: outer,
  };
  const written = embed(doc, parent, true);
  return {
    doc: withSignatures({ ...written, functions: [...(written.functions ?? []), made] }),
    callId,
  };
}
