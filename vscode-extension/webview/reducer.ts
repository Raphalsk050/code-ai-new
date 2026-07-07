// Structured chat state built from the Code-AI event stream. This is the
// TypeScript counterpart of `src/code_ai/ui/terminal/view_models.py` `apply()`,
// but it produces rich, typed items (not flat strings) so the UI can render a
// Claude/Codex/Cline-style transcript. Event names are the versioned contract.

import type { EventEnvelope, StoredMessage } from "../src/protocol";

export type ToolStatus = "running" | "done" | "failed";
export type NoticeLevel = "info" | "warning" | "error" | "plan" | "permission";

export type Item =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; text: string; streaming: boolean }
  | { kind: "working"; id: string; text: string }
  | { kind: "thinking"; id: string; text: string }
  | { kind: "tool"; id: string; toolId: string; name: string; status: ToolStatus; detail: string }
  | { kind: "notice"; id: string; level: NoticeLevel; text: string };

export interface PendingApproval {
  call_id: string;
  tool_name: string;
  reason: string;
  arguments: Record<string, any>;
}

export interface ViewState {
  status: string;
  phase: string;
  items: Item[];
  pendingApproval: PendingApproval | null;
  contextTokens: string;
  permissionMode: string;
  /** Seconds elapsed in the current turn, from the bridge heartbeat (0 = idle). */
  heartbeat: number;
}

export const initialState: ViewState = {
  status: "STARTING",
  phase: "starting",
  items: [],
  pendingApproval: null,
  contextTokens: "",
  permissionMode: "ask",
  heartbeat: 0,
};

// AgentState values that mean the agent is actively doing something. Must stay
// in sync with `code_ai.core.state.AgentState` — the bridge forwards those exact
// names via `status.changed`, and the terminal UI keys off the same set
// (`code_ai.ui.terminal.widgets.WORKING_STATES`).
const WORKING_STATES = new Set([
  "CALLING_MODEL",
  "EXECUTING_TOOL",
  "COMPRESSING_CONTEXT",
  "CANCELLING",
]);

export function isBusy(status: string): boolean {
  return WORKING_STATES.has(status);
}

/** Human label for a working state, mirroring the terminal `working_label`. */
export function workingLabel(status: string): string {
  switch (status) {
    case "CALLING_MODEL":
      return "calling model";
    case "EXECUTING_TOOL":
      return "running tools";
    case "COMPRESSING_CONTEXT":
      return "compacting context";
    case "CANCELLING":
      return "cancelling";
    default:
      return "working";
  }
}

// -- helpers ---------------------------------------------------------------

function push(items: Item[], item: Item): Item[] {
  return [...items, item];
}

/** Append `text` to the last item if it matches `kind`, else start a new one. */
function extendText(items: Item[], kind: Item["kind"], text: string, makeNew: () => Item): Item[] {
  const last = items[items.length - 1];
  if (last && last.kind === kind) {
    const copy = items.slice();
    copy[copy.length - 1] = { ...last, text: (last as any).text + text } as Item;
    return copy;
  }
  return [...items, makeNew()];
}

function updateTool(items: Item[], toolId: string, patch: Partial<Extract<Item, { kind: "tool" }>>): Item[] {
  let patched = false;
  const next = items.map((it) => {
    if (!patched && it.kind === "tool" && it.toolId === toolId && it.status === "running") {
      patched = true;
      return { ...it, ...patch };
    }
    return it;
  });
  return next;
}

function toolDetail(name: string, result: any): string {
  if (!result || typeof result !== "object") return "";
  if (name === "web_search") return webSearchDetail(result);
  if (name === "list_files") return `${(result.entries ?? []).length} entries`;
  if (name === "search_code") return `${(result.matches ?? []).length} matches`;
  if (name === "write_file" || name === "edit_code") return String(result.path ?? "");
  const stdout = String(result.stdout ?? "").trim();
  if (stdout) return stdout.slice(0, 240);
  const cwd = String(result.cwd ?? "").trim();
  if (cwd) return `cwd ${cwd}`;
  return "";
}

