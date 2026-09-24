// What the rail sections and the turn-scoped cards look like: plan, agents,
// terminal, session, goal, live code, questions, usage meter, history summary.

export const PANELS_STYLE = `
:root {
  --ok: var(--vscode-testing-iconPassed, #89d185);
  --warn: var(--vscode-editorWarning-foreground, #cca700);
  --bad: var(--vscode-editorError-foreground, #f14c4c);
  --mono: var(--vscode-editor-font-family, ui-monospace, Consolas, monospace);
}

/* Labels that exist for screen readers only: an icon-only control still needs
   a name, and a name still needs to be text somewhere. */
.sr-only {
  position: absolute;
  width: 1px; height: 1px;
  padding: 0; margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}

/* ---- usage meter ---- */
.usagebar {
  display: flex; align-items: center; gap: 8px;
  padding: 5px 14px 7px;
  border-bottom: 1px solid var(--border);
  font-size: 11px; color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.usagebar .track {
  flex: 1; height: 4px; border-radius: 2px;
  background: var(--vscode-editorWidget-border, rgba(127,127,127,.25));
  overflow: hidden;
}
.usagebar .fill {
  height: 100%; width: 0;
  background: var(--accent); border-radius: 2px;
  transition: width var(--dur-slow) var(--ease-out), background-color var(--dur-base) var(--ease-out);
}
.usagebar .fill.warn { background: var(--warn); }
.usagebar .fill.over { background: var(--bad); }
.usagebar .compact-btn {
  background: transparent; border: 1px solid var(--border); border-radius: 5px;
  color: inherit; font-size: 10.5px; padding: 1px 7px; cursor: pointer;
}

/* ---- plan ---- */
.plan-step {
  display: flex; gap: 8px; align-items: flex-start;
  padding: 5px 7px; margin: 0 -7px;
  border-radius: 6px;
  line-height: 1.45;
}
.plan-step .mark { width: 12px; flex-shrink: 0; text-align: center; color: var(--muted); }
.plan-step.done .mark { color: var(--ok); }
.plan-step.failed .mark { color: var(--bad); }
.plan-step.running {
  background: var(--vscode-list-activeSelectionBackground, rgba(55,148,255,.16));
  box-shadow: inset 2px 0 0 var(--accent);
}
.plan-step.running .mark { color: var(--accent); }
.plan-step.done .title, .plan-step.pending .title { color: var(--muted); }
.plan-evidence { margin-top: 7px; font-size: 11px; color: var(--muted); font-family: var(--mono); }
.plan-reject {
  margin-top: 8px; padding: 7px 9px;
  border: 1px solid var(--warn); border-radius: 7px;
  background: var(--vscode-inputValidation-warningBackground, rgba(204,167,0,.14));
  color: var(--vscode-foreground); font-size: 11.5px; line-height: 1.45;
}
.panel-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 9px; }
.panel-btn {
  background: transparent; border: 1px solid var(--border); border-radius: 6px;
  color: var(--vscode-foreground); font-size: 11.5px; padding: 4px 10px; cursor: pointer;
}
.panel-btn:hover { background: var(--vscode-toolbar-hoverBackground, rgba(127,127,127,.16)); }
.panel-btn.danger { border-color: var(--bad); color: var(--bad); }
.panel-btn.primary { background: var(--vscode-button-background, var(--accent)); border-color: transparent; color: var(--vscode-button-foreground, #fff); }

/* ---- agents ---- */
.agent-card {
  border: 1px solid var(--border); border-radius: 8px;
  background: var(--surface);
  padding: 8px 9px; margin-bottom: 8px;
}
.agent-card.failed, .agent-card.rejected { border-color: var(--bad); }
.agent-head { display: flex; align-items: center; gap: 7px; }
.agent-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agent-state { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); flex-shrink: 0; }
.agent-card.running .agent-state { animation: pulse 1.2s ease-in-out infinite; }
.agent-card.completed .agent-state { background: var(--ok); }
.agent-card.failed .agent-state, .agent-card.rejected .agent-state { background: var(--bad); }
.agent-detail { color: var(--muted); font-size: 11px; margin-top: 3px; font-family: var(--mono); overflow-wrap: anywhere; }
.agent-task { color: var(--muted); font-size: 11px; margin-top: 3px; }

/* ---- terminal ---- */
.term-screen {
  font-family: var(--mono);
  font-size: 11.5px; line-height: 1.45;
  white-space: pre;
  overflow: auto;
  padding: 8px 9px;
  border: 1px solid var(--border); border-radius: 7px;
  background: var(--vscode-terminal-background, var(--vscode-editor-background));
  max-height: 260px;
}
.term-row { display: flex; gap: 6px; align-items: center; margin-top: 8px; }
.term-input {
  flex: 1; min-width: 0;
  font-family: var(--mono); font-size: 11.5px;
  color: var(--vscode-input-foreground, var(--vscode-foreground));
  background: var(--vscode-input-background, var(--surface));
  border: 1px solid var(--vscode-input-border, var(--border)); border-radius: 6px;
  padding: 4px 7px;
}
.term-input:focus { outline: none; border-color: var(--accent); }

/* ---- session ---- */
.kv { display: flex; gap: 8px; font-size: 11.5px; line-height: 1.7; }
.kv .k { width: 82px; flex-shrink: 0; color: var(--muted); }
.kv .v { font-family: var(--mono); overflow-wrap: anywhere; }
.kv .v.warn { color: var(--warn); }
.kv .v.ok { color: var(--ok); }
.mini-track { height: 4px; border-radius: 2px; background: var(--border); overflow: hidden; margin-top: 6px; }
.mini-fill { height: 100%; background: var(--accent); transition: width var(--dur-base) var(--ease-out); }

/* ---- live code ---- */
.livecode {
  margin: 0 14px 8px;
  border: 1px solid var(--accent); border-radius: 9px;
  background: var(--surface);
  overflow: hidden;
  animation: rise-in var(--dur-base) var(--ease-out) both;
}
.livecode-head {
  display: flex; align-items: center; gap: 7px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
  font-size: 11.5px;
}
.livecode-path { font-family: var(--mono); color: var(--muted); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; direction: rtl; text-align: left; }
.livecode-body {
  font-family: var(--mono); font-size: 11.5px; line-height: 1.5;
  white-space: pre; overflow: hidden;
  padding: 8px 10px;
  max-height: 168px;
  -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 16px);
  mask-image: linear-gradient(to bottom, transparent 0, #000 16px);
}
.livecode-reason { padding: 0 10px 8px; font-size: 11.5px; color: var(--muted); }

/* ---- approval ---- */
.approval-card {
  margin: 10px 14px;
  border: 1px solid var(--warn); border-radius: 10px;
  background: var(--surface);
  overflow: hidden;
  animation: rise-in var(--dur-slow) var(--ease-spring) both;
}
.approval-card .approval-head {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px;
  background: var(--vscode-inputValidation-warningBackground, rgba(204,167,0,.14));
  border-bottom: 1px solid var(--border);
  font-size: 12.5px;
}
.approval-card .approval-head .rail-section-note { margin-left: auto; }
.approval-body { padding: 11px 12px; }
.approval-card .approval-args {
  margin: 0 0 9px; padding: 8px 10px;
  background: var(--vscode-editor-background);
  border: 1px solid var(--border); border-radius: 7px;
  font-family: var(--mono); font-size: 11.5px; line-height: 1.5;
  white-space: pre-wrap; overflow-wrap: anywhere;
}
.approval-card .approval-reason { font-size: 12px; color: var(--muted); margin-bottom: 10px; }
.approval-card .approval-actions { display: flex; gap: 7px; flex-wrap: wrap; }
.approval-card .deny-hint { font-size: 11px; color: var(--muted); margin-top: 7px; }
.approval-card .deny-form label { display: block; font-size: 12px; margin-bottom: 6px; }
.approval-card .diff-wrap { margin-bottom: 10px; }

/* ---- questions ---- */
.qcard {
  margin: 10px 14px;
  border: 1px solid var(--accent); border-radius: 10px;
  background: var(--surface);
  overflow: hidden;
  animation: rise-in var(--dur-slow) var(--ease-spring) both;
}
.qcard-head {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px;
  background: var(--vscode-list-activeSelectionBackground, rgba(55,148,255,.18));
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}
.qcard-body { padding: 11px 12px; }
.qcard-prompt { font-size: 13px; line-height: 1.5; margin-bottom: 9px; }
.qcard-why { font-size: 11.5px; color: var(--muted); margin-bottom: 9px; }
.qopt {
  width: 100%;
  display: flex; gap: 9px; text-align: left;
  padding: 8px 10px; margin-bottom: 6px;
  border: 1px solid var(--border); border-radius: 8px;
  background: var(--vscode-editor-background);
  color: var(--vscode-foreground); cursor: pointer;
}
.qopt:hover { border-color: var(--accent); }
.qopt.picked { border-color: var(--accent); background: var(--vscode-list-activeSelectionBackground, rgba(55,148,255,.18)); }
.qopt-key { font-family: var(--mono); font-size: 11px; color: var(--accent); }
.qopt-label { font-size: 12.5px; }
.qopt-desc { font-size: 11.5px; color: var(--muted); margin-top: 2px; }
.qcard-foot { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
.qcard-foot .hint { flex: 1; font-size: 11px; color: var(--muted); }
.qcard textarea {
  width: 100%; box-sizing: border-box;
  font-family: inherit; font-size: 12.5px;
  color: var(--vscode-input-foreground, var(--vscode-foreground));
  background: var(--vscode-input-background, var(--vscode-editor-background));
  border: 1px solid var(--vscode-input-border, var(--border)); border-radius: 7px;
  padding: 7px 9px; resize: vertical;
}
.qcard textarea:focus { outline: none; border-color: var(--accent); }

/* ---- doctor: tool switches ---- */
.tool-switches { display: flex; flex-direction: column; gap: 7px; }
.tool-switch-all {
  margin-left: 8px; padding: 1px 8px; font-size: 11px;
  border: 1px solid var(--border); border-radius: 5px;
  background: transparent; color: var(--vscode-foreground); cursor: pointer;
}
.tool-switch-list {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 4px 10px;
  max-height: 220px; overflow-y: auto;
  padding: 8px 9px;
  border: 1px solid var(--border); border-radius: 7px;
  background: var(--vscode-editor-background);
}
.tool-switch { display: flex; align-items: center; gap: 7px; font-size: 12px; cursor: pointer; }
.tool-switch input { accent-color: var(--accent); }
.tool-switch-name { font-family: var(--mono); font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ---- settings: MCP servers ---- */
.mcp-server {
  display: flex; flex-direction: column; gap: 6px;
  padding: 9px 11px;
  border: 1px solid var(--border); border-radius: 8px;
  background: var(--vscode-editor-background);
}
.mcp-server[data-status="failed"], .mcp-server[data-status="invalid"] {
  border-color: var(--vscode-errorForeground, #f85149);
}
.mcp-server[data-status="needs_approval"], .mcp-server[data-status="needs_auth"] { border-color: var(--warn); }
.mcp-server-head { display: flex; align-items: center; gap: 8px; min-width: 0; }
.mcp-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: var(--muted); }
.mcp-dot-connected { background: var(--vscode-testing-iconPassed, #3fb950); }
.mcp-dot-connecting { background: var(--accent); animation: pulse 1.2s ease-in-out infinite; }
.mcp-dot-failed, .mcp-dot-invalid { background: var(--vscode-errorForeground, #f85149); }
.mcp-dot-needs_approval, .mcp-dot-needs_auth { background: var(--warn); }
.mcp-server-name {
  font-family: var(--mono); font-size: 12.5px; font-weight: 600;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.mcp-server-meta { font-size: 11.5px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mcp-server-actions { margin-left: auto; display: flex; align-items: center; gap: 6px; flex: none; }
.mcp-approve { font-size: 11.5px; padding: 3px 10px; }
.mcp-server-source { font-size: 11.5px; color: var(--muted); overflow-wrap: anywhere; }
.mcp-server-warn { font-size: 11.5px; color: var(--warn); overflow-wrap: anywhere; }
.mcp-server-error { font-size: 12px; color: var(--vscode-errorForeground, #f85149); overflow-wrap: anywhere; }
.mcp-details {
  margin: 0; padding: 7px 9px; max-height: 180px; overflow: auto;
  font-family: var(--mono); font-size: 11px; line-height: 1.45; white-space: pre-wrap;
  border: 1px solid var(--border); border-radius: 6px; background: var(--surface);
}
.mcp-tools summary { cursor: pointer; font-size: 11.5px; color: var(--muted); }
.mcp-tools .tool-switch-list { margin-top: 6px; }
.mcp-tag {
  font-size: 10px; padding: 0 5px; flex: none;
  border: 1px solid var(--border); border-radius: 4px; color: var(--muted);
}
.mcp-server-foot { display: flex; align-items: center; gap: 12px; }
.mcp-link {
  background: none; border: none; padding: 0; margin-left: 8px;
  font-size: 11.5px; color: var(--vscode-textLink-foreground, var(--accent)); cursor: pointer;
}
.mcp-server-foot .mcp-link, .mcp-confirm .mcp-link { margin-left: 0; }
.mcp-link.danger { color: var(--vscode-errorForeground, #f85149); }
.mcp-link:disabled { opacity: .5; cursor: default; }
.mcp-confirm { display: inline-flex; align-items: center; gap: 10px; font-size: 11.5px; }
.mcp-editor {
  display: flex; flex-direction: column; gap: 10px;
  padding: 11px 12px;
  border: 1px solid var(--accent); border-radius: 8px;
  background: var(--surface);
  animation: rise-in var(--dur-base) var(--ease-spring) both;
}
.mcp-editor textarea {
  font-family: var(--mono); font-size: 11.5px; line-height: 1.45;
  color: var(--vscode-input-foreground);
  background: var(--vscode-input-background); border: 1px solid var(--vscode-input-border, var(--border));
  border-radius: 7px; padding: 6px 9px; resize: vertical;
}
.mcp-editor textarea:focus { outline: none; border-color: var(--accent); }
.mcp-editor-actions, .mcp-add { display: flex; gap: 8px; flex-wrap: wrap; }
.mcp-trust { font-size: 12px; }
.mcp-alert {
  font-size: 12px; color: var(--vscode-errorForeground, #f85149);
  padding: 7px 9px; border: 1px solid var(--vscode-errorForeground, #f85149); border-radius: 7px;
}

/* ---- pasted images ---- */
.composer-wrap.dropping .composer {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--vscode-list-activeSelectionBackground, rgba(55,148,255,.18));
}
.paste-strip { display: flex; gap: 6px; flex-wrap: wrap; padding: 0 0 6px; }
.paste-chip { position: relative; animation: rise-in var(--dur-base) var(--ease-spring) both; }
.paste-chip img {
  width: 54px; height: 40px; object-fit: cover;
  border: 1px solid var(--border); border-radius: 6px; display: block;
}
.paste-chip button {
  position: absolute; top: -5px; right: -5px;
  width: 16px; height: 16px; line-height: 14px; padding: 0;
  border-radius: 50%; border: 1px solid var(--border);
  background: var(--vscode-editor-background); color: var(--vscode-foreground);
  font-size: 11px; cursor: pointer;
}
.mention-menu .cmd-name { font-family: var(--mono); }
.mention-menu .cmd-desc { direction: rtl; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.cmd-item.cmd-off { opacity: .55; }
.cmd-off-tag { font-style: italic; }

/* ---- history summary ---- */
.history-summary {
  font-size: 11.5px; line-height: 1.5; color: var(--muted);
  margin-top: 4px;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
}
.history-tags { display: flex; gap: 5px; flex-wrap: wrap; margin-top: 6px; }
.history-tag {
  font-family: var(--mono); font-size: 10.5px;
  padding: 1px 6px; border: 1px solid var(--border); border-radius: 5px;
  color: var(--muted);
}
`;
