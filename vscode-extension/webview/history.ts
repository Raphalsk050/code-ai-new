// Local conversation history. What each conversation holds is kept by the host
// (src/transcript-store.ts), one file each; the webview's own state keeps only
// the list - title, date, size - and the preferences, so saving it stays cheap
// however long the conversations grow. The live agent runs in a single bridge
// process, so this is a client-side transcript archive: it lets the user browse
// and reopen past conversations, and start fresh ones.

import type { AppMode, ServerConversation } from "../src/protocol";
import type { Place } from "./avatar-motion";
import type { AvatarColor, AvatarGlasses, AvatarSkin } from "./avatar-skin";
import type { Item } from "./store/transcript";

export interface Conversation {
  id: string;
  title: string;
  updatedAt: number;
  /** How many items it holds; the items themselves are the host's. */
  count: number;
}

/** What an older panel kept: the items too, in its own state. */
interface LegacyConversation extends Partial<Conversation> {
  id: string;
  items?: Item[];
}

/** A conversation shown on the home screen, merged from the bridge (source of
 * truth for what exists) and the local cache (rich transcript for preview). */
export interface HistoryEntry {
  id: string;
  title: string;
  updatedAt: number;
  count: number;
  /** What the conversation actually did, written by the bridge when it closed. */
  summary?: string;
  /** Compact token cost, e.g. "128k". */
  tokens?: string;
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
      summary: s.summary || undefined,
      tokens: s.total_tokens ? compactTokens(s.total_tokens) : undefined,
    });
  }
  for (const c of client) {
    if (c.count === 0) continue;
    const prev = byId.get(c.id);
    byId.set(c.id, {
      id: c.id,
      title: c.title,
      updatedAt: Math.max(c.updatedAt, prev?.updatedAt ?? 0),
      count: c.count,
      summary: prev?.summary,
      tokens: prev?.tokens,
    });
  }
  return [...byId.values()].sort((a, b) => b.updatedAt - a.updatedAt);
}

/** Extension-side preferences (not part of the backend config.json). */
export interface ExtPrefs {
  mode: AppMode;
  autoRunRefactor: boolean;
  /** The avatar loose in the panel, and where it was last left. */
  floatingAvatar: boolean;
  avatarAt?: Place;
  /** What the avatar wears: the season's, by the calendar, or one picked by hand. */
  avatarSkin: "auto" | AvatarSkin;
  /** The glyph's colour: the theme's accent, or one picked by hand. */
  avatarColor: AvatarColor;
  /** The glasses the avatar wears, whatever its costume and colour. */
  avatarGlasses: AvatarGlasses;
  /** Whether the floating avatar looks at the pointer while it is idle. */
  avatarWatches: boolean;
  /** Whether the avatar gets anxious while the user types. */
  avatarAnxious: boolean;
  /** What the floating avatar does of its own accord (avatar-acts.ts): each can be turned off. */
  avatarGreets: boolean;
  avatarWaits: boolean;
  avatarCheers: boolean;
  avatarSleeps: boolean;
  avatarTouch: boolean;
  avatarHides: boolean;
  /** The floating avatar's box, in px: set from its right-click menu. */
  avatarSize: number;
}

export const DEFAULT_PREFS: ExtPrefs = { mode: "agent", autoRunRefactor: false, floatingAvatar: true, avatarSkin: "auto", avatarColor: "theme", avatarGlasses: "none", avatarWatches: true, avatarAnxious: true,
  avatarGreets: true, avatarWaits: true, avatarCheers: true, avatarSleeps: true, avatarTouch: true, avatarHides: true,
  avatarSize: 88,
};

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

function compactTokens(n: number): string {
  if (n < 1000) return `${n} tok`;
  if (n < 1_000_000) return `${Math.round(n / 1000)}k tok`;
  return `${(n / 1_000_000).toFixed(1)}M tok`;
}

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
    .filter((c) => c.count > 0)
    .slice()
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

/** Upsert the active conversation's entry in the list, returning a new state. */
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
    count: items.length,
  };
  // Keep an empty active conversation out of the persisted list until it has
  // content, so the history doesn't fill up with blank threads.
  const conversations = items.length > 0 ? [record, ...rest] : existing ? rest : state.conversations;
  return { ...state, conversations, activeId };
}

/**
 * Where `next` stops being `prev`: the first item that is not the same object.
 * The store replaces only what changed, so everything before it is unchanged.
 */
export function firstChange(prev: readonly Item[], next: readonly Item[]): number {
  const shared = Math.min(prev.length, next.length);
  let i = 0;
  while (i < shared && prev[i] === next[i]) i += 1;
  return i;
}

/**
 * State saved by a panel that kept the items itself: the list as it is kept
 * now, and the items to hand over to the host. Nothing to hand over is `[]`.
 */
export function takeLegacy(state: PersistedState): {
  state: PersistedState;
  handOver: { id: string; items: Item[] }[];
} {
  const old = state.conversations as LegacyConversation[];
  if (!old.some((c) => Array.isArray(c.items))) return { state, handOver: [] };
  const handOver = old
    .filter((c) => Array.isArray(c.items) && c.items.length > 0)
    .map((c) => ({ id: c.id, items: c.items as Item[] }));
  const conversations: Conversation[] = old.map((c) => ({
    id: c.id,
    title: c.title ?? deriveTitle(c.items ?? []),
    updatedAt: c.updatedAt ?? 0,
    count: c.count ?? c.items?.length ?? 0,
  }));
  return { state: { ...state, conversations }, handOver };
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
