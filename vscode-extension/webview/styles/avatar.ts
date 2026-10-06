// ---- idle ----
// Timed the way it is thought about, in seconds, and turned into keyframes
// below. Three things hold all through it. The eyes lead, the head follows
// late and heavy, the leaf arrives last and swings to a stop. Nothing starts or
// stops together with anything else. And every arrival goes a little past and
// comes back, which is what reads as weight.

/** One pass of the idle, in seconds: long enough not to read as a loop. */
export const IDLE_LOOP_S = 12;
const IDLE = IDLE_LOOP_S;

/** At `at` seconds the part is at `transform`, and eases on to the next step as `ease` says. */
type Step = [at: number, transform: string, ease?: string];

// Chromium 102 has no linear(), so every curve is a cubic-bezier and the
// overshoots are keyframes of their own.
const SNAP = "cubic-bezier(.2,.75,.25,1)"; // a saccade: all its speed at the start
const SETTLE = "cubic-bezier(.35,0,.3,1)"; // coming back from having gone too far
const HEAVY = "cubic-bezier(.42,0,.16,1)"; // a mass getting under way, then easing in
const SWING = "cubic-bezier(.37,0,.63,1)"; // a pendulum, extreme to extreme
const SHUT = "cubic-bezier(.5,0,1,1)"; // a lid falling
const OPEN = "cubic-bezier(0,0,.25,1)"; // and lifting, slower than it fell

function timeline(name: string, steps: Step[]): string {
  const frames = steps.map(([at, transform, ease]) => {
    const percent = Math.round((at / IDLE) * 100000) / 1000;
    return `${percent}% { transform: ${transform};${ease ? ` animation-timing-function: ${ease};` : ""} }`;
  });
  return `@keyframes ${name} { ${frames.join(" ")} }`;
}

const look = (x: number, y = 0) => `translate(${x}px, ${y}px)`;
const lid = (wide: number, open: number) => `scale(${wide}, ${open})`;
const turn = (deg: number, x = 0, y = 0) => `rotate(${deg}deg) translate(${x}px, ${y}px)`;
const lean = (deg: number) => `rotate(${deg}deg)`;

/** A blink from `at`: shut fast, a beat closed, open slower and a touch too wide. */
function blink(at: number, wideFrom: number, wideTo = wideFrom, rest = 1): Step[] {
  return [
    [at, lid(wideFrom, rest), SHUT],
    [at + 0.075, lid((wideFrom + wideTo) / 2, 0.08)],
    [at + 0.115, lid(wideTo, 0.08), OPEN],
    [at + 0.265, lid(wideTo, 1.07), SETTLE],
    [at + 0.36, lid(wideTo, 1)],
  ];
}

// Where it looks: ahead, left, across to the right, back, then up at nothing.
// A glance is a saccade - there in a sixth of a second, a little past, back.
const GAZE = timeline("av-i-gaze", [
  [0, look(0)],
  [2.86, look(0), SETTLE],
  [2.92, look(0.12), SNAP],
  [3.07, look(-1.7, 0.05), SETTLE],
  [3.24, look(-1.55, 0.05)],
  [4.52, look(-1.55, 0.05), SETTLE],
  [4.6, look(-1.72, 0.05), SNAP],
  [4.8, look(1.75, 0.02), SETTLE],
  [4.98, look(1.6, 0.02)],
  [6.45, look(1.6, 0.02), SNAP],
  [6.62, look(-0.22), SETTLE],
  [6.8, look(0)],
  [9.8, look(0), SWING],
  [10.18, look(0.42, -0.78), SWING],
  [10.95, look(0.38, -0.74), SWING],
  [11.21, look(0, 0.04), SETTLE],
  [11.36, look(0)],
  [IDLE, look(0)],
]);

/**
 * One eye: its lids, and how wide it is drawn. People blink as they shift
 * their gaze, so the two big glances each hide inside a blink; the eye on the
 * side the head turns to is foreshortened, which is what makes a flat face
 * read as turning.
 */