function webSearchDetail(result: any): string {
  const list = result.results;
  if (!Array.isArray(list)) return "";
  if (list.length === 0) return "0 results";
  const titles = list
    .slice(0, 3)
    .map((r: any) => String(r?.title ?? "").trim())
    .filter(Boolean)
    .map((t: string) => t.slice(0, 80));
  return titles.length ? `${list.length} result(s): ${titles.join(" · ")}` : `${list.length} result(s)`;
}

/**
 * Rebuild transcript items from a saved conversation's raw provider messages.
 * Used when reopening a conversation whose rich client-side transcript is no
 * longer cached (e.g. after clearing webview state or on another machine), so
 * history stays viewable straight from the bridge. Best-effort: tool result
 * bodies were never stored verbatim in the UI, so cards show a short summary.
 */
export function messagesToItems(messages: StoredMessage[]): Item[] {
  const items: Item[] = [];
  const toolIndexById = new Map<string, number>();
  let seq = 0;
  const nid = () => `load-${seq++}`;
  for (const m of messages) {
    if (m.role === "user") {
      const text = m.content ?? "";
      // Skip the injected "[Editor context]" preamble messages — they are plumbing,
      // not something the user typed.
      if (!text.trim() || text.startsWith("[Editor context]")) continue;
      items.push({ kind: "user", id: nid(), text });
    } else if (m.role === "assistant") {
      if (m.content && m.content.trim()) {
        items.push({ kind: "assistant", id: nid(), text: m.content, streaming: false });
      }
      for (const call of m.tool_calls ?? []) {
        items.push({ kind: "tool", id: nid(), toolId: call.id, name: call.name, status: "done", detail: "" });
        toolIndexById.set(call.id, items.length - 1);
      }
    } else if (m.role === "tool") {
      const content = m.content ?? "";
      const isError = content.startsWith("ERROR: ");
      const detail = (isError ? content.slice(7) : content).slice(0, 240);
      const idx = m.tool_call_id != null ? toolIndexById.get(m.tool_call_id) : undefined;
      if (idx != null) {
        const it = items[idx];
        if (it.kind === "tool") items[idx] = { ...it, status: isError ? "failed" : "done", detail };
      }
    }
  }
  return items;
}

// -- reducer ---------------------------------------------------------------

