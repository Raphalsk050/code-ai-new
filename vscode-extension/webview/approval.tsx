import * as React from "react";

import type { ApprovalScope } from "../src/protocol";
import { DiffView } from "./diff";
import { IconTool } from "./icons";
import type { PendingApproval } from "./reducer";

function summarizeArgs(args: Record<string, any>): string {
  if (!args || typeof args !== "object") return "";
  if (typeof args.command === "string") return args.command;
  if (Array.isArray(args.argv)) return args.argv.join(" ");
  if (typeof args.path === "string") return args.path;
  const entries = Object.entries(args).slice(0, 4);
  return entries.map(([k, v]) => `${k}: ${truncate(String(v), 60)}`).join("\n");
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function headline(tool: string): React.ReactNode {
  switch (tool) {
    case "edit_code":
      return "Apply this code edit?";
    case "write_file":
      return "Write this file?";
    case "execute_command":
      return "Run this command?";
    default:
      return (
        <>
          Allow Code-AI to run <strong>{tool}</strong>?
        </>
      );
  }
}

/** Build a colored diff for code-mutating tools; null for everything else. */
function renderDiff(tool: string, args: Record<string, any>): JSX.Element | null {
  if (tool === "edit_code") {
    const newText = String(args.new_text ?? "");
    const oldText = typeof args.old_text === "string" ? args.old_text : "";
    return <DiffView oldText={oldText} newText={newText} />;
  }
  if (tool === "write_file") {
    return <DiffView oldText="" newText={String(args.content ?? "")} />;
  }
  return null;
}

/**
 * Modal popup shown when the agent needs permission to run a gated tool.
 * Allow once / for the session, or deny — and on deny, a text field lets the
 * developer tell the agent why or what to do instead. That text is sent as the
 * denial reason and reaches the model as the tool result.
 */
export function ApprovalModal(props: {
  approval: PendingApproval;
  onResolve: (callId: string, scope: ApprovalScope, reason?: string) => void;
}): JSX.Element {
  const { approval, onResolve } = props;
  const [denying, setDenying] = React.useState(false);
  const [feedback, setFeedback] = React.useState("");
  const feedbackRef = React.useRef<HTMLTextAreaElement>(null);

  React.useEffect(() => {
    if (denying) feedbackRef.current?.focus();
  }, [denying]);

  const args = approval.arguments ?? {};
  const deny = () => onResolve(approval.call_id, "deny", feedback.trim());

  const diff = renderDiff(approval.tool_name, args);
  const argSummary = diff ? "" : summarizeArgs(args);
  const path = typeof args.path === "string" ? args.path : "";

  return (
    <div className="modal-overlay">
      <div className="modal">
        <div className="approval-head">
          <IconTool size={15} />
          <span>{headline(approval.tool_name)}</span>
        </div>

        {diff && (
          <div className="diff-wrap">
            {path && <div className="diff-path">{path}</div>}
            {diff}
          </div>
        )}
        {argSummary && <pre className="approval-args">{argSummary}</pre>}
        {approval.reason && <div className="approval-reason">{approval.reason}</div>}

        {!denying ? (
          <div className="approval-actions">
            <button className="btn-primary" onClick={() => onResolve(approval.call_id, "once")}>
              Allow once
            </button>
            <button className="btn-ghost" onClick={() => onResolve(approval.call_id, "session")}>
              Allow for session
            </button>
            <button className="btn-deny" onClick={() => setDenying(true)}>
              Deny…
            </button>
          </div>
        ) : (
          <div className="deny-form">
            <label>Tell the agent why, or what to do instead (optional):</label>
            <textarea
              ref={feedbackRef}
              value={feedback}
              rows={3}
              placeholder="e.g. don't delete that file — update the config instead"
              onChange={(e) => setFeedback(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  deny();
                }
              }}
            />
            <div className="approval-actions">
              <button className="btn-deny" onClick={deny}>
                Send & deny
              </button>
              <button className="btn-ghost" onClick={() => setDenying(false)}>
                Back
              </button>
            </div>
            <div className="deny-hint">Cmd/Ctrl+Enter to send</div>
          </div>
        )}
      </div>
    </div>
  );
}
