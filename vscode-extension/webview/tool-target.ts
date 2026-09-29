// What a tool call works on, in one short line. Mirrors code_ai/tools/summary.py:
// live events carry the backend's answer as `target`, and this covers the calls
// of a saved conversation, which only have their arguments.

const MAX_CHARS = 80;

// Most specific first: a search is about its query more than its folder. Typed
// text and key presses are left out on purpose: they can be a password.
const TARGET_KEYS = [
  "query",
  "path",
  "command",
  "url",
  "source",
  "output",
  "name",
  "site",
  "selector",
  "expression",
  "question",
  "completed_step",
  "group",
  "session_id",
  "focus",
  "action",
  "aspect",
];
const PATH_KEYS = new Set(["path", "source", "output"]);
const COUNTED: [string, string][] = [
  ["tasks", "task"],
  ["steps", "step"],
  ["operations", "change"],
  ["sources", "file"],
];
const SECRET_HINTS = ["password", "passwd", "secret", "token", "key", "auth", "credential"];
const TYPED = new Set(["text", "content", "values", "chord", "new_text", "old_text"]);

const oneLine = (text: string) => text.split(/\s+/).filter(Boolean).join(" ");

function pick(args: Record<string, unknown>): [string, string] {
  const argv = args.argv;
  if (Array.isArray(argv) && argv.length) return ["command", argv.map(String).join(" ")];
  for (const key of TARGET_KEYS) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) return [key, oneLine(value)];
  }
  for (const [key, noun] of COUNTED) {
    const value = args[key];
    if (Array.isArray(value) && value.length) {
      return [key, `${value.length} ${noun}${value.length === 1 ? "" : "s"}`];
    }
  }
  for (const [key, value] of Object.entries(args)) {
    const lower = key.toLowerCase();
    if (TYPED.has(key) || SECRET_HINTS.some((hint) => lower.includes(hint))) continue;
    if (typeof value === "string" && value.trim() && value.trim().length <= 200) {
      return [key, oneLine(value)];
    }
  }
  return ["", ""];
}

export function callTarget(args: unknown): string {
  if (!args || typeof args !== "object" || Array.isArray(args)) return "";
  const record = args as Record<string, unknown>;
  const [key, found] = pick(record);
  let target = found;
  if (!target) return "";
  if (key === "path" && typeof record.start_line === "number") {
    target += `:${record.start_line}`;
    if (typeof record.end_line === "number") target += `-${record.end_line}`;
  } else if (key === "query" && typeof record.path === "string") {
    const where = oneLine(record.path);
    if (where && where !== ".") target = `${target} in ${where}`;
  }
  if (target.length <= MAX_CHARS) return target;
  return PATH_KEYS.has(key)
    ? "…" + target.slice(-(MAX_CHARS - 1))
    : target.slice(0, MAX_CHARS - 1) + "…";
}

/** The icon family a tool belongs to, by what it touches. */
export type ToolKind = "read" | "write" | "run" | "search" | "web" | "agent" | "plan" | "tool";

export function toolKind(name: string): ToolKind {
  if (/^(search_|web_search)/.test(name)) return "search";
  if (/^(execute_command|start_terminal|send_terminal_text|read_screen|terminal_|interrupt_terminal|terminate_terminal)/.test(name)) {
    return "run";
  }
  if (/^(browser_|ui_preview|ui_audit|web_)/.test(name)) return "web";
  if (/^(dispatch_agent)/.test(name)) return "agent";
  if (/^(submit_plan|complete_plan_step|complete_task)/.test(name)) return "plan";
  if (/(^write_file|^edit_code|_edit$|_create$|_format$|_convert$|^create_)/.test(name)) return "write";
  if (/(^read_file|^list_files|_inspect$)/.test(name)) return "read";
  return "tool";
}
