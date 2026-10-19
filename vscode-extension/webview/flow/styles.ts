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
  flex: 0 0 auto; min-height: 42px;
}
.flow-toolbar .brand { display: flex; align-items: center; gap: 6px; font-weight: 600; min-width: 0; }
.flow-toolbar .brand .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); flex: 0 0 auto; }
.flow-toolbar .brand .dot.running { animation: flow-pulse 1s ease-in-out infinite; }
.flow-toolbar .name {
  color: var(--muted); font-weight: 400;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.flow-toolbar .dirty { color: var(--accent); font-size: 16px; line-height: 1; }
.flow-toolbar .spacer { flex: 1 1 auto; }
.flow-toolbar button { display: inline-flex; align-items: center; gap: 6px; }
.flow-toolbar button.icon { padding: 5px 6px; }
.flow-toolbar button.icon.on { color: var(--accent); }
.flow-btn-group {
  display: inline-flex; align-items: center; flex: 0 0 auto;
  border: 1px solid var(--border); border-radius: 7px; overflow: hidden;
}
.flow-btn-group button { border: 0; border-radius: 0; background: transparent; }
.flow-btn-group button + button { border-left: 1px solid var(--border); }
.flow-btn-group button:hover:not(:disabled) { background: var(--surface-2); }
.flow-zoom {
  min-width: 52px; justify-content: center; font-variant-numeric: tabular-nums;
  font-size: 11px; color: var(--muted);
}
.flow-play {
  display: flex; align-items: center; gap: 8px;
  position: absolute; left: 50%; transform: translateX(-50%);
  top: 5px; z-index: 5;
}
.flow-play button.flow-run { padding: 5px 16px; font-weight: 600; border-radius: 7px; }
.flow-play button.primary.flow-run { box-shadow: 0 2px 10px rgba(55,148,255,.35); }
.flow-pill {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 2px 9px; border-radius: 999px; font-size: 11px;
  border: 1px solid var(--border); color: var(--muted); white-space: nowrap;
}
.flow-pill.ok { color: var(--ok); border-color: rgba(63,185,80,.45); background: rgba(63,185,80,.08); }
.flow-pill.bad { color: var(--danger); border-color: rgba(241,76,76,.5); background: rgba(241,76,76,.08); }
.flow-pill.warn { color: var(--warn); border-color: rgba(204,167,0,.5); background: rgba(204,167,0,.08); }
.flow-pill.live { color: var(--accent); border-color: rgba(55,148,255,.5); background: rgba(55,148,255,.08); }
.flow-pill .pulse {
  width: 7px; height: 7px; border-radius: 50%; background: currentColor;
  animation: flow-pulse 1s ease-in-out infinite;
}
@keyframes flow-pulse { 0%,100% { opacity: 1 } 50% { opacity: .35 } }

/* A node kind's glyph on its tint, as in the node header. */
.flow-glyph-badge {
  display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto;
  border-radius: 5px; box-shadow: inset 0 0 0 1px rgba(255,255,255,.18);
}
.flow-glyph-badge svg .stroke {
  fill: none; stroke: #fff; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round;
}
.flow-glyph-badge svg .fill { fill: #fff; }

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
  display: flex; align-items: center; gap: 8px; width: 100%;
  border: 0; border-radius: 0; background: transparent;
  padding: 4px 10px; text-align: left; min-height: 28px;
}
.flow-menu-row.active, .flow-menu-row:hover { background: var(--surface-2); }
.flow-menu-row .swatch { width: 8px; height: 8px; border-radius: 2px; flex: 0 0 auto; }
.flow-menu-row .label { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.flow-menu-row .hint { color: var(--muted); font-size: 10.5px; flex: 0 0 auto; }
.flow-menu-empty { padding: 10px; color: var(--muted); }

/* ---- canvas ---- */
.flow-canvas-wrap { position: relative; flex: 1 1 auto; min-height: 0; overflow: hidden; }
.flow-canvas { width: 100%; height: 100%; display: block; cursor: default; }
/* Dragging across the canvas moves nodes and draws marquees, never selects text. */
.flow-canvas, .flow-canvas *, .flow-minimap, .flow-minimap * {
  -webkit-user-select: none; user-select: none;
}
.flow-canvas.panning { cursor: grabbing; }

/* ---- minimap ---- */
.flow-minimap {
  position: absolute; right: 12px; bottom: 12px; z-index: 4;
  border-radius: 8px; cursor: pointer;
  box-shadow: 0 6px 20px rgba(0,0,0,.35);
}
.flow-minimap-bg {
  fill: var(--vscode-editorWidget-background, #252526); fill-opacity: .92;
  stroke: var(--border); stroke-width: 1;
}
.flow-minimap-node { opacity: .8; }
.flow-minimap-node.selected { opacity: 1; stroke: var(--vscode-foreground); stroke-width: 1; }
.flow-minimap-node.running { opacity: 1; stroke: var(--accent); stroke-width: 1.5; }
.flow-minimap-node.failed { opacity: 1; stroke: var(--danger); stroke-width: 1.5; }
.flow-minimap-view {
  fill: var(--accent); fill-opacity: .1;
  stroke: var(--accent); stroke-width: 1.2; pointer-events: none;
}

/* ---- nodes ---- */
/* Flat, n8n-style: a square in its kind's colour with its glyph, its name
   under it; values (models, variables) are circles. */
.flow-node { cursor: default; }
/* Paint only: the box underneath takes the clicks, so a drag starts anywhere. */
.flow-node-glyph, .flow-node-summary, .flow-badge-pill, .flow-badge, .flow-mark,
.flow-spinner, .flow-node-problem { pointer-events: none; }
.flow-node-box { stroke: none; }
/* Selected or at work, a dashed ring in the node's colour. */
.flow-node-halo {
  fill: none; stroke-width: 2; stroke-dasharray: 6 5; stroke-linecap: round;
  opacity: 0; pointer-events: none; transition: opacity .12s ease;
}
.flow-node.selected .flow-node-halo { opacity: 1; }
.flow-node.running .flow-node-halo { opacity: 1; animation: flow-ring 700ms linear infinite; }
.flow-node.failed .flow-node-halo { opacity: 1; stroke: var(--danger); }
.flow-node.error .flow-node-box, .flow-node.failed .flow-node-box { stroke: var(--danger); stroke-width: 2; }
.flow-node-glyph .stroke {
  fill: none; stroke: #fff; stroke-width: 1.1; stroke-linecap: round; stroke-linejoin: round;
}
.flow-node-glyph .fill { fill: #fff; }
@keyframes flow-ring { to { stroke-dashoffset: -22; } }
/* Inside a wide node, on its colour; under a square or a circle, on the canvas. */
.flow-node-title { font-size: 12px; font-weight: 600; fill: #fff; }
.flow-node-summary { font-size: 10.5px; fill: rgba(255,255,255,.75); }
.flow-node-title.under { fill: var(--vscode-foreground); }
.flow-node-summary.under { fill: var(--muted); }
.flow-badge-pill { fill: var(--vscode-editor-background, #1e1e1e); stroke: var(--border); }
.flow-badge { font-size: 9.5px; fill: var(--vscode-foreground); font-weight: 600; }
.flow-mark circle { fill: none; }
.flow-mark.done circle { fill: var(--ok); }
.flow-mark.failed circle { fill: var(--danger); }
.flow-mark path {
  fill: none; stroke: #fff; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round;
}
.flow-node-problem circle { stroke: var(--vscode-editor-background, #1e1e1e); stroke-width: 2; }
.flow-node-problem.error circle { fill: var(--danger); }
.flow-node-problem.warning circle { fill: var(--warn); }
.flow-node-problem text { font-size: 10px; font-weight: 800; fill: #fff; }

/* ---- comments and notes ---- */
/* The body lets clicks through: a marquee or a right-click inside a comment is
   about the canvas and its nodes. Only the title bar and the edges are live. */
.flow-comment-body { fill-opacity: .12; stroke-opacity: .55; stroke-width: 1.2; pointer-events: none; }
.flow-comment-header { opacity: .82; cursor: move; }
.flow-comment-title { fill: #fff; font-weight: 650; pointer-events: none; white-space: pre; }
.flow-comment-halo {
  fill: none; stroke: var(--accent); stroke-width: 2; opacity: 0; pointer-events: none;
}
.flow-comment.selected .flow-comment-halo { opacity: .95; }
.flow-comment.error .flow-comment-body { stroke: var(--danger); stroke-opacity: 1; }
.flow-comment .flow-node-problem { pointer-events: none; }
.flow-comment-handle { fill: transparent; }
.flow-comment-handle.n, .flow-comment-handle.s { cursor: ns-resize; }
.flow-comment-handle.e, .flow-comment-handle.w { cursor: ew-resize; }
.flow-comment-handle.ne, .flow-comment-handle.sw { cursor: nesw-resize; }
.flow-comment-handle.nw, .flow-comment-handle.se { cursor: nwse-resize; }
.flow-minimap-comment { fill-opacity: .18; stroke-opacity: .6; stroke-width: 1; }
.flow-bubble { cursor: default; }
.flow-bubble-shape {
  fill: var(--vscode-editorHoverWidget-background, #2d2d30);
  stroke: var(--vscode-editorHoverWidget-border, var(--border)); stroke-width: 1;
  filter: url(#flow-shadow);
}
.flow-bubble-text { font-size: 11px; fill: var(--vscode-editorHoverWidget-foreground, var(--vscode-foreground)); white-space: pre; }
.flow-bubble-close { cursor: pointer; opacity: .55; }
.flow-bubble-close:hover { opacity: 1; }
.flow-bubble-close circle { fill: var(--surface-2); }
.flow-bubble-close path { stroke: var(--vscode-foreground); stroke-width: 1.4; stroke-linecap: round; }
.flow-note-mark { cursor: pointer; }
.flow-note-mark path {
  fill: var(--vscode-editorHoverWidget-background, #2d2d30);
  stroke: var(--muted); stroke-width: 1.2; stroke-linejoin: round;
}
.flow-note-mark:hover path { stroke: var(--accent); }
.flow-inline-edit {
  width: 100%; height: 100%; resize: none; margin: 0; padding: 2px 4px;
  font-family: var(--vscode-font-family); line-height: 1.3;
  color: var(--vscode-input-foreground, var(--vscode-foreground));
  background: var(--vscode-input-background, #3c3c3c);
  border: 1px solid var(--accent); border-radius: 4px; outline: none;
}
.flow-swatches { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.flow-swatch {
  width: 18px; height: 18px; padding: 0; border-radius: 50%;
  border: 2px solid transparent;
}
.flow-swatch.on { border-color: var(--vscode-foreground); }
.flow-swatches input[type="color"] { width: 26px; height: 22px; padding: 0; border: none; background: none; }

/* ---- a node with an agent working inside it ---- */
/* The running halo already says "this node"; these say "and it is moving". */
.flow-spinner-track { fill: none; stroke: #fff; stroke-opacity: .3; stroke-width: 1.7; }
.flow-spinner-arc {
  fill: none; stroke: #fff; stroke-width: 1.9; stroke-linecap: round;
  transform-box: fill-box; transform-origin: 50% 50%;
  animation: flow-spin 900ms linear infinite;
}
@keyframes flow-spin { to { transform: rotate(360deg) } }
@media (prefers-reduced-motion: reduce) {
  /* Still alive, without the movement: the arc pulses in place instead. */
  .flow-spinner-arc { animation: flow-pulse 1.4s ease-in-out infinite; }
  .flow-node.running .flow-node-halo { animation: none; }
  .flow-wire.data.lit { animation: none; }
}

/* ---- pins and wires ---- */
/* A side pin is named only while pointed at, or while a wire is being pulled
   and the name decides where it goes; a bottom port is always named. */
.flow-pin-label {
  font-size: 10.5px; fill: var(--vscode-foreground); opacity: 0; pointer-events: none;
  paint-order: stroke; stroke: var(--vscode-editor-background, #1e1e1e); stroke-width: 3px;
  stroke-linejoin: round; transition: opacity .1s ease;
}
.flow-pin:hover + .flow-pin-label, .flow-canvas.wiring .flow-pin-label { opacity: 1; }
.flow-pin-label.port { opacity: 1; font-size: 10px; fill: var(--muted); }
.flow-pin { cursor: crosshair; }
.flow-pin:hover .flow-pin-hit { opacity: .28; }
/* A dot on the card's edge, hollow until wired; a wired one is ringed in the
   canvas colour so it stands off a card of its own colour. */
.flow-pin-shape { stroke-width: 2; }
.flow-pin-shape:not(.filled) { fill: var(--vscode-editor-background, #1e1e1e); }
.flow-pin-shape.filled { stroke: var(--vscode-editor-background, #1e1e1e); }
.flow-pin:hover .flow-pin-shape { stroke-width: 2.6; }
.flow-pin-shape.exec { fill: var(--vscode-editor-background, #1e1e1e); stroke: var(--flow-exec); }
.flow-pin-shape.exec.filled { fill: var(--flow-exec); stroke: var(--vscode-editor-background, #1e1e1e); }
.flow-wire {
  fill: none; stroke-width: 2.2; stroke-linecap: round; pointer-events: none;
  transition: stroke-opacity .2s ease;
}
/* The flow is a solid line from one node's colour into the next; a value runs
   dashed in its type's colour, and its dashes move while it is being passed. */
.flow-wire.exec { stroke-width: 2.6; }
.flow-wire.data { stroke-dasharray: 6 5; }
.flow-wire.data.lit { animation: flow-ring 600ms linear infinite; }
.flow-wire.pending { stroke-dasharray: 6 5; }
.flow-wire.traveled { stroke-width: 2.8; }
.flow-wire.lit { stroke-width: 3; }
.flow-wire.selected { stroke-width: 3; }
.flow-wire-glow {
  fill: none; stroke-width: 9; stroke-linecap: round; opacity: .22; pointer-events: none;
}
.flow-wire-hit { fill: none; stroke: transparent; stroke-width: 12; cursor: pointer; }
.flow-grid-dot { fill: var(--border); opacity: .75; }
.flow-grid-dot.major { opacity: 1; }
.flow-marquee { fill: var(--accent); fill-opacity: .12; stroke: var(--accent); stroke-dasharray: 4 3; }

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
.flow-section-title.flow-node-card {
  text-transform: none; letter-spacing: 0; font-size: 13px; color: var(--vscode-foreground);
  gap: 8px; padding: 8px 10px; border-radius: 8px;
  background: var(--surface); border: 1px solid var(--border); border-left: 3px solid;
}
.flow-node-card .flow-glyph-badge { order: -1; }
.flow-node-card .title { font-weight: 650; }
.flow-node-card .id {
  font-size: 10.5px; color: var(--muted);
  font-family: var(--vscode-editor-font-family, monospace);
}
.flow-group { display: flex; flex-direction: column; gap: 10px; }
.flow-group-head {
  display: flex; align-items: center; gap: 6px; width: 100%;
  border: 0; background: transparent; padding: 2px 0; border-radius: 0;
  text-transform: uppercase; letter-spacing: .06em; font-size: 10px; color: var(--muted);
}
.flow-group-head:hover:not(:disabled) { background: transparent; color: var(--vscode-foreground); }
.flow-group-head .caret { width: 9px; }
.flow-group-head .line { flex: 1 1 auto; height: 1px; background: var(--border); }
.flow-group-body { display: flex; flex-direction: column; gap: 10px; }
.flow-picker-toggle { align-self: flex-start; font-size: 11px; }
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

/* ---- permissions and tools ---- */
.flow-link {
  border: 0; background: transparent; padding: 0 4px; color: var(--accent);
}
.flow-link:hover:not(:disabled) { background: transparent; text-decoration: underline; }
.flow-tag {
  font-size: 10px; color: var(--muted); white-space: nowrap;
  border: 1px solid var(--border); border-radius: 999px; padding: 0 6px;
}
.flow-switches { display: flex; flex-direction: column; gap: 5px; }
.flow-switches.locked { opacity: .45; }
.flow-switch-row { display: flex; align-items: center; gap: 6px; min-height: 20px; }
.flow-switch {
  position: relative; display: flex; align-items: center; gap: 8px; flex: 1 1 auto;
  color: var(--vscode-foreground); font-size: 12px; cursor: pointer;
}
.flow-switch input {
  position: absolute; inset: 0; width: 100%; height: 100%; margin: 0;
  opacity: 0; cursor: pointer;
}
.flow-switch input:disabled { cursor: default; }
.flow-switch .track {
  position: relative; flex: 0 0 auto; width: 28px; height: 16px; border-radius: 999px;
  background: var(--surface-2); border: 1px solid var(--border);
  transition: background .15s ease, border-color .15s ease;
}
.flow-switch .thumb {
  position: absolute; top: 1px; left: 1px; width: 12px; height: 12px; border-radius: 50%;
  background: var(--muted); transition: transform .15s ease, background .15s ease;
}
.flow-switch input:checked + .track { background: var(--accent); border-color: var(--accent); }
.flow-switch input:checked + .track .thumb { transform: translateX(12px); background: #fff; }
.flow-switch input:focus-visible + .track {
  outline: 1px solid var(--vscode-focusBorder, var(--accent)); outline-offset: 2px;
}
.flow-switch-row.explicit .text { font-weight: 600; }
.flow-granted-head {
  border: 0; background: transparent; padding: 2px 0; color: var(--muted);
  font-size: 11px; text-align: left;
}
.flow-granted-head:hover:not(:disabled) { background: transparent; color: var(--vscode-foreground); }
.flow-granted-head:disabled { opacity: 1; }
.flow-granted { display: flex; flex-direction: column; gap: 4px; }
.flow-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.flow-chip {
  display: inline-flex; align-items: center; gap: 2px; padding: 1px 7px;
  font-size: 10.5px; font-family: var(--vscode-editor-font-family, monospace);
  border-radius: 999px; background: var(--surface-2); border: 1px solid var(--border);
}
.flow-chip.unknown { border-color: var(--warn); color: var(--warn); }
.flow-chip-x {
  border: 0; background: transparent; padding: 0 0 0 3px; line-height: 1; color: var(--muted);
}
.flow-chip-x:hover:not(:disabled) { background: transparent; color: var(--danger); }
.flow-tool-picker { display: flex; flex-direction: column; gap: 6px; }
.flow-tool-groups {
  display: flex; flex-direction: column; max-height: 340px; overflow-y: auto;
  border: 1px solid var(--border); border-radius: 6px;
}
.flow-tool-group + .flow-tool-group { border-top: 1px solid var(--border); }
.flow-tool-group-head { display: flex; align-items: center; padding-right: 4px; }
.flow-tool-group-toggle {
  flex: 1 1 auto; display: flex; align-items: center; gap: 6px; text-align: left;
  border: 0; border-radius: 0; background: transparent; padding: 5px 8px;
}
.flow-tool-group-toggle .caret { color: var(--muted); width: 8px; }
.flow-tool-group-toggle .title { flex: 1 1 auto; }
.flow-tool-group-toggle .count { font-size: 10.5px; color: var(--muted); }
.flow-tool-group-toggle .count.some { color: var(--accent); font-weight: 600; }
.flow-tool-row {
  display: flex; align-items: center; gap: 6px; padding: 3px 8px 3px 22px; cursor: pointer;
  font-family: var(--vscode-editor-font-family, monospace); font-size: 11px;
}
.flow-tool-row:hover { background: var(--surface-2); }
.flow-tool-row .name { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; }
.flow-tool-row.off .name { opacity: .55; text-decoration: line-through; }

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
.flow-log-row {
  display: flex; gap: 8px; padding: 1px 0 1px 8px;
  border-left: 2px solid transparent;
}
.flow-log-row.agent { border-left-color: var(--accent); }
.flow-log-row.print { border-left-color: var(--flow-exec); }
.flow-log-row.decision { border-left-color: var(--muted); }
.flow-log-row.warn { border-left-color: var(--warn); }
.flow-log-row.error { border-left-color: var(--danger); }
.flow-log-row .at { color: var(--muted); flex: 0 0 auto; }
.flow-log-row .who {
  color: var(--accent); flex: 0 0 auto;
  padding: 0 5px; border-radius: 4px; background: rgba(55,148,255,.1);
}
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

/* ---- an open node: what it writes, inside it ---- */
.flow-output-frame { overflow: visible; }
.flow-output {
  position: relative; width: 100%; height: 100%;
  border: 1px solid var(--border); border-radius: 6px;
  background: var(--vscode-editor-background, #1e1e1e);
  cursor: auto; user-select: text;
}
.flow-output-scroll {
  position: absolute; inset: 0; overflow-y: auto; overflow-x: hidden;
  padding: 7px 9px 9px; font-size: 11.5px; line-height: 1.45;
  scrollbar-width: thin;
}
.flow-output-part { white-space: pre-wrap; overflow-wrap: anywhere; margin-bottom: 5px; }
.flow-output-part.thinking {
  color: var(--muted); font-style: italic;
  border-left: 2px solid var(--border); padding-left: 7px;
}
.flow-output-part.tool {
  font-family: var(--vscode-editor-font-family, monospace); font-size: 11px;
  color: var(--accent); margin-bottom: 3px;
}
.flow-output-part.info { color: var(--muted); font-size: 10.5px; }
.flow-output-part.error { color: var(--danger); }
.flow-output-part.result { font-weight: 600; }
.flow-output-label {
  display: block; font-style: normal; font-size: 10px; letter-spacing: .04em;
  text-transform: uppercase; margin-bottom: 2px;
}
.flow-output-caret {
  display: inline-block; width: 6px; height: 12px; vertical-align: -2px;
  background: var(--accent); animation: flow-caret 1s steps(2) infinite;
}
@keyframes flow-caret { 50% { opacity: 0; } }
.flow-output-close {
  position: absolute; top: 3px; right: 5px; z-index: 1;
  border: 0; background: var(--surface-2); border-radius: 4px;
  padding: 0 6px; line-height: 16px; opacity: .7;
}
.flow-output-close:hover { opacity: 1; }
.flow-output-pill { cursor: pointer; }
.flow-output-pill rect {
  fill: var(--vscode-editorWidget-background, #252526);
  stroke: var(--border); stroke-width: 1;
}
.flow-output-pill text { font-size: 10.5px; fill: var(--muted); }
.flow-output-pill:hover rect { stroke: var(--accent); }
.flow-output-pill:hover text { fill: var(--vscode-foreground); }
`;
