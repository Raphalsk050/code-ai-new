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
  background: var(--vscode-sideBar-background, var(--vscode-editor-background));
}
.rail-btn {
  position: relative;
  width: 28px; height: 26px;
  display: flex; align-items: center; justify-content: center;
  background: transparent; border: none; border-radius: 6px;
  color: var(--muted); cursor: pointer;
}
.rail-btn:hover { background: var(--vscode-toolbar-hoverBackground, rgba(127,127,127,.16)); color: var(--vscode-foreground); }
.rail-btn.active { background: var(--vscode-list-activeSelectionBackground, rgba(55,148,255,.22)); color: var(--vscode-foreground); }
.rail-badge {
  position: absolute; top: 0; right: 0;
  min-width: 13px; height: 13px; padding: 0 3px;
  border-radius: 7px;
  background: var(--accent); color: var(--vscode-button-foreground, #fff);
  font-size: 9px; line-height: 13px; font-variant-numeric: tabular-nums;
}
.rail-dot {
  position: absolute; top: 3px; right: 4px;
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--ok);
}
.rail-sep { width: 22px; height: 1px; background: var(--border); margin: 4px 0; }

/* ---- docked column (md) ---- */
.rail-dock {
  /* Width comes from the component (the user drags it); this is the fallback
     for the first paint, before the stored value is read. */
  width: 236px; flex-shrink: 0;
  display: flex; flex-direction: column;
  min-height: 0;
  border-left: 1px solid var(--border);
  background: var(--vscode-sideBar-background, var(--vscode-editor-background));
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
  background: var(--vscode-sideBar-background, var(--vscode-editor-background));
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
  color: var(--vscode-foreground);
  font-size: 11px; letter-spacing: .07em; text-transform: uppercase;
  cursor: pointer; text-align: left;
}
.rail-section-head:hover { background: var(--vscode-list-hoverBackground, rgba(127,127,127,.12)); }
.rail-section-head .chev { transition: transform var(--dur-base) var(--ease-spring); color: var(--muted); }
.rail-section.open .rail-section-head .chev { transform: rotate(90deg); }
.rail-section-title { flex: 1; }
.rail-section-note { font-size: 10.5px; letter-spacing: 0; text-transform: none; color: var(--muted); }
.rail-section-body { padding: 0 12px 10px 12px; font-size: 12px; }
.rail-empty { color: var(--muted); font-size: 11.5px; padding: 2px 0 6px; }

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

/* Sits in the flow at the foot of an open section, so it moves with it. */
.rail-grip-y { height: 9px; margin: -4px 0 0; cursor: row-resize; }
.rail-grip-y::after { left: 12px; right: 12px; top: 4px; height: 1px; }

/* A dragged section scrolls its own content instead of growing the rail. */
.rail-section-body.sized { overflow-y: auto; }

.rail-foot {
  border-top: 1px solid var(--border);
  padding: 7px 12px;
  display: flex; align-items: center; gap: 7px;
  font-size: 11px; color: var(--muted);
}
.rail-foot .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ok); }
.rail-foot .dot.off { background: var(--vscode-editorError-foreground, #f14c4c); }
`;
