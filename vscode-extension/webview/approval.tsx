import * as React from "react";

import { t, tf } from "./i18n";

import type { ApprovalScope } from "../src/protocol";
import { DiffView } from "./diff";
import { IconTool } from "./icons";
import { ripple } from "./motion";
import type { PendingApproval } from "./store/approvals";

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
      return t("Apply this edit?");
    case "write_file":
      return t("Write this file?");
    case "execute_command":
      return t("Run this command?");
    default:
      return (
        <>
          Permitir <strong>{tool}</strong>?
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
 * The decision the agent is blocked on, inline at the end of the transcript.
 *
 * It was a modal, and a modal is exactly wrong here: the sidebar is narrow, so
 * the overlay covered the diff, the command output and the plan - the context
 * the decision is made from. Inline, the answer sits where the question was
 * asked, and the transcript above it stays readable.
 */
export function ApprovalCard(props: {
  approval: PendingApproval;
  onResolve: (callId: string, scope: ApprovalScope, reason?: string) => void;
}): JSX.Element {
  const { approval, onResolve } = props;
  const [denying, setDenying] = React.useState(false);
  const [feedback, setFeedback] = React.useState("");
  const feedbackRef = React.useRef<HTMLTextAreaElement>(null);
  const cardRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (denying) feedbackRef.current?.focus();
  }, [denying]);

  // A decision that scrolled off screen is a stuck agent, so the card brings
  // itself into view when it opens.
  React.useEffect(() => {
    cardRef.current?.scrollIntoView({ block: "nearest" });
  }, [approval.call_id]);

  // A, S and D decide without leaving the keyboard, like the terminal's dialog.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (denying || e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "TEXTAREA" || target.tagName === "INPUT")) return;
      const key = e.key.toLowerCase();
      if (key === "a") onResolve(approval.call_id, "once");
      else if (key === "s") onResolve(approval.call_id, "session");
      else if (key === "d") setDenying(true);
      else return;
      e.preventDefault();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [approval.call_id, denying, onResolve]);

  const args = approval.arguments ?? {};
  const deny = () => onResolve(approval.call_id, "deny", feedback.trim());

  const diff = renderDiff(approval.tool_name, args);
  const argSummary = diff ? "" : summarizeArgs(args);
  const path = typeof args.path === "string" ? args.path : "";

  return (
    <div className="approval-card" ref={cardRef}>
      <div className="approval-head">
        <IconTool size={14} />
        <span>{headline(approval.tool_name)}</span>
        <span className="rail-section-note">{t("approval")}</span>
      </div>

      <div className="approval-body">
        {diff && (
          <div className="diff-wrap">
            {path && <div className="diff-path">{path}</div>}
            {diff}
          </div>
        )}
        {argSummary && <pre className="approval-args">{argSummary}</pre>}
        {approval.reason && <div className="approval-reason">{approval.reason}</div>}

        {!denying ? (
          <>
            <div className="approval-actions">
              <button
                className="btn-primary"
                onPointerDown={ripple}
                onClick={() => onResolve(approval.call_id, "once")}
              >
                Permitir uma vez
              </button>
              <button
                className="btn-ghost"
                onPointerDown={ripple}
                onClick={() => onResolve(approval.call_id, "session")}
              >
                {t("Allow for the session")}
              </button>
              <button className="btn-deny" onPointerDown={ripple} onClick={() => setDenying(true)}>
                Negar…
              </button>
            </div>
            <div className="deny-hint">
              {t("A allows · S allows for the session · D denies")}
            </div>
          </>
        ) : (
          <div className="deny-form">
            <label>{t("Tell the agent why, or what to do instead (optional):")}</label>
            <textarea
              ref={feedbackRef}
              value={feedback}
              rows={3}
              placeholder={t("e.g. do not delete that file — update the config instead")}
              onChange={(e) => setFeedback(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  deny();
                }
              }}
            />
            <div className="approval-actions">
              <button className="btn-deny" onPointerDown={ripple} onClick={deny}>
                {t("Send and deny")}
              </button>
              <button className="btn-ghost" onClick={() => setDenying(false)}>
                {t("Back")}
              </button>
            </div>
            <div className="deny-hint">Cmd/Ctrl+Enter envia</div>
          </div>
        )}
      </div>
    </div>
  );
}