export function applyEvent(state: ViewState, event: EventEnvelope): ViewState {
  const p = event.payload ?? {};
  const id = event.event_id;

  switch (event.event_type) {
    // Synthetic client events (dispatched by the webview, not the bridge) and
    // the bridge's own reset confirmation. `conversation.reset` clears the live
    // transcript; `client.load` restores a saved one for viewing.
    case "conversation.reset":
      return { ...state, items: [], pendingApproval: null, contextTokens: "", status: "READY" };
    case "client.load":
      return { ...state, items: (p.items as Item[]) ?? [], pendingApproval: null };

    case "session.started":
      return { ...state, permissionMode: String(p.permission_mode ?? state.permissionMode) };
    case "status.changed": {
      const status = String(p.state ?? state.status);
      // Reset the heartbeat when the turn leaves a working state.
      return { ...state, status, heartbeat: isBusy(status) ? state.heartbeat : 0 };
    }
    case "turn.heartbeat":
      return { ...state, heartbeat: Number(p.elapsed_s ?? 0) };
    case "phase.changed":
      return { ...state, phase: String(p.phase ?? state.phase) };

    case "user.message":
      return {
        ...state,
        heartbeat: 0,
        items: push(state.items, { kind: "user", id, text: String(p.text ?? "") }),
      };

    case "model.stream.delta": {
      const channel = String(p.channel ?? "answer");
      const text = String(p.text ?? "");
      if (channel === "working") {
        return { ...state, items: extendText(state.items, "working", text, () => ({ kind: "working", id, text })) };
      }
      return {
        ...state,
        items: extendText(state.items, "assistant", text, () => ({ kind: "assistant", id, text, streaming: true })),
      };
    }

    case "model.thinking.delta": {
      const text = String(p.text ?? "");
      return { ...state, items: extendText(state.items, "thinking", text, () => ({ kind: "thinking", id, text })) };
    }

    case "tool.calls.recovered": {
      // A weak model printed its tool call as prose; replace the streamed
      // assistant/working line with the cleaned text (or drop it).
      const cleaned = String(p.text ?? "").trim();
      const last = state.items[state.items.length - 1];
      if (last && (last.kind === "assistant" || last.kind === "working")) {
        const copy = state.items.slice();
        if (cleaned) copy[copy.length - 1] = { ...last, text: cleaned } as Item;
        else copy.pop();
        return { ...state, items: copy };
      }
      return state;
    }

    case "assistant.final": {
      const text = String(p.text ?? "");
      if (!text) return state;
      // Close any live assistant bubble; dedupe when the final text is what we
      // already streamed, otherwise add the authoritative final answer.
      const last = state.items[state.items.length - 1];
      if (last && last.kind === "assistant" && (last.text.trim() === text.trim() || last.streaming)) {
        const copy = state.items.slice();
        copy[copy.length - 1] = { ...last, text, streaming: false };
        return { ...state, items: copy };
      }
      return { ...state, items: push(state.items, { kind: "assistant", id, text, streaming: false }) };
    }

    case "tool.call.started":
      return {
        ...state,
        items: push(state.items, {
          kind: "tool",
          id,
          toolId: String(p.tool_call_id ?? id),
          name: String(p.name ?? "tool"),
          status: "running",
          detail: "",
        }),
      };
    case "tool.call.completed":
      return {
        ...state,
        items: updateTool(state.items, String(p.tool_call_id ?? ""), {
          status: "done",
          detail: toolDetail(String(p.name ?? ""), p.result),
        }),
      };
    case "tool.call.failed":
      return {
        ...state,
        items: updateTool(state.items, String(p.tool_call_id ?? ""), {
          status: "failed",
          detail: String(p.message ?? ""),
        }),
      };

    case "tool.approval.requested":
      return {
        ...state,
        pendingApproval: {
          call_id: String(p.call_id ?? ""),
          tool_name: String(p.tool_name ?? ""),
          reason: String(p.reason ?? ""),
          arguments: (p.arguments ?? {}) as Record<string, any>,
        },
      };
    case "tool.approval.resolved":
      return { ...state, pendingApproval: null };

    case "permission.mode.changed":
      return {
        ...state,
        permissionMode: String(p.mode ?? state.permissionMode),
        items: push(state.items, { kind: "notice", id, level: "permission", text: `Permission mode: ${p.mode}` }),
      };

    case "planning.step.started":
      return { ...state, items: push(state.items, { kind: "notice", id, level: "plan", text: `▶ ${p.current_step ?? "step"}` }) };
    case "planning.step.completed":
      return { ...state, items: push(state.items, { kind: "notice", id, level: "plan", text: `✓ ${p.current_step ?? "step"}` }) };
    case "planning.step.failed":
      return { ...state, items: push(state.items, { kind: "notice", id, level: "error", text: `✗ ${p.current_step ?? "step"}` }) };
    case "planning.plan.waiting":
      return { ...state, items: push(state.items, { kind: "notice", id, level: "plan", text: `◌ paused, waiting for you (${p.progress ?? ""})` }) };

    case "warning":
      return { ...state, items: push(state.items, { kind: "notice", id, level: "warning", text: String(p.message ?? "") }) };
    case "error":
      return { ...state, items: push(state.items, { kind: "notice", id, level: "error", text: String(p.message ?? "") }) };

    case "usage.updated": {
      const active = p.active_context_tokens;
      if (active == null) return state;
      const prefix = p.active_context_estimated ? "~" : "";
      return { ...state, contextTokens: `${prefix}${active}` };
    }

    default:
      return state;
  }
}
