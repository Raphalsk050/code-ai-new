// The MCP server editor's form, and the text boxes it is made of.
//
// Pure functions so they can be tested without a DOM: the panel keeps a
// ServerForm in state and turns it into the declaration the bridge saves.

import type { McpServerInfo, McpServerSpec } from "../src/protocol";

export type McpTransport = "stdio" | "http" | "sse";
export type McpDefer = "auto" | "always" | "never";

export interface ServerForm {
  name: string;
  transport: McpTransport;
  command: string;
  /** One argument per line, so an argument with spaces needs no quoting. */
  args: string;
  /** KEY=value per line. */
  env: string;
  cwd: string;
  url: string;
  /** Name: value per line. */
  headers: string;
  trust: boolean;
  defer: McpDefer;
  enabled: boolean;
}

export const EMPTY_FORM: ServerForm = {
  name: "",
  transport: "stdio",
  command: "",
  args: "",
  env: "",
  cwd: "",
  url: "",
  headers: "",
  trust: false,
  defer: "auto",
  enabled: true,
};

export function linesOf(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

/** `KEY=value` (env) or `Name: value` (headers) lines as a map. */
export function mapOf(text: string, separator: "=" | ":"): Record<string, string> {
  const map: Record<string, string> = {};
  for (const line of linesOf(text)) {
    const at = line.indexOf(separator);
    if (at <= 0) continue;
    map[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return map;
}

export function linesFromMap(map: Record<string, string>, separator: "=" | ":"): string {
  const glue = separator === ":" ? ": " : "=";
  return Object.entries(map)
    .map(([key, value]) => `${key}${glue}${value}`)
    .join("\n");
}

export function formFromSpec(spec: McpServerSpec): ServerForm {
  return {
    name: spec.name,
    transport: (spec.transport as McpTransport) || "stdio",
    command: spec.command ?? "",
    args: (spec.args ?? []).join("\n"),
    env: linesFromMap(spec.env ?? {}, "="),
    cwd: spec.cwd ?? "",
    url: spec.url ?? "",
    headers: linesFromMap(spec.headers ?? {}, ":"),
    trust: Boolean(spec.trust),
    defer: (spec.defer as McpDefer) || "auto",
    enabled: spec.enabled !== false,
  };
}

/** What `mcpSave` takes: the declaration, with only the fields its transport uses. */
export function declarationOf(form: ServerForm): Record<string, unknown> {
  const declaration: Record<string, unknown> = {
    name: form.name.trim(),
    transport: form.transport,
    enabled: form.enabled,
    trust: form.trust,
    defer: form.defer,
  };
  if (form.transport === "stdio") {
    declaration.command = form.command.trim();
    declaration.args = linesOf(form.args);
    declaration.env = mapOf(form.env, "=");
    if (form.cwd.trim()) declaration.cwd = form.cwd.trim();
  } else {
    declaration.url = form.url.trim();
    declaration.headers = mapOf(form.headers, ":");
  }
  return declaration;
}

/** Why the form cannot be saved yet, or "" when it can. */
export function formProblem(form: ServerForm, taken: string[]): string {
  const name = form.name.trim();
  if (!name) return "Give the server a name.";
  if (!/^[A-Za-z0-9_-]+$/.test(name)) return "Use letters, digits, - and _ in the name.";
  if (taken.includes(name)) return "There is already a server with this name.";
  if (form.transport === "stdio" && !form.command.trim()) return "Say which command starts it.";
  if (form.transport !== "stdio" && !/^https?:\/\//.test(form.url.trim()))
    return "The URL must start with http:// or https://.";
  return "";
}

export interface ImportedServer {
  name: string;
  declaration: Record<string, unknown>;
}

/**
 * Servers out of a pasted snippet, in any shape a README hands over:
 * `{"mcpServers": {...}}` (Claude, Cursor), `{"servers": {...}}` (VS Code),
 * or the bare `{"name": {...}}` map.
 */
export function parseImport(text: string): { servers: ImportedServer[]; error: string } {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch (err) {
    return { servers: [], error: `Not valid JSON: ${(err as Error).message}` };
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return { servers: [], error: "Paste a JSON object." };
  }
  const root = data as Record<string, unknown>;
  const block = (root.mcpServers ?? root.servers ?? root.mcp_servers ?? root) as Record<
    string,
    unknown
  >;
  const servers: ImportedServer[] = [];
  for (const [name, entry] of Object.entries(block ?? {})) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) continue;
    const declaration = entry as Record<string, unknown>;
    if (!declaration.command && !declaration.url && !declaration.serverUrl) continue;
    servers.push({ name: name.replace(/[^A-Za-z0-9_-]+/g, "_"), declaration: { ...declaration, name } });
  }
  if (servers.length === 0) {
    return { servers, error: "No server found: each one needs a command or a url." };
  }
  for (const server of servers) server.declaration.name = server.name;
  return { servers, error: "" };
}

/** The panel's list after a status event: the event's news, the list's secrets. */
export function mergeStatus(list: McpServerInfo[], update: McpServerInfo): McpServerInfo[] {
  const at = list.findIndex((server) => server.name === update.name);
  if (at < 0) return [...list, update];
  // Events carry env and header values masked; the editor needs the real ones.
  const next = { ...update, spec: list[at].spec };
  return list.map((server, index) => (index === at ? next : server));
}

export const STATUS_TEXT: Record<string, string> = {
  connected: "connected",
  connecting: "connecting…",
  failed: "failed",
  disabled: "off",
  needs_approval: "waiting for approval",
  needs_auth: "needs sign-in",
  invalid: "invalid",
};
