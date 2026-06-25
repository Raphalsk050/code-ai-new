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

// JSON-RPC methods the bridge accepts (extension host -> bridge).
export type BridgeMethod =
  | "submitUserMessage"
  | "newConversation"
  | "getSettings"
  | "updateSettings"
  | "explainCode"
  | "cancel"
  | "compact"
  | "setPlannerMode"
  | "setPermissionMode"
  | "resolveApproval"
  | "answerQuestion"
  | "shutdown";

/** The three operating modes; only `agent` shows the chat composer. */
export type AppMode = "agent" | "refactor" | "explain";

/** User-editable backend settings surfaced in the settings panel. */
export interface Settings {
  model: string;
  api_mode: string;
  base_url: string;
  api_key_set: boolean;
  language: string;
  permission_mode: string;
  reasoning_effort: string;
  learn: boolean;
  max_context_tokens: number;
  workspace: string;
  supported: {
    api_mode: string[];
    permission_mode: string[];
    reasoning_effort: string[];
  };
}

export interface UpdateSettingsResult {
  applied: string[];
  restart_required: string[];
  errors: Record<string, string>;
  settings: Settings;
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

// Messages exchanged between the extension host and the webview.
export type HostToWebview =
  | { type: "event"; event: EventEnvelope }
  | { type: "editorContext"; context: EditorContext | null }
  | { type: "settings"; settings: Settings }
  | { type: "settingsUpdated"; result: UpdateSettingsResult };

export type PermissionMode = "ask" | "auto" | "bypass";

export type WebviewToHost =
  | { type: "submit"; text: string; includeContext?: boolean }
  | { type: "newConversation" }
  | { type: "getSettings" }
  | { type: "updateSettings"; updates: Record<string, unknown> }
  | { type: "setMode"; mode: AppMode; autoRunRefactor: boolean }
  | { type: "cancel" }
  | { type: "compact" }
  | { type: "resolveApproval"; call_id: string; scope: ApprovalScope; reason?: string }
  | { type: "setPermissionMode"; mode: PermissionMode };
