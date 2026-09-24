// Wire contract shared by the extension host and the webview.
//
// The event vocabulary mirrors what the Python core emits and what
// `src/code_ai/ui/terminal/view_models.py` consumes. Event *names* are the
// versioned contract: keep this in sync with that reducer.

export interface EventEnvelope {
  event_id: string;
  event_type: string;
  event_version: number;
  session_id: string;
  sequence: number;
  timestamp: string;
  source: string;
  payload: Record<string, any>;
}

export type ApprovalScope = "once" | "session" | "deny";

/** An image pasted into the composer, as the clipboard hands it over. */
export interface PastedImage {
  /** Base64 payload, without the data: URL prefix. */
  data: string;
  media_type: string;
}

// JSON-RPC methods the bridge accepts (extension host -> bridge).
export type BridgeMethod =
  | "submitUserMessage"
  | "replan"
  | "startPlan"
  | "planSnapshot"
  | "goalDefine"
  | "goalStart"
  | "goalStop"
  | "goalStatus"
  | "terminalStart"
  | "terminalWrite"
  | "terminalControl"
  | "terminalKill"
  | "terminalStatus"
  | "indexRefresh"
  | "indexStatus"
  | "listSlashCommands"
  | "summarizeConversation"
  | "graphCatalog"
  | "graphValidate"
  | "graphRun"
  | "graphStop"
  | "graphAnswer"
  | "graphStatus"
  | "graphListModels"
  | "listAssets"
  | "runAsset"
  | "newConversation"
  | "getSettings"
  | "updateSettings"
  | "listModels"
  | "listConversations"
  | "loadConversation"
  | "deleteConversation"
  | "explainCode"
  | "inlineComplete"
  | "analyzeRefactor"
  | "planRefactor"
  | "cancel"
  | "compact"
  | "setPlannerMode"
  | "setPermissionMode"
  | "resolveApproval"
  | "answerQuestion"
  | "shutdown";

/** The three operating modes; only `agent` shows the chat composer. */
export type AppMode = "agent" | "refactor" | "explain";

/** One registered tool and whether the agent is currently built with it. */
export interface ToolSpec {
  name: string;
  description: string;
  enabled: boolean;
}

/** User-editable backend settings surfaced in the settings panel. */
export interface Settings {
  model: string;
  api_mode: string;
  base_url: string;
  api_key_set: boolean;
  language: string;
  permission_mode: string;
  reasoning_effort: string;
  followup_reasoning_effort: string;
  learn: boolean;
  inline_hints_enabled: boolean;
  inline_model: string;
  max_context_tokens: number;
  workspace: string;
  /** Doctor: tools switched off, sub-agent models, concurrency, sampling. */
  disabled_tools: string[];
  subagent_models: string[];
  max_concurrent_subagents: number;
  temperature: number | null;
  top_p: number | null;
  top_k: number | null;
  presence_penalty?: number | null;
  supported: {
    api_mode: string[];
    permission_mode: string[];
    reasoning_effort: string[];
    tools: ToolSpec[];
  };
}

/** One architectural improvement suggested in refactor mode. */
export interface RefactorImprovement {
  id: string;
  title: string;
  rationale: string;
  impact: string;
}

export interface UpdateSettingsResult {
  applied: string[];
  restart_required: string[];
  errors: Record<string, string>;
  settings: Settings;
}

/** A tool call as persisted in a saved conversation (round-trip shape). */
export interface StoredToolCall {
  id: string;
  name: string;
  arguments: Record<string, any>;
}

/** One message as persisted in a saved conversation. */
export interface StoredMessage {
  role: string;
  content: string;
  tool_call_id?: string;
  name?: string;
  tool_calls?: StoredToolCall[];
}

/** Metadata for a saved conversation, as listed by the bridge. */
export interface ServerConversation {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
  /** What the conversation did, written when it was last persisted. */
  summary?: string;
  /** Cumulative tokens spent on the thread. */
  total_tokens?: number;
}

