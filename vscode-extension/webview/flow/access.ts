// What an agent may touch, worked out the way the backend will resolve it.
// The inspector shows it and the node draws it, so both must agree with a run:
// wired Tools nodes are the whole grant, otherwise each switch set on the node
// overrides its profession, and blocked tools always go.

import type {
  FlowGraph,
  FlowNode,
  PermissionSpec,
  ProfessionSpec,
  ToolSpec,
} from "../../src/flow-schema";
import type { Catalog } from "./model";

export interface PermissionState {
  spec: PermissionSpec;
  on: boolean;
  /** Set on the node itself rather than inherited. */
  explicit: boolean;
}

export interface ToolGroupView {
  name: string;
  title: string;
  tools: ToolSpec[];
}

export function asFlag(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  return ["true", "1", "yes", "on", "sim"].includes(String(value ?? "").trim().toLowerCase());
}

/** A stored tool list, tolerating whatever a hand edit left in it. */
export function toolNames(node: FlowNode | undefined, field = "tools"): string[] {
  const raw = node?.data?.[field];
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  for (const item of raw) {
    if (typeof item === "string" && item.trim()) seen.add(item.trim());
  }
  return [...seen];
}

/** The built-in profession an Agent node hires; the plain one when empty. */
export function professionOf(
  catalog: Catalog | null,
  node: FlowNode
): ProfessionSpec | undefined {
  const list = catalog?.raw.professions ?? [];
  const name = String(node.data?.profession ?? "").trim();
  return name ? list.find((item) => item.name === name) : list.find((item) => item.plain);
}

function wiredInto(graph: FlowGraph, nodeId: string, pin: string): FlowNode[] {
  const ids = graph.edges
    .filter((edge) => edge.kind === "data" && edge.to[0] === nodeId && edge.to[1] === pin)
    .map((edge) => edge.from[0]);
  return ids.flatMap((id) => graph.nodes.filter((node) => node.id === id));
}

/** The Profession node on an agent's profession pin, if one is wired. */
export function wiredProfession(graph: FlowGraph, nodeId: string): FlowNode | undefined {
  return wiredInto(graph, nodeId, "profession").find((node) => node.kind === "custom_profession");
}

/** The Tools nodes on an agent's tools pin, in the order they were wired. */
export function wiredToolsets(graph: FlowGraph, nodeId: string): FlowNode[] {
  return wiredInto(graph, nodeId, "tools").filter((node) => node.kind === "tools");
}

/** The five switches as they will apply to this node's agent. */
export function permissionsOf(
  catalog: Catalog | null,
  graph: FlowGraph,
  node: FlowNode
): PermissionState[] {
  const specs = catalog?.raw.permissions ?? [];
  const hired = node.kind === "profession" ? wiredProfession(graph, node.id) : undefined;
  const profession = node.kind === "profession" && !hired ? professionOf(catalog, node) : undefined;
  return specs.map((spec) => {
    const own = node.data?.[spec.field];
    if (own !== undefined && own !== null) return { spec, on: asFlag(own), explicit: true };
    if (node.kind !== "profession") {
      return { spec, on: spec.field === "can_read", explicit: false };
    }
    if (hired) {
      const inherited = hired.data?.[spec.field];
      const on =
        inherited === undefined || inherited === null
          ? spec.field === "can_read"
          : asFlag(inherited);
      return { spec, on, explicit: false };
    }
    return { spec, on: Boolean(profession?.capabilities.includes(spec.capability)), explicit: false };
  });
}

/** The tools this agent will actually be given, in catalog order. */
export function grantedTools(catalog: Catalog | null, graph: FlowGraph, node: FlowNode): string[] {
  const tools = (catalog?.raw.tools ?? []).filter((tool) => tool.enabled);
  const hired = node.kind === "profession" ? wiredProfession(graph, node.id) : undefined;
  const blocked = new Set([...toolNames(node, "blocked_tools"), ...toolNames(hired, "blocked_tools")]);
  const wired = node.kind === "profession" ? wiredToolsets(graph, node.id) : [];
  if (wired.length > 0) {
    const picked = new Set(wired.flatMap((item) => toolNames(item)));
    return tools.filter((tool) => picked.has(tool.name) && !blocked.has(tool.name)).map((t) => t.name);
  }
  const caps = new Set(
    permissionsOf(catalog, graph, node)
      .filter((item) => item.on)
      .map((item) => item.spec.capability)
  );
  return tools
    .filter(
      (tool) =>
        tool.capabilities.length > 0 &&
        tool.capabilities.every((capability) => caps.has(capability)) &&
        !blocked.has(tool.name)
    )
    .map((tool) => tool.name);
}

/** The catalog's tools in their display groups, filtered by a search. */
export function groupTools(catalog: Catalog | null, query = ""): ToolGroupView[] {
  const tools = catalog?.raw.tools ?? [];
  const order = catalog?.raw.tool_groups ?? [];
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const matches = tools.filter((tool) => {
    const haystack = `${tool.name} ${tool.description} ${tool.group}`.toLowerCase();
    return words.every((word) => haystack.includes(word));
  });
  const known = new Set(order.map((group) => group.name));
  const groups: ToolGroupView[] = order.map((group) => ({
    name: group.name,
    title: group.title,
    tools: matches.filter((tool) => tool.group === group.name),
  }));
  const rest = matches.filter((tool) => !known.has(tool.group));
  if (rest.length > 0) groups.push({ name: "other", title: "Other", tools: rest });
  return groups.filter((group) => group.tools.length > 0);
}
