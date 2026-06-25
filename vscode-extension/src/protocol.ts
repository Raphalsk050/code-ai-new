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
  | "cancel"
  | "compact"
  | "setPlannerMode"
  | "setPermissionMode"
  | "resolveApproval"
  | "answerQuestion"
  | "shutdown";

// Messages exchanged between the extension host and the webview.
export type HostToWebview = { type: "event"; event: EventEnvelope };

export type PermissionMode = "ask" | "auto" | "bypass";

export type WebviewToHost =
  | { type: "submit"; text: string }
  | { type: "cancel" }
  | { type: "resolveApproval"; call_id: string; scope: ApprovalScope; reason?: string }
  | { type: "setPermissionMode"; mode: PermissionMode };
