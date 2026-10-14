// Wire contract shared by the extension host and the webview.
//
// The event vocabulary mirrors what the Python core emits and what
// `src/code_ai/ui/terminal/view_models.py` consumes. Event *names* are the
// versioned contract: keep this in sync with that reducer.

import type { ChangeSummary } from "./change-ledger";

export type { ChangeSummary };

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
  | "listRules"
  | "listAgents"
  | "runAsset"
  | "newConversation"
  | "getSettings"
  | "updateSettings"
  | "listModels"
  | "listConversations"
  | "loadConversation"
  | "deleteConversation"
  | "deleteMessage"
  | "answerNow"
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
  | "mcpList"
  | "mcpReconnect"
  | "mcpApprove"
  | "mcpSetEnabled"
  | "mcpSave"
  | "mcpRemove"
  | "mcpElicitationAnswer"
  | "mcpSignIn"
  | "mcpSignOut"
  | "checkForUpdate"
  | "signInForUpdates"
  | "downloadUpdate"
  | "getProfile"
  | "signInForPicture"
  | "shutdown";

/** Bridge methods whose whole answer is a listing to show the user. */
export type ReportMethod = "listRules" | "listAgents";

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
  /** The workspace's git branch; "" outside a repository. */
  branch?: string;
  /** The backend's own version, "X.Y.Z-W"; a backend older than the panel says nothing. */
  version?: string;
  /** Doctor: tools switched off, sub-agent models, concurrency, sampling. */
  disabled_tools: string[];
  subagent_models: string[];
  max_concurrent_subagents: number;
  temperature: number | null;
  top_p: number | null;
  top_k: number | null;
  presence_penalty?: number | null;
  /** MCP servers on at all; each server has its own switch in the panel. */
  mcp_enabled?: boolean;
  /** Keys and tokens from the user's environment reach the agent's commands. */
  commands_see_secrets?: boolean;
  supported: {
    api_mode: string[];
    permission_mode: string[];
    reasoning_effort: string[];
    tools: ToolSpec[];
  };
}

/** The extension's own settings, from VS Code's settings rather than the backend. */
export interface EditorSettings {
  /** `code-ai.diff.openInEditor` */
  diffInEditor: boolean;
  /** `code-ai.command`: empty finds the backend by itself. */
  command: string;
  /** `code-ai.args` */
  args: string[];
}

/** An MCP server's declaration, as the editor edits it. */
export interface McpServerSpec {
  name: string;
  transport: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  cwd: string;
  url: string;
  headers: Record<string, string>;
  /** client_id, client_secret, scopes, for a server that does not register clients. */
  oauth?: Record<string, string>;
  enabled: boolean;
  trust: boolean;
  defer: string;
  timeout_s: number;
  tool_timeout_s: number;
  /** "config" for the user's own; otherwise the agent whose file declared it. */
  source: string;
  source_path: string;
  problem: string;
}

export interface McpToolInfo {
  /** The name the model sees: mcp__<server>__<tool>. */
  name: string;
  remote: string;
  title: string;
  description: string;
  read_only: boolean;
  enabled: boolean;
}

export interface McpPromptInfo {
  name: string;
  description: string;
  arguments: { name: string; required: boolean }[];
}

export type McpStatus =
  | "connected"
  | "connecting"
  | "failed"
  | "disabled"
  | "needs_approval"
  | "needs_auth"
  | "invalid";

/** One MCP server of the session, as `mcpList` and `mcp.server.status` describe it. */
export interface McpServerInfo {
  name: string;
  status: McpStatus;
  error: string;
  transport: string;
  source: string;
  source_path: string;
  /** source_path relative to the workspace, for display. */
  source_label?: string;
  /** Declared in another agent's user config, not in the project. */
  user_level?: boolean;
  deferred: boolean;
  group: string;
  server: { name: string; title: string; version: string; protocol: string; instructions: string } | null;
  tools: McpToolInfo[];
  prompts: McpPromptInfo[];
  resources: boolean;
  /** Reached over HTTP, so it may need a sign-in. */
  remote?: boolean;
  /** A token is stored for it. */
  signed_in?: boolean;
  missing_env: string[];
  logs: string[];
  diagnostics: string;
  spec: McpServerSpec;
}

export interface McpList {
  enabled: boolean;
  servers: McpServerInfo[];
}

export type McpAction =
  | { action: "list" }
  | { action: "reconnect"; name: string }
  | { action: "approve"; name: string }
  | { action: "setEnabled"; name: string; enabled: boolean }
  | { action: "save"; server: Record<string, unknown>; previous?: string }
  | { action: "remove"; name: string }
  | { action: "signIn"; name: string }
  | { action: "signOut"; name: string };

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
  /** Workspace-relative path of the active file, e.g. `src/app.ts`; "" when no file has the focus. */
  path: string;
  /** VSCode language id, e.g. `typescript`. */
  language: string;
  /** Selected text, if any. */
  selection?: string;
  /** 1-based inclusive line range of the selection. */
  startLine?: number;
  endLine?: number;
  /** The other files open in the editor, workspace-relative, in tab order. */
  openFiles?: string[];
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
  /** A built-in command, one of the user's own workflows/skills, or an MCP server's prompt. */
  kind?: "command" | "workflow" | "skill" | "mcp";
}

