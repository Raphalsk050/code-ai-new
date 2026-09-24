// The chat transcript: one item per thing that happened, in order. Split out of
// the old monolithic reducer so a streaming delta repaints the transcript and
// nothing else - the panels keep their own slices.

import type { EventEnvelope, StoredMessage } from "../../src/protocol";

export type ToolStatus = "running" | "done" | "failed";
export type NoticeLevel = "info" | "warning" | "error" | "plan" | "permission" | "success";

export type Item =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; text: string; streaming: boolean }
  | { kind: "working"; id: string; text: string }
  | { kind: "thinking"; id: string; text: string; streaming?: boolean }
  | { kind: "tool"; id: string; toolId: string; name: string; status: ToolStatus; detail: string }
  | { kind: "notice"; id: string; level: NoticeLevel; text: string };

export interface TranscriptState {
  items: Item[];
}

export const initialTranscript: TranscriptState = { items: [] };

const WORKING_STATES = new Set([
  "CALLING_MODEL",
  "EXECUTING_TOOL",
  "COMPRESSING_CONTEXT",
  "CANCELLING",
]);

export function isBusy(status: string): boolean {
  return WORKING_STATES.has(status);
}

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

function push(items: Item[], item: Item): Item[] {
  return [...items, item];
}

/** Server log levels worth interrupting the chat for; the rest stay on the settings card. */
const LOUD_MCP_LOGS = new Set(["warning", "error", "critical", "alert", "emergency"]);

/** A server whose state needs the user, as one line; "" for the ones that do not. */
function mcpStatusNotice(p: Record<string, any>): string {
  const name = String(p.name ?? "");
  switch (p.status) {
    case "failed":
      return `MCP server ${name} failed: ${String(p.error ?? "").split("\n")[0]}`;
    case "needs_approval":
      return `MCP server ${name} (${p.source_path || "declared by the project"}) is waiting for your approval in Settings.`;
    case "needs_auth":
      return `MCP server ${name} needs you to sign in.`;
    default:
      return "";
  }
}

function extendText(items: Item[], kind: Item["kind"], text: string, makeNew: () => Item): Item[] {
  const last = items[items.length - 1];
  if (last && last.kind === kind) {
    const copy = items.slice();
    copy[copy.length - 1] = { ...last, text: (last as any).text + text } as Item;
    return copy;
  }
  return [...items, makeNew()];
}

/**
 * Close the open reasoning block, if the last thing in the transcript is one.
 * The model has stopped thinking, so the block stops holding the view open and
 * goes back to being a header the reader can expand.
 */
function settleThinking(items: Item[]): Item[] {
  const last = items[items.length - 1];
  if (!last || last.kind !== "thinking" || !last.streaming) return items;
  const copy = items.slice();
  copy[copy.length - 1] = { ...last, streaming: false };
  return copy;
}

function updateTool(
  items: Item[],
  toolId: string,
  patch: Partial<Extract<Item, { kind: "tool" }>>
): Item[] {
  let patched = false;
  return items.map((it) => {
    if (!patched && it.kind === "tool" && it.toolId === toolId && it.status === "running") {
      patched = true;
      return { ...it, ...patch };
    }
    return it;
  });
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
  return titles.length
    ? `${list.length} result(s): ${titles.join(" · ")}`
    : `${list.length} result(s)`;
}

