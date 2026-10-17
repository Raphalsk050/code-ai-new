import KATEX_STYLE from "katex-style";
import * as React from "react";
import { createRoot } from "react-dom/client";

import type {
  AppMode,
  ApprovalScope,
  ChangeSummary,
  EditorSettings,
  EditorContext,
  EventEnvelope,
  HostToWebview,
  McpAction,
  McpServerInfo,
  PastedImage,
  PermissionMode,
  ServerConversation,
  Settings,
  SlashCommandSpec,
  TerminalRequest,
  WebviewToHost,
} from "../src/protocol";
import { ApprovalCard } from "./approval";
import { Banner, BannerState } from "./banner";
import { BusyRing } from "./busy-ring";
import { EmptyAction, EmptyState } from "./empty-state";
import { CommandMenu } from "./command-menu";
import { ElicitationCard } from "./elicitation-card";
import { buildCatalog, matchCommands, resolveCommand, SlashCommand } from "./commands";
import { ExplainPanel, ExplainViewState, INITIAL_EXPLAIN } from "./explain";
import { HomeScreen } from "./home";
import { setLanguage, t, tf } from "./i18n";
import {
  DEFAULT_PREFS,
  EMPTY_PERSISTED,
  ExtPrefs,
  firstChange,
  mergeHistory,
  newId,
  PersistedState,
  removeConversation,
  takeLegacy,
  upsertActive,
} from "./history";
import { ChatHeader } from "./chat-header";
import { Codicon } from "./codicon";
import { ComposerBar } from "./composer-bar";
import { LiveCodePanel } from "./live-code";
import { UpdateButton, UpdateView } from "./update-button";
import { mergeStatus } from "./mcp-form";
import { INITIAL_MCP, McpViewState } from "./mcp-settings";
import { MentionMenu } from "./mention-menu";
import { applyMention, mentionPrefix, rankPaths } from "./mentions";
import { agentKey, Block, endsOnAnswer, groupBlocks } from "./blocks";
import { useFollow } from "./follow";
import { activityLabel, BlockView, formatElapsed, TypingIndicator } from "./messages";
import { useSteadyPose } from "./agent-avatar";
import { isPlace } from "./avatar-motion";
import { cue } from "./avatar-acts";
import { stroke, useRapt } from "./avatar-nerves";
import { avatarPose, turnMood } from "./avatar-pose";
import { seasonalSkin, setAvatarColor, setAvatarSkin } from "./avatar-skin";
import { FloatingAvatar } from "./floating-avatar";
import { VersionMark } from "./version-mark";
import { PromptPin, usePromptPin } from "./prompt-pin";
import { ripple } from "./motion";
import { QuestionsCard } from "./questions-card";
import { INITIAL_REFACTOR, RefactorPanel, RefactorViewState } from "./refactor";
import { ModelsState, SaveState, SettingsScreen } from "./settings";
import { applyEvent, initialAppState, isBusy, Item, messagesToItems } from "./store";
import { STYLE } from "./styles";
import { RailActions, TaskRail } from "./task-rail";
import { useContainerWidth } from "./use-container-width";
import { setResourceRoots } from "./workspace-images";

declare function acquireVsCodeApi(): {
  postMessage: (message: WebviewToHost) => void;
  getState: () => PersistedState | undefined;
  setState: (state: PersistedState) => void;
};
const vscode = acquireVsCodeApi();
const send = (message: WebviewToHost) => vscode.postMessage(message);
/** This page is the chat's own window (see popOut in src/extension.ts), not the sidebar. */
const IS_WINDOW = document.body?.dataset.surface === "window";
/** Saved where it survives a reload; the window also tells the host, which hands it back to the sidebar. */
const saveState = (next: PersistedState) => {
  vscode.setState(next);
  if (IS_WINDOW) send({ type: "surfaceState", state: next });
};

