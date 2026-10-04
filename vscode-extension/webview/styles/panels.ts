// What the rail sections and the turn-scoped cards look like: plan, agents,
// terminal, session, goal, live code, questions, usage meter, history summary.

export const PANELS_STYLE = `

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

/* ---- plan ---- */
.plan-steps { list-style: none; margin: 0; padding: 0; }
/* One height for every row, the mark centred on the text's line. */
.plan-step { display: flex; gap: var(--space-3); align-items: flex-start; min-height: 22px; line-height: 22px; }
.plan-step .mark { flex: none; width: 16px; height: 22px; display: inline-grid; place-items: center; color: var(--fg-muted); }
.plan-step .mark .spinner { width: 12px; height: 12px; border-width: 2px; }
.plan-step .title { min-width: 0; color: var(--fg); }
.plan-step.done .mark { color: var(--success); }
.plan-step.done .title { color: var(--fg-muted); }
.plan-step.running .mark { color: var(--accent); }
.plan-step.running .title { color: var(--fg-strong); font-weight: 600; }
.plan-step.failed .mark, .plan-step.failed .title { color: var(--danger); }
.plan-goal { margin-top: 8px; padding: 7px 9px; border: 1px solid var(--border); border-radius: var(--radius-sm); font-size: var(--fs-small); }
.plan-goal-line { margin-top: 3px; }
.plan-evidence { margin-top: 7px; font-size: var(--fs-label); color: var(--fg-muted); font-family: var(--font-mono); }
.plan-reject {
  margin-top: 8px; padding: 7px 9px;
  border: 1px solid var(--warn); border-radius: var(--radius-md);
  background: var(--warn-soft);
  color: var(--fg); font-size: var(--fs-small); line-height: 1.45;
}
.panel-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 9px; }
.panel-btn {
  background: transparent; border: 1px solid var(--border); border-radius: var(--radius-md);
  color: var(--fg); font-size: var(--fs-small); padding: 4px 10px; cursor: pointer;
}
.panel-btn:hover { background: var(--hover); }
.panel-btn.danger { border-color: var(--danger); color: var(--danger); }
.panel-btn.primary { background: var(--button); border-color: transparent; color: var(--button-fg); }

/* ---- agents ---- */
.agent-card {
  border: 1px solid var(--border); border-radius: var(--radius-lg);
  background: var(--surface);
  padding: 8px 9px; margin-bottom: 8px;
}
.agent-card.failed, .agent-card.rejected { border-color: var(--danger); }
.agent-head { display: flex; align-items: center; gap: 7px; }
.agent-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agent-state { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); flex-shrink: 0; }
.agent-card.running .agent-state { animation: pulse 1.2s ease-in-out infinite; }
.agent-card.completed .agent-state { background: var(--success); }
.agent-card.failed .agent-state, .agent-card.rejected .agent-state { background: var(--danger); }
.agent-detail { color: var(--fg-muted); font-size: var(--fs-label); margin-top: 3px; font-family: var(--font-mono); overflow-wrap: anywhere; }
.agent-task { color: var(--fg-muted); font-size: var(--fs-label); margin-top: 3px; }

/* ---- terminal: one shared session, cut into command blocks ---- */
.term { display: flex; flex-direction: column; gap: 6px; }
.term-bar { display: flex; align-items: center; gap: var(--space-2); min-width: 0; }
.term-dot { flex: none; width: 7px; height: 7px; border-radius: 50%; background: var(--success); }
.term-dot.running { background: var(--accent); animation: pulse 1.2s ease-in-out infinite; }
.term-dot.closed { background: var(--fg-muted); }
.term-where { flex: 1 1 auto; min-width: 0; display: flex; gap: 6px; font-family: var(--font-mono); font-size: var(--fs-label); color: var(--fg-muted); overflow: hidden; white-space: nowrap; }
.term-shell { flex: none; color: var(--fg); }
.term-cwd { min-width: 0; overflow: hidden; text-overflow: ellipsis; direction: rtl; text-align: left; }
.term-more { position: relative; flex: none; }
.term-more .icon-btn { width: 24px; height: 22px; }
.term-menu {
  position: absolute; right: 0; top: calc(100% + 4px); z-index: 20; min-width: 190px; padding: 4px;
  border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: var(--surface);
  box-shadow: 0 8px 24px var(--shadow);
}
.term-menu-item { display: block; width: 100%; padding: 5px 8px; border: none; border-radius: 4px; background: none; color: var(--fg); font: inherit; font-size: var(--fs-small); text-align: left; cursor: pointer; }
.term-menu-item:hover { background: var(--hover); }
.term-menu-item.danger { color: var(--danger); }
.term-menu-sep { height: 1px; margin: 4px 2px; background: var(--border); }
.term-body { max-height: 320px; overflow: auto; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--term-bg); }
.term-screen { font-family: var(--font-mono); font-size: var(--fs-small); line-height: 1.45; white-space: pre; padding: 8px 9px; }
.term-blocks { display: flex; flex-direction: column; }
.term-block { border-left: 2px solid transparent; }
.term-block + .term-block { border-top: 1px solid var(--border); }
.term-block.running { border-left-color: var(--accent); }
.term-block.failed { border-left-color: var(--danger); }
.term-block-head {
  display: flex; align-items: center; gap: var(--space-2); width: 100%; min-width: 0; padding: 5px 8px;
  border: none; background: none; color: var(--fg); font: inherit; text-align: left; cursor: pointer;
}
.term-block-head:hover { background: var(--hover); }
.term-author { flex: none; display: inline-grid; place-items: center; width: 16px; color: var(--fg-muted); }
.term-author.agent { color: var(--accent); }
.term-command { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--font-mono); font-size: var(--fs-small); color: var(--fg-strong); }
.term-meta { flex: none; display: inline-flex; align-items: center; gap: 6px; }
.term-meta .spinner { width: 11px; height: 11px; border-width: 2px; }
.term-ok { color: var(--success); }
.term-exit { font-family: var(--font-mono); font-size: var(--fs-label); color: var(--danger); }
.term-output { margin: 0; padding: 2px 10px 8px 32px; font-family: var(--font-mono); font-size: var(--fs-small); line-height: 1.45; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--fg); }
.term-cut { color: var(--fg-muted); font-style: italic; }
.ansi-red { color: var(--danger); }
.ansi-green { color: var(--success); }
.ansi-yellow { color: var(--warn); }
.ansi-blue, .ansi-cyan { color: var(--accent); }
.ansi-magenta { color: var(--vscode-terminal-ansiMagenta, #c678dd); }
.ansi-black, .ansi-white { color: var(--fg-muted); }
.ansi-bold { font-weight: 600; }
.ansi-dim { opacity: .7; }
.term-row { display: flex; gap: 6px; align-items: center; }
.term-prompt { font-family: var(--font-mono); color: var(--success); }
.term-input {
  flex: 1; min-width: 0; height: 26px; box-sizing: border-box; padding: 0 7px;
  font-family: var(--font-mono); font-size: var(--fs-small); color: var(--fg);
  background: var(--surface-input); border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
}
.term-input:focus { outline: none; border-color: var(--focus); }
.term-agent, .term-ended {
  display: flex; align-items: center; gap: var(--space-2); padding: 5px 6px 5px 9px; border-radius: var(--radius-sm);
  font-size: var(--fs-small);
}
.term-agent { background: var(--accent-soft); color: var(--fg); }
.term-agent > svg { color: var(--accent); flex: none; }
.term-agent > span, .term-ended > span { flex: 1; min-width: 0; }
.term-ended { background: var(--hover); color: var(--fg-muted); }
.term-foot { font-family: var(--font-mono); font-size: 10px; color: var(--fg-muted); }

/* ---- session ---- */
.kv { display: flex; gap: 8px; font-size: var(--fs-small); line-height: 1.7; }
.kv .k { width: 82px; flex-shrink: 0; color: var(--fg-muted); }
.kv .v { font-family: var(--font-mono); overflow-wrap: anywhere; }
.kv .v.one-line { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kv .v.warn { color: var(--warn); }
.kv .v.ok { color: var(--success); }
.mini-track { height: 4px; border-radius: 2px; background: var(--border); overflow: hidden; margin-top: 6px; }
.mini-fill { height: 100%; background: var(--accent); transition: width var(--dur-base) var(--ease-out); }

/* ---- live code ---- */
.livecode {
  margin: 0 14px 8px;
  border: 1px solid var(--accent); border-radius: var(--radius-lg);
  background: var(--surface);
  overflow: hidden;
  animation: rise-in var(--dur-base) var(--ease-out) both;
}
.livecode-head {
  display: flex; align-items: center; gap: 7px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
  font-size: var(--fs-small);
}
.livecode-path { font-family: var(--font-mono); color: var(--fg-muted); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; direction: rtl; text-align: left; }
.livecode-body {
  font-family: var(--font-mono); font-size: var(--fs-small); line-height: 1.5;
  white-space: pre; overflow: hidden;
  padding: 8px 10px;
  max-height: 168px;
  -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 16px);
  mask-image: linear-gradient(to bottom, transparent 0, #000 16px);
}
.livecode-reason { padding: 0 10px 8px; font-size: var(--fs-small); color: var(--fg-muted); }

/* ---- questions ---- */
.qcard {
  margin: 10px 14px;
  border: 1px solid var(--accent); border-radius: var(--radius-lg);
  background: var(--surface);
  overflow: hidden;
  animation: rise-in var(--dur-slow) var(--ease-spring) both;
}
.qcard-head {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px;
  background: var(--accent-soft);
  border-bottom: 1px solid var(--border);
  font-size: var(--fs-small);
}
.qcard-body { padding: 11px 12px; }
.qcard-prompt { font-size: var(--fs-body); line-height: 1.5; margin-bottom: 9px; }
.qcard-why { font-size: var(--fs-small); color: var(--fg-muted); margin-bottom: 9px; }
.qopt {
  width: 100%;
  display: flex; gap: 9px; text-align: left;
  padding: 8px 10px; margin-bottom: 6px;
  border: 1px solid var(--border); border-radius: var(--radius-lg);
  background: var(--bg);
  color: var(--fg); cursor: pointer;
}
.qopt:hover { border-color: var(--accent); }
.qopt.picked { border-color: var(--accent); background: var(--accent-soft); }
.qopt-key { font-family: var(--font-mono); font-size: var(--fs-label); color: var(--accent); }
.qopt-label { font-size: var(--fs-small); }
.qopt-desc { font-size: var(--fs-small); color: var(--fg-muted); margin-top: 2px; }
.qcard-foot { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
.qcard-foot .hint { flex: 1; font-size: var(--fs-label); color: var(--fg-muted); }
.qcard textarea {
  width: 100%; box-sizing: border-box;
  font-family: inherit; font-size: var(--fs-small);
  color: var(--fg);
  background: var(--surface-input);
  border: 1px solid var(--border-strong); border-radius: var(--radius-md);
  padding: 7px 9px; resize: vertical;
}
.qcard textarea:focus { outline: none; border-color: var(--accent); }

/* ---- questions from an MCP server ---- */
.elicit-field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 9px; font-size: var(--fs-small); }
.elicit-check { flex-direction: row; align-items: center; gap: 8px; flex-wrap: wrap; cursor: pointer; }
.elicit-check input { accent-color: var(--accent); }
.elicit-check .elicit-hint { flex-basis: 100%; }
.elicit-label { font-size: var(--fs-small); }
.elicit-required { color: var(--danger); }
.elicit-hint { font-size: var(--fs-label); color: var(--fg-muted); }
.elicit-field input:not([type="checkbox"]), .elicit-field select {
  font-family: inherit; font-size: var(--fs-small);
  color: var(--fg); background: var(--surface-input);
  border: 1px solid var(--border-strong); border-radius: var(--radius-md); padding: 6px 9px;
}
.elicit-field input:focus, .elicit-field select:focus { outline: none; border-color: var(--accent); }
.elicit-error { font-size: var(--fs-small); color: var(--danger); margin-bottom: 8px; }

/* ---- doctor: tool switches ---- */
.tool-switches { display: flex; flex-direction: column; gap: 7px; }
.tool-switch-all {
  margin-left: 8px; padding: 1px 8px; font-size: var(--fs-label);
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: transparent; color: var(--fg); cursor: pointer;
}
.tool-switch-list {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 4px 10px;
  max-height: 220px; overflow-y: auto;
  padding: 8px 9px;
  border: 1px solid var(--border); border-radius: var(--radius-md);
  background: var(--bg);
}
.tool-switch { display: flex; align-items: center; gap: 7px; font-size: var(--fs-small); cursor: pointer; }
.tool-switch input { accent-color: var(--accent); }
.tool-switch-name { font-family: var(--font-mono); font-size: var(--fs-small); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ---- settings: MCP servers ---- */
.mcp-server {
  display: flex; flex-direction: column; gap: 6px;
  padding: 9px 11px;
  border: 1px solid var(--border); border-radius: var(--radius-lg);
  background: var(--bg);
}
.mcp-server[data-status="failed"], .mcp-server[data-status="invalid"] {
  border-color: var(--danger);
}
.mcp-server[data-status="needs_approval"], .mcp-server[data-status="needs_auth"] { border-color: var(--warn); }
.mcp-server-head { display: flex; align-items: center; gap: 8px; min-width: 0; }
.mcp-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: var(--fg-muted); }
.mcp-dot-connected { background: var(--success); }
.mcp-dot-connecting { background: var(--accent); animation: pulse 1.2s ease-in-out infinite; }
.mcp-dot-failed, .mcp-dot-invalid { background: var(--danger); }
.mcp-dot-needs_approval, .mcp-dot-needs_auth { background: var(--warn); }
.mcp-server-name {
  font-family: var(--font-mono); font-size: var(--fs-small); font-weight: 600;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.mcp-server-meta { font-size: var(--fs-small); color: var(--fg-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mcp-server-actions { margin-left: auto; display: flex; align-items: center; gap: 6px; flex: none; }
.mcp-approve { font-size: var(--fs-small); padding: 3px 10px; }
.mcp-server-source { font-size: var(--fs-small); color: var(--fg-muted); overflow-wrap: anywhere; }
.mcp-server-warn { font-size: var(--fs-small); color: var(--warn); overflow-wrap: anywhere; }
.mcp-server-error { font-size: var(--fs-small); color: var(--danger); overflow-wrap: anywhere; }
.mcp-details {
  margin: 0; padding: 7px 9px; max-height: 180px; overflow: auto;
  font-family: var(--font-mono); font-size: var(--fs-label); line-height: 1.45; white-space: pre-wrap;
  border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface);
}
.mcp-tools summary { cursor: pointer; font-size: var(--fs-small); color: var(--fg-muted); }
.mcp-tools .tool-switch-list { margin-top: 6px; }
.mcp-tag {
  font-size: var(--fs-label); padding: 0 5px; flex: none;
  border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--fg-muted);
}
.mcp-server-foot { display: flex; align-items: center; gap: 12px; }
.mcp-link {
  background: none; border: none; padding: 0; margin-left: 8px;
  font-size: var(--fs-small); color: var(--accent); cursor: pointer;
}
.mcp-server-foot .mcp-link, .mcp-confirm .mcp-link { margin-left: 0; }
.mcp-link.danger { color: var(--danger); }
.mcp-link:disabled { opacity: .5; cursor: default; }
.mcp-confirm { display: inline-flex; align-items: center; gap: 10px; font-size: var(--fs-small); }
.mcp-editor {
  display: flex; flex-direction: column; gap: 10px;
  padding: 11px 12px;
  border: 1px solid var(--accent); border-radius: var(--radius-lg);
  background: var(--surface);
  animation: rise-in var(--dur-base) var(--ease-spring) both;
}
.mcp-editor textarea {
  font-family: var(--font-mono); font-size: var(--fs-small); line-height: 1.45;
  color: var(--fg);
  background: var(--surface-input); border: 1px solid var(--border-strong);
  border-radius: var(--radius-md); padding: 6px 9px; resize: vertical;
}
.mcp-editor textarea:focus { outline: none; border-color: var(--accent); }
.mcp-editor-actions, .mcp-add { display: flex; gap: 8px; flex-wrap: wrap; }
.mcp-trust { font-size: var(--fs-small); }
.mcp-alert {
  font-size: var(--fs-small); color: var(--danger);
  padding: 7px 9px; border: 1px solid var(--danger); border-radius: var(--radius-md);
}

/* ---- pasted images ---- */
.composer-wrap.dropping .composer {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.paste-strip { display: flex; gap: 6px; flex-wrap: wrap; padding: 0 0 6px; }
.paste-chip { position: relative; animation: rise-in var(--dur-base) var(--ease-spring) both; }
.paste-chip img {
  width: 54px; height: 40px; object-fit: cover;
  border: 1px solid var(--border); border-radius: var(--radius-md); display: block;
}
.paste-chip button {
  position: absolute; top: -5px; right: -5px;
  width: 16px; height: 16px; line-height: 14px; padding: 0;
  border-radius: 50%; border: 1px solid var(--border);
  background: var(--bg); color: var(--fg);
  font-size: var(--fs-label); cursor: pointer;
}
.mention-menu .cmd-name { font-family: var(--font-mono); }
.mention-menu .cmd-desc { direction: rtl; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.cmd-item.cmd-off { opacity: .55; }
.cmd-off-tag { font-style: italic; }

/* ---- history summary ---- */
.history-summary {
  font-size: var(--fs-small); line-height: 1.5; color: var(--fg-muted);
  margin-top: 4px;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
}
.history-tags { display: flex; gap: 5px; flex-wrap: wrap; margin-top: 6px; }
.history-tag {
  font-family: var(--font-mono); font-size: var(--fs-label);
  padding: 1px 6px; border: 1px solid var(--border); border-radius: var(--radius-sm);
  color: var(--fg-muted);
}
`;
