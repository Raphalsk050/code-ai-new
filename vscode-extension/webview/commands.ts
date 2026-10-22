// The composer's slash commands. The catalog is the backend's (the bridge sends
// it on connect, so the TUI and the extension never drift); this module only
// decides what the webview does with the one the user picked.

import type { ReportMethod, SlashCommandSpec } from "../src/protocol";

export type CommandAction =
  | { kind: "clear" }
  | { kind: "new" }
  | { kind: "compact" }
  | { kind: "cancel" }
  | { kind: "permission"; mode: "ask" | "auto" | "bypass" }
  | { kind: "planner"; mode: "auto" | "plan" | "act" }
  | { kind: "replan" }
  | { kind: "goal"; action: "define" | "start" | "stop" | "status" }
  | { kind: "terminal"; action: "start" | "write" | "ctrl" | "kill" | "status" }
  | { kind: "index"; full: boolean }
  | { kind: "board" }
  | { kind: "settings" }
  | { kind: "settings-update"; field: string }
  | { kind: "history" }
  | { kind: "assets"; which: "skills" | "workflows" }
  | { kind: "report"; method: ReportMethod }
  | { kind: "asset" }
  | { kind: "update" }
  | { kind: "unsupported" };

export interface SlashCommand extends SlashCommandSpec {
  action: CommandAction;
}

/** Commands the webview handles by itself, with no backend catalog needed. */
const LOCAL: { command: string; description: string; action: CommandAction }[] = [
  { command: "/new", description: "New conversation", action: { kind: "new" } },
  { command: "/clear", description: "Clears the transcript in this view", action: { kind: "clear" } },
  { command: "/history", description: "Opens the conversation history", action: { kind: "history" } },
  { command: "/settings", description: "Opens the settings", action: { kind: "settings" } },
];

/** What a backend command maps to in the webview. Prefix match, longest first. */
const ROUTES: { prefix: string; action: CommandAction }[] = [
  { prefix: "/compact", action: { kind: "compact" } },
  { prefix: "/cancel", action: { kind: "cancel" } },
  { prefix: "/clear", action: { kind: "clear" } },
  { prefix: "/mode ask", action: { kind: "permission", mode: "ask" } },
  { prefix: "/mode auto", action: { kind: "permission", mode: "auto" } },
  { prefix: "/mode bypass", action: { kind: "permission", mode: "bypass" } },
  { prefix: "/mode", action: { kind: "permission", mode: "ask" } },
  { prefix: "/auto", action: { kind: "planner", mode: "auto" } },
  { prefix: "/plan-status", action: { kind: "unsupported" } },
  { prefix: "/plan", action: { kind: "planner", mode: "plan" } },
  { prefix: "/act", action: { kind: "planner", mode: "act" } },
  { prefix: "/replan", action: { kind: "replan" } },
  { prefix: "/goal start", action: { kind: "goal", action: "start" } },
  { prefix: "/goal stop", action: { kind: "goal", action: "stop" } },
  { prefix: "/goal resume", action: { kind: "goal", action: "start" } },
  { prefix: "/goal status", action: { kind: "goal", action: "status" } },
  { prefix: "/goal", action: { kind: "goal", action: "define" } },
  { prefix: "/index full", action: { kind: "index", full: true } },
  { prefix: "/index status", action: { kind: "unsupported" } },
  { prefix: "/index", action: { kind: "index", full: false } },
  { prefix: "/term start", action: { kind: "terminal", action: "start" } },
  { prefix: "/term kill", action: { kind: "terminal", action: "kill" } },
  { prefix: "/term status", action: { kind: "terminal", action: "status" } },
  { prefix: "/term ctrl", action: { kind: "terminal", action: "ctrl" } },
  { prefix: "/term", action: { kind: "terminal", action: "write" } },
  { prefix: "/board", action: { kind: "board" } },
  { prefix: "/skills", action: { kind: "assets", which: "skills" } },
  { prefix: "/rules", action: { kind: "report", method: "listRules" } },
  { prefix: "/agents", action: { kind: "report", method: "listAgents" } },
  // The servers live in the settings panel, with their switches and status.
  { prefix: "/mcp", action: { kind: "settings" } },
  { prefix: "/workflows", action: { kind: "assets", which: "workflows" } },
  { prefix: "/update", action: { kind: "update" } },
  { prefix: "/doctor", action: { kind: "settings" } },
  { prefix: "/status", action: { kind: "settings" } },
  { prefix: "/config show", action: { kind: "settings" } },
  { prefix: "/config help", action: { kind: "settings" } },
  { prefix: "/config models", action: { kind: "settings" } },
  { prefix: "/config model", action: { kind: "settings-update", field: "model" } },
  { prefix: "/config api-key", action: { kind: "settings-update", field: "api_key" } },
  { prefix: "/config api-mode", action: { kind: "settings-update", field: "api_mode" } },
  { prefix: "/config base-url", action: { kind: "settings-update", field: "base_url" } },
  { prefix: "/config language", action: { kind: "settings-update", field: "language" } },
  { prefix: "/config effort", action: { kind: "settings-update", field: "reasoning_effort" } },
  { prefix: "/config workspace", action: { kind: "settings-update", field: "workspace" } },
  {
    prefix: "/config max-context-window",
    action: { kind: "settings-update", field: "max_context_tokens" },
  },
  { prefix: "/config learn", action: { kind: "settings-update", field: "learn" } },
  { prefix: "/config desktop-avatar", action: { kind: "settings-update", field: "desktop_avatar" } },
  { prefix: "/config", action: { kind: "settings" } },
  { prefix: "/quit", action: { kind: "unsupported" } },
];

