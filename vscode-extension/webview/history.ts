// Local conversation history, persisted in the webview's VSCode state so it
// survives reloads (like the task list in Cline/Codex/Claude). The live agent
// runs in a single bridge process, so this is a client-side transcript archive:
// it lets the user browse and reopen past conversations, and start fresh ones.

import type { AppMode, ServerConversation } from "../src/protocol";
import type { Item } from "./reducer";

export interface Conversation {
  id: string;
  title: string;
  updatedAt: number;
  items: Item[];
}

/** A conversation shown on the home screen, merged from the bridge (source of
 * truth for what exists) and the local cache (rich transcript for preview). */
export interface HistoryEntry {
  id: string;
  title: string;
  updatedAt: number;
  count: number;
}

/**
 * Union of the bridge's saved conversations and the local transcript cache. The
 * bridge is authoritative for existence (so history survives a cleared webview
 * state), while a locally-cached conversation contributes its fresher title and
 * message count. Newest-first.
 */
export function mergeHistory(
  client: Conversation[],
  server: ServerConversation[]
): HistoryEntry[] {
  const byId = new Map<string, HistoryEntry>();
  for (const s of server) {
    byId.set(s.id, {
      id: s.id,
      title: s.title || "Conversation",
      updatedAt: (s.updated_at || 0) * 1000, // stored in seconds
      count: s.message_count,
    });
  }
  for (const c of client) {
    if (c.items.length === 0) continue;
    const prev = byId.get(c.id);
    byId.set(c.id, {
      id: c.id,
      title: c.title,
      updatedAt: Math.max(c.updatedAt, prev?.updatedAt ?? 0),
      count: c.items.length,
    });
  }
  return [...byId.values()].sort((a, b) => b.updatedAt - a.updatedAt);
}

/** Extension-side preferences (not part of the backend config.json). */
export interface ExtPrefs {
  mode: AppMode;
  autoRunRefactor: boolean;
}

export const DEFAULT_PREFS: ExtPrefs = { mode: "agent", autoRunRefactor: false };

export interface PersistedState {
  conversations: Conversation[];
  activeId: string | null;
  prefs: ExtPrefs;
}

export const EMPTY_PERSISTED: PersistedState = {
  conversations: [],
  activeId: null,
  prefs: DEFAULT_PREFS,
};

let _seq = 0;
export function newId(): string {
  _seq += 1;
  return `c-${Date.now().toString(36)}-${_seq}`;
}

/** First user line, trimmed to a readable card title. */
export function deriveTitle(items: Item[]): string {
  const firstUser = items.find((it) => it.kind === "user") as { text: string } | undefined;
  const raw = (firstUser?.text ?? "").replace(/\s+/g, " ").trim();
  if (!raw) return "New conversation";
  return raw.length > 60 ? raw.slice(0, 60) + "…" : raw;
}

/** Conversations newest-first, dropping any that never accrued a message. */
export function sorted(conversations: Conversation[]): Conversation[] {
  return conversations
    .filter((c) => c.items.length > 0)
    .slice()
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

/** Upsert the active conversation's transcript, returning a new state. */
export function upsertActive(
  state: PersistedState,
  activeId: string,
  items: Item[]
): PersistedState {
  const rest = state.conversations.filter((c) => c.id !== activeId);
  const existing = state.conversations.find((c) => c.id === activeId);
  const record: Conversation = {
    id: activeId,
    title: deriveTitle(items),
    updatedAt: Date.now(),
    items,
  };
  // Keep an empty active conversation out of the persisted list until it has
  // content, so the history doesn't fill up with blank threads.
  const conversations = items.length > 0 ? [record, ...rest] : existing ? rest : state.conversations;
  return { ...state, conversations, activeId };
}

export function removeConversation(state: PersistedState, id: string): PersistedState {
  return { ...state, conversations: state.conversations.filter((c) => c.id !== id) };
}

export function relativeTime(ts: number): string {
  const diff = Date.now() - ts;
  const min = Math.floor(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(ts).toLocaleDateString();
}