function eye(name: string, lookingLeft: number, lookingRight: number): string {
  return timeline(name, [
    [0, lid(1, 1)],
    ...blink(2.9, 1, lookingLeft),
    ...blink(4.58, lookingLeft, lookingRight),
    [6.45, lid(lookingRight, 1), SETTLE],
    [6.65, lid(1, 1)],
    // Two in a row, the first cut short by the second.
    [8.3, lid(1, 1), SHUT],
    [8.37, lid(1, 0.08)],
    [8.4, lid(1, 0.08), OPEN],
    [8.53, lid(1, 0.94)],
    ...blink(8.58, 1),
    // Looking up opens them a little; the blink brings the gaze back down.
    [9.8, lid(1, 1), SWING],
    [10.18, lid(1, 1.05)],
    ...blink(10.97, 1, 1, 1.05),
    [IDLE, lid(1, 1)],
  ]);
}

// The head sets off some 80 ms after the eyes and takes three times as long.
const HEAD = timeline("av-i-head", [
  [0, turn(0)],
  [2.94, turn(0), SETTLE],
  [3.02, turn(0.5, 0.06, 0.05), HEAVY],
  [3.4, turn(-3, -0.39), SETTLE],
  [3.66, turn(-2.6, -0.34), SWING],
  [4.62, turn(-2.5, -0.33), SETTLE],
  [4.7, turn(-3, -0.38, 0.05), HEAVY],
  [5.14, turn(3.25, 0.42), SETTLE],
  [5.42, turn(2.8, 0.36), SWING],
  [6.5, turn(2.7, 0.35), HEAVY],
  [6.92, turn(-0.6, -0.07), SETTLE],
  [7.2, turn(0)],
  [9.88, turn(0), SWING],
  [10.4, turn(1.6, 0.1, -0.12), SWING],
  [11.02, turn(1.5, 0.1, -0.1), SWING],
  [11.5, turn(-0.2), SETTLE],
  [11.74, turn(0)],
  [IDLE, turn(0)],
]);

// The leaf is dragged the other way when the head sets off, then swings
// through and back, less each time. Between turns it barely stirs.
const LEAF = timeline("av-i-leaf", [
  [0, lean(0), SWING],
  [0.9, lean(-0.8), SWING],
  [2, lean(0.7), SWING],
  [3.02, lean(0.4), SWING],
  [3.2, lean(6.5), SWING],
  [3.52, lean(-6), SWING],
  [3.82, lean(3), SWING],
  [4.1, lean(-1.6), SWING],
  [4.36, lean(0.5), SWING],
  [4.7, lean(0.3), SWING],
  [4.92, lean(-8.5), SWING],
  [5.28, lean(7.5), SWING],
  [5.6, lean(-3.8), SWING],
  [5.9, lean(1.9), SWING],
  [6.18, lean(-0.8), SWING],
  [6.52, lean(0), SWING],
  [6.72, lean(5), SWING],
  [7.06, lean(-4.2), SWING],
  [7.36, lean(2), SWING],
  [7.64, lean(-0.9), SWING],
  [7.9, lean(0), SWING],
  [8.9, lean(0.8), SWING],
  [9.92, lean(-0.3), SWING],
  [10.2, lean(-3.2), SWING],
  [10.62, lean(2.2), SWING],
  [11.02, lean(-0.9), SWING],
  [11.34, lean(3), SWING],
  [11.62, lean(-1.6), SWING],
  [11.84, lean(0.5), SWING],
  [IDLE, lean(0)],
]);

const IDLE_STYLE = `
.agent-av-breath { transform-origin: 12px 20.4px; }
.agent-av[data-pose=idle] .agent-av-breath { animation: av-i-breathe 3.4s ${SWING} infinite alternate; }
.agent-av[data-pose=idle] .agent-av-eyes { animation: av-i-gaze ${IDLE}s ${SWING} infinite; }
.agent-av[data-pose=idle] .agent-av-eye { transform-origin: 50% 60%; animation: av-i-eye-l ${IDLE}s ${SWING} infinite; }
.agent-av[data-pose=idle] .agent-av-eye:nth-child(2) { animation-name: av-i-eye-r; }
.agent-av[data-pose=idle] .agent-av-rig { animation: av-i-head ${IDLE}s ${SWING} infinite; }
.agent-av[data-pose=idle] .agent-av-leaf { animation: av-i-leaf ${IDLE}s ${SWING} infinite; }
/* Each avatar starts somewhere else in the loop (--av-phase, set per element):
   several of them falling idle at once would otherwise blink as one. */
.agent-av[data-pose=idle] .agent-av-eyes,
.agent-av[data-pose=idle] .agent-av-eye,
.agent-av[data-pose=idle] .agent-av-rig,
.agent-av[data-pose=idle] .agent-av-leaf { animation-delay: var(--av-phase, 0s); }
@keyframes av-i-breathe { from { transform: scale(1, 1); } to { transform: scale(1.016, .972); } }
${GAZE}
${eye("av-i-eye-l", 0.86, 1.02)}
${eye("av-i-eye-r", 1.02, 0.86)}
${HEAD}
${LEAF}
`;

