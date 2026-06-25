// Webview stylesheet. Leans on VSCode theme variables so it blends with the
// user's editor theme (light or dark), with a few tuned accents.

export const STYLE = `
:root {
  --accent: var(--vscode-textLink-foreground, #3794ff);
  --surface: var(--vscode-editorWidget-background, rgba(127,127,127,0.08));
  --border: var(--vscode-panel-border, rgba(127,127,127,0.25));
  --muted: var(--vscode-descriptionForeground, rgba(127,127,127,0.9));
  --radius: 10px;
}

* { box-sizing: border-box; }
html, body, #root { height: 100%; }
body {
  margin: 0;
  font-family: var(--vscode-font-family);
  font-size: 13px;
  line-height: 1.55;
  color: var(--vscode-foreground);
  background: var(--vscode-editor-background);
}

.app { display: flex; flex-direction: column; height: 100vh; }

/* ---- top bar ---- */
.statusbar {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border);
  background: var(--vscode-editor-background);
  font-size: 12px;
}
.statusbar .brand { display: flex; align-items: center; gap: 7px; font-weight: 600; }
.statusbar .brand .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }
.statusbar .brand .dot.busy { animation: pulse 1.1s ease-in-out infinite; }
.statusbar .meta { margin-left: auto; display: flex; align-items: center; gap: 10px; color: var(--muted); }
.statusbar .meta .pill {
  padding: 2px 8px; border: 1px solid var(--border); border-radius: 999px;
}
.perm { display: inline-flex; align-items: center; gap: 6px; }
.perm > span { text-transform: uppercase; letter-spacing: .05em; font-size: 10.5px; opacity: .8; }
.perm select {
  font-family: inherit; font-size: 11.5px; color: var(--vscode-foreground);
  background: var(--vscode-dropdown-background, var(--surface));
  border: 1px solid var(--vscode-dropdown-border, var(--border));
  border-radius: 6px; padding: 3px 6px; cursor: pointer;
}
.perm select:focus { outline: 1px solid var(--accent); }

/* ---- transcript ---- */
.transcript { flex: 1; overflow-y: auto; padding: 18px 0 8px; }
.transcript-inner { max-width: 820px; margin: 0 auto; padding: 0 18px; }
.empty {
  margin: 64px auto; max-width: 420px; text-align: center; color: var(--muted);
}
.empty .spark { color: var(--accent); margin-bottom: 10px; }
.empty h2 { font-size: 15px; margin: 0 0 6px; color: var(--vscode-foreground); }

/* ---- message rows ---- */
.row { display: flex; gap: 12px; padding: 12px 0; }
.row + .row { border-top: 1px solid color-mix(in srgb, var(--border) 50%, transparent); }
.avatar {
  flex: none; width: 26px; height: 26px; border-radius: 7px;
  display: flex; align-items: center; justify-content: center;
  color: #fff;
}
.avatar-user { background: color-mix(in srgb, var(--accent) 75%, #000 0%); }
.avatar-assistant {
  background: linear-gradient(135deg, var(--accent), color-mix(in srgb, var(--accent) 40%, #a855f7));
}
.row-body { flex: 1; min-width: 0; }
.row-name { font-weight: 600; font-size: 12px; margin-bottom: 2px; opacity: 0.9; }
.row-content { min-width: 0; }
.user-text { white-space: pre-wrap; word-wrap: break-word; }

/* ---- markdown ---- */
.markdown { word-wrap: break-word; }
.markdown > *:first-child { margin-top: 0; }
.markdown > *:last-child { margin-bottom: 0; }
.markdown p { margin: 0 0 10px; }
.markdown ul, .markdown ol { margin: 0 0 10px; padding-left: 22px; }
.markdown li { margin: 2px 0; }
.markdown h1, .markdown h2, .markdown h3 { margin: 16px 0 8px; line-height: 1.3; }
.markdown h1 { font-size: 1.35em; } .markdown h2 { font-size: 1.2em; } .markdown h3 { font-size: 1.05em; }
.markdown a { color: var(--accent); text-decoration: none; }
.markdown a:hover { text-decoration: underline; }
.markdown blockquote {
  margin: 0 0 10px; padding: 2px 12px; border-left: 3px solid var(--border); color: var(--muted);
}
.markdown :not(pre) > code {
  font-family: var(--vscode-editor-font-family, monospace); font-size: 0.92em;
  background: var(--surface); border: 1px solid var(--border);
  padding: 1px 5px; border-radius: 5px;
}
.markdown pre {
  margin: 0 0 10px; padding: 12px 14px; overflow-x: auto;
  background: var(--vscode-textCodeBlock-background, rgba(127,127,127,0.12));
  border: 1px solid var(--border); border-radius: var(--radius);
}
.markdown pre code {
  font-family: var(--vscode-editor-font-family, monospace); font-size: 12.5px;
  background: none; border: none; padding: 0;
}
.markdown table { border-collapse: collapse; margin: 0 0 10px; }
.markdown th, .markdown td { border: 1px solid var(--border); padding: 4px 9px; }

/* ---- working note ---- */
.working-note {
  color: var(--muted); font-style: italic; font-size: 12px;
  padding: 2px 0 2px 38px; white-space: pre-wrap;
}

/* ---- thinking ---- */
.thinking { margin: 4px 0 4px 38px; }
.thinking-head {
  display: inline-flex; align-items: center; gap: 6px;
  background: none; border: none; color: var(--muted); cursor: pointer;
  padding: 3px 0; font-size: 12px;
}
.thinking-head .chev { transition: transform .15s ease; }
.thinking.open .thinking-head .chev { transform: rotate(90deg); }
.thinking-body {
  margin-top: 4px; padding: 8px 12px; border-left: 2px solid var(--border);
  color: var(--muted); font-family: var(--vscode-editor-font-family, monospace);
  font-size: 12px; white-space: pre-wrap;
}

/* ---- tool cards ---- */
.tool {
  margin: 6px 0 6px 38px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--surface); overflow: hidden;
}
.tool-head { display: flex; align-items: center; gap: 8px; padding: 7px 11px; }
.tool-icon { display: flex; width: 15px; justify-content: center; }
.tool-done .tool-icon { color: var(--vscode-testing-iconPassed, #2ea043); }
.tool-failed .tool-icon { color: var(--vscode-errorForeground, #f85149); }
.tool-wrench { color: var(--muted); }
.tool-name { font-weight: 600; font-family: var(--vscode-editor-font-family, monospace); font-size: 12.5px; }
.tool-badge { font-size: 10.5px; padding: 1px 7px; border-radius: 999px; text-transform: uppercase; letter-spacing: .04em; }
.badge-running { color: var(--accent); background: color-mix(in srgb, var(--accent) 16%, transparent); }
.badge-done { color: var(--vscode-testing-iconPassed, #2ea043); background: color-mix(in srgb, #2ea043 14%, transparent); }
.badge-failed { color: var(--vscode-errorForeground, #f85149); background: color-mix(in srgb, #f85149 14%, transparent); }
.tool-detail-inline { color: var(--muted); font-size: 12px; margin-left: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tool-head .chev { margin-left: auto; color: var(--muted); transition: transform .15s ease; }
.tool-head .chev-open { transform: rotate(90deg); }
.tool-detail-block {
  margin: 0; padding: 9px 12px; border-top: 1px solid var(--border);
  font-family: var(--vscode-editor-font-family, monospace); font-size: 12px;
  white-space: pre-wrap; max-height: 280px; overflow: auto; color: var(--muted);
}

/* ---- notices ---- */
.notice {
  display: flex; align-items: center; gap: 7px; margin: 5px 0 5px 38px;
  font-size: 12px; color: var(--muted);
}
.notice-warning { color: var(--vscode-editorWarning-foreground, #d7a000); }
.notice-error { color: var(--vscode-errorForeground, #f85149); }
.notice-plan { color: var(--accent); }
.notice-permission { color: var(--muted); }

/* ---- approval modal ---- */
.modal-overlay {
  position: fixed; inset: 0; z-index: 50;
  display: flex; align-items: flex-end; justify-content: center;
  padding: 16px;
  background: color-mix(in srgb, #000 45%, transparent);
  backdrop-filter: blur(1.5px);
  animation: fade .12s ease;
}
.modal {
  width: 100%; max-width: 760px; max-height: 86vh; overflow-y: auto;
  padding: 16px 18px;
  border: 1px solid color-mix(in srgb, var(--accent) 50%, var(--border));
  border-radius: var(--radius);
  background: var(--vscode-editorWidget-background, var(--vscode-editor-background));
  box-shadow: 0 12px 40px rgba(0,0,0,0.4);
  animation: rise .14s ease;
}

/* ---- diff ---- */
.diff-wrap { margin-top: 12px; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.diff-path {
  padding: 6px 10px; font-family: var(--vscode-editor-font-family, monospace); font-size: 12px;
  color: var(--muted); background: var(--surface); border-bottom: 1px solid var(--border);
}
.diff {
  max-height: 46vh; overflow: auto;
  font-family: var(--vscode-editor-font-family, monospace); font-size: 12.5px; line-height: 1.5;
  background: var(--vscode-textCodeBlock-background, rgba(0,0,0,0.22));
}
.diff-empty { padding: 10px; color: var(--muted); }
.diff-hunk-head {
  padding: 2px 10px; color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  position: sticky; left: 0;
}
.diff-line { display: flex; white-space: pre; min-width: max-content; }
.diff-gutter {
  flex: none; width: 38px; padding: 0 6px; text-align: right;
  color: var(--muted); opacity: .65; user-select: none;
  border-right: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
}
.diff-sign { flex: none; width: 16px; text-align: center; user-select: none; opacity: .8; }
.diff-text { flex: 1; padding-right: 12px; }
.diff-add { background: color-mix(in srgb, #2ea043 18%, transparent); }
.diff-add .diff-sign { color: #3fb950; }
.diff-del { background: color-mix(in srgb, #f85149 16%, transparent); }
.diff-del .diff-sign { color: #f85149; }
.diff-ctx .diff-text { color: var(--muted); }
.approval-head { display: flex; align-items: center; gap: 8px; font-size: 13.5px; }
.deny-form { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.deny-form label { font-size: 12px; color: var(--muted); }
.deny-form textarea {
  resize: vertical; min-height: 60px; padding: 8px 10px; border-radius: 8px;
  color: var(--vscode-input-foreground); background: var(--vscode-input-background);
  border: 1px solid var(--vscode-input-border, var(--border));
  font-family: inherit; font-size: 13px; line-height: 1.5;
}
.deny-form textarea:focus { outline: none; border-color: var(--accent); }
.deny-hint { font-size: 11px; color: var(--muted); text-align: right; }
@keyframes fade { from { opacity: 0; } }
@keyframes rise { from { opacity: 0; transform: translateY(8px); } }
.approval-args {
  margin: 9px 0 0; padding: 8px 11px; border-radius: 7px;
  background: var(--vscode-textCodeBlock-background, rgba(0,0,0,0.25));
  font-family: var(--vscode-editor-font-family, monospace); font-size: 12px;
  white-space: pre-wrap; word-break: break-all;
}
.approval-reason { margin-top: 8px; color: var(--muted); font-size: 12px; }
.approval-actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }

/* ---- composer ---- */
.composer-wrap { border-top: 1px solid var(--border); padding: 12px 18px 14px; }
.composer {
  max-width: 820px; margin: 0 auto; display: flex; align-items: flex-end; gap: 8px;
  border: 1px solid var(--border); border-radius: 12px; padding: 8px 8px 8px 12px;
  background: var(--vscode-input-background);
  transition: border-color .15s ease;
}
.composer:focus-within { border-color: var(--accent); }
.composer textarea {
  flex: 1; resize: none; max-height: 180px; min-height: 22px;
  border: none; outline: none; background: transparent;
  color: var(--vscode-input-foreground);
  font-family: inherit; font-size: 13px; line-height: 1.5; padding: 2px 0;
}
.composer textarea::placeholder { color: var(--muted); }
.hint { max-width: 820px; margin: 6px auto 0; color: var(--muted); font-size: 11px; text-align: right; }

/* ---- buttons ---- */
button { font-family: inherit; cursor: pointer; }
.btn-primary, .btn-ghost, .btn-deny, .icon-btn {
  display: inline-flex; align-items: center; gap: 6px;
  border-radius: 7px; padding: 6px 12px; font-size: 12px; border: 1px solid transparent;
}
.btn-primary { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
.btn-primary:hover { background: var(--vscode-button-hoverBackground); }
.btn-ghost {
  background: transparent; color: var(--vscode-foreground);
  border-color: var(--border);
}
.btn-ghost:hover { background: var(--surface); }
.btn-deny { background: transparent; color: var(--vscode-errorForeground, #f85149); border-color: color-mix(in srgb, #f85149 50%, transparent); }
.btn-deny:hover { background: color-mix(in srgb, #f85149 12%, transparent); }
.send-btn {
  flex: none; width: 32px; height: 32px; border-radius: 8px; border: none;
  display: flex; align-items: center; justify-content: center;
  background: var(--vscode-button-background); color: var(--vscode-button-foreground);
}
.send-btn:disabled { opacity: .4; cursor: default; }
.send-btn.stop { background: var(--vscode-errorForeground, #f85149); }

/* ---- animations ---- */
.caret {
  display: inline-block; width: 7px; height: 1.05em; vertical-align: text-bottom;
  margin-left: 2px; background: var(--accent); border-radius: 1px;
  animation: blink 1s steps(2) infinite;
}
.spinner {
  display: inline-block; width: 12px; height: 12px; border-radius: 50%;
  border: 2px solid color-mix(in srgb, var(--accent) 30%, transparent);
  border-top-color: var(--accent); animation: spin .7s linear infinite;
}
.typing { display: flex; align-items: center; gap: 5px; color: var(--muted); }
.typing span {
  width: 6px; height: 6px; border-radius: 50%; background: currentColor; opacity: .5;
  animation: bounce 1.2s infinite;
}
.typing span:nth-child(2) { animation-delay: .15s; }
.typing span:nth-child(3) { animation-delay: .3s; }
.typing em { margin-left: 6px; font-style: normal; font-size: 12px; }

@keyframes spin { to { transform: rotate(360deg); } }
@keyframes blink { 50% { opacity: 0; } }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
@keyframes bounce { 0%,60%,100% { transform: translateY(0); opacity:.4; } 30% { transform: translateY(-4px); opacity:1; } }

/* ---- highlight.js (theme-agnostic accents) ---- */
.hljs-comment, .hljs-quote { color: var(--muted); font-style: italic; }
.hljs-keyword, .hljs-selector-tag, .hljs-built_in, .hljs-name, .hljs-tag { color: #c586c0; }
.hljs-string, .hljs-title, .hljs-section, .hljs-attribute, .hljs-literal, .hljs-template-tag, .hljs-template-variable, .hljs-type, .hljs-addition { color: #ce9178; }
.hljs-number, .hljs-symbol, .hljs-bullet, .hljs-link { color: #b5cea8; }
.hljs-function .hljs-title, .hljs-title.function_ { color: #dcdcaa; }
.hljs-attr, .hljs-variable, .hljs-property { color: #9cdcfe; }
.hljs-meta { color: #569cd6; }
.hljs-deletion { color: #f85149; }
.hljs-emphasis { font-style: italic; }
.hljs-strong { font-weight: bold; }
`;
