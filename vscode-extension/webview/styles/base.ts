// Webview stylesheet. Leans on VSCode theme variables so it blends with the
// user's editor theme (light or dark), with a few tuned accents.

export const STYLE = `

* { box-sizing: border-box; }
html, body, #root { height: 100%; }
body {
  margin: 0;
  font-family: var(--font-ui);
  font-size: var(--fs-body);
  line-height: 1.55;
  color: var(--fg);
  background: var(--bg);
}

.app { position: relative; display: flex; flex-direction: column; height: 100vh; }

/* ---- top bar ---- */
/* The workspace's git branch, beside the title: quiet, and cut short when narrow. */
.branch-chip {
  display: inline-flex; align-items: center; gap: 4px; min-width: 0; max-width: 32vw;
  padding: 1px 7px; border: 1px solid var(--border); border-radius: 999px;
  font-size: var(--fs-label); font-weight: 400; color: var(--fg-muted);
}
.branch-chip > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.branch-chip svg { flex: none; }
/* A newer release is out. Loud enough to be seen, small enough to ignore. */
.update-btn {
  display: inline-flex; align-items: center; gap: 5px; flex: none;
  padding: 2px 9px; border-radius: 999px; cursor: pointer;
  font-size: var(--fs-label); font-weight: 600; white-space: nowrap;
  color: var(--button-fg);
  background: var(--button);
  border: 1px solid transparent;
}
.update-btn:hover:not(:disabled) { background: var(--button-hover); }
.update-btn:disabled { opacity: .85; cursor: default; }
.update-btn .spinner { width: 9px; height: 9px; border-width: 1.5px; }
.update-failed {
  color: var(--danger); background: transparent;
  border-color: color-mix(in srgb, var(--danger) 60%, transparent);
}
/* No login yet for the releases host: an invitation, quieter than an update. */
.update-login, .update-signingIn {
  color: var(--accent); background: transparent;
  border-color: color-mix(in srgb, var(--accent) 55%, transparent);
}
.update-login:hover:not(:disabled) {
  background: color-mix(in srgb, var(--accent) 14%, transparent);
}
/* ---- transcript ---- */
.transcript { flex: 1; overflow-y: auto; padding: 0 0 8px; }
/* The top padding lives here, not on the scroller, so the pin's sticky anchor
   sits flush with the scroller's top edge from the first pixel. */
.transcript-inner { max-width: 820px; margin: 0 auto; padding: 18px 18px 0; }
/* ---- message blocks ---- */
/* A run of messages from one side shares one quiet box, so where a request
   ends and the agent's work on it begins is never in doubt. The user's hug the
   right edge like a chat; the agent's take the width their work needs. */
.block {
  margin: 0 0 12px; padding: 9px 12px 11px; min-width: 0;
  border: 1px solid var(--border); border-radius: var(--radius-lg);
  transition: border-color var(--dur-slow) var(--ease-out), background-color var(--dur-slow) var(--ease-out);
}
/* The turn is over and ended on an answer: its box turns green, so "it is all
   done" needs no reading. Only the last one - the next request takes it away. */
.block-agent.block-done {
  border-color: var(--success);
  border-color: color-mix(in srgb, var(--success) 65%, var(--border));
  background-color: color-mix(in srgb, var(--success) 7%, transparent);
}
.block-user {
  width: fit-content; max-width: min(85%, 620px); margin-left: auto;
  background: rgba(127,127,127,.06);
  background: color-mix(in srgb, var(--accent) 7%, transparent);
  border-color: color-mix(in srgb, var(--accent) 30%, var(--border));
}
.block-head {
  display: flex; align-items: center; gap: 7px; margin-bottom: 6px;
  font-size: var(--fs-small); font-weight: 600; opacity: .9;
}
.block-user .block-head { flex-direction: row-reverse; }
.block-body { min-width: 0; }
.block-body > :first-child { margin-top: 0; }
.block-body > :last-child { margin-bottom: 0; }
.avatar {
  flex: none; width: 20px; height: 20px; border-radius: var(--radius-md);
  display: flex; align-items: center; justify-content: center;
  color: #fff;
}
.avatar-user { background: color-mix(in srgb, var(--accent) 75%, #000 0%); }
.avatar-assistant {
  background: var(--accent);
}
.avatar-photo { object-fit: cover; }
/* No picture, but a sign-in can fetch it: the icon is the button. */
.avatar-sign-in { border: none; padding: 0; font: inherit; cursor: pointer; }
.avatar-sign-in:hover:not(:disabled) { box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 45%, transparent); }
.avatar-sign-in:disabled { cursor: progress; }
.avatar-pending { animation: pulse 1.1s ease-in-out infinite; }
/* One line per send: each is what went to the model in one go. */
.user-text { white-space: pre-wrap; word-wrap: break-word; }
.user-text + .user-text {
  margin-top: 7px; padding-top: 7px;
  border-top: 1px solid color-mix(in srgb, var(--accent) 22%, transparent);
}
.assistant-text { margin: 6px 0; }

/* ---- pinned request ---- */
/* Sticky with no height at the top of the scroller: the pin lies over the
   transcript instead of taking room, so showing or hiding it never moves a
   line under the reader. */
.prompt-pin-anchor { position: sticky; top: 0; height: 0; z-index: 4; }
/* Hung from the top edge over the content column: flush, so no cut-off line
   peeks above it, and the side gutters it leaves are empty anyway. */
.prompt-pin-anchor > .prompt-pin {
  position: absolute; top: 0; left: 18px; right: 18px; max-width: 784px; margin: 0 auto;
  display: flex; align-items: center; gap: 8px;
  padding: 6px 11px 7px; border-radius: 0 0 8px 8px;
  font: inherit; font-size: var(--fs-small); line-height: 1.45; text-align: left;
  color: var(--fg);
  background: var(--bg);
  border: 1px solid var(--border); border-top: none;
  border-color: color-mix(in srgb, var(--accent) 30%, var(--border));
  box-shadow: 0 6px 12px -6px var(--shadow);
  cursor: pointer;
  opacity: 0; transform: translateY(-6px); visibility: hidden; pointer-events: none;
  transition: opacity var(--dur-fast) var(--ease-out), transform var(--dur-fast) var(--ease-out),
    visibility 0s linear var(--dur-fast);
}
.prompt-pin-anchor > .prompt-pin.shown {
  opacity: 1; transform: none; visibility: visible; pointer-events: auto;
  transition: opacity var(--dur-fast) var(--ease-out), transform var(--dur-fast) var(--ease-out);
}
.prompt-pin-anchor > .prompt-pin:hover { border-color: color-mix(in srgb, var(--accent) 60%, var(--border)); }
.prompt-pin .avatar { border-radius: var(--radius-sm); }
.prompt-pin-text {
  flex: 1; min-width: 0; overflow: hidden; white-space: pre-wrap; overflow-wrap: anywhere;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
}

/* ---- markdown ---- */
.markdown { word-wrap: break-word; }
/* One .md-block per top-level element; margins collapse through it. */
.markdown > .md-block:first-child > :first-child { margin-top: 0; }
.markdown > .md-block:last-child > :last-child { margin-bottom: 0; }
.markdown p { margin: 0 0 10px; }
.markdown ul, .markdown ol { margin: 0 0 10px; padding-left: 22px; }
.markdown li { margin: 2px 0; }
.markdown h1, .markdown h2, .markdown h3 { margin: 16px 0 8px; line-height: 1.3; }
.markdown h1 { font-size: 1.35em; } .markdown h2 { font-size: 1.2em; } .markdown h3 { font-size: 1.05em; }
.markdown a { color: var(--accent); text-decoration: none; }
.markdown a:hover { text-decoration: underline; }
.markdown blockquote {
  margin: 0 0 10px; padding: 2px 12px; border-left: 3px solid var(--border); color: var(--fg-muted);
}
.markdown :not(pre) > code {
  font-family: var(--font-mono); font-size: 0.92em;
  background: var(--surface); border: 1px solid var(--border);
  padding: 1px 5px; border-radius: var(--radius-sm);
}
.markdown pre {
  margin: 0 0 10px; padding: 12px 14px; overflow-x: auto;
  background: var(--code-bg);
  border: 1px solid var(--border); border-radius: var(--radius-lg);
  line-height: 1.5;
}
.markdown pre.has-line-numbers { display: flex; padding: 12px 0; }
.markdown pre code {
  font-family: var(--font-mono); font-size: var(--fs-small);
  background: none; border: none; padding: 0; line-height: inherit;
}
.markdown pre.has-line-numbers code { flex: 1; padding: 0 14px; min-width: max-content; }
.code-gutter {
  flex: none; padding: 0 12px 0 14px; text-align: right; white-space: pre;
  font-family: var(--font-mono); font-size: var(--fs-small);
  line-height: inherit; color: var(--fg-muted); opacity: .55; user-select: none;
  position: sticky; left: 0;
  background: var(--code-bg);
  border-right: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
}
.markdown table { border-collapse: collapse; margin: 0 0 10px; }
.markdown th, .markdown td { border: 1px solid var(--border); padding: 4px 9px; }
/* ---- formulas ---- */
.markdown .katex { font-size: 1.12em; }
/* A long one scrolls inside its own line instead of widening the message. */
.math-display { margin: 0 0 10px; padding: 4px 0; overflow-x: auto; overflow-y: hidden; text-align: center; }
.math-display > .katex-display { margin: 0; }
.math-inline > .katex-display { margin: 6px 0; overflow-x: auto; overflow-y: hidden; }
/* Not LaTeX, or not finished yet: the source, as written. */
.markdown code.math-source { white-space: pre-wrap; color: var(--fg-muted); }
.math-pending { text-align: left; }

/* ---- working note ---- */
.working-note {
  color: var(--fg-muted); font-style: italic; font-size: var(--fs-small);
  padding: 2px 0; white-space: pre-wrap;
}

/* ---- thinking ---- */
.thinking { margin: 4px 0; }
.thinking-head {
  display: inline-flex; align-items: center; gap: 6px;
  background: none; border: none; color: var(--fg-muted); cursor: pointer;
  padding: 3px 0; font-size: var(--fs-small);
}
.thinking-head .chev { transition: transform .15s ease; }
.thinking.open .thinking-head .chev { transform: rotate(90deg); }
.thinking-body {
  margin-top: 4px; padding: 8px 12px; border-left: 2px solid var(--border);
  color: var(--fg-muted); font-family: var(--font-mono);
  font-size: var(--fs-small); white-space: pre-wrap;
  /* Reasoning runs to thousands of words. Capped and scrolled while it streams,
     so a long one does not push the answer off the screen. */
  max-height: 260px; overflow-y: auto; overscroll-behavior: contain;
}
.thinking.live .thinking-body { border-left-color: var(--accent); }
/* A pulse on the header is the only thing that says the block is still filling
   when its text has scrolled past the fold. */
.thinking-live {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--accent);
  animation: thinking-pulse 1.4s ease-in-out infinite;
}
@keyframes thinking-pulse { 0%, 100% { opacity: .25; } 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) {
  .thinking-live { animation: none; opacity: .8; }
}

/* ---- notices ---- */
.notice {
  display: flex; align-items: center; gap: 7px; margin: 5px 0;
  font-size: var(--fs-small); color: var(--fg-muted);
}
/* A listing (/rules, /agents) keeps its lines; long paths wrap. */
.notice > span { white-space: pre-wrap; overflow-wrap: anywhere; }
.notice-warning { color: var(--warn); }
.notice-error { color: var(--danger); }
.notice-plan { color: var(--accent); }
.notice-permission { color: var(--fg-muted); }

/* ---- diff ---- */
.diff-wrap { margin-top: 12px; border: 1px solid var(--border); border-radius: var(--radius-lg); overflow: hidden; }
.diff-path {
  padding: 6px 10px; font-family: var(--font-mono); font-size: var(--fs-small);
  color: var(--fg-muted); background: var(--surface); border-bottom: 1px solid var(--border);
}
.diff {
  max-height: 46vh; overflow: auto;
  font-family: var(--font-mono); font-size: var(--fs-small); line-height: 1.5;
  background: var(--code-bg);
}
.diff-empty { padding: 10px; color: var(--fg-muted); }
.diff-hunk-head {
  padding: 2px 10px; color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  position: sticky; left: 0;
}
.diff-line { display: flex; white-space: pre; min-width: max-content; }
.diff-gutter {
  flex: none; width: 38px; padding: 0 6px; text-align: right;
  color: var(--fg-muted); opacity: .65; user-select: none;
  border-right: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
}
.diff-sign { flex: none; width: 16px; text-align: center; user-select: none; opacity: .8; }
.diff-text { flex: 1; padding-right: 12px; }
.diff-add { background: color-mix(in srgb, var(--success) 18%, transparent); }
.diff-add .diff-sign { color: var(--success); }
.diff-del { background: color-mix(in srgb, var(--danger) 16%, transparent); }
.diff-del .diff-sign { color: var(--danger); }
.diff-ctx .diff-text { color: var(--fg-muted); }
.deny-form { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.deny-form label { font-size: var(--fs-small); color: var(--fg-muted); }
.deny-form textarea {
  resize: vertical; min-height: 60px; padding: 8px 10px; border-radius: var(--radius-lg);
  color: var(--fg); background: var(--surface-input);
  border: 1px solid var(--border-strong);
  font-family: inherit; font-size: var(--fs-body); line-height: 1.5;
}
.deny-form textarea:focus { outline: none; border-color: var(--accent); }
/* ---- buttons ---- */
button { font-family: inherit; cursor: pointer; }
.btn-primary, .btn-ghost, .btn-deny, .icon-btn {
  display: inline-flex; align-items: center; gap: 6px;
  border-radius: var(--radius-md); padding: 6px 12px; font-size: var(--fs-small); border: 1px solid transparent;
}
.btn-primary { background: var(--button); color: var(--button-fg); }
.btn-primary:hover { background: var(--button-hover); }
.btn-ghost {
  background: transparent; color: var(--fg);
  border-color: var(--border);
}
.btn-ghost:hover { background: var(--surface); }
.btn-deny { background: transparent; color: var(--danger); border-color: color-mix(in srgb, var(--danger) 50%, transparent); }
.btn-deny:hover { background: color-mix(in srgb, var(--danger) 12%, transparent); }
/* ---- topbar icon buttons ---- */
.icon-btn {
  padding: 4px; width: 28px; height: 26px; justify-content: center;
  background: transparent; color: var(--fg-muted); border: 1px solid transparent;
  border-radius: var(--radius-md);
}
.icon-btn:hover { background: var(--surface); color: var(--fg); }
.icon-btn.danger:hover {
  color: var(--danger);
  background: color-mix(in srgb, var(--danger) 12%, transparent);
}
.topbar-actions { display: inline-flex; align-items: center; gap: 2px; margin-left: 2px; }

/* ---- home / history screen ---- */
.home { display: flex; flex-direction: column; height: 100%; overflow-y: auto; }
.home-hero { text-align: center; padding: 40px 18px 20px; color: var(--fg-muted); }
.home-hero .spark { color: var(--accent); margin-bottom: 10px; }
.home-hero h2 { font-size: var(--fs-title); margin: 0 0 4px; color: var(--fg); }
.home-sub { font-size: var(--fs-small); margin-bottom: 16px; }
.home-new { margin: 0 auto; font-size: var(--fs-small); padding: 8px 16px; }
.home-history { max-width: 820px; width: 100%; margin: 0 auto; padding: 8px 18px 24px; }
.home-history-head {
  display: flex; align-items: center; gap: 7px; color: var(--fg-muted);
  font-size: var(--fs-label); text-transform: uppercase; letter-spacing: .06em;
  margin: 12px 0 8px; padding-bottom: 6px; border-bottom: 1px solid var(--border);
}
.home-empty { color: var(--fg-muted); font-size: var(--fs-small); padding: 10px 2px; }
.history-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.history-item {
  display: flex; align-items: center; gap: 10px; padding: 9px 11px;
  border: 1px solid var(--border); border-radius: var(--radius-lg); cursor: pointer;
  background: var(--surface); transition: border-color .12s ease, background .12s ease;
}
.history-item:hover { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); }
.history-item-main { flex: 1; min-width: 0; }
.history-title {
  font-size: var(--fs-body); font-weight: 500; color: var(--fg);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.history-meta { font-size: var(--fs-label); color: var(--fg-muted); margin-top: 2px; }
.history-del { flex: none; opacity: 0; }
.history-item:hover .history-del { opacity: 1; }

/* ---- slash command menu ---- */
.composer-stack { position: relative; max-width: 820px; margin: 0 auto; }
.cmd-menu {
  position: absolute; left: 0; right: 0; bottom: calc(100% + 6px);
  max-height: 240px; overflow-y: auto; z-index: 20;
  border: 1px solid var(--border); border-radius: var(--radius-lg);
  background: var(--surface);
  box-shadow: 0 8px 28px rgba(0,0,0,0.35); padding: 4px;
}
.cmd-item {
  display: flex; align-items: baseline; gap: 10px; padding: 6px 9px;
  border-radius: var(--radius-md); cursor: pointer;
}
.cmd-item.active { background: color-mix(in srgb, var(--accent) 16%, transparent); }
.cmd-name {
  font-family: var(--font-mono); font-size: var(--fs-small);
  color: var(--fg); flex: none; min-width: 92px;
}
.cmd-desc { font-size: var(--fs-small); color: var(--fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ---- mode hint (refactor/explain placeholder) ---- */
.mode-hint {
  margin: 56px auto; max-width: 380px; text-align: center; color: var(--fg-muted);
  padding: 0 20px;
}
.mode-hint .spark { color: var(--accent); margin-bottom: 12px; }
.mode-hint h2 { font-size: var(--fs-title); margin: 0 0 10px; color: var(--fg); }
.mode-hint-line { margin: 6px 0; font-size: var(--fs-small); line-height: 1.5; }

/* ---- home settings affordance ---- */
.home-topbar { display: flex; justify-content: flex-end; align-items: center; gap: 8px; padding: 8px 12px 0; }

/* ---- refactor panel ---- */
.refactor { max-width: 820px; margin: 0 auto; padding: 16px 18px 28px; }
.refactor-head { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.refactor-title {
  display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: var(--fs-body);
  color: var(--fg);
}
.refactor-title svg { color: var(--accent); }
.refactor-analyze { margin-left: auto; font-size: var(--fs-small); }
.refactor-target {
  font-family: var(--font-mono); font-size: var(--fs-small);
  color: var(--fg-muted); margin-bottom: 14px;
}
.refactor-status { display: flex; align-items: center; gap: 8px; color: var(--fg-muted); font-size: var(--fs-small); padding: 10px 0; }
.refactor-error {
  color: var(--danger); font-size: var(--fs-small); padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--danger) 40%, transparent); border-radius: var(--radius-lg);
  background: color-mix(in srgb, var(--danger) 8%, transparent);
}
.refactor-empty {
  display: flex; align-items: center; gap: 8px; color: var(--fg-muted);
  font-size: var(--fs-small); padding: 16px 0;
}
.refactor-empty svg { color: var(--success); }
.refactor-cards { display: flex; flex-direction: column; gap: 10px; }
.refactor-card {
  display: flex; align-items: stretch; gap: 12px; padding: 12px 13px;
  border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--surface);
}
.refactor-card-body { flex: 1; min-width: 0; }
.refactor-card-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.refactor-card-title { font-weight: 600; font-size: var(--fs-body); color: var(--fg); }
.impact {
  font-size: var(--fs-label); text-transform: uppercase; letter-spacing: .04em;
  padding: 1px 7px; border-radius: 999px; margin-left: auto;
}
.impact-high { color: var(--danger); background: color-mix(in srgb, var(--danger) 14%, transparent); }
.impact-medium { color: var(--warn); background: color-mix(in srgb, var(--warn) 14%, transparent); }
.impact-low { color: var(--success); background: color-mix(in srgb, var(--success) 14%, transparent); }
.refactor-card-rationale { font-size: var(--fs-small); color: var(--fg-muted); line-height: 1.5; }
.refactor-card-action { display: flex; align-items: center; flex: none; }
.refactor-card-action button { font-size: var(--fs-small); white-space: nowrap; }
.refactor-all {
  margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border);
  display: flex; justify-content: flex-end;
}
.refactor-all button { font-size: var(--fs-small); }

/* ---- explain panel ---- */
.explain { max-width: 820px; margin: 0 auto; padding: 16px 18px 28px; }
.explain-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.explain-head > svg { color: var(--accent); }
.explain-title { font-weight: 600; font-size: var(--fs-body); color: var(--fg); }
.explain-toggle {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; padding: 0; border: none; border-radius: var(--radius-md);
  background: none; color: var(--fg-muted); cursor: pointer;
}
.explain-toggle:hover:not(:disabled) { background: var(--surface); color: var(--fg); }
.explain-toggle:disabled { opacity: .4; cursor: default; }
.explain-chevron { transform: rotate(0deg); transition: transform .15s ease; }
.explain-chevron.open { transform: rotate(90deg); }
.explain-spin { margin-left: auto; }
.explain-target {
  font-family: var(--font-mono); font-size: var(--fs-small);
  color: var(--fg-muted); margin: 0 0 12px 30px;
}
.explain-body { font-size: var(--fs-small); line-height: 1.5; }
.explain-empty { color: var(--fg-muted); font-size: var(--fs-small); padding: 6px 0; line-height: 1.6; }
.explain-status { display: flex; align-items: center; gap: 8px; color: var(--fg-muted); font-size: var(--fs-small); padding: 10px 0; }
.explain-error {
  color: var(--danger); font-size: var(--fs-small); padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--danger) 40%, transparent); border-radius: var(--radius-lg);
  background: color-mix(in srgb, var(--danger) 8%, transparent);
}

/* ---- animations ---- */
.spinner {
  display: inline-block; width: 12px; height: 12px; border-radius: 50%;
  border: 2px solid color-mix(in srgb, var(--accent) 30%, transparent);
  border-top-color: var(--accent); animation: spin .7s linear infinite;
}
.typing { display: flex; align-items: center; gap: 5px; color: var(--fg-muted); }
.typing span {
  width: 6px; height: 6px; border-radius: 50%; background: currentColor; opacity: .5;
  animation: bounce 1.2s infinite;
}
.typing span:nth-child(2) { animation-delay: .15s; }
.typing span:nth-child(3) { animation-delay: .3s; }
.typing em { margin-left: 6px; font-style: normal; font-size: var(--fs-small); }

@keyframes spin { to { transform: rotate(360deg); } }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
@keyframes bounce { 0%,60%,100% { transform: translateY(0); opacity:.4; } 30% { transform: translateY(-4px); opacity:1; } }

/* ---- highlight.js (theme-agnostic accents) ---- */
.hljs-comment, .hljs-quote { color: var(--fg-muted); font-style: italic; }
.hljs-keyword, .hljs-selector-tag, .hljs-built_in, .hljs-name, .hljs-tag { color: #c586c0; }
.hljs-string, .hljs-title, .hljs-section, .hljs-attribute, .hljs-literal, .hljs-template-tag, .hljs-template-variable, .hljs-type, .hljs-addition { color: #ce9178; }
.hljs-number, .hljs-symbol, .hljs-bullet, .hljs-link { color: #b5cea8; }
.hljs-function .hljs-title, .hljs-title.function_ { color: #dcdcaa; }
.hljs-attr, .hljs-variable, .hljs-property { color: #9cdcfe; }
.hljs-meta { color: #569cd6; }
.hljs-deletion { color: var(--danger); }
.hljs-emphasis { font-style: italic; }
.hljs-strong { font-weight: bold; }
`;
