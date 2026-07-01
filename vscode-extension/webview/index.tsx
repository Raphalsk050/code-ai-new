import * as React from "react";
import { createRoot } from "react-dom/client";

import type {
  AppMode,
  ApprovalScope,
  EditorContext,
  EventEnvelope,
  HostToWebview,
  PermissionMode,
  Settings,
  WebviewToHost,
} from "../src/protocol";
import { ApprovalModal } from "./approval";
import { CommandMenu } from "./command-menu";
import { exactCommand, matchCommands, SlashCommand } from "./commands";
import { ExplainPanel, ExplainViewState, INITIAL_EXPLAIN } from "./explain";
import { HomeScreen } from "./home";
import {
  DEFAULT_PREFS,
  EMPTY_PERSISTED,
  ExtPrefs,
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
import { formatElapsed, ItemView, TypingIndicator } from "./messages";
import { ModeSwitch } from "./mode-switch";
import { applyEvent, initialState, isBusy, Item, ViewState } from "./reducer";
import { INITIAL_REFACTOR, RefactorPanel, RefactorViewState } from "./refactor";
import { SettingsScreen } from "./settings";
import { STYLE } from "./styles";

declare function acquireVsCodeApi(): {
  postMessage: (message: WebviewToHost) => void;
  getState: () => PersistedState | undefined;
  setState: (state: PersistedState) => void;
};
const vscode = acquireVsCodeApi();
const send = (message: WebviewToHost) => vscode.postMessage(message);

function reducer(state: ViewState, event: EventEnvelope): ViewState {
  return applyEvent(state, event);
}

/** Build a synthetic envelope so the webview can drive the reducer locally. */
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
  const [state, dispatch] = React.useReducer(reducer, initialState);
  const [persisted, setPersisted] = React.useState<PersistedState>(
    () => vscode.getState() ?? EMPTY_PERSISTED
  );
  const [screen, setScreen] = React.useState<Screen>("home");
  const [returnScreen, setReturnScreen] = React.useState<Screen>("home");
  const [activeId, setActiveId] = React.useState<string | null>(null);
  const [settings, setSettings] = React.useState<Settings | null>(null);
  const [refactor, setRefactor] = React.useState<RefactorViewState>(INITIAL_REFACTOR);
  const [explain, setExplain] = React.useState<ExplainViewState>(INITIAL_EXPLAIN);

  const [draft, setDraft] = React.useState("");
  const [editorContext, setEditorContext] = React.useState<EditorContext | null>(null);
  const [includeContext, setIncludeContext] = React.useState(true);
  const [cmdIndex, setCmdIndex] = React.useState(0);
  const [menuDismissed, setMenuDismissed] = React.useState(false);

  const scrollRef = React.useRef<HTMLDivElement>(null);
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  const pinnedRef = React.useRef(true);

  const prefs: ExtPrefs = persisted.prefs ?? DEFAULT_PREFS;
  const mode = prefs.mode;

  // -- host messages -------------------------------------------------------
  React.useEffect(() => {
    const onMessage = (e: MessageEvent<HostToWebview>) => {
      const data = e.data;
      if (data?.type === "event") {
        dispatch(data.event as EventEnvelope);
      } else if (data?.type === "editorContext") {
        setEditorContext(data.context);
        setIncludeContext(true);
      } else if (data?.type === "settings") {
        setSettings(data.settings);
      } else if (data?.type === "settingsUpdated") {
        setSettings(data.result.settings);
      } else if (data?.type === "refactorStatus") {
        if (data.status === "analyzing") setRefactor((r) => ({ ...r, status: "analyzing", error: undefined }));
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
        // A new selection is being analyzed: drop the previous explanation so
        // the panel never shows a stale result for a different range.
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

  // Tell the host the active mode on first load so editor-driven behaviour
  // (explain/refactor) knows what to do.
  React.useEffect(() => {
    send({ type: "setMode", mode: prefs.mode, autoRunRefactor: prefs.autoRunRefactor });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // -- persist the active transcript to local history ----------------------
  React.useEffect(() => {
    if (screen !== "chat" || mode !== "agent" || !activeId) return;
    setPersisted((prev) => {
      const next = upsertActive(prev, activeId, state.items);
      vscode.setState(next);
      return next;
    });
  }, [state.items, screen, activeId, mode]);

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

  const busy = isBusy(state.status);
  const lastItem = state.items[state.items.length - 1];
  const showTyping = busy && lastItem?.kind !== "assistant";

  const commands = menuDismissed ? [] : matchCommands(draft);
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
  const startNewConversation = () => {
    setActiveId(newId());
    dispatch(clientEvent("conversation.reset"));
    send({ type: "newConversation" });
    setDraft("");
    updatePrefs({ mode: "agent" });
    setScreen("chat");
  };

  const openConversation = (id: string) => {
    const convo = persisted.conversations.find((c) => c.id === id);
    setActiveId(id);
    dispatch(clientEvent("client.load", { items: (convo?.items ?? []) as Item[] }));
    updatePrefs({ mode: "agent" });
    setScreen("chat");
  };

  const deleteConversation = (id: string) => {
    persistState(removeConversation(persisted, id));
    if (id === activeId) setActiveId(null);
  };

  const clearMessages = () => dispatch(clientEvent("conversation.reset"));
  const goHome = () => setScreen("home");

  const openSettings = () => {
    setReturnScreen(screen);
    if (!settings) send({ type: "getSettings" });
    setScreen("settings");
  };

  // -- command + submit ----------------------------------------------------
  const runCommand = (cmd: SlashCommand) => {
    setDraft("");
    setMenuDismissed(false);
    switch (cmd.action.kind) {
      case "clear":
        clearMessages();
        break;
      case "new":
        startNewConversation();
        break;
      case "compact":
        send({ type: "compact" });
        break;
      case "cancel":
        send({ type: "cancel" });
        break;
      case "permission":
        send({ type: "setPermissionMode", mode: cmd.action.mode });
        break;
    }
  };

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
    const cmd = exactCommand(text);
    if (cmd) {
      runCommand(cmd);
      return;
    }
    send({ type: "submit", text, includeContext: includeContext && !!editorContext });
    setDraft("");
    setMenuDismissed(false);
  };

  const onComposerKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
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
        setDraft(commands[cmdIndex].command + " ");
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        runCommand(commands[cmdIndex]);
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
      submit();
    }
  };

  const resolveApproval = (callId: string, scope: ApprovalScope, reason?: string) =>
    send({ type: "resolveApproval", call_id: callId, scope, reason });
  const setPermission = (m: PermissionMode) => send({ type: "setPermissionMode", mode: m });

  // Apply a refactoring plan: hand the markdown to the agent as a normal turn,
  // which then implements it through the usual edit/approval flow.
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

  // -- screens -------------------------------------------------------------
  if (screen === "settings") {
    return (
      <div className="app">
        <SettingsScreen
          settings={settings}
          prefs={prefs}
          onBack={() => setScreen(returnScreen)}
          onSave={(updates) => send({ type: "updateSettings", updates })}
          onPrefsChange={updatePrefs}
          onRestart={() => send({ type: "restartBridge" })}
        />
      </div>
    );
  }

  if (screen === "home") {
    return (
      <div className="app">
        <HomeScreen
          conversations={persisted.conversations}
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
    <div className="app">
      <div className="statusbar">
        <div className="brand">
          <button className="icon-btn back-btn" title="Back to history" onClick={goHome}>
            <IconBack size={16} />
          </button>
          <span className={`dot ${busy ? "busy" : ""}`} />
          Code-AI
          {busy && (
            <span className="heartbeat" title="The agent is working — this ticks while the process is alive">
              working {formatElapsed(state.heartbeat)}
            </span>
          )}
        </div>
        <div className="meta">
          {state.contextTokens && <span className="pill">{state.contextTokens} tok</span>}
          <div className="topbar-actions">
            {busy && (
              <button className="icon-btn danger" title="Stop the agent" onClick={() => send({ type: "cancel" })}>
                <IconStop size={14} />
              </button>
            )}
            <button className="icon-btn" title="Clear messages" onClick={clearMessages}>
              <IconBroom size={16} />
            </button>
            <button className="icon-btn" title="New conversation" onClick={startNewConversation}>
              <IconPlus size={16} />
            </button>
            <button
              className="icon-btn"
              title="Restart Code-AI (reload settings without reloading the window)"
              onClick={() => send({ type: "restartBridge" })}
            >
              <IconRefresh size={15} />
            </button>
            <button className="icon-btn" title="Settings" onClick={openSettings}>
              <IconSettings size={16} />
            </button>
          </div>
        </div>
      </div>

      <div className="modebar">
        <ModeSwitch mode={mode} onChange={(m) => updatePrefs({ mode: m })} />
        <label className="perm" title="Permission mode for running commands and editing files">
          <span>perms</span>
          <select value={state.permissionMode} onChange={(e) => setPermission(e.target.value as PermissionMode)}>
            <option value="ask">Ask</option>
            <option value="auto">Auto</option>
            <option value="bypass">Bypass</option>
          </select>
        </label>
      </div>

      {mode === "agent" ? (
        <AgentBody
          state={state}
          scrollRef={scrollRef}
          onScroll={onScroll}
          showTyping={showTyping}
          resolveApproval={resolveApproval}
        />
      ) : mode === "refactor" ? (
        <RefactorPanel
          state={refactor}
          autoRun={prefs.autoRunRefactor}
          onAnalyze={() => send({ type: "analyzeRefactor" })}
          onPlan={(id, improvements) => send({ type: "planRefactor", id, improvements })}
          onApply={applyRefactor}
        />
      ) : (
        <ExplainPanel state={explain} />
      )}

      {mode === "agent" && (
        <div className="composer-wrap">
          {ctxChip && (
            <div className="ctx-chip" title="This is sent to the agent with your next message">
              <IconFile size={13} />
              <span className="ctx-label">{ctxLabel}</span>
              <span className="ctx-kind">{editorContext?.selection ? "selection" : "open file"}</span>
              <button className="ctx-remove" title="Don't include editor context" onClick={() => setIncludeContext(false)}>
                ×
              </button>
            </div>
          )}
          <div className="composer-stack">
            {menuOpen && (
              <CommandMenu commands={commands} active={cmdIndex} onPick={runCommand} onHover={setCmdIndex} />
            )}
            <div className="composer">
              <textarea
                ref={textareaRef}
                value={draft}
                placeholder="Message Code-AI…  (type / for commands)"
                rows={1}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setMenuDismissed(false);
                }}
                onKeyDown={onComposerKey}
              />
              {busy ? (
                <button className="send-btn stop" title="Cancel" onClick={() => send({ type: "cancel" })}>
                  <IconStop />
                </button>
              ) : (
                <button className="send-btn" title="Send" disabled={!draft.trim()} onClick={submit}>
                  <IconSend size={15} />
                </button>
              )}
            </div>
          </div>
          <div className="hint">Enter to send · Shift+Enter for newline · / for commands</div>
        </div>
      )}
    </div>
  );
}

function AgentBody({
  state,
  scrollRef,
  onScroll,
  showTyping,
  resolveApproval,
}: {
  state: ViewState;
  scrollRef: React.RefObject<HTMLDivElement>;
  onScroll: () => void;
  showTyping: boolean;
  resolveApproval: (callId: string, scope: ApprovalScope, reason?: string) => void;
}): JSX.Element {
  return (
    <>
      <div className="transcript" ref={scrollRef} onScroll={onScroll}>
        <div className="transcript-inner">
          {state.items.length === 0 && !state.pendingApproval ? (
            <div className="empty">
              <div className="spark">
                <IconCI size={30} />
              </div>
              <h2>Code-AI</h2>
              <div>Ask anything about this workspace. Tool runs and approvals show up here.</div>
            </div>
          ) : (
            state.items.map((item) => <ItemView key={item.id} item={item} />)
          )}
          {showTyping && <TypingIndicator status={state.status} heartbeat={state.heartbeat} />}
        </div>
      </div>
      {state.pendingApproval && <ApprovalModal approval={state.pendingApproval} onResolve={resolveApproval} />}
    </>
  );
}

const styleTag = document.createElement("style");
styleTag.textContent = STYLE;
document.head.appendChild(styleTag);

createRoot(document.getElementById("root")!).render(<App />);
