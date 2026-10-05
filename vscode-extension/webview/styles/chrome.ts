// The chat's chrome: header, floating composer, its chips and the context meter.

export const CHROME_STYLE = `
/* ---- header ---- */
.statusbar {
  display: flex; align-items: center; gap: var(--space-3);
  height: 40px; flex: none; padding: 0 var(--space-3) 0 var(--space-2);
  background: var(--bg-rail); border-bottom: 1px solid var(--border);
  font-size: var(--fs-small); min-width: 0;
}
.statusbar > * { flex: none; }
.brand-mark { display: inline-grid; place-items: center; width: 22px; height: 22px; color: var(--accent); }
.chat-title {
  flex: 0 1 auto; min-width: 0; font-size: var(--fs-body); font-weight: 600; color: var(--fg-strong);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.branch-chip {
  display: inline-flex; align-items: center; gap: 4px; min-width: 0; max-width: 32vw; height: 20px;
  padding: 0 7px; border: 1px solid var(--border-strong); border-radius: var(--radius-pill);
  font-size: var(--fs-label); color: var(--fg-muted);
}
.branch-chip > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.live-status { display: inline-grid; place-items: center; width: 16px; height: 16px; }
.live-status .spinner { width: 14px; height: 14px; border-width: 2px; }
.live-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--success); }
.live-status.offline .live-dot { background: var(--danger); }
.bar-spacer { flex: 1 1 auto; }
.statusbar .icon-btn .codicon { display: block; }
.statusbar .icon-btn.on { color: var(--fg); }
.typing-clock { color: var(--accent); font-variant-numeric: tabular-nums; }

/* ---- context meter ---- */
.context-meter {
  display: inline-flex; align-items: center; gap: var(--space-3);
  font-size: var(--fs-small); line-height: 18px; font-variant-numeric: tabular-nums;
  color: var(--fg); background: none; border: 0; padding: 0 2px; font-family: inherit;
}
button.context-meter { cursor: pointer; border-radius: var(--radius-sm); }
button.context-meter:hover { background: var(--hover); }
.context-meter .meter-budget { color: var(--fg-muted); }
.meter-track { width: 64px; height: 4px; border-radius: 2px; background: var(--border-strong); overflow: hidden; }
.meter-fill {
  display: block; height: 100%; background: var(--accent);
  transition: width var(--dur-slow) var(--ease-out), background-color var(--dur-base) var(--ease-out);
}
.context-meter.warn .meter-fill { background: var(--warn); }
.context-meter.warn .meter-used { color: var(--warn); }
.context-meter.over .meter-fill { background: var(--danger); }
.context-meter.over .meter-used { color: var(--danger); }
.context-meter.unknown .meter-track {
  background: repeating-linear-gradient(90deg, var(--border-strong) 0 4px, transparent 4px 8px);
}
.context-meter.unknown .meter-used { color: var(--fg-muted); }

/* ---- chat column: the composer floats over the transcript ---- */
.chat-col { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; }
.chat-col .transcript { flex: 1; min-height: 0; padding-bottom: calc(var(--composer-h, 140px) + var(--space-3)); }
.composer-wrap {
  position: absolute; left: 0; right: 0; bottom: 0; z-index: 3;
  padding: var(--space-3) 18px var(--space-4); isolation: isolate;
}
/* The glass, in two layers so the blur builds up instead of switching on. One
   masked layer cross-fades sharp text with its blurred copy over a few pixels,
   which reads as a ghosted line jammed against the field. Here a light layer
   starts well above the field and softens the text, and a heavy one closer in
   dissolves it, so nothing legible ever reaches the field's edge. */
.composer-wrap::before, .composer-wrap::after {
  content: ""; position: absolute; left: 0; right: 0; bottom: 0; z-index: -1; pointer-events: none;
}
.composer-wrap::before {
  top: -64px;
  background: linear-gradient(to bottom, transparent 0, var(--bg-glass) 64px);
  -webkit-backdrop-filter: blur(3px); backdrop-filter: blur(3px);
  -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 44px);
  mask-image: linear-gradient(to bottom, transparent 0, #000 44px);
}
.composer-wrap::after {
  top: -28px;
  background: linear-gradient(to bottom, transparent 0, var(--bg-veil) 40px);
  -webkit-backdrop-filter: blur(18px) saturate(1.1); backdrop-filter: blur(18px) saturate(1.1);
  -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 28px);
  mask-image: linear-gradient(to bottom, transparent 0, #000 28px);
}
.composer-stack { position: relative; max-width: 820px; margin: 0 auto; }
.composer {
  position: relative; display: flex; flex-direction: column;
  border: 1px solid var(--accent); border-radius: var(--radius-lg);
  background: var(--surface-input-glass);
  transition: box-shadow var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out);
}
.composer:focus-within { box-shadow: 0 0 0 3px var(--accent-soft); }
.composer.off { border-color: var(--border-strong); }
.composer-wrap.dropping .composer { box-shadow: 0 0 0 3px var(--accent-soft); }
/* Working: an accent arc runs around the field. It is a dash on the field's
   own outline, moved by its offset, so it covers the same distance every
   instant. An angle swept from the centre does not: on a field wider than it
   is tall it crawls along the long sides and whips round the ends. */
.composer.busy { border-color: var(--turn-user-line); }
.busy-ring {
  position: absolute; inset: -1px; width: calc(100% + 2px); height: calc(100% + 2px);
  overflow: visible; pointer-events: none;
}
.busy-ring rect {
  x: .5px; y: .5px; width: calc(100% - 1px); height: calc(100% - 1px); rx: calc(var(--radius-lg) - .5px);
  fill: none; stroke: var(--accent); stroke-width: 1px;
  stroke-dasharray: var(--ring-dash) var(--ring-gap);
  animation: ring-run 2.4s linear infinite;
}
@keyframes ring-run {
  from { stroke-dashoffset: var(--ring-from); }
  to { stroke-dashoffset: var(--ring-to); }
}
@media (prefers-reduced-motion: reduce) {
  .composer.busy { border-color: var(--accent); }
  .busy-ring { display: none; }
}
.composer textarea {
  resize: none; max-height: 180px; min-height: 22px;
  border: none; outline: none; background: transparent; color: var(--fg);
  font-family: inherit; font-size: var(--fs-body); line-height: 1.5;
  padding: var(--space-3) var(--space-4) var(--space-2);
}
.composer textarea::placeholder { color: var(--fg-muted); }
.composer-ctx { display: flex; flex-wrap: wrap; gap: var(--space-2); padding: var(--space-3) var(--space-3) 0; }
.ctx-chip {
  display: inline-flex; align-items: center; gap: 6px; min-width: 0; max-width: 100%;
  height: 22px; padding: 0 4px 0 8px; border: 1px solid var(--border-strong); border-radius: var(--radius-pill);
  font-size: var(--fs-small); color: var(--fg-muted);
}
.ctx-chip .ctx-label { font-family: var(--font-mono); color: var(--fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ctx-chip .ctx-kind { font-size: var(--fs-label); color: var(--fg-muted); }
.ctx-remove {
  display: inline-grid; place-items: center; width: 18px; height: 18px; padding: 0;
  border: none; border-radius: 50%; background: transparent; color: var(--fg-muted); cursor: pointer;
}
.ctx-remove:hover { color: var(--fg); background: var(--hover); }
.hint { max-width: 820px; margin: 6px auto 0; color: var(--fg-muted); font-size: var(--fs-label); }

/* the bar: options wrap on the left, model + send never wrap and never move */
.composer-bar { display: flex; align-items: flex-end; gap: var(--space-2); padding: var(--space-2) var(--space-3) var(--space-3); }
.composer-opts { display: flex; flex-wrap: wrap; gap: var(--space-2); flex: 1 1 auto; min-width: 0; padding-bottom: 3px; }
.composer-end { display: flex; align-items: center; gap: var(--space-2); flex: none; }
.send-btn {
  flex: none; width: 28px; height: 28px; border-radius: var(--radius-md); border: none;
  display: grid; place-items: center; background: var(--button); color: var(--button-fg); cursor: pointer;
}
.send-btn:hover:not(:disabled) { background: var(--button-hover); }
.send-btn:disabled { background: var(--hover); color: var(--fg-muted); cursor: default; }
.send-btn.stop { background: var(--danger); color: var(--button-fg); }
.composer-wrap > .jump-btn {
  position: absolute; right: 28px; top: -40px; width: 30px; height: 30px; border-radius: 50%;
  display: grid; place-items: center; cursor: pointer; color: var(--fg);
  background: var(--surface-input-glass); border: 1px solid var(--border-strong);
  -webkit-backdrop-filter: blur(12px); backdrop-filter: blur(12px);
}
.jump-btn:hover { border-color: var(--accent); color: var(--fg-strong); }

/* ---- chips ---- */
.chip-menu { position: relative; min-width: 0; }
.chip {
  display: inline-flex; align-items: center; gap: var(--space-2); max-width: 100%;
  height: 22px; padding: 0 8px; border-radius: var(--radius-pill);
  border: 1px solid var(--border-strong); background: transparent;
  font: inherit; font-size: var(--fs-small); line-height: 18px; color: var(--fg); cursor: pointer; white-space: nowrap;
}
.chip:hover, .chip.open { background: var(--hover); }
.chip:focus-visible { outline: 1px solid var(--focus); outline-offset: 1px; }
.chip-key { color: var(--fg-muted); }
.chip-value { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.chip-chev { color: var(--fg-muted); }
.model-chip .chip { max-width: 180px; }
.statusbar.narrow ~ .workbench .model-chip .chip { max-width: 104px; }
.chip-pop {
  position: absolute; left: 0; bottom: calc(100% + 6px); z-index: 30;
  min-width: 200px; max-width: 320px; max-height: 280px; overflow-y: auto; padding: 4px;
  border: 1px solid var(--border-strong); border-radius: var(--radius-md); background: var(--surface);
  box-shadow: 0 8px 24px var(--shadow);
}
.composer-end .chip-pop { left: auto; right: 0; }
.chip-opt {
  display: grid; grid-template-columns: 16px minmax(0,1fr); column-gap: var(--space-3); row-gap: 0;
  align-items: center; padding: 4px 8px; border-radius: var(--radius-sm); cursor: pointer; font-size: var(--fs-small);
}
.chip-opt.active { background: var(--accent-soft); }
.chip-opt-label { color: var(--fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chip-opt.selected .chip-opt-label { color: var(--fg-strong); font-weight: 600; }
.chip-opt-hint { grid-column: 2; color: var(--fg-muted); font-size: var(--fs-label); }
.chip-check { display: grid; place-items: center; color: var(--accent); }
.chip-note { padding: 6px 8px; font-size: var(--fs-small); color: var(--fg-muted); display: flex; align-items: center; gap: 6px; }
.chip-note.bad { color: var(--danger); }

/* refactor / explain: the same controls under the panel */
.mode-col { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.mode-col > :first-child { flex: 1; min-height: 0; overflow-y: auto; }
.mode-foot { flex: none; border-top: 1px solid var(--border); background: var(--bg-rail); }
.mode-foot .composer-bar { padding: var(--space-3); }
`;
