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

@keyframes board-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
@keyframes board-spin { to { transform: rotate(360deg); } }
@keyframes board-rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@keyframes board-land {
  0% { border-color: var(--ok); background: color-mix(in srgb, var(--ok) 16%, var(--vscode-editor-background)); }
  100% { background: var(--vscode-editor-background); }
}
@keyframes board-pop { 0% { transform: scale(0); } 70% { transform: scale(1.3); } 100% { transform: scale(1); } }
@media (prefers-reduced-motion: reduce) {
  .card.landed, .card.landed .card-check, .board-notice { animation: none; }
}
`;