export const AVATAR_STYLE = `
.agent-av { display: block; flex: none; overflow: visible; }
.agent-av * { transform-box: view-box; }
.agent-av-rig { transform-origin: 12px 20.4px; }
.agent-av-leaf { transform-origin: 12.6px 3.9px; }
.agent-av-eye { transform-box: fill-box; transform-origin: center; }
.agent-av-lens { transform-origin: 18.3px 15.6px; }
.agent-av-ring { stroke: var(--bg); fill: none; }
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

${IDLE_STYLE}
/* ---- inertia: what a movement asks of the body (avatar-motion.ts sets these) ---- */
.agent-av-inertia {
  transform-origin: 12px 20.4px;
  transform: rotate(var(--av-tilt, 0deg)) scale(var(--av-sx, 1), var(--av-sy, 1));
}
.agent-av-leaf-lag { transform-origin: 12.6px 3.9px; transform: rotate(var(--av-leaf-lag, 0deg)); }
.agent-av-look { transform: translate(var(--av-look-x, 0px), var(--av-look-y, 0px)); }

/* ---- skins ---- */
/* The face, the eyes and the flat fill are the mark and stay. A skin changes
   what is on the head and adds at most two colours, taken from the theme
   where the theme has one. */
.agent-av {
  --av-pumpkin: #ee8732;
  --av-ember: #ffd166;
  --av-snow: color-mix(in srgb, var(--fg) 12%, #fff);
  --av-snow-shade: color-mix(in srgb, var(--av-snow) 80%, #5d6f86);
  --av-blush: #f2a0bd;
}
.agent-av[data-skin=halloween] .agent-av-face { fill: var(--av-pumpkin); }
.agent-av[data-skin=halloween] .agent-av-stem { fill: var(--success); }
.agent-av-stump { fill: color-mix(in srgb, var(--av-pumpkin) 55%, #4a2c0a); }
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
/* On the same glass as the composer, so it reads as part of the panel and
   stays legible over whatever is under it. */
.av-float {
  position: absolute; left: 0; top: 0; z-index: 30;
  display: grid; place-items: center;
  color: var(--accent); cursor: grab; touch-action: none;
  -webkit-user-select: none; user-select: none; will-change: transform;
}
.av-float-puck {
  position: absolute; inset: 0; border-radius: 50%;
  background: var(--surface-input-glass);
  border: 1px solid var(--border-strong);
  -webkit-backdrop-filter: blur(12px); backdrop-filter: blur(12px);
  box-shadow: 0 8px 18px -8px var(--shadow);
  transition: transform var(--dur-base) var(--ease-spring), box-shadow var(--dur-base) var(--ease-out),
    border-color var(--dur-base) var(--ease-out);
}
.av-float .agent-av { position: relative; pointer-events: none; }
.av-float .agent-av-ring { stroke: var(--surface-input); }
.av-float[data-busy] .av-float-puck { border-color: color-mix(in srgb, var(--accent) 55%, var(--border-strong)); }
.av-float[data-held] { cursor: grabbing; }
.av-float[data-held] .av-float-puck { transform: scale(1.08); box-shadow: 0 16px 28px -12px var(--shadow); }
.av-float:focus-visible { outline: none; }
.av-float:focus-visible .av-float-puck { outline: 1px solid var(--focus); outline-offset: 2px; }
.av-float[data-bare] .av-float-puck {
  background: none; border-color: transparent; box-shadow: none;
  -webkit-backdrop-filter: none; backdrop-filter: none;
}

@media (prefers-reduced-motion: reduce) {
  .agent-av * { animation: none !important; }
  .agent-av[data-pose=search] .agent-av-eyes { transform: translate(-1.6px,.2px); }
  .agent-av[data-pose=type] .agent-av-eyes { transform: translate(0,1.3px); }
  .agent-av[data-pose=think] .agent-av-eyes { transform: translate(1.1px,-1.5px); }
}
`;
