// The task rail: one component, three layouts. Which one is live comes from
// `data-w` on the app root, written by a ResizeObserver - the webview only ever
// knows its own width, so media queries would be measuring the wrong box.

export const RAIL_STYLE = `
.workbench { display: flex; flex: 1; min-height: 0; position: relative; }
.workarea { display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; }

/* ---- icon strip (always present, collapses to nothing when docked) ---- */
.rail-strip {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  width: 40px; flex-shrink: 0;
  padding: 8px 0;
  border-left: 1px solid var(--border);
  background: var(--bg-rail);
}
.rail-btn {
  position: relative;
  width: 28px; height: 26px;
  display: flex; align-items: center; justify-content: center;
  background: transparent; border: none; border-radius: var(--radius-md);
  color: var(--fg-muted); cursor: pointer;
}
.rail-btn:hover { background: var(--hover); color: var(--fg); }
.rail-btn.active { background: var(--accent-soft); color: var(--fg); }
.rail-badge {
  position: absolute; top: -1px; right: -1px;
  min-width: 14px; height: 14px; padding: 0 3px; box-sizing: border-box;
  border-radius: var(--radius-pill);
  background: var(--accent); color: var(--button-fg);
  font-size: 10px; line-height: 14px; text-align: center; font-variant-numeric: tabular-nums;
}
/* Bottom right, so it never covers the second glyph of the agents icon. */
.rail-dot {
  position: absolute; bottom: 2px; right: 2px;
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--accent); box-shadow: 0 0 0 2px var(--bg-rail);
}
.rail-dot.tone-warn { background: var(--warn); }
.rail-dot.tone-bad { background: var(--danger); }
.rail-dot.tone-ok { background: var(--success); }
.rail-badge-text {
  flex: none; font-size: var(--fs-label); letter-spacing: 0; text-transform: none;
  line-height: 16px; padding: 0 7px; border-radius: var(--radius-pill);
  color: var(--fg-muted); background: var(--hover); font-variant-numeric: tabular-nums;
}
.rail-badge-text.tone-run { color: var(--accent); background: var(--accent-soft); }
.rail-badge-text.tone-warn { color: var(--warn); background: var(--warn-soft); }
.rail-badge-text.tone-bad { color: var(--danger); background: var(--danger-soft); }
.rail-badge-text.tone-ok { color: var(--success); background: var(--success-soft); }
.rail-sep { width: 22px; height: 1px; background: var(--border); margin: 4px 0; }

/* ---- docked column (md) ---- */
.rail-dock {
  /* Width comes from the component (the user drags it); this is the fallback
     for the first paint, before the stored value is read. */
  width: 288px; flex-shrink: 0;
  display: flex; flex-direction: column;
  min-height: 0;
  border-left: 1px solid var(--border);
  background: var(--bg-rail);
  overflow: hidden;
}
.rail-dock-head {
  display: flex; align-items: center; gap: 6px;
  padding: 7px 6px 7px 12px;
  border-bottom: 1px solid var(--border);
}
.rail-dock-body { flex: 1; min-height: 0; overflow-y: auto; }

/* ---- overlay drawer (xs / sm) ---- */
.rail-drawer {
  position: absolute; top: 0; bottom: 0;
  /* Beside the icon strip, not over it: switching sections stays one click,
     and the strip is the only thing that says which one is open. */
  right: 40px;
  width: 280px; max-width: calc(100% - 40px);
  display: flex; flex-direction: column;
  border-left: 1px solid var(--border);
  background: var(--bg-rail);
  box-shadow: -14px 0 28px rgba(0,0,0,.34);
  z-index: 6;
  transition: transform var(--dur-base) var(--ease-out), opacity var(--dur-base) var(--ease-out);
}
/* Under 380px there is no room for both, so the sheet takes the panel and the
   only way back is its own close button. */
.app[data-w="xs"] .rail-drawer { right: 0; width: 100%; max-width: 100%; border-left: none; }
.rail-drawer.exit { transform: translateX(16px); opacity: 0; }
.rail-drawer.enter { transform: none; opacity: 1; }
.rail-scrim {
  position: absolute; top: 0; bottom: 0; left: 0; right: 40px;
  background: rgba(0,0,0,.34);
  z-index: 5;
  transition: opacity var(--dur-base) var(--ease-out);
}
.rail-scrim.exit { opacity: 0; }
.rail-scrim.enter { opacity: 1; }
.app[data-w="xs"] .rail-scrim { display: none; }

/* ---- sections ---- */
.rail-section + .rail-section { border-top: 1px solid var(--border); }
.rail-section-head {
  width: 100%;
  display: flex; align-items: center; gap: 7px;
  padding: 8px 12px;
  background: transparent; border: none;
  color: var(--fg);
  font-size: var(--fs-label); letter-spacing: .07em; text-transform: uppercase;
  cursor: pointer; text-align: left;
}
.rail-section-head:hover { background: var(--hover); }
.rail-section-head .chev { transition: transform var(--dur-base) var(--ease-spring); color: var(--fg-muted); }
.rail-section.open .rail-section-head .chev { transform: rotate(90deg); }
.rail-section-title { flex: 1; }
.rail-section-note { font-size: var(--fs-label); letter-spacing: 0; text-transform: none; color: var(--fg-muted); }
.rail-section-body { padding: 0 12px 10px 12px; font-size: var(--fs-small); }
.rail-empty { color: var(--fg-muted); font-size: var(--fs-small); padding: 2px 0 6px; }

/* ---- splitters ---- */
/* 1px of line, 9px of target: a 1px hit area is unusable with a mouse and
   impossible with a trackpad, so the strip is padded and the line is drawn
   inside it. */
.rail-grip {
  position: relative;
  flex-shrink: 0;
  background: transparent;
  touch-action: none;
}
.rail-grip::after {
  content: "";
  position: absolute;
  background: transparent;
  transition: background var(--dur-fast, .12s) var(--ease-out);
}
.rail-grip:hover::after,
.rail-grip:focus-visible::after { background: var(--accent); }
.rail-grip:focus-visible { outline: none; }

.rail-grip-x { width: 9px; margin: 0 -4px; cursor: col-resize; z-index: 4; }
.rail-grip-x::after { top: 0; bottom: 0; left: 4px; width: 1px; }

/* At the foot of an open section, straddling the boundary with the next one.
   The margins cancel the height so it takes no room in the column, and the
   z-index puts it over the neighbouring section header - which is a full-width
   button, and was taking the drag for itself. */
.rail-grip-y {
  position: relative; z-index: 3;
  height: 11px; margin: -5px 0 -6px;
  cursor: row-resize;
}
.rail-grip-y::after { left: 12px; right: 12px; top: 5px; height: 2px; border-radius: 1px; }
/* Wider than the line it draws, so the target is findable by feel. */
.rail-grip-y:hover::after, .rail-grip-y:focus-visible::after { left: 0; right: 0; }

/* A dragged section scrolls its own content instead of growing the rail. */
.rail-section-body.sized { overflow-y: auto; }

/* ---- session ---- */
.session-meter { margin: 2px 0 8px; }
.session-meter-head { display: flex; align-items: baseline; justify-content: space-between; font-size: var(--fs-small); margin-bottom: 5px; }
.session-meter-head .k { color: var(--fg-muted); }
.session-meter-value { font-variant-numeric: tabular-nums; color: var(--fg-strong); }
.session-meter-value.warn { color: var(--warn); }
.session-meter-value.over { color: var(--danger); }
.session-meter-value .muted { color: var(--fg-muted); }
.rail-meter { position: relative; height: 6px; border-radius: 3px; background: var(--border-strong); }
.rail-meter-fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 3px; background: var(--accent); transition: width var(--dur-slow) var(--ease-out); }
.rail-meter.warn .rail-meter-fill { background: var(--warn); }
.rail-meter.over .rail-meter-fill { background: var(--danger); }
.rail-meter.unknown { background: repeating-linear-gradient(90deg, var(--border-strong) 0 4px, transparent 4px 8px); }
.rail-meter-mark { position: absolute; top: -3px; bottom: -3px; width: 2px; margin-left: -1px; border-radius: 1px; background: var(--fg-muted); }
.state-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 2px; vertical-align: 1px; background: var(--fg-muted); }
.state-run .state-dot { background: var(--accent); }
.state-ok .state-dot { background: var(--success); }
.state-warn { color: var(--warn); }
.state-warn .state-dot { background: var(--warn); }
.state-bad { color: var(--danger); }
.state-bad .state-dot { background: var(--danger); }
.session-panel .kv .v { font-family: var(--font-ui); }
.details-toggle {
  display: flex; align-items: center; gap: 4px; margin: 10px 0 0; padding: 2px 0;
  border: none; background: none; color: var(--fg-muted); font: inherit; font-size: var(--fs-small); cursor: pointer;
}
.details-toggle:hover { color: var(--fg); }
.details { margin-top: 4px; }
.details .kv .v { font-family: var(--font-mono); }

/* ---- changes ---- */
.change-list { list-style: none; margin: 0; padding: 0; }
.change-row { display: flex; align-items: center; gap: var(--space-2); min-height: 26px; margin: 0 -6px; padding: 0 2px 0 6px; border-radius: var(--radius-sm); }
.change-row:hover { background: var(--hover); }
.change-open {
  display: flex; align-items: center; gap: 6px; flex: 1 1 auto; min-width: 0;
  border: none; background: none; padding: 3px 0; color: var(--fg); font: inherit; text-align: left; cursor: pointer;
}
.change-open > .codicon { color: var(--fg-muted); flex: none; }
.change-name { flex: none; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.change-dir { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--fg-muted); font-size: var(--fs-label); direction: rtl; text-align: left; }
.change-unknown { color: var(--fg-muted); font-size: var(--fs-small); }
.change-revert { width: 22px; height: 22px; padding: 0; opacity: 0; }
.change-row:hover .change-revert, .change-revert:focus-visible { opacity: 1; }
.panel-btn { display: inline-flex; align-items: center; gap: 5px; }
.rail-foot .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--success); }
.rail-foot .dot.off { background: var(--danger); }
`;
