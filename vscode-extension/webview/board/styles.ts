// The board tab's stylesheet. Flat, like the graph editor: no gradients or
// glow, colour only where it says something (a status, a running agent).

export const BOARD_STYLE = `
:root {
  --accent: var(--vscode-textLink-foreground, #3794ff);
  --surface: var(--vscode-editorWidget-background, rgba(127,127,127,0.10));
  --surface-2: var(--vscode-editor-inactiveSelectionBackground, rgba(127,127,127,0.16));
  --lane-bg: color-mix(in srgb, var(--vscode-foreground, #ccc) 4%, transparent);
  --border: var(--vscode-panel-border, rgba(127,127,127,0.28));
  --muted: var(--vscode-descriptionForeground, rgba(127,127,127,0.9));
  --danger: var(--vscode-errorForeground, #f14c4c);
  --warn: var(--vscode-editorWarning-foreground, #cca700);
  --ok: var(--vscode-testing-iconPassed, #3fb950);
  --radius: 8px;
}
* { box-sizing: border-box; }
html, body, #root { height: 100%; margin: 0; }
body {
  font-family: var(--vscode-font-family);
  font-size: 12px;
  color: var(--vscode-foreground);
  background: var(--vscode-editor-background);
  overflow: hidden;
}
button {
  font: inherit; color: inherit; cursor: pointer;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 6px; padding: 4px 9px;
  display: inline-flex; align-items: center; gap: 6px;
}
button:hover:not(:disabled) { background: var(--surface-2); }
button:disabled { opacity: .45; cursor: default; }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.danger { border-color: var(--danger); color: var(--danger); }
button.ghost { background: transparent; border-color: transparent; }
button.ghost:hover:not(:disabled) { background: var(--surface-2); }
button.icon { padding: 3px 5px; }
input, textarea, select {
  font: inherit; color: var(--vscode-input-foreground, inherit);
  background: var(--vscode-input-background, var(--surface));
  border: 1px solid var(--vscode-input-border, var(--border));
  border-radius: 5px; padding: 4px 6px; width: 100%;
}
input:focus, textarea:focus, select:focus { outline: 1px solid var(--accent); outline-offset: -1px; }
textarea { resize: vertical; min-height: 60px; line-height: 1.45; }
input[type="checkbox"] { width: auto; margin: 0; accent-color: var(--accent); }
.codicon { font-size: 14px; }
.codicon-loading { animation: board-spin 1s linear infinite; }

.board-app { display: flex; flex-direction: column; height: 100vh; }

/* ---- toolbar ---- */
.board-toolbar {
  display: flex; align-items: center; gap: 8px; flex: 0 0 auto;
  padding: 6px 12px; min-height: 42px; border-bottom: 1px solid var(--border);
}
.board-toolbar .brand { display: flex; align-items: center; gap: 7px; font-weight: 600; }
.board-toolbar .brand .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }
.board-toolbar .brand .dot.live { animation: board-pulse 1s ease-in-out infinite; }
.board-toolbar .state { color: var(--muted); }
.board-toolbar .spacer { flex: 1 1 auto; }

/* ---- review banner ---- */
.board-review {
  display: flex; align-items: center; gap: 10px; flex: 0 0 auto;
  margin: 10px 12px 0; padding: 8px 12px; border-radius: var(--radius);
  border: 1px solid var(--accent); background: color-mix(in srgb, var(--accent) 10%, transparent);
}
.board-review .text { flex: 1 1 auto; line-height: 1.45; }
.board-review .text b { font-weight: 600; }
.board-review .agent-note { display: block; color: var(--muted); margin-top: 2px; }

/* ---- lanes ---- */
.board-body { flex: 1 1 auto; display: flex; min-height: 0; }
.board-lanes {
  flex: 1 1 auto; display: flex; gap: 10px; padding: 12px;
  overflow-x: auto; overflow-y: hidden; align-items: stretch;
}
.lane {
  flex: 1 0 230px; max-width: 340px; display: flex; flex-direction: column; min-height: 0;
  background: var(--lane-bg); border-radius: var(--radius); border: 1px solid transparent;
}
.lane.drop { border-color: var(--accent); }
.lane-head {
  display: flex; align-items: center; gap: 6px; padding: 8px 8px 6px 10px; flex: 0 0 auto;
}
.lane-title {
  font-size: 11px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: default;
}
.lane-count {
  color: var(--muted); font-size: 11px; min-width: 18px; text-align: center;
  border-radius: 9px; background: var(--surface-2); padding: 0 6px;
}
.lane-head .spacer { flex: 1 1 auto; }
.lane-head input { padding: 2px 5px; }
.lane-cards {
  flex: 1 1 auto; overflow-y: auto; padding: 2px 8px 8px;
  display: flex; flex-direction: column; gap: 8px; min-height: 40px;
}
.lane-add { margin: 0 8px 8px; justify-content: flex-start; color: var(--muted); }
.lane-add-form { margin: 0 8px 8px; display: flex; flex-direction: column; gap: 6px; }
.lane-add-form .row { display: flex; gap: 6px; }
.lane.new { flex-basis: 240px; background: transparent; border: 1px dashed var(--border); padding: 8px; gap: 6px; }
.lane-menu {
  position: absolute; z-index: 30; min-width: 170px; padding: 4px;
  background: var(--vscode-menu-background, var(--vscode-editorWidget-background));
  border: 1px solid var(--border); border-radius: 6px;
}
.lane-menu button { width: 100%; border: 0; background: transparent; justify-content: flex-start; }
.lane-menu button:hover:not(:disabled) { background: var(--surface-2); }

/* ---- cards ---- */
.card {
  position: relative; padding: 8px 10px 8px 12px; border-radius: 7px;
  background: var(--vscode-editor-background); border: 1px solid var(--border);
  cursor: pointer; user-select: none;
}
.card:hover { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); }
.card.selected { border-color: var(--accent); }
.card.dragging { opacity: .35; }
.card::before {
  content: ""; position: absolute; left: -1px; top: 8px; bottom: 8px; width: 3px; border-radius: 2px;
  background: transparent;
}
.card.s-running::before, .card.s-queued::before, .card.s-verifying::before, .card.s-planning::before {
  background: var(--accent); animation: board-pulse 1.2s ease-in-out infinite;
}
.card.s-failed { border-color: color-mix(in srgb, var(--danger) 60%, var(--border)); }
.card.s-failed::before { background: var(--danger); }
.card.s-done::before { background: var(--ok); }
.card-top { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.card-key { color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.card-title { line-height: 1.4; font-size: 12.5px; word-break: break-word; }
.card-meta { display: flex; align-items: center; gap: 10px; margin-top: 7px; color: var(--muted); font-size: 11px; }
.card-meta .item { display: inline-flex; align-items: center; gap: 3px; }
.card-meta .codicon { font-size: 12px; }
.card-meta .ok { color: var(--ok); }
.card-meta .bad { color: var(--danger); }
.card-bar { height: 3px; border-radius: 2px; background: var(--surface-2); margin-top: 7px; overflow: hidden; }
.card-bar > span { display: block; height: 100%; background: var(--accent); transition: width .4s ease; }
.card.s-done .card-bar > span { background: var(--ok); }
.card-activity {
  margin-top: 6px; color: var(--muted); font-size: 11px; font-family: var(--vscode-editor-font-family);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.card-after { color: var(--muted); font-size: 11px; margin-top: 4px; }
/* Doubled up: the codicon stylesheet's own display rule outranks one class. */
.card .card-check.codicon {
  position: absolute; right: 8px; top: 8px; color: var(--ok); display: none;
}
.card.s-done .card-check.codicon { display: inline-block; }

.pill {
  display: inline-flex; align-items: center; gap: 4px; font-size: 10.5px; line-height: 16px;
  padding: 0 7px; border-radius: 8px; background: var(--surface-2); color: var(--muted);
  white-space: nowrap;
}
.pill.running, .pill.queued, .pill.verifying, .pill.planning { background: color-mix(in srgb, var(--accent) 18%, transparent); color: var(--accent); }
.pill.ready { background: color-mix(in srgb, var(--warn) 16%, transparent); color: var(--warn); }
.pill.done { background: color-mix(in srgb, var(--ok) 16%, transparent); color: var(--ok); }
.pill.failed { background: color-mix(in srgb, var(--danger) 16%, transparent); color: var(--danger); }

/* the card in flight between lanes, and its landing */
.card-ghost { pointer-events: none; will-change: transform; border-color: var(--accent); }
.card.landed { animation: board-land .7s ease-out; }
.card.landed .card-check { animation: board-pop .5s cubic-bezier(.2,1.6,.4,1) both; }

/* ---- detail drawer ---- */
.detail {
  flex: 0 0 430px; max-width: 55vw; border-left: 1px solid var(--border);
  display: flex; flex-direction: column; min-height: 0; background: var(--vscode-editor-background);
}
.detail-head { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border); }
.detail-head .key { color: var(--muted); font-variant-numeric: tabular-nums; }
.detail-head .spacer { flex: 1 1 auto; }
.detail-body { flex: 1 1 auto; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 14px; }
.detail-title { font-size: 15px; font-weight: 600; }
.detail section { display: flex; flex-direction: column; gap: 6px; }
.detail h4 {
  margin: 0; font-size: 11px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase;
  color: var(--muted); display: flex; align-items: center; gap: 6px;
}
.detail h4 .count { font-weight: 400; letter-spacing: 0; text-transform: none; }
.detail .locked {
  display: flex; align-items: center; gap: 6px; color: var(--accent);
  padding: 6px 8px; border-radius: 6px; background: color-mix(in srgb, var(--accent) 10%, transparent);
}
.detail .error {
  white-space: pre-wrap; color: var(--danger); line-height: 1.45; padding: 6px 8px;
  border-radius: 6px; background: color-mix(in srgb, var(--danger) 8%, transparent);
}
.detail .report { white-space: pre-wrap; line-height: 1.45; color: var(--muted); }
.detail-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.row { display: flex; align-items: center; gap: 6px; }
.row .grow { flex: 1 1 auto; min-width: 0; }
.item-row { display: flex; align-items: center; gap: 6px; min-height: 24px; }
.item-row .text { flex: 1 1 auto; min-width: 0; line-height: 1.4; word-break: break-word; }
.item-row .text.done { color: var(--muted); text-decoration: line-through; }
.item-row .remove { visibility: hidden; }
.item-row:hover .remove { visibility: visible; }
.item-row .codicon.passed { color: var(--ok); }
.item-row .codicon.failed { color: var(--danger); }
.item-row .codicon.pending { color: var(--muted); }
.item-note { color: var(--muted); font-size: 11px; margin-left: 22px; }
.add-row { display: flex; gap: 6px; }
.add-row input { flex: 1 1 auto; }

.sub {
  border: 1px solid var(--border); border-radius: 7px; padding: 6px 8px;
  display: flex; flex-direction: column; gap: 6px;
}
.sub-head { display: flex; align-items: center; gap: 6px; }
.sub-head .id { color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.sub-head .title { flex: 1 1 auto; min-width: 0; cursor: pointer; line-height: 1.4; }
.sub-head .agent { color: var(--muted); font-size: 11px; }
.sub .codicon.st-done { color: var(--ok); }
.sub .codicon.st-failed, .sub .codicon.st-blocked { color: var(--danger); }
.sub .codicon.st-running { color: var(--accent); }
.sub-body { display: flex; flex-direction: column; gap: 8px; padding: 2px 0 2px 22px; }
.deps { display: flex; flex-wrap: wrap; gap: 6px; }
.deps label { display: inline-flex; align-items: center; gap: 4px; color: var(--muted); }

.confirm { color: var(--danger); }

/* ---- notices ---- */
.board-notices { position: fixed; right: 14px; bottom: 14px; display: flex; flex-direction: column; gap: 6px; z-index: 60; max-width: 420px; }
.board-notice {
  padding: 8px 12px; border-radius: 7px; border: 1px solid var(--border);
  background: var(--vscode-editorWidget-background, var(--surface)); line-height: 1.45;
  animation: board-rise .25s ease-out;
}
.board-notice.warn { border-color: var(--warn); }
.board-notice.error { border-color: var(--danger); }

.board-empty { color: var(--muted); text-align: center; padding: 18px 8px; line-height: 1.5; }

/* ---- a task with its sub-cards ---- */
.card-group { display: flex; flex-direction: column; gap: 6px; }
.subcards {
  display: flex; flex-direction: column; gap: 6px;
  margin-left: 10px; padding-left: 10px; border-left: 2px solid var(--border);
}
.card-group.stubbed .subcards { margin-left: 6px; }
.card-stub {
  display: flex; align-items: center; gap: 6px; padding: 3px 6px; border-radius: 6px;
  color: var(--muted); font-size: 11px; cursor: pointer; min-width: 0;
}
.card-stub:hover { background: var(--surface-2); }
.card-stub .stub-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-stub .codicon { font-size: 12px; }
.subcard {
  position: relative; padding: 6px 8px; border-radius: 6px; cursor: pointer;
  background: var(--vscode-editor-background); border: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 4px; user-select: none;
}
.subcard:hover { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); }
.subcard.st-running { border-color: var(--accent); }
.subcard.st-failed, .subcard.st-blocked { border-color: color-mix(in srgb, var(--danger) 60%, var(--border)); }
.subcard.st-done { opacity: .8; }
.subcard.landed { animation: board-land .7s ease-out; }
.sub-top { display: flex; align-items: center; gap: 6px; min-width: 0; }
.sub-top .codicon { font-size: 13px; flex: 0 0 auto; }
.sub-key { color: var(--muted); font-size: 10.5px; font-variant-numeric: tabular-nums; flex: 0 0 auto; }
.sub-title { font-size: 12px; line-height: 1.35; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.codicon.st-done { color: var(--ok); }
.codicon.st-running { color: var(--accent); animation: board-spin 1s linear infinite; }
.codicon.st-failed, .codicon.st-blocked { color: var(--danger); }
.sub-live { display: flex; flex-direction: column; gap: 3px; padding-top: 2px; }
.sub-live .row { display: flex; align-items: center; gap: 6px; }
.sub-live .grow { flex: 1 1 auto; }
.sub-live.inline { padding: 0 0 0 22px; }
.sub-meta { display: flex; align-items: center; gap: 6px; }
.sub-error { color: var(--danger); font-size: 11px; line-height: 1.35; }
.sub-after { color: var(--muted); font-size: 10.5px; }
.clock, .steps { color: var(--muted); font-size: 10.5px; font-variant-numeric: tabular-nums; }
.live-line { display: flex; align-items: center; gap: 5px; min-width: 0; font-size: 11.5px; }
.live-line .codicon { font-size: 12px; color: var(--accent); flex: 0 0 auto; }
.live-line .verb { flex: 0 0 auto; }
.live-line .target {
  font-family: var(--vscode-editor-font-family); font-size: 11px; color: var(--muted);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0;
}
.live-line.bad .codicon { color: var(--danger); }
.said {
  font-size: 11px; color: var(--muted); font-style: italic; line-height: 1.35;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.agent-chip {
  --agent-hue: 210;
  display: inline-flex; align-items: center; gap: 4px; flex: 0 0 auto;
  font-size: 10.5px; line-height: 16px; padding: 0 6px; border-radius: 8px;
  color: hsl(var(--agent-hue) 70% 70%); background: hsl(var(--agent-hue) 60% 50% / .14);
}
body.vscode-light .agent-chip { color: hsl(var(--agent-hue) 60% 35%); }
.agent-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.agent-chip.live .agent-dot { animation: board-pulse 1s ease-in-out infinite; }
.card .card-meta .working { color: var(--accent); }
.lane-count.subs { display: inline-flex; align-items: center; gap: 3px; }
.lane-count.subs .codicon { font-size: 11px; }
.board-toolbar button.on { border-color: var(--accent); color: var(--accent); }

/* ---- activity feed ---- */
.activity {
  flex: 0 0 210px; display: flex; flex-direction: column; min-height: 0;
  border-top: 1px solid var(--border);
}
.activity-head { display: flex; align-items: center; gap: 6px; padding: 4px 12px; }
.activity-head .title { font-weight: 600; font-size: 11px; letter-spacing: .04em; text-transform: uppercase; }
.activity-head .count { color: var(--muted); font-size: 11px; }
.activity-head .spacer { flex: 1 1 auto; }
.activity-list { flex: 1 1 auto; overflow-y: auto; padding: 0 12px 8px; font-size: 11.5px; }
.activity-row {
  display: flex; align-items: center; gap: 8px; padding: 2px 4px; border-radius: 4px;
  cursor: pointer; min-width: 0;
}
.activity-row:hover { background: var(--surface-2); }
.activity-row .when { color: var(--muted); font-variant-numeric: tabular-nums; flex: 0 0 auto; }
.activity-row .where { color: var(--muted); flex: 0 0 auto; font-variant-numeric: tabular-nums; min-width: 34px; }
.activity-row .what { display: inline-flex; align-items: center; gap: 5px; min-width: 0; overflow: hidden; }
.activity-row .what code {
  font-family: var(--vscode-editor-font-family); font-size: 11px; color: var(--muted);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.activity-row .what .codicon { font-size: 12px; }
.activity-row .what .state.ok { color: var(--ok); }
.activity-row .what .state.bad { color: var(--danger); }
.activity-row .what.said { font-style: italic; color: var(--muted); white-space: nowrap; text-overflow: ellipsis; display: block; }
.activity-row .what.phase { color: var(--accent); }

/* ---- attachments ---- */
.attachments { outline: none; border-radius: 7px; }
.attachments.over { outline: 1px dashed var(--accent); outline-offset: 4px; }
.attachment .text { justify-content: flex-start; padding: 0; text-align: left; }
.attachment .size { color: var(--muted); font-size: 11px; }
button.link { color: var(--accent); }
button.link:hover:not(:disabled) { background: transparent; text-decoration: underline; }
.attachments .hint { color: var(--muted); font-size: 11px; }

@keyframes board-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
@keyframes board-spin { to { transform: rotate(360deg); } }
@keyframes board-rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@keyframes board-land {
  0% { border-color: var(--ok); background: color-mix(in srgb, var(--ok) 16%, var(--vscode-editor-background)); }
  100% { background: var(--vscode-editor-background); }
}
@keyframes board-pop { 0% { transform: scale(0); } 70% { transform: scale(1.3); } 100% { transform: scale(1); } }
@media (prefers-reduced-motion: reduce) {
  .card.landed, .card.landed .card-check, .board-notice, .subcard.landed,
  .codicon.st-running, .agent-chip.live .agent-dot { animation: none; }
}
`;