function routeFor(command: string): CommandAction {
  const text = command.toLowerCase();
  let best: { prefix: string; action: CommandAction } | null = null;
  for (const route of ROUTES) {
    if (!text.startsWith(route.prefix)) continue;
    if (!best || route.prefix.length > best.prefix.length) best = route;
  }
  return best ? best.action : { kind: "unsupported" };
}

/** Merge the backend catalog with the webview's own commands. */
export function buildCatalog(specs: SlashCommandSpec[]): SlashCommand[] {
  const local: SlashCommand[] = LOCAL.map((c) => ({
    command: c.command,
    description: c.description,
    completion: c.command,
    available: true,
    action: c.action,
  }));
  const taken = new Set(local.map((c) => c.command));
  const remote: SlashCommand[] = specs
    .filter((spec) => !taken.has(spec.command))
    .map((spec) => {
      // A workflow, a skill or an MCP prompt is not in the route table: it is
      // whatever the user put on disk or a server offers, resolved by name.
      if (spec.kind === "workflow" || spec.kind === "skill" || spec.kind === "mcp") {
        return { ...spec, action: { kind: "asset" } as CommandAction, available: true };
      }
      const action = routeFor(spec.command);
      return {
        ...spec,
        action,
        // The webview handles some of what the bridge cannot (settings,
        // history), so availability is the union, not the bridge's word alone.
        available:
          action.kind === "unsupported"
            ? false
            : spec.available ||
              action.kind === "settings" ||
              action.kind === "settings-update" ||
              action.kind === "history" ||
              action.kind === "assets" ||
              action.kind === "clear" ||
              action.kind === "new",
      };
    });
  return [...local, ...remote];
}

/** Catalog entries whose command starts with what is typed. */
export function matchCommands(catalog: SlashCommand[], draft: string): SlashCommand[] {
  const text = draft.trimStart().toLowerCase();
  if (!text.startsWith("/")) return [];
  const head = text.split(/\s+/)[0];
  return catalog
    .filter((c) => {
      const command = c.command.toLowerCase();
      return command.startsWith(text) || command.startsWith(head);
    })
    .slice(0, 12);
}

/** The command the draft names, plus whatever the user typed after it. */
export function resolveCommand(
  catalog: SlashCommand[],
  draft: string
): { command: SlashCommand; argument: string } | null {
  const text = draft.trim();
  if (!text.startsWith("/")) return null;
  const lower = text.toLowerCase();
  let best: SlashCommand | null = null;
  for (const entry of catalog) {
    // `/goal <objetivo>` in the catalog is `/goal` plus an argument.
    const name = entry.command.split(" <")[0].toLowerCase();
    if (lower !== name && !lower.startsWith(name + " ")) continue;
    if (!best || name.length > best.command.split(" <")[0].length) best = entry;
  }
  if (!best) return null;
  const name = best.command.split(" <")[0];
  return { command: best, argument: text.slice(name.length).trim() };
}