export function messagesToItems(messages: StoredMessage[]): Item[] {
  const items: Item[] = [];
  const toolIndexById = new Map<string, number>();
  let seq = 0;
  const nid = () => `load-${seq++}`;
  for (const m of messages) {
    if (m.role === "user") {
      const text = m.content ?? "";
      if (!text.trim() || text.startsWith("[Editor context]")) continue;
      items.push({ kind: "user", id: nid(), text });
    } else if (m.role === "assistant") {
      if (m.content && m.content.trim()) {
        items.push({ kind: "assistant", id: nid(), text: m.content, streaming: false });
      }
      for (const call of m.tool_calls ?? []) {
        items.push({
          kind: "tool",
          id: nid(),
          toolId: call.id,
          name: call.name,
          status: "done",
          detail: "",
        });
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

export function applyTranscript(state: TranscriptState, event: EventEnvelope): TranscriptState {
  const p = event.payload ?? {};
  const id = event.event_id;
  const items = state.items;

  switch (event.event_type) {
    case "conversation.reset":
      return { items: [] };
    case "client.load":
      return { items: (p.items as Item[]) ?? [] };

    case "user.message":
      return { items: push(items, { kind: "user", id, text: String(p.text ?? "") }) };
    case "user.message.queued":
      return {
        items: push(push(items, { kind: "user", id, text: String(p.text ?? "") }), {
          kind: "notice",
          id: id + "-q",
          level: "info",
          text: "queued: waiting for the current step to finish",
        }),
      };
    // Waiting its turn rather than steering the running one: the bubble is the
    // same, the note says it will be answered on its own.
    case "user.message.enqueued": {
      const position = Number(p.position ?? 0);
      const place = position > 1 ? ` (#${position} in the queue)` : "";
      return {
        items: push(push(items, { kind: "user", id, text: String(p.text ?? "") }), {
          kind: "notice",
          id: id + "-w",
          level: "info",
          text: `queued: answered once the running turn ends${place}`,
        }),
      };
    }

    case "user.message.delivered": {
      const last = items[items.length - 1];
      if (last && last.kind === "notice" && last.text.startsWith("queued:")) {
        const copy = items.slice();
        copy[copy.length - 1] = { ...last, text: "queued: delivered to the model" };
        return { items: copy };
      }
      return state;
    }

    case "model.stream.delta": {
      const channel = String(p.channel ?? "answer");
      const text = String(p.text ?? "");
      if (channel === "working") {
        return {
          items: extendText(items, "working", text, () => ({ kind: "working", id, text })),
        };
      }
      return {
        items: extendText(items, "assistant", text, () => ({
          kind: "assistant",
          id,
          text,
          streaming: true,
        })),
      };
    }

    case "model.thinking.delta": {
      const text = String(p.text ?? "");
      // `streaming` is what makes the block open itself while the model is
      // reasoning: collapsed-by-default hid the one thing the user is waiting
      // to see, and it only became findable once the turn was over.
      return {
        items: extendText(items, "thinking", text, () => ({ kind: "thinking", id, text, streaming: true })),
      };
    }

    case "tool.calls.recovered": {
      const cleaned = String(p.text ?? "").trim();
      const last = items[items.length - 1];
      if (last && (last.kind === "assistant" || last.kind === "working")) {
        const copy = items.slice();
        if (cleaned) copy[copy.length - 1] = { ...last, text: cleaned } as Item;
        else copy.pop();
        return { items: copy };
      }
      return state;
    }

    // Reasoning is over the moment the model answers or asks for a tool.
    case "model.response.completed":
      return { items: settleThinking(items) };

    case "assistant.final": {
      const text = String(p.text ?? "");
      if (!text) return state;
      const last = items[items.length - 1];
      if (last && last.kind === "assistant" && (last.text.trim() === text.trim() || last.streaming)) {
        const copy = items.slice();
        copy[copy.length - 1] = { ...last, text, streaming: false };
        return { items: copy };
      }
      if (last && last.kind === "working" && last.text.trim() === text.trim()) {
        const copy = items.slice();
        copy[copy.length - 1] = { kind: "assistant", id, text, streaming: false };
        return { items: copy };
      }
      return { items: push(items, { kind: "assistant", id, text, streaming: false }) };
    }

    case "tool.call.started":
      return {
        items: push(items, {
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
        items: updateTool(items, String(p.tool_call_id ?? ""), {
          status: "done",
          detail: toolDetail(String(p.name ?? ""), p.result),
        }),
      };
    case "tool.call.failed":
      return {
        items: updateTool(items, String(p.tool_call_id ?? ""), {
          status: "failed",
          detail: String(p.message ?? ""),
        }),
      };
    case "tool.call.interrupted":
      return {
        items: push(items, {
          kind: "notice",
          id,
          level: "warning",
          text: `call cut off mid-stream, asking again (${p.attempt ?? "?"}/${p.max_attempts ?? "?"})`,
        }),
      };

    case "mcp.tool.progress": {
      // The running call of that tool shows how far it has got.
      const name = String(p.name ?? "");
      for (let i = items.length - 1; i >= 0; i--) {
        const it = items[i];
        if (it.kind === "tool" && it.status === "running" && it.name === name) {
          const total = typeof p.total === "number" ? `/${p.total}` : "";
          const note = p.message ? ` · ${p.message}` : "";
          const copy = items.slice();
          copy[i] = { ...it, detail: `${p.progress ?? ""}${total}${note}` };
          return { items: copy };
        }
      }
      return state;
    }
    case "mcp.server.log": {
      const level = String(p.level ?? "");
      if (!LOUD_MCP_LOGS.has(level)) return state;
      return {
        items: push(items, {
          kind: "notice",
          id,
          level: level === "warning" ? "warning" : "error",
          text: `MCP ${p.server}: ${p.message}`,
        }),
      };
    }
    case "mcp.server.status": {
      const text = mcpStatusNotice(p);
      const last = items[items.length - 1];
      if (!text || (last && last.kind === "notice" && last.text === text)) return state;
      return {
        items: push(items, {
          kind: "notice",
          id,
          level: p.status === "failed" ? "warning" : "info",
          text,
        }),
      };
    }

    case "command.output": {
      const text = String(p.text ?? "");
      if (!text) return state;
      for (let i = items.length - 1; i >= 0; i--) {
        const it = items[i];
        if (it.kind === "tool" && it.status === "running") {
          const copy = items.slice();
          copy[i] = { ...it, detail: (it.detail + text).slice(-4000) };
          return { items: copy };
        }
      }
      return state;
    }

    case "permission.mode.changed":
      return {
        items: push(items, {
          kind: "notice",
          id,
          level: "permission",
          text: `permission mode: ${p.mode}`,
        }),
      };
    case "planning.policy.denied":
      return {
        items: push(items, {
          kind: "notice",
          id,
          level: "warning",
          text: `policy denied ${p.tool_name}: ${p.reason ?? ""}`,
        }),
      };
    case "planning.completion.rejected":
      return {
        items: push(items, {
          kind: "notice",
          id,
          level: "warning",
          text: `completion rejected: ${(p.missing_requirements ?? []).toString()}`,
        }),
      };
    case "model.request.failed":
      return {
        items: push(items, {
          kind: "notice",
          id,
          level: "error",
          text: `model request failed: ${p.message ?? ""}`,
        }),
      };
    case "images.trimmed":
      return {
        items: push(items, {
          kind: "notice",
          id,
          level: "info",
          text: `${p.dropped} older image(s) not sent: this endpoint takes ${p.limit} per request`,
        }),
      };
    case "vision.analysis.started":
      return {
        items: push(items, {
          kind: "notice",
          id,
          level: "info",
          text: `reading ${p.images} image(s) with ${p.model ?? ""}`,
        }),
      };
    case "index.refreshed":
    case "index.embedded":
      return {
        items: push(items, {
          kind: "notice",
          id,
          level: "success",
          text: String(p.summary ?? "code index updated"),
        }),
      };
    case "warning":
      return {
        items: push(items, { kind: "notice", id, level: "warning", text: String(p.message ?? "") }),
      };
    case "error":
      return {
        items: push(items, { kind: "notice", id, level: "error", text: String(p.message ?? "") }),
      };

    default:
      return state;
  }
}
