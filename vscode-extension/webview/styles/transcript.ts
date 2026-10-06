// The transcript's parts: tool cards and groups, the approval card, the turn
// footer, the banner over the composer and the empty conversation.

export const TRANSCRIPT_STYLE = `
/* ---- tool cards ---- */
.tool {
  margin: 6px 0; border: 1px solid var(--border); border-radius: var(--radius-md);
  background: var(--surface); overflow: hidden;
}
.tool-head { display: flex; align-items: center; gap: var(--space-3); padding: 6px 10px; min-width: 0; }
.tool-head.expandable { cursor: pointer; }
.tool-icon { flex: none; display: inline-grid; place-items: center; width: 16px; height: 16px; }
.tool-icon .spinner { width: 12px; height: 12px; border-width: 2px; }
.tool-done .tool-icon { color: var(--success); }
.tool-failed .tool-icon { color: var(--danger); }
.tool-kind { flex: none; color: var(--fg-muted); }
.tool-name { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; font-family: var(--font-mono); font-size: var(--fs-small); color: var(--fg-strong); }
.tool-end { margin-left: auto; flex: none; display: inline-flex; align-items: center; gap: var(--space-2); }
.tool-time { font-size: var(--fs-small); color: var(--fg-muted); font-variant-numeric: tabular-nums; }
.chev { color: var(--fg-muted); transition: transform var(--dur-fast) var(--ease-out); }
.chev-open { transform: rotate(90deg); }
.tool-badge { flex: none; font-size: var(--fs-label); line-height: 16px; padding: 0 7px; border-radius: var(--radius-pill); }
.badge-running { color: var(--accent); background: var(--accent-soft); }
.badge-failed { color: var(--danger); background: var(--danger-soft); }
.badge-waiting { color: var(--warn); background: var(--warn-soft); }
/* The target and the result, lined up under the name. */
.tool-sub {
  display: flex; align-items: baseline; gap: var(--space-3); min-width: 0;
  padding: 0 10px 7px 54px; margin-top: -2px;
}
.tool-target {
  min-width: 0; flex: 0 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-family: var(--font-mono); font-size: var(--fs-small); color: var(--fg);
}
.tool-target-link {
  background: none; border: none; padding: 0; margin: 0; cursor: pointer; text-align: left;
  color: var(--accent); text-decoration: underline; text-decoration-color: transparent; text-underline-offset: 2px;
}
.tool-target-link:hover { text-decoration-color: currentColor; }
.tool-target-link:focus-visible { outline: 1px solid var(--focus); outline-offset: 2px; }
.tool-detail-inline { flex: 1 1 0; min-width: 40px; color: var(--fg-muted); font-size: var(--fs-small); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.line-delta { flex: none; font-family: var(--font-mono); font-size: var(--fs-small); font-variant-numeric: tabular-nums; }
.delta-add { color: var(--success); }
.delta-del { color: var(--danger); }
.tool-detail { border-top: 1px solid var(--border); }
.tool-detail-block {
  margin: 0; padding: 8px 12px; font-family: var(--font-mono); font-size: var(--fs-small);
  white-space: pre-wrap; overflow-wrap: anywhere; max-height: 280px; overflow: auto; color: var(--fg-muted);
}
.tool-actions { display: flex; gap: var(--space-2); padding: 0 12px 8px; }
.tool-running { border-color: var(--accent); }
.tool-running .tool-name { color: var(--accent); }
.tool-failed { border-color: var(--danger); background: var(--danger-soft); }
.tool-failed .tool-detail { border-top-color: color-mix(in srgb, var(--danger) 35%, transparent); }
.tool-failed .tool-detail-block { color: var(--fg); }
.btn-sm { padding: 3px 9px; font-size: var(--fs-small); gap: 5px; }

/* ---- a run of calls that went through, folded ---- */
.tool-group { margin: 6px 0; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface); }
.tool-group-head {
  display: flex; align-items: center; gap: var(--space-3); width: 100%; min-width: 0;
  padding: 6px 10px; border: none; background: none; color: var(--fg); font: inherit; text-align: left; cursor: pointer;
}
.tool-group-head .tool-icon { color: var(--success); }
.tool-group-label { flex: none; font-size: var(--fs-small); font-weight: 600; color: var(--fg-strong); }
.tool-group-names { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--font-mono); font-size: var(--fs-small); color: var(--fg-muted); }
.tool-group-head:focus-visible { outline: 1px solid var(--focus); outline-offset: -1px; border-radius: var(--radius-md); }
.tool-group-body { padding: 0 8px 2px; border-top: 1px solid var(--border); }
.tool-group-body > .tool { background: var(--bg); }

/* ---- turn footer ---- */
.turn-footer {
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px var(--space-4);
  margin: 8px 0 0; padding-top: 7px; border-top: 1px solid var(--border);
  font-size: var(--fs-small); color: var(--fg-muted); font-variant-numeric: tabular-nums;
}
.turn-footer > span { display: inline-flex; align-items: center; gap: 4px; min-width: 0; }
.turn-file { border: none; background: none; padding: 0; font: inherit; color: var(--accent); cursor: pointer; }
.turn-file:hover { text-decoration: underline; }
.turn-ok { color: var(--success); }
.turn-bad { color: var(--danger); }

/* ---- approval ---- */
.approval-card {
  margin: 10px 0; border: 1px solid var(--warn); border-radius: var(--radius-md);
  background: var(--surface); overflow: hidden;
  animation: rise-in var(--dur-slow) var(--ease-spring) both;
}
.approval-head {
  display: flex; align-items: center; gap: var(--space-3); padding: 7px 12px;
  background: var(--warn-soft); border-bottom: 1px solid var(--border);
  font-size: var(--fs-small); color: var(--fg-strong);
}
.approval-title { flex: 1 1 auto; min-width: 0; }
.approval-body { padding: 10px 12px 12px; }
.approval-reason { margin: 0 0 10px; font-size: var(--fs-small); color: var(--fg-muted); }
.approval-args {
  margin: 0 0 10px; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: var(--code-bg); font-family: var(--font-mono); font-size: var(--fs-small); line-height: 1.5;
  white-space: pre-wrap; overflow-wrap: anywhere;
}
.approval-card .diff-wrap { margin: 0 0 10px; border-radius: var(--radius-sm); }
.approval-card .diff-path { display: flex; align-items: center; gap: var(--space-2); }
/* In a narrow panel the file's name gives way, not the button: it is a label,
   on one line, in the interface's own face rather than the path's. */
.approval-card .diff-path > button { flex: none; white-space: nowrap; font-family: var(--font-ui); }
.diff-path-name { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--fg); }
.approval-card .diff { max-height: 240px; }
.diff-more { padding: 3px 10px; color: var(--fg-muted); font-family: var(--font-ui); border-top: 1px solid var(--border); }
.approval-actions { display: flex; gap: var(--space-2); flex-wrap: wrap; margin-top: 2px; }
.approval-actions .btn-ghost { border-color: var(--border-strong); }
.approval-actions .btn-deny { border-color: var(--danger); }
.key {
  display: inline-grid; place-items: center; min-width: 16px; height: 16px; padding: 0 4px;
  border-radius: 4px; font-family: var(--font-ui); font-size: var(--fs-label); line-height: 1;
  border: 1px solid currentColor; opacity: .7;
}

/* ---- banner over the composer ---- */
.banner {
  display: flex; align-items: center; gap: var(--space-3); margin: 0 0 var(--space-2);
  padding: 6px 8px 6px 12px; border: 1px solid; border-radius: var(--radius-md);
  font-size: var(--fs-small); color: var(--fg); background: var(--surface-input-glass);
  -webkit-backdrop-filter: blur(12px); backdrop-filter: blur(12px);
  animation: rise-in var(--dur-base) var(--ease-out) both;
}
.banner .spinner { width: 12px; height: 12px; border-width: 2px; }
.banner-text { flex: 1 1 auto; min-width: 0; }
.banner-note { color: var(--fg-muted); }
.banner-warn { border-color: var(--warn); }
.banner-warn > .codicon { color: var(--warn); }
.banner-info { border-color: var(--border-strong); }
.banner-danger { border-color: var(--danger); }
.banner-danger > .codicon { color: var(--danger); }

/* ---- empty conversation ---- */
.empty { margin: 48px auto 24px; max-width: 440px; text-align: center; color: var(--fg-muted); }
.empty .spark { color: var(--accent); margin-bottom: 10px; }
.empty h2 { font-size: var(--fs-title); margin: 0 0 6px; color: var(--fg-strong); }
.empty-sub { margin-bottom: var(--space-5); }
.empty-actions { display: grid; gap: var(--space-2); text-align: left; }
.empty-action {
  display: flex; align-items: center; gap: var(--space-3); width: 100%; padding: 9px 12px;
  border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface);
  color: var(--fg); font: inherit; text-align: left; cursor: pointer;
}
.empty-action:hover { border-color: var(--accent); }
.empty-action:focus-visible { outline: 1px solid var(--focus); outline-offset: 1px; }
.empty-action > .codicon { color: var(--accent); flex: none; }
.empty-action-text { display: flex; flex-direction: column; min-width: 0; }
.empty-action-label { color: var(--fg-strong); }
.empty-action-hint { font-size: var(--fs-small); color: var(--fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty-recent { margin-top: var(--space-5); text-align: left; }
.empty-recent-head { font-size: var(--fs-label); text-transform: uppercase; letter-spacing: .06em; margin: 0 0 4px 4px; }
.empty-recent-item {
  display: flex; align-items: center; gap: var(--space-3); width: 100%; padding: 6px 8px;
  border: none; border-radius: var(--radius-sm); background: none; color: var(--fg); font: inherit; text-align: left; cursor: pointer;
}
.empty-recent-item:hover { background: var(--hover); }
.empty-recent-item > .codicon { color: var(--fg-muted); flex: none; }
.empty-recent-title { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty-recent-when { flex: none; font-size: var(--fs-small); color: var(--fg-muted); }
`;
