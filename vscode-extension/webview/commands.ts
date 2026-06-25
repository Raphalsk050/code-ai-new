// Slash commands available from the composer. These are handled client-side in
// the webview (the bridge runs turns, not terminal-style commands), so the list
// is intentionally the subset that maps to host actions the extension exposes.

export type CommandAction =
  | { kind: "clear" }
  | { kind: "new" }
  | { kind: "compact" }
  | { kind: "cancel" }
  | { kind: "permission"; mode: "ask" | "auto" | "bypass" };

export interface SlashCommand {
  /** What the user types, e.g. `/mode auto`. */
  command: string;
  description: string;
  action: CommandAction;
}

export const SLASH_COMMANDS: SlashCommand[] = [
  { command: "/new", description: "Start a new conversation", action: { kind: "new" } },
  { command: "/clear", description: "Clear the messages in this view", action: { kind: "clear" } },
  { command: "/compact", description: "Compress the conversation context", action: { kind: "compact" } },
  { command: "/cancel", description: "Cancel the active turn", action: { kind: "cancel" } },
  { command: "/mode ask", description: "Permissions: ask every time", action: { kind: "permission", mode: "ask" } },
  { command: "/mode auto", description: "Permissions: auto (read-only safe)", action: { kind: "permission", mode: "auto" } },
  { command: "/mode bypass", description: "Permissions: bypass (allow all)", action: { kind: "permission", mode: "bypass" } },
];

/** Commands whose `command` starts with the typed prefix (case-insensitive). */
export function matchCommands(draft: string): SlashCommand[] {
  const text = draft.trimStart().toLowerCase();
  if (!text.startsWith("/")) return [];
  return SLASH_COMMANDS.filter((c) => c.command.toLowerCase().startsWith(text));
}

/** True when the draft is exactly one command (so submitting should run it). */
export function exactCommand(draft: string): SlashCommand | undefined {
  const text = draft.trim().toLowerCase();
  return SLASH_COMMANDS.find((c) => c.command.toLowerCase() === text);
}
