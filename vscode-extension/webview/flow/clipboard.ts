// Copy and paste of a subgraph.
//
// The payload travels as JSON text on the system clipboard, so a subgraph can
// be pasted into another graph - or another window. Reading it back happens in
// the DOM `paste` event, which is the dependable path inside a webview:
// `navigator.clipboard.readText()` needs focus plus a user gesture and fails
// silently without both. An in-memory buffer covers the case where the
// clipboard refuses entirely.

import type { FlowEdge, FlowGraph, FlowNode } from "../../src/flow-schema";
import { nextId } from "../../src/flow-schema";

export const CLIP_MARKER = "codeai.flow.clip";
export const PASTE_OFFSET = 24;

export interface Clip {
  [CLIP_MARKER]: number;
  nodes: FlowNode[];
  edges: FlowEdge[];
}

let memory: Clip | null = null;

/** Serialize the selection: the nodes, plus only the edges inside it. */
export function buildClip(graph: FlowGraph, selection: string[]): Clip | null {
  const ids = new Set(selection);
  const nodes = graph.nodes.filter((node) => ids.has(node.id));
  if (nodes.length === 0) return null;
  const edges = graph.edges.filter((edge) => ids.has(edge.from[0]) && ids.has(edge.to[0]));
  return { [CLIP_MARKER]: 1, nodes: JSON.parse(JSON.stringify(nodes)), edges: JSON.parse(JSON.stringify(edges)) };
}

export function rememberClip(clip: Clip | null): void {
  memory = clip;
}

export function lastClip(): Clip | null {
  return memory;
}

export function parseClip(text: string): Clip | null {
  if (!text || !text.includes(CLIP_MARKER)) return null;
  try {
    const raw = JSON.parse(text) as Clip;
    if (!raw || typeof raw !== "object" || !raw[CLIP_MARKER]) return null;
    if (!Array.isArray(raw.nodes) || !Array.isArray(raw.edges)) return null;
    return raw;
  } catch {
    return null;
  }
}

/**
 * Give a clip fresh ids against the graph it is landing in, and offset it so
 * the copy is visibly a copy rather than hidden under the original.
 */
export function instantiate(
  clip: Clip,
  graph: FlowGraph,
  offset = PASTE_OFFSET
): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const usedNodes = new Set(graph.nodes.map((node) => node.id));
  const usedEdges = new Set(graph.edges.map((edge) => edge.id));
  const remap = new Map<string, string>();

  const nodes = clip.nodes.map((node) => {
    const id = nextId("n", usedNodes);
    usedNodes.add(id);
    remap.set(node.id, id);
    return {
      ...node,
      id,
      pos: [node.pos[0] + offset, node.pos[1] + offset] as [number, number],
      data: { ...node.data },
    };
  });

  const edges = clip.edges.flatMap((edge) => {
    const from = remap.get(edge.from[0]);
    const to = remap.get(edge.to[0]);
    if (!from || !to) return [];
    const id = nextId("e", usedEdges);
    usedEdges.add(id);
    return [
      {
        ...edge,
        id,
        from: [from, edge.from[1]] as [string, string],
        to: [to, edge.to[1]] as [string, string],
      },
    ];
  });

  return { nodes, edges };
}

/**
 * Best-effort write to the system clipboard; the memory buffer always works.
 *
 * A webview without clipboard permission answers with a *rejected promise*, not
 * a throw, so the catch has to be on the promise - an unhandled rejection shows
 * up as a page error and buries whatever the user was actually debugging.
 */
export function writeClip(clip: Clip): void {
  rememberClip(clip);
  const text = JSON.stringify(clip);
  try {
    navigator.clipboard?.writeText(text).catch(() => {
      // Denied or unavailable: the in-memory copy is what paste will use.
    });
  } catch {
    // No clipboard API at all (an old webview): same fallback.
  }
}
