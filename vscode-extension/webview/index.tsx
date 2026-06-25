import * as React from "react";
import { createRoot } from "react-dom/client";

import type {
  ApprovalScope,
  EventEnvelope,
  HostToWebview,
  PermissionMode,
  WebviewToHost,
} from "../src/protocol";
import { ApprovalModal } from "./approval";
import { IconCI, IconSend, IconStop } from "./icons";
import { ItemView, TypingIndicator } from "./messages";
import { applyEvent, initialState, isBusy, ViewState } from "./reducer";
import { STYLE } from "./styles";

declare function acquireVsCodeApi(): { postMessage: (message: WebviewToHost) => void };
const vscode = acquireVsCodeApi();
const send = (message: WebviewToHost) => vscode.postMessage(message);

function reducer(state: ViewState, event: EventEnvelope): ViewState {
  return applyEvent(state, event);
}

function App(): JSX.Element {
  const [state, dispatch] = React.useReducer(reducer, initialState);
  const [draft, setDraft] = React.useState("");
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  const pinnedRef = React.useRef(true);

  React.useEffect(() => {
    const onMessage = (e: MessageEvent<HostToWebview>) => {
      if (e.data?.type === "event") dispatch(e.data.event as EventEnvelope);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // Autoscroll only when the user is already near the bottom.
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };
  React.useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  });

  // Auto-grow the composer.
  React.useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 180) + "px";
  }, [draft]);

  const busy = isBusy(state.status);
  const lastItem = state.items[state.items.length - 1];
  const showTyping = busy && lastItem?.kind !== "assistant";

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
    send({ type: "submit", text });
    setDraft("");
  };

  const resolveApproval = (callId: string, scope: ApprovalScope, reason?: string) =>
    send({ type: "resolveApproval", call_id: callId, scope, reason });

  const setPermission = (mode: PermissionMode) => send({ type: "setPermissionMode", mode });

  return (
    <div className="app">
      <div className="statusbar">
        <div className="brand">
          <span className={`dot ${busy ? "busy" : ""}`} />
          Code-AI
        </div>
        <div className="meta">
          {state.contextTokens && <span className="pill">{state.contextTokens} tok</span>}
          <label className="perm" title="Permission mode for running commands and editing files">
            <span>perms</span>
            <select
              value={state.permissionMode}
              onChange={(e) => setPermission(e.target.value as PermissionMode)}
            >
              <option value="ask">Ask every time</option>
              <option value="auto">Auto (read-only safe)</option>
              <option value="bypass">Bypass (allow all)</option>
            </select>
          </label>
        </div>
      </div>

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
          {showTyping && <TypingIndicator status={state.status} />}
        </div>
      </div>

      {state.pendingApproval && (
        <ApprovalModal approval={state.pendingApproval} onResolve={resolveApproval} />
      )}

      <div className="composer-wrap">
        <div className="composer">
          <textarea
            ref={textareaRef}
            value={draft}
            placeholder="Message Code-AI…"
            rows={1}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
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
        <div className="hint">Enter to send · Shift+Enter for newline</div>
      </div>
    </div>
  );
}

const styleTag = document.createElement("style");
styleTag.textContent = STYLE;
document.head.appendChild(styleTag);

createRoot(document.getElementById("root")!).render(<App />);