/** Build a synthetic envelope so the webview can drive the store locally. */
function clientEvent(event_type: string, payload: Record<string, any> = {}): EventEnvelope {
  return {
    event_id: `client-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    event_type,
    event_version: 1,
    session_id: "webview",
    sequence: 0,
    timestamp: new Date().toISOString(),
    source: "webview",
    payload,
  };
}

type Screen = "home" | "chat" | "settings";
/** How long after the last streamed piece the transcript is saved. */
const PERSIST_AFTER_MS = 400;
/** Blocks drawn at once when a conversation opens, and how many join after each frame. */
const FIRST_BLOCKS = 24;
const MORE_BLOCKS = 12;

// Backend states a turn ends well in; ERROR, FAILED and CANCELLED end it too,
// but not with an answer worth marking done.
const SETTLED = new Set(["READY", "IDLE"]);
const TURN_OVER = new Set(["READY", "IDLE", "ERROR", "FAILED", "CANCELLED"]);
// What waits on the user, for the avatar to wait by.
const WAITS_ON = ".approval-card, .qcard";
// Scrolled this far from the end, the glass over the composer is fully drawn.
const GLASS_RAMP_PX = 24;
// No backend to send to: the composer says so and will not send.
const OFFLINE = new Set(["STARTING", "RECONNECTING", "DISCONNECTED"]);

function App(): JSX.Element {
  const [state, dispatch] = React.useReducer(applyEvent, initialAppState);
  // An older panel kept every conversation's items in this state: they go to
  // the host once, and only the list stays here.
  const legacy = React.useRef<ReturnType<typeof takeLegacy> | null>(null);
  const [persisted, setPersisted] = React.useState<PersistedState>(() => {
    legacy.current = takeLegacy(vscode.getState() ?? EMPTY_PERSISTED);
    return legacy.current.state;
  });
  const [screen, setScreen] = React.useState<Screen>("home");
  const [returnScreen, setReturnScreen] = React.useState<Screen>("home");
  const [activeId, setActiveId] = React.useState<string | null>(null);
  const [settings, setSettings] = React.useState<Settings | null>(null);
  const [models, setModels] = React.useState<ModelsState>({ status: "idle", list: [] });
  const [save, setSave] = React.useState<SaveState>({ status: "idle", errors: {} });
  const [mcp, setMcp] = React.useState<McpViewState>(INITIAL_MCP);
  const [elicitErrors, setElicitErrors] = React.useState<Record<string, string>>({});
  const [serverConvos, setServerConvos] = React.useState<ServerConversation[]>([]);
  const [refactor, setRefactor] = React.useState<RefactorViewState>(INITIAL_REFACTOR);
  const [explain, setExplain] = React.useState<ExplainViewState>(INITIAL_EXPLAIN);
  const [catalog, setCatalog] = React.useState<SlashCommand[]>(() => buildCatalog([]));

  const [draft, setDraft] = React.useState("");
  const [editorContext, setEditorContext] = React.useState<EditorContext | null>(null);
  const [includeContext, setIncludeContext] = React.useState(true);
  const [cmdIndex, setCmdIndex] = React.useState(0);
  const [menuDismissed, setMenuDismissed] = React.useState(false);
  const [mention, setMention] = React.useState<{ query: string; paths: string[] } | null>(null);
  const [mentionIndex, setMentionIndex] = React.useState(0);
  const mentionQueryRef = React.useRef<string | null>(null);
  const [images, setImages] = React.useState<PastedImage[]>([]);
  const [dropping, setDropping] = React.useState(false);
  const [reveal, setReveal] = React.useState<{ section: "terminal"; nonce: number } | null>(null);
  const [terminalFocus, setTerminalFocus] = React.useState(0);
  const [railDocked, setRailDocked] = React.useState(true);
  // Edits are shown in the editor's diff view: no live code or diff in here.
  const [editorDiff, setEditorDiff] = React.useState(false);
  // What the agent changed in this conversation, from the host.
  const [changes, setChanges] = React.useState<ChangeSummary[]>([]);
  const [editorSettings, setEditorSettings] = React.useState<EditorSettings | null>(null);
  // A newer release, once the backend has found one.
  const [update, setUpdate] = React.useState<UpdateView | null>(null);
  // The user's picture on the releases host; the icon stands in until then,
  // with the reason there is none on hover - and, when a browser sign-in can
  // fetch it, a click that opens one (pending while that window is up).
  const [avatar, setAvatar] = React.useState({ src: "", problem: "", signIn: false, pending: false });

  const [rootRef, width] = useContainerWidth<HTMLDivElement>();
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  const pendingReconstructRef = React.useRef<string | null>(null);
  // The chat is in its own window: this sidebar only says so.
  const [elsewhere, setElsewhere] = React.useState(false);
  // A conversation to open once this surface takes the chat over.
  const [takeOver, setTakeOver] = React.useState<string | null>(null);
  // Asked to pop out from outside the page: what it hands over, as of now.
  const popOutRef = React.useRef<() => void>(() => undefined);
  // The conversation being opened: whether the host has said what it kept,
  // and whether the backend has rebuilt it. Saved before both are settled, it
  // would be recorded as empty, or as the rebuild over the fuller copy.
  const openingRef = React.useRef<{ id: string; kept: "waiting" | "none" | "loaded"; rebuilt: boolean } | null>(null);
  // What the host was last given for the active conversation: a save sends
  // only what changed since. Null makes the next one send it all.
  const savedRef = React.useRef<{ id: string; items: Item[] } | null>(null);

  // State saved by an older build has no word on what came after it.
  const prefs: ExtPrefs = { ...DEFAULT_PREFS, ...persisted.prefs };
  const mode = prefs.mode;

  // The season's costume unless one was picked by hand. A panel left open
  // overnight has to notice the date by itself.
  React.useEffect(() => {
    const dress = () => setAvatarSkin(prefs.avatarSkin === "auto" ? seasonalSkin(new Date()) : prefs.avatarSkin);
    dress();
    const hourly = window.setInterval(dress, 3_600_000);
    return () => window.clearInterval(hourly);
  }, [prefs.avatarSkin]);
  React.useEffect(() => setAvatarColor(prefs.avatarColor), [prefs.avatarColor]);

  // -- host messages -------------------------------------------------------
  React.useEffect(() => {
    const onMessage = (e: MessageEvent<HostToWebview>) => {
      const data = e.data;
      if (data?.type === "event") {
        const event = data.event as EventEnvelope;
        if (event.event_type === "mcp.server.status") {
          const update = event.payload as McpServerInfo;
          setMcp((m) =>
            m.list ? { ...m, list: { ...m.list, servers: mergeStatus(m.list.servers, update) } } : m
          );
          // A server that just connected may bring prompts for the / menu.
          if (update.status === "connected") send({ type: "listSlashCommands" });
        } else if (event.event_type === "mcp.auth.url") {
          const server = String(event.payload.server ?? "");
          const url = String(event.payload.url ?? "");
          setMcp((m) => ({ ...m, authUrls: { ...m.authUrls, [server]: url } }));
        } else if (event.event_type === "mcp.server.removed") {
          const gone = String(event.payload.server ?? "");
          setMcp((m) =>
            m.list
              ? { ...m, list: { ...m.list, servers: m.list.servers.filter((s) => s.name !== gone) } }
              : m
          );
        }
        dispatch(event);
      } else if (data?.type === "editorDiff") {
        setEditorDiff(data.enabled);
      } else if (data?.type === "sessionChanges") {
        setChanges(data.files);
      } else if (data?.type === "editorSettings") {
        setEditorSettings(data.settings);
      } else if (data?.type === "updateAvailable") {
        setUpdate((u) => ({
          version: data.version,
          current: data.current,
          notes: data.notes,
          // A check that runs again mid-install must not reset the button.
          state: u && u.version === data.version ? u.state : "available",
          message: "",
        }));
      } else if (data?.type === "updateLogin") {
        setUpdate((u) =>
          u?.state === "signingIn"
            ? u
            : { version: "", current: "", notes: "", state: "login", message: "", host: data.host }
        );
      } else if (data?.type === "updateCleared") {
        setUpdate(null);
      } else if (data?.type === "profile") {
        const picture = String(data.avatar ?? "");
        setAvatar({
          src: picture.startsWith("data:image/") ? picture : "",
          problem: String(data.problem ?? ""),
          signIn: !!data.signIn,
          pending: false,
        });
      } else if (data?.type === "updateProgress") {
        setUpdate((u) => (u ? { ...u, state: data.state, message: data.message ?? "" } : u));
      } else if (data?.type === "mcpServers") {
        setMcp((m) => ({ ...m, list: data.list, error: "", busy: "" }));
      } else if (data?.type === "mcpError") {
        setMcp((m) => ({ ...m, error: data.message, busy: "" }));
      } else if (data?.type === "mcpElicitationResult") {
        // A refused answer leaves the question open; the card says why.
        setElicitErrors((errors) => ({ ...errors, [data.id]: data.ok ? "" : data.message }));
      } else if (data?.type === "fileMatches") {
        // A slower answer for a prefix the user has already typed past is noise.
        if (mentionQueryRef.current === data.query) {
          setMention({ query: data.query, paths: rankPaths(data.paths, data.query) });
          setMentionIndex(0);
        }
      } else if (data?.type === "slashCommands") {
        setCatalog(buildCatalog(data.commands as SlashCommandSpec[]));
      } else if (data?.type === "report") {
        if (data.text.trim()) {
          dispatch(clientEvent(data.ok ? "report" : "error", { message: data.text.trim() }));
        }
      } else if (data?.type === "commandResult") {
        if (data.text.trim()) {
          dispatch(
            clientEvent(data.ok ? "warning" : "error", { message: data.text.trim() })
          );
        }
      } else if (data?.type === "editorContext") {
        setEditorContext(data.context);
        setIncludeContext(true);
      } else if (data?.type === "settings") {
        // The backend owns the language the user picked; the panel is
        // written in English and translates only once it knows.
        setLanguage(data.settings.language ?? "en");
        setSettings(data.settings);
      } else if (data?.type === "settingsUpdated") {
        // The snapshot carries the backend's word on each field, so the errors
        // have to travel with it: the panel needs them to tell "saved" from
        // "refused and reverted to what you were replacing". The language is in
        // there too, so saving it repaints the panel without a reload.
        setLanguage(data.result.settings.language ?? "en");
        setSettings(data.result.settings);
        setSave({
          status: Object.keys(data.result.errors ?? {}).length ? "error" : "saved",
          errors: data.result.errors ?? {},
        });
      } else if (data?.type === "modelsListed") {
        setModels({ status: "ready", list: data.models });
      } else if (data?.type === "modelsError") {
        setModels((m) => ({ ...m, status: "error", error: data.message }));
      } else if (data?.type === "conversationsList") {
        setServerConvos(data.conversations);
      } else if (data?.type === "conversationLoaded") {
        const opening = openingRef.current?.id === data.id ? openingRef.current : null;
        if (opening) opening.rebuilt = true;
        if (pendingReconstructRef.current === data.id && opening?.kept !== "loaded") {
          pendingReconstructRef.current = null;
          setDrawn(FIRST_BLOCKS);
          dispatch(clientEvent("client.load", { items: messagesToItems(data.messages) }));
        }
        if (opening?.kept === "none") openingRef.current = null;
      } else if (data?.type === "transcript") {
        // What the host kept wins over what the backend can rebuild: it has
        // every tool call and notice the backend never saw.
        const opening = openingRef.current?.id === data.id ? openingRef.current : null;
        if (opening) {
          const items = (data.items ?? []) as Item[];
          if (items.length > 0) {
            opening.kept = "loaded";
            pendingReconstructRef.current = null;
            savedRef.current = { id: data.id, items };
            setDrawn(FIRST_BLOCKS);
            dispatch(clientEvent("client.load", { items }));
          } else {
            opening.kept = "none";
          }
          if (opening.kept === "loaded" || opening.rebuilt) openingRef.current = null;
        }
      } else if (data?.type === "surface") {
        setElsewhere(!data.active);
        if (data.active && data.state) {
          const next = takeLegacy(data.state as PersistedState).state;
          vscode.setState(next);
          setPersisted(next);
        }
        if (data.active && data.conversation) setTakeOver(data.conversation);
      } else if (data?.type === "popOutRequested") {
        popOutRef.current();
      } else if (data?.type === "resourceRoots") {
        setResourceRoots(data.roots);
      } else if (data?.type === "transcriptResend") {
        if (savedRef.current?.id === data.id) {
          savedRef.current = null;
          setResend((n) => n + 1);
        }
      } else if (data?.type === "refactorStatus") {
        if (data.status === "analyzing")
          setRefactor((r) => ({ ...r, status: "analyzing", error: undefined }));
      } else if (data?.type === "refactorResult") {
        setRefactor({
          status: "ready",
          improvements: data.improvements,
          path: data.path,
          language: data.language,
          planning: {},
          planned: {},
        });
      } else if (data?.type === "refactorError") {
        setRefactor((r) => ({ ...r, status: "error", error: data.message }));
      } else if (data?.type === "refactorPlanning") {
        setRefactor((r) => ({ ...r, planning: { ...r.planning, [data.id]: true } }));
      } else if (data?.type === "refactorPlanned") {
        setRefactor((r) => ({
          ...r,
          planning: { ...r.planning, [data.id]: false },
          planned: { ...r.planned, [data.id]: data.markdown },
        }));
      } else if (data?.type === "explainStatus") {
        setExplain({ status: "analyzing", target: data.target });
      } else if (data?.type === "explainResult") {
        setExplain({ status: "ready", markdown: data.markdown, target: data.target });
      } else if (data?.type === "explainError") {
        setExplain((e) => ({ ...e, status: "error", error: data.message }));
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  React.useEffect(() => {
    send({ type: "setMode", mode: prefs.mode, autoRunRefactor: prefs.autoRunRefactor });
    send({ type: "listSlashCommands" });
    // The rail names the model and the workspace, so the settings are session
    // state, not a screen's private fetch.
    send({ type: "getSettings" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The bridge answers with its catalog once it is up; the first request can
  // land while it is still spawning, so ask again when the session reports in.
  React.useEffect(() => {
    if (state.session.status !== "STARTING") send({ type: "listSlashCommands" });
  }, [state.session.status]);

  // A request lost to a backend that died, or one restarted since, leaves the
  // settings screen loading for ever: ask again once a live session reports in.
  React.useEffect(() => {
    if (!settings && !OFFLINE.has(state.session.status)) send({ type: "getSettings" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.session.status]);

  React.useEffect(() => {
    if (screen === "home") send({ type: "listConversations" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screen, state.session.status]);

  // -- persist the active transcript to local history ----------------------
  // A new message is saved at once. Text streaming into the last one is saved a
  // moment after it stops: an answer arrives in hundreds of pieces. The host
  // is sent only what changed since its last copy, and the webview's own state
  // only the entry in the list.
  const pendingSave = React.useRef<(() => void) | null>(null);
  const savedCount = React.useRef(-1);
  const [resend, setResend] = React.useState(0);

  // A conversation opened with hundreds of messages is drawn from the newest:
  // what is on screen first, then the older ones a slice at a time, each after
  // a frame, so typing and scrolling are never held up behind all of it.
  const [drawn, setDrawn] = React.useState(Infinity);
  React.useEffect(() => {
    if (screen !== "chat" || mode !== "agent" || !activeId) return;
    if (openingRef.current?.id === activeId) return;
    const items = state.transcript.items;
    const save = () => {
      pendingSave.current = null;
      savedCount.current = items.length;
      const saved = savedRef.current?.id === activeId ? savedRef.current.items : null;
      const from = saved ? firstChange(saved, items) : 0;
      if (saved ? from < items.length || from < saved.length : items.length > 0) {
        send({ type: "saveTranscript", id: activeId, from, items: items.slice(from) });
        savedRef.current = { id: activeId, items };
      }
      setPersisted((prev) => {
        const next = upsertActive(prev, activeId, items);
        saveState(next);
        return next;
      });
    };
    if (items.length !== savedCount.current) {
      save();
      return;
    }
    pendingSave.current = save;
    const timer = window.setTimeout(save, PERSIST_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [state.transcript.items, screen, activeId, mode, resend]);

  // Once: what an older panel kept goes to the host, and the list alone stays.
  React.useEffect(() => {
    const handOver = legacy.current?.handOver ?? [];
    legacy.current = null;
    if (handOver.length === 0) return;
    send({ type: "adoptTranscripts", conversations: handOver });
    saveState(persisted);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Leaving the conversation, or the panel going away: what was waiting is saved now.
  React.useEffect(() => {
    const flush = () => pendingSave.current?.();
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, [screen, activeId, mode]);

  // Esc hands the keyboard to the interactive terminal and back, the binding
  // the TUI has. Registered on the document because the terminal input lives
  // in the rail, which the composer knows nothing about.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const active = document.activeElement as HTMLElement | null;
      if (active?.dataset?.terminalInput === "1") {
        e.preventDefault();
        textareaRef.current?.focus();
        return;
      }
      if (active === textareaRef.current) {
        e.preventDefault();
        setReveal({ section: "terminal", nonce: Date.now() });
        setTerminalFocus((n) => n + 1);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // -- autoscroll / auto-grow ---------------------------------------------
  const follow = useFollow(scrollRef, { page: true });
  const pin = usePromptPin(scrollRef, follow.following);
  const jumpToLatest = () => follow.rejoin(true);
  // A conversation opens on its newest message, wherever the last one was left.
  React.useLayoutEffect(() => follow.rejoin(), [activeId, screen, mode]);
  // The composer floats over the transcript; the transcript keeps that much
  // room at its end so the newest line is never under the glass.
  const composerRef = React.useRef<HTMLDivElement>(null);
  // The glass reaches above the composer only for text on its way under it:
  // 0 at the end of the conversation, 1 once scrolled away from it.
  const syncGlass = () => {
    const el = scrollRef.current;
    const col = el?.parentElement;
    if (!el || !col) return;
    const below = el.scrollHeight - el.scrollTop - el.clientHeight;
    // Under a pixel is rounding at a fractional zoom, not a scroll.
    const glass = below < 1 ? 0 : Math.min(1, below / GLASS_RAMP_PX);
    col.style.setProperty("--glass", String(Math.round(glass * 100) / 100));
  };
  React.useLayoutEffect(() => {
    const el = composerRef.current;
    const col = el?.parentElement;
    if (!el || !col || typeof ResizeObserver === "undefined") return;
    const apply = () => {
      col.style.setProperty("--composer-h", `${Math.ceil(el.getBoundingClientRect().height)}px`);
      // A taller composer moves the end of the transcript without a scroll.
      syncGlass();
    };
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(el);
    return () => observer.disconnect();
  }, [screen, mode]);
  const onScroll = () => {
    follow.handlers.onScroll();
    syncGlass();
    pin.onScroll();
  };
  // The end of the transcript moves away without a scroll when content grows
  // under a reader who scrolled up. Watched rather than read after every
  // render: reading it then lays the whole transcript out again.
  React.useLayoutEffect(() => {
    const inner = scrollRef.current?.querySelector(".transcript-inner");
    if (!inner || typeof ResizeObserver === "undefined") return;
    syncGlass();
    const observer = new ResizeObserver(syncGlass);
    observer.observe(inner);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screen, mode]);
  React.useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 180) + "px";
  }, [draft]);

  const busy = isBusy(state.session.status);
  const connected = !OFFLINE.has(state.session.status);

  // The avatars' loops ask for a frame sixty times a second. Nobody sees them
  // while the panel is hidden, and while the user works elsewhere with nothing
  // running they are only a cost: they hold where they are until either ends.
  const [seen, setSeen] = React.useState(() => ({ shown: !document.hidden, focused: document.hasFocus() }));
  React.useEffect(() => {
    const update = () => setSeen({ shown: !document.hidden, focused: document.hasFocus() });
    window.addEventListener("focus", update);
    window.addEventListener("blur", update);
    document.addEventListener("visibilitychange", update);
    return () => {
      window.removeEventListener("focus", update);
      window.removeEventListener("blur", update);
      document.removeEventListener("visibilitychange", update);
    };
  }, []);
  const still = !seen.shown || (!seen.focused && !busy);
  React.useEffect(() => {
    document.documentElement.toggleAttribute("data-still", still);
  }, [still]);
  const items = state.transcript.items;
  const lastItem = items[items.length - 1];
  const showTyping = busy && lastItem?.kind !== "assistant";
  // At work it has its own things to do; waiting on the user, it is anxious while they type.
  const rapt = useRapt();
  const pose = useSteadyPose(busy ? avatarPose(state.session, lastItem) : rapt ? "anxious" : "idle");
  // Nothing written, the agent set to work, or told not to be: nothing to be anxious about.
  const anxious = prefs.avatarAnxious && !busy;
  React.useEffect(() => {
    if (!draft || !anxious) stroke("calm");
  }, [draft, anxious]);
  // A turn over, the avatar is told how it went. Waiting on an approval is not over.
  const inTurn = React.useRef(false);
  React.useEffect(() => {
    if (busy) inTurn.current = true;
    if (busy || !inTurn.current || !TURN_OVER.has(state.session.status)) return;
    inTurn.current = false;
    const mood = turnMood(state.session.status, items);
    if (mood) cue(mood);
  }, [busy, state.session.status, items]);
  const blocks = React.useMemo(() => groupBlocks(items), [items]);

  React.useEffect(() => {
    if (drawn === Infinity) return;
    if (drawn >= blocks.length + 1) {
      setDrawn(Infinity);
      return;
    }
    let timer = 0;
    const frame = requestAnimationFrame(() => {
      timer = window.setTimeout(() => setDrawn((n) => n + MORE_BLOCKS), 0);
    });
    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(timer);
    };
  }, [drawn, blocks.length]);
  const pinText = React.useMemo(() => {
    const found = pin.target && items.find((item) => item.id === pin.target?.message);
    return found && found.kind === "user" ? found.text : "";
  }, [items, pin.target]);

  const updateMention = (text: string, caret: number) => {
    const prefix = mentionPrefix(text, caret);
    mentionQueryRef.current = prefix;
    if (prefix === null) {
      setMention(null);
      return;
    }
    // Ask the host on every keystroke but let the answer decide: the query is
    // matched against the caret's own prefix when it comes back.
    send({ type: "searchFiles", query: prefix });
  };

  /** Read image files into the pending strip, however they arrived. */
  const attachImages = (files: File[]) => {
    for (const file of files) {
      if (!file.type.startsWith("image/")) continue;
      const reader = new FileReader();
      reader.onload = () => {
        const result = String(reader.result ?? "");
        const comma = result.indexOf(",");
        if (comma < 0) return;
        setImages((prev) => [
          ...prev,
          { data: result.slice(comma + 1), media_type: file.type || "image/png" },
        ]);
      };
      reader.readAsDataURL(file);
    }
  };

  const onComposerPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData?.items ?? [])
      .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null);
    if (files.length === 0) return;
    e.preventDefault();
    attachImages(files);
  };

  // Dropping a screenshot on the composer is the other half of pasting one.
  // Text drops are left alone: dragging a path in from the editor should type
  // it, which is the browser's own behaviour.
  const onComposerDrop = (e: React.DragEvent<HTMLElement>) => {
    const files = Array.from(e.dataTransfer?.files ?? []).filter((file) =>
      file.type.startsWith("image/")
    );
    if (files.length === 0) return;
    e.preventDefault();
    setDropping(false);
    attachImages(files);
  };

  const pickMention = (path: string) => {
    const textarea = textareaRef.current;
    const caret = textarea ? textarea.selectionStart : draft.length;
    const next = applyMention(draft, caret, path);
    setDraft(next.text);
    setMention(null);
    mentionQueryRef.current = null;
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (el) el.setSelectionRange(next.caret, next.caret);
    });
  };

  const mentionOpen = mention !== null && mention.paths.length > 0;
  const commands = menuDismissed ? [] : matchCommands(catalog, draft);
  const menuOpen = commands.length > 0;
  React.useEffect(() => setCmdIndex(0), [draft]);

  // -- persisted state helpers --------------------------------------------
  const persistState = (next: PersistedState) => {
    saveState(next);
    setPersisted(next);
  };

  const updatePrefs = (patch: Partial<ExtPrefs>) => {
    const nextPrefs = { ...prefs, ...patch };
    persistState({ ...persisted, prefs: nextPrefs });
    if (patch.mode !== undefined || patch.autoRunRefactor !== undefined) {
      send({ type: "setMode", mode: nextPrefs.mode, autoRunRefactor: nextPrefs.autoRunRefactor });
    }
  };

  // -- conversation lifecycle ---------------------------------------------
  // Leaving a thread is when its summary can be written: the turn is over and
  // the user has moved on, so the model call costs nobody any waiting.
  const summarizeOutgoing = () => {
    if (activeId && state.transcript.items.length > 1) {
      send({ type: "summarizeConversation", id: activeId });
    }
  };

  const startNewConversation = () => {
    summarizeOutgoing();
    const id = newId();
    setActiveId(id);
    dispatch(clientEvent("conversation.reset"));
    send({ type: "newConversation", id });
    setDraft("");
    updatePrefs({ mode: "agent" });
    setScreen("chat");
    cue("greet");
  };

  /**
   * `here` opens it in this page only: the backend already has it, as when a
   * surface takes over the chat the other one was showing, turn and all.
   */
  const openConversation = (id: string, here = false) => {
    if (id !== activeId && !here) summarizeOutgoing();
    setActiveId(id);
    // Asked of the host and of the backend at once; see the two answers above.
    savedRef.current = null;
    openingRef.current = { id, kept: "waiting", rebuilt: here };
    pendingReconstructRef.current = here ? null : id;
    dispatch(clientEvent("client.load", { items: [] }));
    send({ type: "loadTranscript", id });
    if (!here) send({ type: "loadConversation", id });
    updatePrefs({ mode: "agent" });
    setScreen("chat");
  };

  React.useEffect(() => {
    if (!takeOver) return;
    setTakeOver(null);
    openConversation(takeOver, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [takeOver]);

  // What this surface hands over when the chat leaves it: what is waiting to
  // be saved first, so the host's copy is the one on screen.
  popOutRef.current = () => {
    pendingSave.current?.();
    const handed = activeId && screen === "chat" ? upsertActive(persisted, activeId, state.transcript.items) : persisted;
    send({ type: "popOut", state: handed, conversation: activeId ?? undefined });
  };

  const deleteConversation = (id: string) => {
    persistState(removeConversation(persisted, id));
    setServerConvos((list) => list.filter((c) => c.id !== id));
    send({ type: "deleteConversation", id });
    if (id === activeId) setActiveId(null);
  };

  const clearMessages = () => dispatch(clientEvent("conversation.reset"));
  const goHome = () => {
    summarizeOutgoing();
    setScreen("home");
  };

  const openSettings = () => {
    setReturnScreen(screen === "settings" ? "chat" : screen);
    if (!settings) send({ type: "getSettings" });
    send({ type: "mcp", action: "list" });
    setScreen("settings");
  };

  const runMcpAction = (action: McpAction) => {
    const name =
      action.action === "save"
        ? String(action.server.name ?? "")
        : action.action === "list"
          ? ""
          : action.name;
    setMcp((m) => ({ ...m, error: "", busy: name }));
    send({ type: "mcp", ...action });
  };

  // -- command + submit ----------------------------------------------------
  const runCommand = (cmd: SlashCommand, argument = "") => {
    setDraft("");
    setMenuDismissed(false);
    switch (cmd.action.kind) {
      case "clear":
        clearMessages();
        break;
      case "new":
        startNewConversation();
        break;
      case "history":
        setScreen("home");
        break;
      case "settings":
        openSettings();
        break;
      case "compact":
        send({ type: "compact" });
        break;
      case "update":
        send({ type: "installUpdate" });
        break;
      case "cancel":
        send({ type: "cancel" });
        break;
      case "permission":
        send({ type: "setPermissionMode", mode: cmd.action.mode });
        break;
      case "planner":
        send({ type: "setPlannerMode", mode: cmd.action.mode });
        break;
      case "replan":
        send({ type: "replan" });
        break;
      case "goal":
        send({
          type: "goal",
          action: cmd.action.action,
          objective: cmd.action.action === "define" ? argument : undefined,
        });
        break;
      case "index":
        send({ type: "indexRefresh", full: cmd.action.full, subtree: argument || undefined });
        break;
      case "terminal": {
        const action = cmd.action.action;
        if (action === "write") send({ type: "terminal", request: { action: "write", text: argument } });
        else if (action === "ctrl")
          send({ type: "terminal", request: { action: "ctrl", key: argument || "c" } });
        else if (action === "start")
          send({ type: "terminal", request: { action: "start", command: argument || undefined } });
        else send({ type: "terminal", request: { action } as TerminalRequest });
        break;
      }
      case "assets": {
        const wanted = cmd.action.which === "skills" ? "skill" : "workflow";
        const list = catalog.filter((c) => c.kind === wanted);
        dispatch(
          clientEvent("warning", {
            message: list.length
              ? `${list.length} ${cmd.action.which}: ` +
                list.map((c) => c.command).join(", ") +
                t(" — type the name with / to force it.")
              : tf("No {0} found in this workspace.", wanted),
          })
        );
        break;
      }
      case "asset":
        send({ type: "runAsset", token: cmd.command, argument });
        break;
      case "report":
        send({ type: "report", method: cmd.action.method });
        break;
      case "settings-update": {
        if (!argument.trim()) {
          openSettings();
          break;
        }
        const field = cmd.action.field;
        const value =
          field === "learn" || field === "desktop_avatar"
            ? argument.trim() === "on"
            : field === "max_context_tokens"
              ? Number(argument.trim())
              : argument.trim();
        send({ type: "updateSettings", updates: { [field]: value } });
        break;
      }
      case "unsupported":
        dispatch(
          clientEvent("warning", {
            message: tf(
              "{0} is not in the extension yet; use the terminal for now.",
              cmd.command
            ),
          })
        );
        break;
    }
  };

  /** A prompt from a button rather than the composer: sent as written. */
  const sendPrompt = (text: string, withContext: boolean) => {
    send({ type: "submit", text, includeContext: withContext && !!editorContext });
  };

  const submit = (steer = false) => {
    const text = draft.trim();
    if (!text) return;
    if (anxious) stroke("send");
    const resolved = resolveCommand(catalog, text);
    if (resolved) {
      runCommand(resolved.command, resolved.argument);
      return;
    }
    // `/name arg` that matches no command may still be a workflow or a skill on
    // disk; the bridge resolves it and says so when it is neither.
    if (text.startsWith("/") && !text.includes("\n")) {
      const [token, ...rest] = text.split(/\s+/);
      send({ type: "runAsset", token, argument: rest.join(" ") });
      setDraft("");
      setMenuDismissed(false);
      return;
    }
    // While the agent works there are two useful things to mean, so the key
    // says which: Enter asks a question and waits its turn, Alt+Enter cuts into
    // the running turn to redirect it.
    send({
      type: "submit",
      text,
      includeContext: includeContext && !!editorContext,
      images: images.length ? images : undefined,
      steer,
    });
    setDraft("");
    setImages([]);
    setMenuDismissed(false);
    // Sending is asking to see the answer: back to the end, wherever they were.
    follow.rejoin();
  };

  const onComposerKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mentionOpen && mention) {
      const paths = mention.paths;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionIndex((i) => (i + 1) % paths.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionIndex((i) => (i - 1 + paths.length) % paths.length);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        pickMention(paths[mentionIndex]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMention(null);
        mentionQueryRef.current = null;
        return;
      }
    }
    if (menuOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setCmdIndex((i) => (i + 1) % commands.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setCmdIndex((i) => (i - 1 + commands.length) % commands.length);
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        setDraft(commands[cmdIndex].completion);
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const picked = commands[cmdIndex];
        const resolved = resolveCommand(catalog, draft);
        runCommand(picked, resolved?.command === picked ? resolved.argument : "");
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMenuDismissed(true);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit(e.altKey);
    }
  };

  const resolveApproval = (callId: string, scope: ApprovalScope, reason?: string) => {
    cue(scope === "deny" ? "refused" : "answered");
    send({ type: "resolveApproval", call_id: callId, scope, reason });
  };
  const openDiff = React.useCallback((path: string) => send({ type: "openDiff", path }), []);
  // A message waiting for a turn of its own goes to the turn that is running instead.
  const answerNow = React.useCallback((text: string) => send({ type: "answerNow", text }), []);

  // One of the user's boxes, deleted with what the agent answered it. The
  // backend finds the message by counting back from the newest one the user
  // sent: older ones may have been folded into a summary there, newer ones not.
  const itemsRef = React.useRef(state.transcript.items);
  itemsRef.current = state.transcript.items;
  const deleteBlock = React.useCallback((block: Block) => {
    const said = (item: Item): item is Item & { kind: "user" } =>
      item.kind === "user" && !/^(\[runtime\]|Compressed context summary\.)/.test(item.text);
    const all = itemsRef.current.filter(said);
    const mine = block.items.filter(said);
    const last = mine.length ? all.findIndex((item) => item.id === mine[mine.length - 1].id) : -1;
    if (last >= 0) {
      send({ type: "deleteMessage", text: mine[0].text, fromEnd: all.length - 1 - last, count: mine.length });
    }
    if (block.items.length) {
      dispatch(
        clientEvent("client.dropExchange", { first: block.items[0].id, last: block.items[block.items.length - 1].id })
      );
    }
  }, []);
  const setPermission = (m: PermissionMode) => send({ type: "setPermissionMode", mode: m });

  const applyRefactor = (markdown: string) => {
    const target = refactor.path ? ` to \`${refactor.path}\`` : "";
    updatePrefs({ mode: "agent" });
    setScreen("chat");
    send({
      type: "submit",
      text: `Apply the following refactoring plan${target}. Make the edits directly in the affected files.\n\n${markdown}`,
      includeContext: false,
    });
  };

  const railActions: RailActions = {
    onIndex: (full) => send({ type: "indexRefresh", full }),
    onCompact: () => send({ type: "compact" }),
    onReplan: () => send({ type: "replan" }),
    onGoal: (action) => send({ type: "goal", action }),
    onTerminal: (request) => send({ type: "terminal", request }),
    onOpenConversation: openConversation,
    onDeleteConversation: deleteConversation,
    onNewConversation: startNewConversation,
    onSeeAllHistory: goHome,
    onSettings: openSettings,
    onOpenChange: (path) => send({ type: "openChange", path }),
    onRevertChange: (path) => send({ type: "revertChange", path }),
    onReviewChanges: () => send({ type: "reviewChanges" }),
  };

  const history = mergeHistory(persisted.conversations, serverConvos);

  // Live from the bridge; the settings snapshot covers a panel opened later.
  const branch = state.session.branch || settings?.branch || "";

  const updateButton = update && (
    <UpdateButton
      update={update}
      onInstall={() => send({ type: "installUpdate" })}
      onReload={() => send({ type: "reloadWindow" })}
      onSignIn={() => send({ type: "signInForUpdates" })}
    />
  );

  // Over every screen, where it was last left. Keyed, so it is the same one on
  // each of them and going from one to another does not put it back.
  // On the desktop it has left the panel: the backend shows it there.
  const floats = prefs.floatingAvatar && !settings?.desktop_avatar;
  const floater = floats && (
    <FloatingAvatar
      key="floater"
      pose={pose}
      start={isPlace(prefs.avatarAt) ? prefs.avatarAt : undefined}
      watch={prefs.avatarWatches}
      dock={screen === "home" ? ".spark-dock" : undefined}
      scene={`${screen}:${mode}`}
      manners={{
        greets: prefs.avatarGreets,
        waits: prefs.avatarWaits,
        cheers: prefs.avatarCheers,
        sleeps: prefs.avatarSleeps,
        touch: prefs.avatarTouch,
        hides: prefs.avatarHides,
      }}
      waitsOn={
        state.approvals.pending || state.questions.pending.length > 0 || state.elicitations.pending.length > 0
          ? WAITS_ON
          : undefined
      }
      onRest={(avatarAt) => updatePrefs({ avatarAt })}
    />
  );

  // -- screens -------------------------------------------------------------
  if (elsewhere) {
    return (
      <div className="app elsewhere" ref={rootRef} data-w={width}>
        <Codicon name="link-external" size={28} />
        <p>{t("Code-AI is open in its own window.")}</p>
        <button className="btn-primary" onClick={() => send({ type: "popIn" })}>
          {t("Bring it back here")}
        </button>
      </div>
    );
  }

  if (screen === "settings") {
    return (
      <div className="app" ref={rootRef} data-w={width}>
        <SettingsScreen
          settings={settings}
          editor={editorSettings}
          narrow={width !== "md"}
          onSaveEditor={(updates) => send({ type: "updateEditorSettings", updates })}
          sessionStatus={state.session.status}
          prefs={prefs}
          models={models}
          onBack={() => setScreen(returnScreen)}
          save={save}
          onSave={(updates) => {
            setSave({ status: "saving", errors: {} });
            send({ type: "updateSettings", updates });
          }}
          onPrefsChange={updatePrefs}
          onRestart={() => send({ type: "restartBridge" })}
          onListModels={() => {
            setModels((m) => ({ ...m, status: "loading", error: undefined }));
            send({ type: "listModels" });
          }}
          mcp={mcp}
          onMcpAction={runMcpAction}
        />
        {floater}
      </div>
    );
  }

  if (screen === "home") {
    return (
      <div className="app" ref={rootRef} data-w={width}>
        <HomeScreen
          entries={history}
          onNew={startNewConversation}
          onOpen={openConversation}
          onDelete={deleteConversation}
          onSettings={openSettings}
          update={updateButton}
          docked={floats}
          version={settings?.version}
        />
        {floater}
      </div>
    );
  }

  // What is still live at the end of the agent's turn. It goes in the agent's
  // box, which it opens itself when the user spoke last.
  const elicitation = state.elicitations.pending[0];
  const live =
    showTyping || state.approvals.pending || state.questions.pending.length > 0 || elicitation ? (
      <>
        {showTyping && (
          <TypingIndicator label={activityLabel(state.session)} heartbeat={state.session.heartbeat} />
        )}
        {state.approvals.pending && (
          <ApprovalCard
            approval={state.approvals.pending}
            onResolve={resolveApproval}
            onOpenDiff={editorDiff ? openDiff : undefined}
          />
        )}
        {state.questions.pending.length > 0 && (
          <QuestionsCard
            questions={state.questions.pending}
            onAnswer={(text) => {
              cue("answered");
              send({ type: "answerQuestions", text });
            }}
          />
        )}
        {elicitation && (
          <ElicitationCard
            request={elicitation}
            error={elicitErrors[elicitation.id] ?? ""}
            onAnswer={(action, content) => {
              if (action === "accept") cue("answered");
              send({ type: "mcpElicitation", id: elicitation.id, action, content });
            }}
          />
        )}
      </>
    ) : null;
  const signInForPicture = () => {
    setAvatar((a) => ({ ...a, pending: true }));
    send({ type: "avatarSignIn" });
  };
  const lastBlock = blocks[blocks.length - 1];
  // All over, and well: the backend is idle again, nothing waits on the user,
  // and the agent's last box ends on its answer. That box goes green.
  const settled = SETTLED.has(state.session.status) && !live;
  const done = settled && lastBlock?.speaker === "agent" && endsOnAnswer(lastBlock);
  const shownBlocks: Block[] =
    live && lastBlock?.speaker !== "agent"
      ? [...blocks, { speaker: "agent", key: agentKey(lastBlock?.key ?? ""), items: [] }]
      : blocks;
  const drawnBlocks = shownBlocks.length > drawn ? shownBlocks.slice(shownBlocks.length - drawn) : shownBlocks;

  const composerBar = (action?: React.ReactNode) => (
    <ComposerBar
      mode={mode}
      planner={state.session.plannerMode}
      permission={state.session.permissionMode}
      model={settings?.model ?? ""}
      models={models}
      onMode={(m: AppMode) => updatePrefs({ mode: m })}
      onPlanner={(m) => send({ type: "setPlannerMode", mode: m })}
      onPermission={setPermission}
      onModel={(m) => {
        setSave({ status: "saving", errors: {} });
        send({ type: "updateSettings", updates: { model: m } });
      }}
      onListModels={() => {
        if (models.status === "loading") return;
        setModels((m) => ({ ...m, status: "loading", error: undefined }));
        send({ type: "listModels" });
      }}
      action={action}
    />
  );

  const hasSelection = !!editorContext?.selection;
  const emptyActions: EmptyAction[] = hasSelection
    ? [
        {
          icon: "lightbulb",
          label: t("Explain the selection"),
          hint: ctxLabelOf(editorContext),
          run: () => sendPrompt(t("Explain the selected code: what it does, why, and anything surprising in it."), true),
        },
        {
          icon: "wand",
          label: t("Refactor the selection"),
          hint: t("Architectural improvements, planned before they are applied"),
          run: () => {
            updatePrefs({ mode: "refactor" });
            send({ type: "analyzeRefactor" });
          },
        },
      ]
    : [
        {
          icon: "book",
          label: t("Explain this workspace"),
          hint: t("Its structure, main modules and how they fit together"),
          run: () => sendPrompt(t("Explain this workspace: its structure, its main modules and how they fit together."), false),
        },
        {
          icon: "git-compare",
          label: t("Review uncommitted changes"),
          hint: t("Bugs, risks and loose ends in the working tree"),
          run: () => sendPrompt(t("Review the uncommitted changes in this workspace: bugs, risks and loose ends."), false),
        },
      ];
  const banner: BannerState | null =
    state.session.status === "DISCONNECTED"
      ? { kind: "offline" }
      : !connected
        ? { kind: "connecting" }
        : state.session.retry
          ? { kind: "retry", attempt: state.session.retry, note: state.session.retryNote }
          : null;
  const showTerminal = () => setReveal({ section: "terminal", nonce: Date.now() });

  const ctxChip = editorContext && includeContext;
  const ctxLabel = ctxLabelOf(editorContext);
  const otherOpen = editorContext?.openFiles?.length ?? 0;

  return (
    <div className="app" ref={rootRef} data-w={width}>
      <ChatHeader
        title={history.find((h) => h.id === activeId)?.title || t("New conversation")}
        branch={branch}
        busy={busy}
        pose={pose}
        offline={!connected}
        activity={`${activityLabel(state.session)} · ${formatElapsed(state.session.heartbeat)}`}
        narrow={width !== "md"}
        usage={state.usage}
        update={updateButton}
        railOpen={railDocked}
        onBack={goHome}
        onNew={startNewConversation}
        onHistory={goHome}
        onToggleRail={() => setRailDocked((d) => !d)}
        onSettings={openSettings}
        onCompact={() => send({ type: "compact" })}
        onPopIn={IS_WINDOW ? () => send({ type: "popIn" }) : undefined}
      />

      <div className="workbench">
        <div className="workarea">
          {mode === "agent" ? (
            <div className="chat-col">
              <div className="transcript" ref={scrollRef} {...follow.handlers} onScroll={onScroll}>
                <PromptPin target={pin.target} text={pinText} avatar={avatar.src} onGo={pin.go} />
                <div className="transcript-inner">
                  {shownBlocks.length === 0 ? (
                    <EmptyState
                      actions={emptyActions}
                      recent={history.filter((h) => h.id !== activeId).slice(0, 3)}
                      onOpen={openConversation}
                    />
                  ) : (
                    drawnBlocks.map((block, i) => (
                      <BlockView
                        key={block.key}
                        block={block}
                        avatar={avatar.src}
                        avatarProblem={avatar.problem}
                        avatarPending={avatar.pending}
                        onAvatarSignIn={avatar.signIn && !avatar.src ? signInForPicture : undefined}
                        done={done && block === lastBlock}
                        pose={i === drawnBlocks.length - 1 && block.speaker === "agent" ? pose : null}
                        onOpenDiff={editorDiff ? openDiff : undefined}
                        onTerminal={showTerminal}
                        onAnswerNow={answerNow}
                        onDelete={block.speaker === "user" && !busy ? deleteBlock : undefined}
                      >
                        {i === drawnBlocks.length - 1 && block.speaker === "agent" ? live : null}
                      </BlockView>
                    ))
                  )}
                </div>
              </div>

              <div
                ref={composerRef}
                className={`composer-wrap ${dropping ? "dropping" : ""}`}
                onDragOver={(e) => {
                  if (!Array.from(e.dataTransfer?.types ?? []).includes("Files")) return;
                  e.preventDefault();
                  setDropping(true);
                }}
                onDragLeave={() => setDropping(false)}
                onDrop={onComposerDrop}
              >
                {follow.away && (
                  <button
                    className="jump-btn"
                    title={t("Go to the latest message")}
                    aria-label={t("Go to the latest message")}
                    onClick={jumpToLatest}
                  >
                    <Codicon name="arrow-down" />
                  </button>
                )}
                {!editorDiff && <LiveCodePanel code={state.code} />}
                <div className="composer-stack">
                  {banner && <Banner state={banner} onReconnect={() => send({ type: "restartBridge" })} />}
                  {mentionOpen && mention && (
                    <MentionMenu
                      paths={mention.paths}
                      active={mentionIndex}
                      onPick={pickMention}
                      onHover={setMentionIndex}
                    />
                  )}
                  {menuOpen && !mentionOpen && (
                    <CommandMenu
                      commands={commands}
                      active={cmdIndex}
                      onPick={(cmd) => runCommand(cmd)}
                      onHover={setCmdIndex}
                    />
                  )}
                  <div className={`composer ${busy ? "busy" : ""} ${connected ? "" : "off"}`}>
                    {busy && <BusyRing />}
                    {(ctxChip || images.length > 0) && (
                      <div className="composer-ctx">
                        {ctxChip && (
                          <span className="ctx-chip" title={ctxTitleOf(editorContext)}>
                            <Codicon name={editorContext?.path ? "file" : "files"} size={13} />
                            <span className="ctx-label">{ctxLabel}</span>
                            {editorContext?.path ? (
                              <span className="ctx-kind">
                                {editorContext.selection ? t("selection") : t("open file")}
                              </span>
                            ) : null}
                            {editorContext?.path && otherOpen > 0 ? (
                              <span className="ctx-more">
                                {otherOpen === 1 ? t("+1 open") : tf("+{0} open", otherOpen)}
                              </span>
                            ) : null}
                            <button
                              className="ctx-remove"
                              title={t("Do not include the editor context")}
                              onClick={() => setIncludeContext(false)}
                            >
                              <Codicon name="close" size={12} />
                            </button>
                          </span>
                        )}
                        {images.map((image, i) => (
                          <span key={i} className="paste-chip">
                            <img
                              src={`data:${image.media_type};base64,${image.data}`}
                              alt={tf("Pasted image {0}", i + 1)}
                            />
                            <button
                              aria-label={t("Remove image")}
                              onClick={() => setImages((prev) => prev.filter((_, j) => j !== i))}
                            >
                              <Codicon name="close" size={11} />
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                    <textarea
                      ref={textareaRef}
                      value={draft}
                      placeholder={
                        !connected
                          ? t("Reconnect to send")
                          : busy
                            ? t("Reply or steer the agent…")
                            : t("Ask, request a change, or type / for commands")
                      }
                      rows={1}
                      onChange={(e) => {
                        setDraft(e.target.value);
                        setMenuDismissed(false);
                        updateMention(e.target.value, e.target.selectionStart ?? 0);
                        if (anxious) {
                          const how = (e.nativeEvent as InputEvent).inputType ?? "";
                          stroke(how.startsWith("delete") ? "erase" : "char", e.target);
                        }
                      }}
                      onClick={(e) =>
                        updateMention(
                          e.currentTarget.value,
                          e.currentTarget.selectionStart ?? 0
                        )
                      }
                      onKeyDown={onComposerKey}
                      onPaste={onComposerPaste}
                    />
                    {composerBar(
                      busy && !draft.trim() ? (
                        <button
                          className="send-btn stop"
                          title={t("Stop the agent")}
                          aria-label={t("Stop the agent")}
                          onPointerDown={ripple}
                          onClick={() => send({ type: "cancel" })}
                        >
                          <Codicon name="debug-stop" />
                        </button>
                      ) : (
                        <button
                          className="send-btn"
                          title={connected ? t("Send (Alt to steer the running turn)") : t("Not connected to the backend")}
                          aria-label={t("Send")}
                          disabled={!draft.trim() || !connected}
                          onPointerDown={ripple}
                          onClick={(e) => submit(e.altKey)}
                        >
                          <Codicon name="send" />
                        </button>
                      )
                    )}
                  </div>
                </div>
                <div className="hint composer-hint">
                  <span>
                    {busy
                      ? t("Enter answers later · Alt+Enter steers this turn · Esc terminal")
                      : t("Enter sends · Shift+Enter new line · / commands · @ files · Esc terminal")}
                  </span>
                  <VersionMark version={settings?.version} />
                </div>
              </div>
            </div>
          ) : mode === "refactor" ? (
            <div className="mode-col">
              <RefactorPanel
                state={refactor}
                autoRun={prefs.autoRunRefactor}
                onAnalyze={() => send({ type: "analyzeRefactor" })}
                onPlan={(id: string, improvements) => send({ type: "planRefactor", id, improvements })}
                onApply={applyRefactor}
              />
              <div className="mode-foot">{composerBar()}</div>
            </div>
          ) : (
            <div className="mode-col">
              <ExplainPanel state={explain} />
              <div className="mode-foot">{composerBar()}</div>
            </div>
          )}
        </div>

        <TaskRail
          width={width}
          reveal={reveal}
          focusNonce={terminalFocus}
          state={state}
          model={settings?.model ?? ""}
          workspace={settings?.workspace ?? ""}
          branch={branch}
          history={history}
          activeId={activeId}
          connected={connected}
          waiting={!!state.approvals.pending}
          changes={changes}
          docked={railDocked}
          onDockedChange={setRailDocked}
          actions={railActions}
        />
      </div>

      {floater}
    </div>
  );
}

/** How many of the other open files the chip's tooltip names. */
const CTX_TOOLTIP_FILES = 12;

function ctxLabelOf(context: EditorContext | null): string {
  if (!context) return "";
  if (!context.path) {
    // No file has the focus: the open ones are the context.
    const count = context.openFiles?.length ?? 0;
    return count === 1 ? t("1 open file") : tf("{0} open files", count);
  }
  return context.selection && context.startLine && context.endLine
    ? `${context.path}:${context.startLine}-${context.endLine}`
    : context.path;
}

/** The chip's tooltip: that the context travels, and which open files travel with it. */
function ctxTitleOf(context: EditorContext | null): string {
  const open = context?.openFiles ?? [];
  if (open.length === 0) return t("Travels with the next message");
  const named = open.slice(0, CTX_TOOLTIP_FILES);
  const left = open.length - named.length;
  return [
    t("Travels with the next message"),
    "",
    context?.path ? t("Also open in the editor:") : t("Open in the editor:"),
    ...named,
    ...(left > 0 ? [tf("and {0} more", left)] : []),
  ].join("\n");
}

const styleTag = document.createElement("style");
// KaTeX's first: the panel's own rules come after it and win.
styleTag.textContent = `${KATEX_STYLE}\n${STYLE}`;
document.head.appendChild(styleTag);

// The icon font is fetched by the first screen that draws an icon, and its
// icons stay blank until it lands; Home draws none, so fetch it now.
void document.fonts?.load("16px codicon").catch(() => undefined);
createRoot(document.getElementById("root")!).render(<App />);
