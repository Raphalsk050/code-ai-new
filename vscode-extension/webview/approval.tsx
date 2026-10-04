import * as React from "react";

import { t, tf } from "./i18n";

import type { ApprovalScope } from "../src/protocol";
import { Codicon } from "./codicon";
import { countChanges, DiffView } from "./diff";
import { ripple } from "./motion";
import type { PendingApproval } from "./store/approvals";
import { LineDelta } from "./tool-row";
import { toolKind } from "./tool-target";

const KIND_ICON: Record<string, string> = {
  read: "file",
  write: "edit",
  run: "terminal",
  search: "search",
  web: "globe",
  agent: "organization",
  plan: "checklist",
  tool: "tools",
};

/** Lines of the change shown in the card before "open the diff". */
const PREVIEW_LINES = 8;

/** The tools the editor shows as a diff while they wait for approval. */
const EDITOR_DIFF_TOOLS = new Set(["write_file", "edit_code"]);

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
          {t("Allow")} <strong>{tool}</strong>?
        </>
      );
  }
}

/** The two sides of a code-mutating call; null for everything else. */
function changeOf(tool: string, args: Record<string, any>): { oldText: string; newText: string } | null {
  if (tool === "edit_code") {
    return {
      oldText: typeof args.old_text === "string" ? args.old_text : "",
      newText: String(args.new_text ?? ""),
    };
  }
  if (tool === "write_file") return { oldText: "", newText: String(args.content ?? "") };
  return null;
}

function Key({ k }: { k: string }): JSX.Element {
  return <kbd className="key">{k}</kbd>;
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
  /** Set when the change is reviewed in the editor's diff view instead of here. */
  onOpenDiff?: (path: string) => void;
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

  const path = typeof args.path === "string" ? args.path : "";
  const inEditor = !!props.onOpenDiff && !!path && EDITOR_DIFF_TOOLS.has(approval.tool_name);
  const change = changeOf(approval.tool_name, args);
  const delta = React.useMemo(() => (change ? countChanges(change.oldText, change.newText) : null), [change?.oldText, change?.newText]);
  const argSummary = change ? "" : summarizeArgs(args);

  return (
    <div className="approval-card" ref={cardRef} role="group" aria-label={t("Approval")}>
      <div className="approval-head">
        <Codicon name={KIND_ICON[toolKind(approval.tool_name)]} size={14} />
        <span className="approval-title">{headline(approval.tool_name)}</span>
        <span className="tool-badge badge-waiting">{t("waiting for you")}</span>
      </div>

      <div className="approval-body">
        {approval.reason && <div className="approval-reason">{approval.reason}</div>}
        {change && (
          <div className="diff-wrap">
            <div className="diff-path">
              <Codicon name="file" size={14} />
              <span className="diff-path-name" title={path}>
                {path || approval.tool_name}
              </span>
              {delta && <LineDelta added={delta.added} removed={delta.removed} />}
              {inEditor && (
                <button className="btn-ghost btn-sm" onPointerDown={ripple} onClick={() => props.onOpenDiff?.(path)}>
                  <Codicon name="git-compare" size={14} /> {t("Open diff")}
                </button>
              )}
            </div>
            <DiffView oldText={change.oldText} newText={change.newText} limit={inEditor ? PREVIEW_LINES : undefined} />
          </div>
        )}
        {argSummary && <pre className="approval-args">{argSummary}</pre>}

        {!denying ? (
          <div className="approval-actions">
            <button
              className="btn-primary"
              onPointerDown={ripple}
              onClick={() => onResolve(approval.call_id, "once")}
            >
              {t("Allow once")} <Key k="A" />
            </button>
            <button
              className="btn-ghost"
              onPointerDown={ripple}
              onClick={() => onResolve(approval.call_id, "session")}
            >
              {t("Allow for the session")} <Key k="S" />
            </button>
            <button className="btn-deny" onPointerDown={ripple} onClick={() => setDenying(true)}>
              {t("Deny…")} <Key k="D" />
            </button>
          </div>
        ) : (
          <div className="deny-form">
            <label>{t("Tell the agent why, or what to do instead (optional):")}</label>
            <textarea
              ref={feedbackRef}
              value={feedback}
              rows={3}
              placeholder={t("e.g. do not delete that file, update the config instead")}
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
                {t("Send and deny")} <Key k="Ctrl+Enter" />
              </button>
              <button className="btn-ghost" onClick={() => setDenying(false)}>
                {t("Back")}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