export type HostToWebview =
  | { type: "event"; event: EventEnvelope }
  | { type: "slashCommands"; commands: SlashCommandSpec[] }
  | { type: "fileMatches"; query: string; paths: string[] }
  | { type: "commandResult"; ok: boolean; text: string }
  /** A listing the user asked for (/rules, /agents): shown as a plain notice. */
  | { type: "report"; ok: boolean; text: string }
  | { type: "editorContext"; context: EditorContext | null }
  | { type: "settings"; settings: Settings }
  | { type: "settingsUpdated"; result: UpdateSettingsResult }
  | { type: "modelsListed"; models: string[] }
  | { type: "modelsError"; message: string }
  | { type: "mcpServers"; list: McpList }
  | { type: "mcpError"; message: string }
  | { type: "mcpElicitationResult"; id: string; ok: boolean; message: string }
  | { type: "conversationsList"; conversations: ServerConversation[] }
  /** A conversation's items as the host keeps them; null when it keeps none. */
  | { type: "transcript"; id: string; items: unknown[] | null }
  /** The host lost what came before the last save: the next one has to carry it all. */
  | { type: "transcriptResend"; id: string }
  | { type: "conversationLoaded"; id: string; messages: StoredMessage[] }
  | { type: "refactorStatus"; status: "analyzing" | "idle" }
  | { type: "refactorResult"; improvements: RefactorImprovement[]; path: string; language: string }
  | { type: "refactorError"; message: string }
  | { type: "refactorPlanning"; id: string }
  | { type: "refactorPlanned"; id: string; markdown: string }
  | { type: "explainStatus"; status: "analyzing"; target: ExplainTarget }
  | { type: "explainResult"; markdown: string; target: ExplainTarget }
  | { type: "explainError"; message: string }
  /** Whether file edits open in the editor's diff view (so the panel shows none). */
  | { type: "editorDiff"; enabled: boolean }
  | { type: "editorSettings"; settings: EditorSettings }
  /** What the agent changed in this conversation, file by file. */
  | { type: "sessionChanges"; files: ChangeSummary[] }
  /** A newer release is out (see src/code_ai/updates.py for where it is looked for). */
  | { type: "updateAvailable"; version: string; current: string; notes: string; url: string }
  /** The releases live on a host this machine has no login for yet. */
  | { type: "updateLogin"; host: string }
  | { type: "updateProgress"; state: UpdateState; message?: string }
  /** Signed in and nothing newer: the button goes away. */
  | { type: "updateCleared" }
  /** Who is signed in on the releases host; `avatar` is a data: URI, and
   * `problem` says why there is none. `signIn`: a browser sign-in can fetch it. */
  | {
      type: "profile";
      login: string;
      name: string;
      avatar: string;
      problem: string;
      signIn: boolean;
    };

export type UpdateState =
  | "available"
  | "downloading"
  | "installing"
  | "installed"
  | "failed"
  | "login"
  | "signingIn";

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
  | { type: "report"; method: ReportMethod }
  | { type: "newConversation"; id: string }
  | { type: "listConversations" }
  | { type: "loadConversation"; id: string }
  | { type: "deleteConversation"; id: string }
  /** What changed in a conversation: its items from `from` on, replacing what the host has there. */
  | { type: "saveTranscript"; id: string; from: number; items: unknown[] }
  | { type: "loadTranscript"; id: string }
  /** Conversations an older panel kept in its own state, handed over once. */
  | { type: "adoptTranscripts"; conversations: { id: string; items: unknown[] }[] }
  /** One of the user's messages, counted from the newest, with the answer(s) it got. */
  | { type: "deleteMessage"; text: string; fromEnd: number; count: number }
  /** A message waiting for a turn of its own is handed to the turn that is running. */
  | { type: "answerNow"; text: string }
  | { type: "getSettings" }
  | { type: "updateSettings"; updates: Record<string, unknown> }
  | { type: "listModels" }
  | ({ type: "mcp" } & McpAction)
  | {
      type: "mcpElicitation";
      id: string;
      action: "accept" | "decline" | "cancel";
      content?: Record<string, unknown>;
    }
  | { type: "setMode"; mode: AppMode; autoRunRefactor: boolean }
  | { type: "analyzeRefactor" }
  | { type: "planRefactor"; id: string; improvements: RefactorImprovement[] }
  | { type: "cancel" }
  | { type: "compact" }
  | { type: "resolveApproval"; call_id: string; scope: ApprovalScope; reason?: string }
  | { type: "setPermissionMode"; mode: PermissionMode }
  | { type: "restartBridge" }
  /** Show what the agent changed in a file, in the editor's diff view. */
  | { type: "openDiff"; path: string }
  /** A file from the Changes section: its change since the conversation began. */
  | { type: "openChange"; path: string }
  /** Put a file back as it was before the agent touched it (the host asks first). */
  | { type: "revertChange"; path: string }
  /** Every changed file in one review. */
  | { type: "reviewChanges" }
  /** Write the extension's own settings to the user's VS Code settings. */
  | { type: "updateEditorSettings"; updates: Partial<EditorSettings> }
  | { type: "installUpdate" }
  | { type: "signInForUpdates" }
  /** Open the browser window that fetches the GitHub picture after a sign-in. */
  | { type: "avatarSignIn" }
  | { type: "reloadWindow" };