/** A snapshot of what the user is looking at in the editor, attached to a turn. */
export interface EditorContext {
  /** Workspace-relative path of the active file, e.g. `src/app.ts`. */
  path: string;
  /** VSCode language id, e.g. `typescript`. */
  language: string;
  /** Selected text, if any. */
  selection?: string;
  /** 1-based inclusive line range of the selection. */
  startLine?: number;
  endLine?: number;
}

/** Where in the editor an explanation applies, mirrored into the side panel. */
export interface ExplainTarget {
  path: string;
  language: string;
  /** 1-based inclusive line range of the explained selection. */
  startLine?: number;
  endLine?: number;
}

// Messages exchanged between the extension host and the webview.
/** One entry of the backend's slash-command catalog. */
export interface SlashCommandSpec {
  command: string;
  description: string;
  /** Text to insert when the user completes it (may end in a space for args). */
  completion: string;
  /** False when this session's bridge has no method behind it yet. */
  available: boolean;
  /** A built-in command, or one of the user's own workflows/skills. */
  kind?: "command" | "workflow" | "skill";
}

export type HostToWebview =
  | { type: "event"; event: EventEnvelope }
  | { type: "slashCommands"; commands: SlashCommandSpec[] }
  | { type: "fileMatches"; query: string; paths: string[] }
  | { type: "commandResult"; ok: boolean; text: string }
  | { type: "editorContext"; context: EditorContext | null }
  | { type: "settings"; settings: Settings }
  | { type: "settingsUpdated"; result: UpdateSettingsResult }
  | { type: "modelsListed"; models: string[] }
  | { type: "modelsError"; message: string }
  | { type: "conversationsList"; conversations: ServerConversation[] }
  | { type: "conversationLoaded"; id: string; messages: StoredMessage[] }
  | { type: "refactorStatus"; status: "analyzing" | "idle" }
  | { type: "refactorResult"; improvements: RefactorImprovement[]; path: string; language: string }
  | { type: "refactorError"; message: string }
  | { type: "refactorPlanning"; id: string }
  | { type: "refactorPlanned"; id: string; markdown: string }
  | { type: "explainStatus"; status: "analyzing"; target: ExplainTarget }
  | { type: "explainResult"; markdown: string; target: ExplainTarget }
  | { type: "explainError"; message: string };

export type PermissionMode = "ask" | "auto" | "bypass";

export type TerminalRequest =
  | { action: "start"; command?: string }
  | { action: "write"; text: string }
  | { action: "ctrl"; key: string }
  | { action: "kill" }
  | { action: "status" };

export type WebviewToHost =
  | {
      type: "submit";
      text: string;
      includeContext?: boolean;
      images?: PastedImage[];
      /** Fold this into the running turn instead of giving it one of its own. */
      steer?: boolean;
    }
  | { type: "answerQuestions"; text: string }
  | { type: "setPlannerMode"; mode: string }
  | { type: "replan" }
  | { type: "startPlan" }
  | { type: "goal"; action: "define" | "start" | "stop" | "status"; objective?: string }
  | { type: "terminal"; request: TerminalRequest }
  | { type: "indexRefresh"; full: boolean; subtree?: string }
  | { type: "listSlashCommands" }
  | { type: "summarizeConversation"; id: string }
  | { type: "searchFiles"; query: string }
  | { type: "runAsset"; token: string; argument: string }
  | { type: "newConversation"; id: string }
  | { type: "listConversations" }
  | { type: "loadConversation"; id: string }
  | { type: "deleteConversation"; id: string }
  | { type: "getSettings" }
  | { type: "updateSettings"; updates: Record<string, unknown> }
  | { type: "listModels" }
  | { type: "setMode"; mode: AppMode; autoRunRefactor: boolean }
  | { type: "analyzeRefactor" }
  | { type: "planRefactor"; id: string; improvements: RefactorImprovement[] }
  | { type: "cancel" }
  | { type: "compact" }
  | { type: "resolveApproval"; call_id: string; scope: ApprovalScope; reason?: string }
  | { type: "setPermissionMode"; mode: PermissionMode }
  | { type: "restartBridge" };
