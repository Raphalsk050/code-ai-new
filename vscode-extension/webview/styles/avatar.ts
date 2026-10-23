import { ANXIOUS_LOOP_S, bakeAnxious, ROUND, WIDE } from "../avatar-anxious";
import { bakeIdle, IDLE_LOOP_S } from "../avatar-rig";
import { AVATAR_COLOR_VALUE, AVATAR_COLORS } from "../avatar-skin";

// The theme's accent unless one was picked: the glyph is drawn in its own
// colour, so the choice only has to be that colour.
const COLOR_STYLE = AVATAR_COLORS.filter((color) => color !== "theme")
  .map((color) => `.agent-av[data-color=${color}] { color: ${AVATAR_COLOR_VALUE[color]}; }`)
  .join("\n");

// ---- idle ----
// The motion is not written here. avatar-rig.ts acts the idle out with a
// physical rig and bakes the run into the keyframes at the end of this block;
// between two of them the browser only has to draw a straight line.
const IDLE_STYLE = `
.agent-av-breath { transform-origin: 12px 20.4px; }
.agent-av[data-pose=idle] .agent-av-breath { animation: av-i-breathe 3.4s cubic-bezier(.37,0,.63,1) infinite alternate; }
.agent-av[data-pose=idle] .agent-av-eyes { animation: av-i-gaze ${IDLE_LOOP_S}s linear infinite; }
.agent-av[data-pose=idle] .agent-av-eye { transform-origin: 50% 60%; animation: av-i-eye-l ${IDLE_LOOP_S}s linear infinite; }
.agent-av[data-pose=idle] .agent-av-eye:nth-child(2) { animation-name: av-i-eye-r; }
.agent-av[data-pose=idle] .agent-av-rig { animation: av-i-head ${IDLE_LOOP_S}s linear infinite; }
.agent-av[data-pose=idle] .agent-av-leaf { animation: av-i-leaf ${IDLE_LOOP_S}s linear infinite; }
/* Ears and a hat's tail are not a leaf: each is thrown its own way. */
.agent-av[data-skin=easter][data-pose=idle] .agent-av-leaf { animation-name: av-i-ears; }
.agent-av[data-pose=idle] .agent-av-leaf.agent-av-hat-tail { animation-name: av-i-tail; }
/* Each avatar starts somewhere else in the loop (--av-phase, set per element):
   several of them falling idle at once would otherwise blink as one. */
.agent-av[data-pose=idle] .agent-av-eyes,
.agent-av[data-pose=idle] .agent-av-eye,
.agent-av[data-pose=idle] .agent-av-rig,
.agent-av[data-pose=idle] .agent-av-leaf { animation-delay: var(--av-phase, 0s); }
@keyframes av-i-breathe { from { transform: scale(1, 1); } to { transform: scale(1.016, .972); } }
${bakeIdle()}
`;

// ---- anxious ----
// Acted by the same rig, to another script (avatar-anxious.ts). Two things
// are not in the bake: the shiver, too fast for keyframes, and the drop.
const ANXIOUS_STYLE = `
.agent-av[data-pose=anxious] .agent-av-eyes { animation: av-a-gaze ${ANXIOUS_LOOP_S}s linear infinite; }
.agent-av[data-pose=anxious] .agent-av-eye { transform-origin: 50% 60%; animation: av-a-eye-l ${ANXIOUS_LOOP_S}s linear infinite; }
.agent-av[data-pose=anxious] .agent-av-eye:nth-child(2) { animation-name: av-a-eye-r; }
.agent-av[data-pose=anxious] .agent-av-rig { animation: av-a-head ${ANXIOUS_LOOP_S}s linear infinite; }
.agent-av[data-pose=anxious] .agent-av-leaf { animation: av-a-leaf ${ANXIOUS_LOOP_S}s linear infinite; }
.agent-av[data-skin=easter][data-pose=anxious] .agent-av-leaf { animation-name: av-a-ears; }
.agent-av[data-pose=anxious] .agent-av-leaf.agent-av-hat-tail { animation-name: av-a-tail; }
/* --av-nerves is how wound up it is, 0 to 1. The floating one counts the keys
   (avatar-nerves.ts); the others have no such count and are simply tense. */
.agent-av[data-pose=anxious] .agent-av-breath { animation: av-a-shiver .08s linear infinite alternate; }
@keyframes av-a-shiver {
  from { transform: translate(calc(var(--av-nerves, .7) * -.15px), 0); }
  to { transform: translate(calc(var(--av-nerves, .7) * .15px), 0); }
}
/* The drop comes once it has waited a while: the mark's own flat fill, kept
   off the face by a ring of the ground's colour. */
.agent-av-drop { fill: currentColor; }
.agent-av-drop-ring { fill: var(--bg); stroke: var(--bg); stroke-width: 1.1; stroke-linejoin: round; }
.agent-av-sweat { transform-box: fill-box; transform-origin: 50% 20%; }
.agent-av[data-pose=anxious] .agent-av-fx-sweat { opacity: calc((var(--av-nerves, 1) - .5) * 5); }
.agent-av[data-pose=anxious] .agent-av-sweat { animation: av-a-drop 2.6s infinite; }
@keyframes av-a-drop {
  0% { transform: translate(0,0) scale(.3); opacity: 0; animation-timing-function: cubic-bezier(.3,1.5,.6,1); }
  12% { transform: translate(0,0) scale(1); opacity: 1; }
  32% { transform: translate(.05px,.35px) scale(1); opacity: 1; animation-timing-function: cubic-bezier(.55,0,.9,.6); }
  80% { transform: translate(1.25px,4.7px) scale(.92,1.14); opacity: 1; }
  90%, 100% { transform: translate(1.3px,5.3px) scale(.7,1.25); opacity: 0; }
}
${bakeAnxious()}
`;

