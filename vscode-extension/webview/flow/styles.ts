// Stylesheet for the orchestrator editor. Leans on VSCode theme variables so it
// blends with whatever theme the user runs, light or dark.

export const FLOW_STYLE = `
:root {
  --accent: var(--vscode-textLink-foreground, #3794ff);
  --surface: var(--vscode-editorWidget-background, rgba(127,127,127,0.10));
  --surface-2: var(--vscode-editor-inactiveSelectionBackground, rgba(127,127,127,0.16));
  --border: var(--vscode-panel-border, rgba(127,127,127,0.28));
  --muted: var(--vscode-descriptionForeground, rgba(127,127,127,0.9));
  --danger: var(--vscode-errorForeground, #f14c4c);
  --warn: var(--vscode-editorWarning-foreground, #cca700);
  --ok: var(--vscode-testing-iconPassed, #3fb950);
  --radius: 8px;
  /* Exec pins and wires are "white" semantically; on a light theme white is
     invisible, so the flow colour follows the foreground instead. */
  --flow-exec: var(--vscode-editor-foreground, var(--vscode-foreground, #e6e6e6));
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
}
button:hover:not(:disabled) { background: var(--surface-2); }
button:disabled { opacity: .45; cursor: default; }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.danger { border-color: var(--danger); color: var(--danger); }
input, select, textarea {
  font: inherit; color: var(--vscode-input-foreground, inherit);
  background: var(--vscode-input-background, var(--surface));
  border: 1px solid var(--vscode-input-border, var(--border));
  border-radius: 5px; padding: 4px 6px; width: 100%;
}
textarea { resize: vertical; min-height: 54px; }
input[type="checkbox"] { width: auto; }

.flow-app { display: flex; flex-direction: column; height: 100vh; }

/* ---- toolbar ---- */
.flow-toolbar {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 10px; border-bottom: 1px solid var(--border);
  background: var(--vscode-editor-background);
  flex: 0 0 auto; min-height: 40px;
}
.flow-toolbar .brand { display: flex; align-items: center; gap: 6px; font-weight: 600; }
.flow-toolbar .brand .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }
.flow-toolbar .brand .dot.running { animation: flow-pulse 1s ease-in-out infinite; }
.flow-toolbar .name { color: var(--muted); font-weight: 400; }
.flow-toolbar .spacer { flex: 1 1 auto; }
.flow-play {
  display: flex; align-items: center; gap: 6px;
  position: absolute; left: 50%; transform: translateX(-50%);
  top: 4px; z-index: 5;
}
.flow-play button { padding: 5px 16px; font-weight: 600; }
.flow-play .run-status { color: var(--muted); font-size: 11px; }
@keyframes flow-pulse { 0%,100% { opacity: 1 } 50% { opacity: .35 } }

.flow-body { display: flex; flex: 1 1 auto; min-height: 0; }
.flow-main { display: flex; flex-direction: column; flex: 1 1 auto; min-width: 0; }

/* ---- right-click menu ---- */
.flow-menu {
  position: fixed; z-index: 30;
  display: flex; flex-direction: column;
  max-height: 420px;
  background: var(--vscode-editorWidget-background, #252526);
  border: 1px solid var(--border); border-radius: var(--radius);
  box-shadow: 0 10px 30px rgba(0,0,0,.45);
  overflow: hidden;
}
.flow-menu-title {
  padding: 7px 10px 5px; font-size: 10px; letter-spacing: .06em;
  text-transform: uppercase; color: var(--muted);
  border-bottom: 1px solid var(--border);
}
.flow-menu-search { margin: 7px 8px; width: calc(100% - 16px); }
.flow-menu-list { overflow-y: auto; padding: 0 0 6px; }
.flow-menu-section {
  padding: 6px 10px 3px; font-size: 10px; letter-spacing: .06em;
  text-transform: uppercase; color: var(--muted);
}
.flow-menu-row {
  display: flex; align-items: center; gap: 7px; width: 100%;
  border: 0; border-radius: 0; background: transparent;
  padding: 5px 10px; text-align: left;
}
.flow-menu-row.active, .flow-menu-row:hover { background: var(--surface-2); }
.flow-menu-row .swatch { width: 8px; height: 8px; border-radius: 2px; flex: 0 0 auto; }
.flow-menu-row .label { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.flow-menu-row .hint { color: var(--muted); font-size: 10.5px; flex: 0 0 auto; }
.flow-menu-empty { padding: 10px; color: var(--muted); }

/* ---- canvas ---- */
.flow-canvas-wrap { position: relative; flex: 1 1 auto; min-height: 0; overflow: hidden; }
.flow-canvas { width: 100%; height: 100%; display: block; cursor: default; }
.flow-canvas.panning { cursor: grabbing; }
.flow-node-box {
  fill: var(--vscode-editorWidget-background, #252526);
  stroke: var(--border); stroke-width: 1;
}
.flow-node.selected .flow-node-box { stroke: var(--accent); stroke-width: 2; }
.flow-node.error .flow-node-box { stroke: var(--danger); stroke-width: 2; }
.flow-node.running .flow-node-box { stroke: var(--accent); stroke-width: 2.5; }
.flow-node.done .flow-node-box { stroke: var(--ok); }
.flow-node.failed .flow-node-box { stroke: var(--danger); stroke-width: 2.5; }
.flow-node-title { font-size: 11.5px; font-weight: 600; fill: #fff; }
.flow-node-summary { font-size: 10.5px; fill: var(--muted); }
.flow-pin-label { font-size: 10px; fill: var(--vscode-foreground); opacity: .85; }
.flow-pin { cursor: crosshair; }
.flow-pin:hover .flow-pin-hit { opacity: .25; }
.flow-wire { fill: none; stroke-width: 2; pointer-events: none; }
.flow-wire.exec { stroke: var(--flow-exec); }
.flow-pin-shape.exec { fill: none; stroke: var(--flow-exec); }
.flow-pin-shape.exec.filled { fill: var(--flow-exec); }
.flow-dot.exec { fill: var(--flow-exec); }
.flow-wire-hit { fill: none; stroke: transparent; stroke-width: 12; cursor: pointer; }
.flow-wire.selected { stroke-dasharray: 5 3; }
.flow-wire.traveled { stroke-width: 3; }
.flow-grid-dot { fill: var(--border); }
.flow-marquee { fill: var(--accent); fill-opacity: .12; stroke: var(--accent); stroke-dasharray: 4 3; }
.flow-badge { font-size: 9.5px; fill: #fff; font-weight: 700; }

.flow-empty {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  color: var(--muted); text-align: center; pointer-events: none; padding: 24px;
}

/* ---- question card ---- */
.flow-question {
  position: absolute; left: 50%; bottom: 16px; transform: translateX(-50%);
  width: min(520px, calc(100% - 32px));
  background: var(--surface); border: 1px solid var(--accent);
  border-radius: var(--radius); padding: 10px 12px;
  display: flex; flex-direction: column; gap: 7px;
  box-shadow: 0 6px 22px rgba(0,0,0,.35);
}
.flow-question .prompt { white-space: pre-wrap; }
.flow-question .row { display: flex; gap: 6px; }

/* ---- inspector ---- */
.flow-inspector {
  width: 300px; flex: 0 0 300px; border-left: 1px solid var(--border);
  display: flex; flex-direction: column; min-height: 0;
  background: var(--vscode-editor-background);
}
.flow-tabs { display: flex; border-bottom: 1px solid var(--border); }
.flow-tabs button {
  flex: 1 1 0; border: 0; border-radius: 0; background: transparent;
  padding: 7px 4px; color: var(--muted);
}
.flow-tabs button.active {
  color: var(--vscode-foreground); box-shadow: inset 0 -2px 0 var(--accent);
}
.flow-inspector-body { overflow-y: auto; padding: 10px; display: flex; flex-direction: column; gap: 10px; }
.flow-field { display: flex; flex-direction: column; gap: 3px; }
.flow-field > label { color: var(--muted); font-size: 11px; }
.flow-field .help { color: var(--muted); font-size: 10.5px; }
.flow-field .row { display: flex; gap: 5px; align-items: center; }
.flow-section-title {
  text-transform: uppercase; letter-spacing: .06em; font-size: 10px;
  color: var(--muted); display: flex; align-items: center; gap: 6px;
}
.flow-section-title .line { flex: 1 1 auto; height: 1px; background: var(--border); }
.flow-var-row {
  display: flex; align-items: center; gap: 5px;
  border: 1px solid var(--border); border-radius: 6px; padding: 6px;
}
.flow-var-row .swatch { width: 9px; height: 9px; border-radius: 2px; flex: 0 0 auto; }
.flow-var-row input.name { flex: 1 1 auto; }
.flow-var-row select { width: 78px; flex: 0 0 auto; }
.flow-hint { color: var(--muted); }
.flow-issue { display: flex; gap: 6px; align-items: flex-start; font-size: 11px; }
.flow-issue .marker { flex: 0 0 auto; margin-top: 3px; width: 6px; height: 6px; border-radius: 50%; }
.flow-issue.error .marker { background: var(--danger); }
.flow-issue.warning .marker { background: var(--warn); }

/* ---- log ---- */
.flow-log {
  flex: 0 0 auto; max-height: 34vh; display: flex; flex-direction: column;
  border-top: 1px solid var(--border); background: var(--surface);
}
.flow-log-head {
  display: flex; align-items: center; gap: 8px; padding: 4px 10px;
  border-bottom: 1px solid var(--border);
}
.flow-log-head .spacer { flex: 1 1 auto; }
.flow-log-rows {
  overflow-y: auto; padding: 6px 10px; font-family: var(--vscode-editor-font-family, monospace);
  font-size: 11px; display: flex; flex-direction: column; gap: 2px;
}
.flow-log-row { display: flex; gap: 8px; }
.flow-log-row .at { color: var(--muted); flex: 0 0 auto; }
.flow-log-row .who { color: var(--accent); flex: 0 0 auto; }
.flow-log-row .text { white-space: pre-wrap; word-break: break-word; }
.flow-log-row.error .text { color: var(--danger); }
.flow-log-row.warn .text { color: var(--warn); }
.flow-log-row.agent .text { color: var(--accent); }

/* ---- notice ---- */
.flow-notice {
  display: flex; align-items: center; gap: 8px; padding: 5px 10px;
  border-bottom: 1px solid var(--border);
}
.flow-notice.error { background: rgba(241,76,76,.14); }
.flow-notice.warn { background: rgba(204,167,0,.14); }
.flow-notice.info { background: var(--surface); }
.flow-notice .spacer { flex: 1 1 auto; }

/* Narrow windows: the inspector goes under the canvas instead of squeezing it. */
@media (max-width: 720px) {
  .flow-body { flex-direction: column; }
  .flow-inspector {
    width: auto; flex: 0 0 auto; max-height: 42vh;
    border-left: 0; border-top: 1px solid var(--border);
  }
  .flow-play { position: static; transform: none; }
  .flow-toolbar { flex-wrap: wrap; }
}
`;
