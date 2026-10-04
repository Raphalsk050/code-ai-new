// The settings screen: categories on the left (tabs on top when narrow),
// one row per setting, a floating bar while anything is unsaved.

export const SETTINGS_STYLE = `
.settings { position: relative; display: flex; flex-direction: column; height: 100%; min-height: 0; }
.settings-bar {
  display: flex; align-items: center; gap: var(--space-3); flex: none;
  height: 40px; padding: 0 var(--space-3) 0 var(--space-2);
  background: var(--bg-rail); border-bottom: 1px solid var(--border);
}
.settings-title { flex: none; font-weight: 600; font-size: var(--fs-body); color: var(--fg-strong); }
.settings-search {
  flex: 1 1 auto; min-width: 0; max-width: 320px; margin-left: auto;
  display: flex; align-items: center; gap: 6px; height: 26px; padding: 0 8px;
  border: 1px solid var(--border-strong); border-radius: var(--radius-pill); background: var(--surface-input);
  color: var(--fg-muted);
}
.settings-search:focus-within { border-color: var(--focus); }
.settings-search input { flex: 1; min-width: 0; border: none; outline: none; background: none; color: var(--fg); font: inherit; font-size: var(--fs-small); }
.settings-status { flex: none; display: inline-flex; align-items: center; gap: 6px; font-size: var(--fs-small); color: var(--fg-muted); }
.settings-status.pending { color: var(--fg); }
.settings-status.error { color: var(--danger); }
.dirty-dot { display: inline-block; flex: none; width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }
.settings-loading {
  padding: 40px; text-align: center; color: var(--fg-muted);
  display: flex; flex-direction: column; align-items: center; gap: 12px;
}

.settings-main { flex: 1; min-height: 0; display: flex; }
.settings-nav {
  flex: none; width: 184px; padding: var(--space-3) var(--space-2); overflow-y: auto;
  display: flex; flex-direction: column; gap: 2px; border-right: 1px solid var(--border);
}
.settings-nav-item {
  display: flex; align-items: center; gap: var(--space-3); width: 100%; padding: 6px 10px;
  border: none; border-radius: var(--radius-sm); background: none; color: var(--fg-muted);
  font: inherit; font-size: var(--fs-small); text-align: left; cursor: pointer; white-space: nowrap;
}
.settings-nav-item > span:not(.dirty-dot) { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.settings-nav-item:hover { background: var(--hover); color: var(--fg); }
.settings-nav-item.active { background: var(--accent-soft); color: var(--fg-strong); }
.settings-nav-item:focus-visible { outline: 1px solid var(--focus); outline-offset: -1px; }
.settings-body { flex: 1; min-width: 0; overflow-y: auto; padding: var(--space-4) var(--space-5) 96px; }
.settings-group { max-width: 720px; margin: 0 0 var(--space-5); }
.settings-group-title { margin: 0 0 var(--space-2); font-size: var(--fs-label); font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: var(--fg-muted); }
.settings-rows { display: flex; flex-direction: column; }

/* A row: name and description left, control right; stacked when narrow. */
.setting-row {
  display: grid; grid-template-columns: minmax(0, 1fr) minmax(160px, 280px); column-gap: var(--space-5); row-gap: 2px;
  align-items: center; padding: 10px 0; border-bottom: 1px solid var(--border);
}
.setting-row > .settings-field-label { grid-column: 1; }
.setting-row > .setting-hint { grid-column: 1; }
.setting-row > .setting-control { grid-column: 2; grid-row: 1 / span 2; display: flex; justify-content: flex-end; min-width: 0; }
.setting-row.wide { grid-template-columns: minmax(0, 1fr); }
.setting-row.wide > .setting-control { grid-column: 1; grid-row: auto; justify-content: stretch; margin-top: 6px; }
.setting-row.wide > .setting-control > * { flex: 1; min-width: 0; }
.setting-row.risk { background: var(--warn-soft); margin: 0 -10px; padding-left: 10px; padding-right: 10px; border-radius: var(--radius-sm); border-bottom-color: transparent; }
.settings-field-label { display: flex; align-items: center; gap: 7px; font-size: var(--fs-body); color: var(--fg-strong); min-width: 0; }
.setting-hint { font-size: var(--fs-small); color: var(--fg-muted); }
.setting-control input:not([type="checkbox"]), .setting-control select {
  width: 100%; min-width: 0; height: 28px; box-sizing: border-box; padding: 0 9px;
  font-family: inherit; font-size: var(--fs-small); color: var(--fg);
  background: var(--surface-input); border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
}
.setting-control input.number { width: 120px; text-align: right; font-variant-numeric: tabular-nums; }
.setting-control input:focus, .setting-control select:focus { outline: none; border-color: var(--focus); }
.restart-tag, .risk-tag {
  flex: none; font-size: var(--fs-label); font-weight: 400; padding: 0 7px; line-height: 16px; border-radius: var(--radius-pill);
  color: var(--warn); background: var(--warn-soft);
}
.risk-tag { color: var(--danger); background: var(--danger-soft); }

.segmented { display: inline-flex; padding: 2px; gap: 2px; border-radius: var(--radius-sm); background: var(--surface-input); border: 1px solid var(--border-strong); }
.segmented button {
  border: none; background: none; color: var(--fg-muted); font: inherit; font-size: var(--fs-small);
  padding: 3px 10px; border-radius: 4px; cursor: pointer; white-space: nowrap;
}
.segmented button:hover { color: var(--fg); }
.segmented button.on { background: var(--button); color: var(--button-fg); }
.segmented button:focus-visible { outline: 1px solid var(--focus); outline-offset: 1px; }

.switch {
  flex: none; width: 34px; height: 20px; border-radius: 999px; border: none; padding: 0;
  background: color-mix(in srgb, var(--fg-muted) 45%, transparent); position: relative; cursor: pointer;
  transition: background var(--dur-fast) var(--ease-out);
}
.switch.on { background: var(--accent); }
.switch:focus-visible { outline: 1px solid var(--focus); outline-offset: 2px; }
.switch-knob {
  position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%;
  background: #fff; transition: transform var(--dur-fast) var(--ease-out);
}
.switch.on .switch-knob { transform: translateX(14px); }

.key-stored { display: inline-flex; align-items: center; gap: 6px; font-size: var(--fs-small); color: var(--fg-muted); }
.key-stored .btn-sm { margin-left: 4px; }
.model-field { display: flex; flex-direction: column; gap: 6px; }
.model-row { display: flex; align-items: stretch; gap: var(--space-2); }
.model-input { flex: 1; min-width: 0; }
.model-list-btn { flex: none; display: inline-flex; align-items: center; gap: 6px; font-size: var(--fs-small); white-space: nowrap; }
.model-options {
  list-style: none; margin: 0; padding: 3px; max-height: 196px; overflow-y: auto;
  border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: var(--surface);
}
.model-option { display: flex; align-items: center; gap: 6px; padding: 3px 6px; border-radius: 4px; font-family: var(--font-mono); font-size: var(--fs-small); cursor: pointer; }
.model-option:hover { background: var(--hover); }
.model-option.current { color: var(--fg-strong); background: var(--accent-soft); }
.model-check { width: 14px; display: inline-grid; place-items: center; color: var(--accent); }
.model-note { font-size: var(--fs-small); color: var(--fg-muted); }
.model-error { font-size: var(--fs-small); color: var(--danger); }
.probe { display: inline-flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; justify-content: flex-end; }
.probe-result { display: inline-flex; align-items: center; gap: 4px; font-size: var(--fs-small); }
.probe-result.ok { color: var(--success); }
.probe-result.bad { color: var(--danger); }
.probe .spinner { width: 12px; height: 12px; border-width: 2px; }

.settings-errors {
  display: flex; flex-direction: column; gap: 6px; max-width: 720px; margin-bottom: var(--space-4);
  padding: 10px 12px; border-radius: var(--radius-sm);
  border: 1px solid var(--danger); background: var(--danger-soft);
}
.settings-errors-head { font-size: var(--fs-small); color: var(--fg); }
.settings-error { font-size: var(--fs-small); color: var(--danger); }
.settings-error code { font-family: var(--font-mono); font-size: var(--fs-small); }
.settings-restart-note {
  display: flex; align-items: center; gap: var(--space-3); max-width: 720px; margin-bottom: var(--space-4);
  padding: 8px 12px; border: 1px solid var(--warn); border-radius: var(--radius-sm); background: var(--warn-soft); font-size: var(--fs-small);
}
.settings-restart-note > span { flex: 1; }
.settings-note { font-size: var(--fs-small); color: var(--fg-muted); margin-top: 4px; }

/* The MCP card keeps its own heading inside its row. */
.setting-row[data-setting="mcp"] > .settings-field-label,
.setting-row[data-setting="mcp"] > .setting-hint { display: none; }
.setting-row[data-setting="mcp"] > .setting-control { margin-top: 0; }
.settings-section { margin: 0; }
.settings-section-head { margin-bottom: 10px; }
.settings-section-head h3 { font-size: var(--fs-body); font-weight: 600; color: var(--fg-strong); margin: 0 0 2px; }
.settings-section-hint { font-size: var(--fs-small); color: var(--fg-muted); }
.settings-fields { display: flex; flex-direction: column; gap: 12px; }
.settings-toggle { display: flex; align-items: flex-start; gap: 10px; cursor: pointer; padding: 2px 0; }
.settings-toggle-text { display: flex; flex-direction: column; gap: 2px; }
.settings-toggle-hint { font-size: var(--fs-small); color: var(--fg-muted); }
.settings-toggle .switch { margin-top: 1px; }

/* ---- floating save bar, the composer's glass ---- */
.save-bar {
  position: absolute; left: 0; right: 0; bottom: var(--space-4); margin: 0 auto; z-index: 5;
  width: min(560px, calc(100% - 32px)); box-sizing: border-box;
  display: flex; align-items: center; gap: var(--space-3); padding: 8px 8px 8px 16px;
  border: 1px solid var(--accent); border-radius: var(--radius-lg);
  background: var(--surface-input-glass); -webkit-backdrop-filter: blur(12px); backdrop-filter: blur(12px);
  box-shadow: 0 8px 24px var(--shadow);
  animation: rise-in var(--dur-base) var(--ease-spring) both;
}
.save-bar-text { flex: 1 1 auto; min-width: 0; font-size: var(--fs-small); color: var(--fg-strong); }
.save-bar-note { color: var(--warn); }
.settings-save { padding: 5px 16px; font-size: var(--fs-small); }

/* ---- narrow: tabs on top, rows stacked ---- */
.settings.narrow .settings-main { flex-direction: column; }
.settings.narrow .settings-nav {
  width: auto; flex-direction: row; overflow-x: auto; overflow-y: hidden; gap: 4px;
  padding: 6px var(--space-3); border-right: none; border-bottom: 1px solid var(--border);
  scrollbar-width: none;
}
.settings.narrow .settings-nav-item { width: auto; flex: none; padding: 4px 10px; border-radius: var(--radius-pill); }
.settings.narrow .settings-nav-item > .codicon { display: none; }
.settings.narrow .settings-body { padding: var(--space-3) var(--space-4) 96px; }
.settings.narrow .setting-row { grid-template-columns: minmax(0, 1fr); }
.settings.narrow .setting-row > .setting-control { grid-column: 1; grid-row: auto; justify-content: flex-start; margin-top: 6px; }
.settings.narrow .settings-search { max-width: none; }
.settings.narrow .settings-title { display: none; }
`;
