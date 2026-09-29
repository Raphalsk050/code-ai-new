import * as React from "react";
import { createRoot } from "react-dom/client";

import type {
  AppMode,
  ApprovalScope,
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
  mergeHistory,
  newId,
  PersistedState,
  removeConversation,
  upsertActive,
} from "./history";
import {
  IconBack,
  IconBroom,
  IconCI,
  IconFile,
  IconPlus,
  IconRefresh,
  IconSend,
  IconSettings,
  IconStop,
} from "./icons";
import { LiveCodePanel } from "./live-code";
import { UpdateButton, UpdateView } from "./update-button";
import { mergeStatus } from "./mcp-form";
import { INITIAL_MCP, McpViewState } from "./mcp-settings";
import { MentionMenu } from "./mention-menu";
import { applyMention, mentionPrefix, rankPaths } from "./mentions";
import { activityLabel, formatElapsed, ItemView, TypingIndicator } from "./messages";
import { ModeSwitch } from "./mode-switch";
import { ripple } from "./motion";
import { QuestionsCard } from "./questions-card";
import { INITIAL_REFACTOR, RefactorPanel, RefactorViewState } from "./refactor";
import { ModelsState, SaveState, SettingsScreen } from "./settings";
import { applyEvent, initialAppState, isBusy, Item, messagesToItems } from "./store";
import { STYLE } from "./styles";
import { RailActions, TaskRail } from "./task-rail";
import { UsageBar } from "./usage-bar";
import { useContainerWidth } from "./use-container-width";

declare function acquireVsCodeApi(): {
  postMessage: (message: WebviewToHost) => void;
  getState: () => PersistedState | undefined;
  setState: (state: PersistedState) => void;
};
const vscode = acquireVsCodeApi();
const send = (message: WebviewToHost) => vscode.postMessage(message);

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