export const AVATAR_STYLE = `
.agent-av { display: block; flex: none; overflow: visible; }
.agent-av * { transform-box: view-box; }
.agent-av-rig { transform-origin: 12px 20.4px; }
.agent-av-leaf { transform-origin: 12.6px 3.9px; }
.agent-av-eye { transform-box: fill-box; transform-origin: center; }
.agent-av-lens { transform-origin: 18.3px 15.6px; }
.agent-av-ring { stroke: var(--bg); fill: none; }
/* Glasses have no motion of their own: the rig and the breath carry them, and the
   look and gaze they share with the eyes (.agent-av-look, .agent-av-eyes) turn them. */
.agent-av-glasses { fill: none; stroke-linecap: round; stroke-linejoin: round; }
.agent-av-glasses-ring { stroke: var(--bg); }
.agent-av-glasses-frame { stroke: currentColor; }
.agent-av-glasses-tint { fill: currentColor; fill-opacity: .24; }
.brand-live .agent-av-glasses-ring { stroke: var(--bg-rail); }
.agent-av-fx { opacity: 0; transition: opacity var(--dur-base, .2s) ease; }
.agent-av-key { opacity: .4; }
.agent-av-dot { transform-box: fill-box; transform-origin: center; }

.agent-av[data-pose] .agent-av-eye { animation: av-blink 4.2s infinite; }
@keyframes av-blink { 0%,91%,97%,100% { transform: scaleY(1); } 94% { transform: scaleY(.12); } }

.agent-av[data-pose=search] .agent-av-eyes { animation: av-s-gaze 2.6s cubic-bezier(.45,0,.2,1) infinite; }
.agent-av[data-pose=search] .agent-av-rig { animation: av-s-tilt 2.6s cubic-bezier(.45,0,.2,1) infinite; }
.agent-av[data-pose=search] .agent-av-leaf { animation: av-s-leaf 2.6s cubic-bezier(.45,0,.2,1) infinite; }
.agent-av[data-pose=search] .agent-av-fx-search { opacity: 1; }
.agent-av[data-pose=search] .agent-av-lens { animation: av-s-lens 2.6s cubic-bezier(.45,0,.2,1) infinite; }
@keyframes av-s-gaze { 0%,100% { transform: translate(0,0); } 15%,40% { transform: translate(-1.6px,.2px); } 55%,80% { transform: translate(1.6px,.2px); } }
@keyframes av-s-tilt { 0%,100% { transform: rotate(0); } 15%,40% { transform: rotate(-3deg); } 55%,80% { transform: rotate(3deg); } }
@keyframes av-s-leaf { 0%,100% { transform: rotate(0); } 20%,42% { transform: rotate(9deg); } 60%,82% { transform: rotate(-9deg); } }
@keyframes av-s-lens { 0%,100% { transform: translate(0,0) rotate(0); } 17%,40% { transform: translate(-2.2px,-.6px) rotate(-8deg); } 57%,80% { transform: translate(.9px,-.3px) rotate(6deg); } }

.agent-av[data-pose=type] .agent-av-eyes { animation: av-t-gaze 2.4s infinite; }
.agent-av[data-pose=type] .agent-av-rig { animation: av-t-bob .26s ease-in-out infinite alternate; }
.agent-av[data-pose=type] .agent-av-leaf { animation: av-t-leaf .52s ease-in-out infinite alternate; }
.agent-av[data-pose=type] .agent-av-fx-type { opacity: 1; }
.agent-av[data-pose=type] .agent-av-key { animation: av-press .62s infinite; }
.agent-av[data-pose=type] .agent-av-key:nth-child(2) { animation-duration: .74s; animation-delay: -.21s; }
.agent-av[data-pose=type] .agent-av-key:nth-child(3) { animation-duration: .53s; animation-delay: -.37s; }
.agent-av[data-pose=type] .agent-av-key:nth-child(4) { animation-duration: .68s; animation-delay: -.09s; }
@keyframes av-t-gaze { 0% { transform: translate(-.9px,1.3px); } 30% { transform: translate(-.2px,1.3px); } 60% { transform: translate(.5px,1.3px); } 86% { transform: translate(1px,1.3px); } 100% { transform: translate(-.9px,1.3px); } }
@keyframes av-t-bob { from { transform: translateY(0); } to { transform: translateY(.45px); } }
@keyframes av-t-leaf { from { transform: rotate(-4deg); } to { transform: rotate(5deg); } }
@keyframes av-press { 0%,30%,100% { transform: translateY(0); opacity: .4; } 8% { transform: translateY(.5px); opacity: 1; } }

.agent-av[data-pose=think] .agent-av-eye { animation-duration: 5.6s; }
.agent-av[data-pose=think] .agent-av-eyes { animation: av-k-gaze 4.8s cubic-bezier(.45,0,.2,1) infinite; }
.agent-av[data-pose=think] .agent-av-rig { animation: av-k-tilt 4.8s cubic-bezier(.45,0,.2,1) infinite; }
.agent-av[data-pose=think] .agent-av-leaf { animation: av-k-leaf 2.4s ease-in-out infinite alternate; }
.agent-av[data-pose=think] .agent-av-fx-think { opacity: 1; }
.agent-av[data-pose=think] .agent-av-dot { animation: av-bubble 1.8s ease-in-out infinite; }
.agent-av[data-pose=think] .agent-av-dot:nth-child(2) { animation-delay: .25s; }
.agent-av[data-pose=think] .agent-av-dot:nth-child(3) { animation-delay: .5s; }
@keyframes av-k-gaze { 0%,45%,100% { transform: translate(1.1px,-1.5px); } 55%,85% { transform: translate(-.6px,-1.7px); } }
@keyframes av-k-tilt { 0%,45%,100% { transform: rotate(3deg); } 55%,85% { transform: rotate(-1.5deg); } }
@keyframes av-k-leaf { from { transform: rotate(-6deg); } to { transform: rotate(6deg); } }
@keyframes av-bubble { 0%,100% { transform: scale(.55); opacity: .25; } 40% { transform: scale(1); opacity: 1; } }

.avatar-live { position: relative; color: var(--accent); background: none; }
.avatar-live .agent-av { position: absolute; left: -3px; top: -6px; }
.spark .agent-av { display: inline-block; }
.brand-live { position: relative; }
.brand-live .agent-av { position: absolute; left: -1px; top: -4px; }
.brand-live .agent-av-ring { stroke: var(--bg-rail); }
.brand-live .agent-av-drop-ring { fill: var(--bg-rail); stroke: var(--bg-rail); }

${IDLE_STYLE}
${ANXIOUS_STYLE}
/* ---- inertia: what a movement asks of the body (avatar-motion.ts sets these) ---- */
.agent-av-inertia {
  transform-origin: 12px 20.4px;
  transform: rotate(var(--av-tilt, 0deg)) scale(var(--av-sx, 1), var(--av-sy, 1));
}
.agent-av-leaf-lag { transform-origin: 12.6px 3.9px; transform: rotate(var(--av-leaf-lag, 0deg)); }
.agent-av-look { transform: translate(var(--av-look-x, 0px), var(--av-look-y, 0px)); }

/* ---- acts: what a script asks of it (avatar-acts.ts; the floating one sets these) ---- */
/* Its eyes are the script's while it acts: there is no pose to blink them. */
.av-float[data-act] .agent-av-eye {
  transform-origin: 50% 60%; transform: scale(1, var(--av-lid, 1)); opacity: calc(1 - 1.6 * var(--av-glad, 0));
}
.agent-av-glad {
  fill: none; stroke: #000; stroke-width: 1.3; stroke-linecap: round; opacity: var(--av-glad, 0);
  transform-origin: 12px 12.9px; transform: scaleY(calc(.3 + .7 * var(--av-glad, 0)));
}
.av-float[data-act] .agent-av-fx-sweat { opacity: var(--av-sweat, 0); transition: none; }
.av-float[data-act] .agent-av-sweat { animation: av-a-drop 2.6s infinite; }
/* What stands beside it: sparks, stars, a heart, a mark. Each is put where it is, frame by frame. */
.av-float-fx { position: absolute; inset: 0; overflow: visible; pointer-events: none; fill: currentColor; }
.av-float-fx > g > * { opacity: 0; }
.av-float-heart { fill: #e85aad; }
/* Asleep, nothing asks for a frame: the breathing and the zs are these two loops. */
.av-float[data-asleep] .agent-av-breath { animation: av-i-breathe 3.8s cubic-bezier(.37,0,.63,1) infinite alternate; }
.av-float-zs path { fill: none; stroke: currentColor; stroke-width: .55; stroke-linecap: round; stroke-linejoin: round; }
.av-float[data-asleep] .av-float-zs path { animation: av-z 2.6s linear infinite; }
.av-float[data-asleep] .av-float-zs path:nth-child(2) { animation-delay: .87s; }
.av-float[data-asleep] .av-float-zs path:nth-child(3) { animation-delay: 1.74s; }
@keyframes av-z {
  0% { transform: translate(19.6px, 3.2px) scale(.55); opacity: 0; }
  25% { opacity: .8; }
  60% { opacity: .9; }
  100% { transform: translate(24.4px, -5.4px) scale(1.4); opacity: 0; }
}

/* ---- colours ---- */
${COLOR_STYLE}

/* ---- skins ---- */
/* The face, the eyes and the flat fill are the mark and stay. A skin changes
   what is on the head and adds at most two colours, taken from the theme
   where the theme has one. */
.agent-av {
  --av-pumpkin: #ee8732;
  --av-ember: #ffd166;
  /* Plain first: Chromium before 111 has no color-mix(), and a colour that
     does not resolve would leave the fur in the glyph's own. */
  --av-snow: #ececec;
  --av-snow-shade: #cdd2d9;
  --av-blush: #f2a0bd;
}
@supports (color: color-mix(in srgb, red, blue)) {
  .agent-av {
    --av-snow: color-mix(in srgb, var(--fg) 12%, #fff);
    --av-snow-shade: color-mix(in srgb, var(--av-snow) 80%, #5d6f86);
  }
}
.agent-av[data-skin=halloween] .agent-av-face { fill: var(--av-pumpkin); }
.agent-av[data-skin=halloween] .agent-av-stem { fill: var(--success); }
.agent-av-stump { fill: #a45e20; fill: color-mix(in srgb, var(--av-pumpkin) 55%, #4a2c0a); }
.agent-av-glow { fill: var(--av-ember); }
.agent-av[data-pose] .agent-av-glow { animation: av-flicker 3.1s infinite; }
/* A shade, not a colour: black over whatever the rind is. */
.agent-av-lobes { fill: #000; fill-opacity: .11; }
@keyframes av-flicker { 0%,16%,24%,54%,66%,100% { opacity: 1; } 20% { opacity: .78; } 58% { opacity: .64; } 62% { opacity: .9; } }
.agent-av-hat { fill: var(--danger); }
.agent-av-hat-fold { fill: #000; fill-opacity: .16; }
.agent-av-snow { fill: var(--av-snow); }
.agent-av-snow-shade { fill: var(--av-snow-shade); }
.agent-av-band { fill: none; stroke: var(--av-snow); stroke-width: 3.1; stroke-linecap: round; }
.agent-av-band.agent-av-band-shade { stroke: var(--av-snow-shade); }
.agent-av-hat-set { transform-origin: 12px 7px; transform: rotate(var(--av-hat-tilt, 0deg)); }
/* The tail turns where it folds over the hat, not where the leaf grew. It
   hangs where the leaf stood, so what trails behind a movement is the other
   way round, and less of it. */
.agent-av-leaf.agent-av-hat-tail { transform-origin: 8.4px -1px; }
.agent-av-leaf-lag.agent-av-hat-lag {
  transform-origin: 8.4px -1px;
  transform: rotate(calc(var(--av-leaf-lag, 0deg) * -.5));
}
.agent-av-blush { fill: var(--av-blush); }

/* ---- floating ---- */
/* Loose over the panel with nothing under it but its shadow. Only the face
   takes the pointer: the rest of its box lets a click through to the panel. */
/* Right-click on the avatar: its size. Outside its box, which tilts and squashes. */
.av-size-menu {
  position: fixed; z-index: 40; box-sizing: border-box; padding: 8px 10px 10px;
  display: flex; flex-direction: column; gap: 7px; pointer-events: auto;
  color: var(--vscode-foreground, #ccc); font-size: 12px;
  background: var(--vscode-menu-background, var(--vscode-editorWidget-background, #252526));
  border: 1px solid var(--vscode-menu-border, var(--vscode-panel-border, #3c3c3c)); border-radius: 8px;
  box-shadow: 0 6px 18px rgba(0, 0, 0, .35);
}
.av-size-title { display: flex; align-items: center; font-weight: 600; }
.av-size-value { margin-left: auto; font-weight: 400; color: var(--vscode-descriptionForeground, #9d9d9d); font-variant-numeric: tabular-nums; }
.av-size-presets { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; }
.av-size-menu button {
  font: inherit; font-size: 11px; color: inherit; cursor: pointer; padding: 3px 2px; white-space: nowrap;
  background: transparent; border: 1px solid var(--vscode-panel-border, #3c3c3c); border-radius: 5px;
}
.av-size-menu button:hover:not(:disabled) { background: var(--vscode-list-hoverBackground, rgba(127,127,127,.16)); }
.av-size-menu button.on { border-color: var(--accent); color: var(--accent); }
.av-size-menu button:disabled { opacity: .45; cursor: default; }
.av-size-menu input[type=range] { width: 100%; margin: 0; accent-color: var(--accent); }

.av-float {
  position: absolute; left: 0; top: 0; z-index: 2;
  color: var(--accent); pointer-events: none;
  -webkit-user-select: none; user-select: none; will-change: transform;
}
.av-float .agent-av {
  filter: drop-shadow(0 3px 5px var(--shadow));
  transition: transform var(--dur-base) var(--ease-spring), filter var(--dur-base) var(--ease-out);
}
.agent-av-back { fill: var(--bg); }
.av-float-grip {
  position: absolute; left: 13.5%; top: 24%; width: 66%; height: 66%; border-radius: 50%;
  pointer-events: auto; cursor: grab; touch-action: none;
}
.av-float[data-held] .av-float-grip { cursor: grabbing; }
.av-float[data-held] .agent-av { transform: scale(1.06); filter: drop-shadow(0 10px 12px var(--shadow)); }
.av-float-grip:focus-visible { outline: 1px solid var(--focus); outline-offset: 3px; }
/* Watching the pointer, the eyes are its to move: the idle's own glances stand down. */
.av-float[data-watching] .agent-av[data-pose=idle] .agent-av-eyes { animation: none; }
/* Typed at, they are on the caret, and so do the anxious loop's. */
.av-float[data-rapt] .agent-av[data-pose=anxious] .agent-av-eyes { animation: none; }
/* Its seat on the home screen, where the mark stands when it does not float. */
.spark-dock { height: 62px; }

/* Hidden, or left alone with nothing running (index.tsx sets it): every loop
   holds where it is, and picks up from there. */
:root[data-still] .agent-av *, :root[data-still] .av-float-fx *, :root[data-still] .av-float-zs path {
  animation-play-state: paused !important;
}

@media (prefers-reduced-motion: reduce) {
  .agent-av *, .av-float-fx * { animation: none !important; }
  .agent-av[data-pose=search] .agent-av-eyes { transform: translate(-1.6px,.2px); }
  .agent-av[data-pose=type] .agent-av-eyes { transform: translate(0,1.3px); }
  .agent-av[data-pose=think] .agent-av-eyes { transform: translate(1.1px,-1.5px); }
  .agent-av[data-pose=anxious] .agent-av-eyes { transform: translate(0,1.25px); }
  .agent-av[data-pose=anxious] .agent-av-eye { transform: scale(${ROUND},${WIDE}); }
}
`;
