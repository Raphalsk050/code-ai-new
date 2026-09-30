// Motion layer: durations, easings, keyframes and the press/hover feedback that
// is pure CSS. Anything that needs measurement lives in motion.ts instead.
//
// Chromium 102 (VS Code 1.74) has no `linear()` easing and no View Transitions,
// so springs are overshooting cubic-beziers and transitions are explicit.

export const MOTION_STYLE = `
:root {
  --dur-fast: 120ms;
  --dur-base: 200ms;
  --dur-slow: 320ms;
  --ease-out: cubic-bezier(.22, 1, .36, 1);
  --ease-in: cubic-bezier(.4, 0, 1, 1);
  --ease-spring: cubic-bezier(.34, 1.4, .52, 1);
}

@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes fade-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes rise-in {
  from { opacity: 0; transform: translateY(8px) scale(.985); }
  to { opacity: 1; transform: none; }
}
@keyframes slide-in-right {
  from { opacity: 0; transform: translateX(14px); }
  to { opacity: 1; transform: none; }
}
@keyframes sheen {
  from { background-position: -160px 0; }
  to { background-position: 220px 0; }
}

/* ---- press + hover feedback ---- */
button, .pressable {
  transition:
    background-color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    transform var(--dur-fast) var(--ease-spring),
    opacity var(--dur-fast) var(--ease-out);
}
button:active:not(:disabled), .pressable:active { transform: scale(.96); }
button:not(:disabled), .pressable { position: relative; overflow: hidden; }
.ripple {
  position: absolute;
  border-radius: 50%;
  background: currentColor;
  pointer-events: none;
  opacity: 0;
}
button:focus-visible, .pressable:focus-visible, [tabindex]:focus-visible {
  outline: 1px solid var(--vscode-focusBorder, var(--accent));
  outline-offset: 1px;
}

/* ---- entrances ---- */
.block, .notice, .tool, .thinking { animation: rise-in var(--dur-base) var(--ease-out) both; }
/* A new block rises with its first item already in it; that item rising again
   inside would double the motion. */
.block-body > :first-child { animation: none; }

/* Where a click on the pinned request landed. A shadow, not padding, so the
   highlight reaches past the text without moving it. */
@keyframes landed {
  from {
    background-color: color-mix(in srgb, var(--accent) 26%, transparent);
    box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 26%, transparent);
  }
  to { background-color: transparent; box-shadow: 0 0 0 4px transparent; }
}
.user-text.flash { border-radius: 3px; animation: landed 1.1s var(--ease-out); }
.panel-card { animation: slide-in-right var(--dur-base) var(--ease-out) both; }
.fade-in { animation: fade-in var(--dur-base) var(--ease-out) both; }

/* Streaming text should not restage on every delta: only the first paint of a
   bubble animates, and the caret rides the last line. */
.assistant-text .markdown > *:last-child::after {
  content: "";
  display: none;
}
.streaming .markdown > *:last-child::after {
  display: inline-block;
  width: 7px; height: 1em;
  margin-left: 2px;
  vertical-align: text-bottom;
  background: var(--accent);
  opacity: .8;
  animation: pulse 1s steps(2, start) infinite;
}

/* ---- skeletons ---- */
.skeleton {
  border-radius: 6px;
  background: linear-gradient(90deg,
    var(--surface) 0%,
    var(--vscode-editor-background, rgba(127,127,127,.12)) 40%,
    var(--surface) 80%);
  background-size: 220px 100%;
  animation: sheen 1.1s linear infinite;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 1ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 1ms !important;
  }
  button:active:not(:disabled), .pressable:active { transform: none; }
}
`;