function App(): JSX.Element {
  const [state, dispatch] = React.useReducer(applyEvent, initialAppState);
  const [persisted, setPersisted] = React.useState<PersistedState>(
    () => vscode.getState() ?? EMPTY_PERSISTED
  );
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
  // Edits are shown in the editor's diff view: no live code or diff in here.
  const [editorDiff, setEditorDiff] = React.useState(false);
  // A newer release, once the backend has found one.
  const [update, setUpdate] = React.useState<UpdateView | null>(null);

  const [rootRef, width] = useContainerWidth<HTMLDivElement>();
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  const pinnedRef = React.useRef(true);
  const pendingReconstructRef = React.useRef<string | null>(null);

  const prefs: ExtPrefs = persisted.prefs ?? DEFAULT_PREFS;
  const mode = prefs.mode;

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
      } else if (data?.type === "updateAvailable") {
        setUpdate((u) => ({
          version: data.version,
          current: data.current,
          notes: data.notes,
          // A check that runs again mid-install must not reset the button.
          state: u && u.version === data.version ? u.state : "available",
          message: "",
        }));
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
        if (pendingReconstructRef.current === data.id) {
          pendingReconstructRef.current = null;
          dispatch(clientEvent("client.load", { items: messagesToItems(data.messages) }));
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

  React.useEffect(() => {
    if (screen === "home") send({ type: "listConversations" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screen, state.session.status]);

  // -- persist the active transcript to local history ----------------------
  React.useEffect(() => {
    if (screen !== "chat" || mode !== "agent" || !activeId) return;
    setPersisted((prev) => {
      const next = upsertActive(prev, activeId, state.transcript.items);
      vscode.setState(next);
      return next;
    });
  }, [state.transcript.items, screen, activeId, mode]);

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
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };
  React.useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  });
  React.useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 180) + "px";
  }, [draft]);

  const busy = isBusy(state.session.status);
  const items = state.transcript.items;
  const lastItem = items[items.length - 1];
  const showTyping = busy && lastItem?.kind !== "assistant";

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
    vscode.setState(next);
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
  };

  const openConversation = (id: string) => {
    if (id !== activeId) summarizeOutgoing();
    const convo = persisted.conversations.find((c) => c.id === id);
    const cached = (convo?.items ?? []) as Item[];
    setActiveId(id);
    dispatch(clientEvent("client.load", { items: cached }));
    pendingReconstructRef.current = cached.length === 0 ? id : null;
    send({ type: "loadConversation", id });
    updatePrefs({ mode: "agent" });
    setScreen("chat");
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
          field === "learn"
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

  const submit = (steer = false) => {
    const text = draft.trim();
    if (!text) return;
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

  const resolveApproval = (callId: string, scope: ApprovalScope, reason?: string) =>
    send({ type: "resolveApproval", call_id: callId, scope, reason });
  const openDiff = React.useCallback((path: string) => send({ type: "openDiff", path }), []);
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
    onPlannerMode: (m) => send({ type: "setPlannerMode", mode: m }),
    onGoal: (action) => send({ type: "goal", action }),
    onTerminal: (request) => send({ type: "terminal", request }),
    onOpenConversation: openConversation,
    onDeleteConversation: deleteConversation,
    onNewConversation: startNewConversation,
    onSeeAllHistory: goHome,
    onSettings: openSettings,
  };

  const history = mergeHistory(persisted.conversations, serverConvos);

  // -- screens -------------------------------------------------------------
  if (screen === "settings") {
    return (
      <div className="app" ref={rootRef} data-w={width}>
        <SettingsScreen
          settings={settings}
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
        />
      </div>
    );
  }

  const ctxChip = editorContext && includeContext;
  const ctxLabel = editorContext
    ? editorContext.selection && editorContext.startLine && editorContext.endLine
      ? `${editorContext.path}:${editorContext.startLine}-${editorContext.endLine}`
      : editorContext.path
    : "";

  return (
    <div className="app" ref={rootRef} data-w={width}>
      <div className="statusbar">
        <div className="brand">
          <button className="icon-btn back-btn" title={t("Back to the history")} onClick={goHome}>
            <IconBack size={16} />
          </button>
          <span className={`dot ${busy ? "busy" : ""}`} />
          Code-AI
          {busy && (
            <span className="heartbeat" title={t("The agent is working")}>
              <span className="spinner heartbeat-spin" />
              <span className="heartbeat-label">{activityLabel(state.session)}</span>
              <span className="heartbeat-clock">{formatElapsed(state.session.heartbeat)}</span>
            </span>
          )}
        </div>
        <div className="meta">
          {update && (
            <UpdateButton
              update={update}
              onInstall={() => send({ type: "installUpdate" })}
              onReload={() => send({ type: "reloadWindow" })}
            />
          )}
          <div className="topbar-actions">
            {busy && (
              <button
                className="icon-btn danger"
                title={t("Stop the agent")}
                onPointerDown={ripple}
                onClick={() => send({ type: "cancel" })}
              >
                <IconStop size={14} />
              </button>
            )}
            <button
              className="icon-btn"
              title={t("Clear messages")}
              onPointerDown={ripple}
              onClick={clearMessages}
            >
              <IconBroom size={16} />
            </button>
            <button
              className="icon-btn"
              title={t("New conversation")}
              onPointerDown={ripple}
              onClick={startNewConversation}
            >
              <IconPlus size={16} />
            </button>
            <button
              className="icon-btn"
              title="Reiniciar Code-AI"
              onPointerDown={ripple}
              onClick={() => send({ type: "restartBridge" })}
            >
              <IconRefresh size={15} />
            </button>
            <button
              className="icon-btn"
              title={t("Settings")}
              onPointerDown={ripple}
              onClick={openSettings}
            >
              <IconSettings size={16} />
            </button>
          </div>
        </div>
      </div>

      <div className="modebar">
        <ModeSwitch mode={mode} onChange={(m: AppMode) => updatePrefs({ mode: m })} />
        <label className="perm" title={t("Permission mode for commands and edits")}>
          <span>perms</span>
          <select
            value={state.session.permissionMode}
            onChange={(e) => setPermission(e.target.value as PermissionMode)}
          >
            <option value="ask">Ask</option>
            <option value="auto">Auto</option>
            <option value="bypass">Bypass</option>
          </select>
        </label>
      </div>

      <UsageBar usage={state.usage} onCompact={() => send({ type: "compact" })} />

      <div className="workbench">
        <div className="workarea">
          {mode === "agent" ? (
            <>
              <div className="transcript" ref={scrollRef} onScroll={onScroll}>
                <div className="transcript-inner">
                  {items.length === 0 && !state.approvals.pending ? (
                    <div className="empty">
                      <div className="spark">
                        <IconCI size={30} />
                      </div>
                      <h2>Code-AI</h2>
                      <div>
                        {t("Ask anything about this workspace. Tools, plan and")}
                        {t("approvals show up here.")}
                      </div>
                    </div>
                  ) : (
                    items.map((item) => (
                      <ItemView key={item.id} item={item} onOpenDiff={editorDiff ? openDiff : undefined} />
                    ))
                  )}
                  {showTyping && (
                    <TypingIndicator
                      label={activityLabel(state.session)}
                      heartbeat={state.session.heartbeat}
                    />
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
                      onAnswer={(text) => send({ type: "answerQuestions", text })}
                    />
                  )}
                  {state.elicitations.pending.length > 0 && (
                    <ElicitationCard
                      request={state.elicitations.pending[0]}
                      error={elicitErrors[state.elicitations.pending[0].id] ?? ""}
                      onAnswer={(action, content) =>
                        send({
                          type: "mcpElicitation",
                          id: state.elicitations.pending[0].id,
                          action,
                          content,
                        })
                      }
                    />
                  )}
                </div>
              </div>

              {!editorDiff && <LiveCodePanel code={state.code} />}

              <div
                className={`composer-wrap ${dropping ? "dropping" : ""}`}
                onDragOver={(e) => {
                  if (!Array.from(e.dataTransfer?.types ?? []).includes("Files")) return;
                  e.preventDefault();
                  setDropping(true);
                }}
                onDragLeave={() => setDropping(false)}
                onDrop={onComposerDrop}
              >
                {ctxChip && (
                  <div className="ctx-chip" title={t("Travels with the next message")}>
                    <IconFile size={13} />
                    <span className="ctx-label">{ctxLabel}</span>
                    <span className="ctx-kind">
                      {editorContext?.selection ? t("selection") : t("open file")}
                    </span>
                    <button
                      className="ctx-remove"
                      title={t("Do not include the editor context")}
                      onClick={() => setIncludeContext(false)}
                    >
                      ×
                    </button>
                  </div>
                )}
                {images.length > 0 && (
                  <div className="paste-strip">
                    {images.map((image, i) => (
                      <div key={i} className="paste-chip">
                        <img
                          src={`data:${image.media_type};base64,${image.data}`}
                          alt={`Imagem colada ${i + 1}`}
                        />
                        <button
                          aria-label="Remover imagem"
                          onClick={() => setImages((prev) => prev.filter((_, j) => j !== i))}
                        >
                          ×
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                <div className="composer-stack">
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
                  <div className="composer">
                    <textarea
                      ref={textareaRef}
                      value={draft}
                      placeholder={t("Message for Code-AI…  (/ for commands)")}
                      rows={1}
                      onChange={(e) => {
                        setDraft(e.target.value);
                        setMenuDismissed(false);
                        updateMention(e.target.value, e.target.selectionStart ?? 0);
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
                    {busy && !draft.trim() ? (
                      <button
                        className="send-btn stop"
                        title="Cancelar"
                        onPointerDown={ripple}
                        onClick={() => send({ type: "cancel" })}
                      >
                        <IconStop />
                      </button>
                    ) : (
                      <button
                        className="send-btn"
                        title={t("Send (Alt to steer the running turn)")}
                        disabled={!draft.trim()}
                        onPointerDown={ripple}
                        onClick={(e) => submit(e.altKey)}
                      >
                        <IconSend size={15} />
                      </button>
                    )}
                  </div>
                </div>
                <div className="hint">
                  {busy
                    ? t("Enter answers later · Alt+Enter steers this turn · Esc terminal")
                    : "Enter envia · Shift+Enter quebra linha · / comandos · @ arquivos · Esc terminal"}
                </div>
              </div>
            </>
          ) : mode === "refactor" ? (
            <RefactorPanel
              state={refactor}
              autoRun={prefs.autoRunRefactor}
              onAnalyze={() => send({ type: "analyzeRefactor" })}
              onPlan={(id: string, improvements) => send({ type: "planRefactor", id, improvements })}
              onApply={applyRefactor}
            />
          ) : (
            <ExplainPanel state={explain} />
          )}
        </div>

        <TaskRail
          width={width}
          reveal={reveal}
          focusNonce={terminalFocus}
          state={state}
          model={settings?.model ?? ""}
          workspace={settings?.workspace ?? ""}
          history={history}
          activeId={activeId}
          connected={state.session.status !== "STARTING"}
          actions={railActions}
        />
      </div>

    </div>
  );
}

const styleTag = document.createElement("style");
styleTag.textContent = STYLE;
document.head.appendChild(styleTag);

createRoot(document.getElementById("root")!).render(<App />);
