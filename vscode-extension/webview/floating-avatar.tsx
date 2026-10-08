import * as React from "react";

import { type Acted, type ActName, Actor, type Effects, LAIR, type Manners, onCue, RESTING } from "./avatar-acts";
import { AgentAvatar } from "./agent-avatar";
import {
  type Body, type Bounds, clampTo, glide, leanFor, type Place, placeOf, pointOf, settled, type Spring, stepSpring,
} from "./avatar-motion";
import { type Felt, Nerves, onStroke, UNFELT } from "./avatar-nerves";
import type { AvatarPose } from "./avatar-pose";
import type { AvatarSkin } from "./avatar-skin";
import { caretPoint } from "./caret-point";
import { t } from "./i18n";

/** Its box. The face is six tenths of it: the agent in the room, not one more button. */
const DEFAULT_SIZE = 88;
/** Kept clear of the container's edges. */
const MARGIN = 8;
/** Its own corner is the bottom right: clear of the rail's strip, just above the composer. */
const HOME: Place = { right: true, bottom: true, x: 32, y: 6 };
/** What it does not go under unless it hides: beneath the composer's glass it can be neither seen nor picked up. */
const FLOOR = ".composer-wrap";
/** Going to where it sits on a screen: quicker than going home, and a little past. */
const DOCKING = { stiffness: 95, damping: 12 };
/** What an arrow key gives it, in px/s: a push, not a step. */
const NUDGE = 520;
/** Going home is a spring too, softer than the body's. */
const RETURN = { stiffness: 70, damping: 13 };
/** Where the middle of the face is in its box. */
const FACE_AT = { x: 0.464, y: 0.571 };
/** Watching the pointer: how far the eyes go, in the glyph's units, and how far the head leans. */
const WATCH = { x: 1.6, y: 1.2, tilt: 5 };
/** Livelier than the body's own: the eyes get there fast and a little past. */
const GLANCE = { stiffness: 190, damping: 15 };
/** As far as an eye goes and stays on the face, when more than one thing is asking. */
const EYE_MOST = { x: 2.2, y: 1.6 };

/** Wound up this far by the typing, it runs for it: some four seconds of steady keys. */
const BOLTS_AT = 0.75;
/** How soon a start at a key is over, in seconds. */
const FLINCH_S = 0.22;
/** Where it hides: behind the field the typing goes into. */
const LAIR_OF = ".composer";
/** Into a wall this fast, in px/s, and it sees stars. */
const DIZZY_AT = 1500;
/** No pointer and no key for this long, in ms, and it nods off; busy then, it is asked again later. */
const SLEEP_AFTER_MS = 3 * 60_000;
const SLEEP_RETRY_MS = 10_000;
/** A press this short, in ms, that went this little way, in px, was a poke and not a carry. */
const POKE = { ms: 300, px: 5 };
/** Stroked: the pointer turns back over its face this often, each time after this far, within this long. */
const PET = { turns: 3, px: 6, ms: 1500 };
/** Typing takes it from these; the rest it finishes first. */
const DROPPED_FOR_KEYS: ReadonlySet<ActName | null> = new Set<ActName>(["greets", "waits", "nods-off", "wakes"]);
const EVERYTHING: Manners = { greets: true, waits: true, cheers: true, sleeps: true, touch: true, hides: true };

/** A four-pointed spark, a five-pointed star, a heart and a z, each drawn about its own middle. */
const SPARK = "M0-1.7Q.3-.3 1.7 0Q.3.3 0 1.7Q-.3.3-1.7 0Q-.3-.3 0-1.7z";
const STAR = "M0-1.25l.37.82.9.1-.67.6.19.88L0 .7l-.79.45.19-.88-.67-.6.9-.1z";
const HEART = "M0 1.45C-2.5-.35-1.9-2-.8-2-.25-2 0-1.6 0-1.3 0-1.6.25-2 .8-2 1.9-2 2.5-.35 0 1.45z";
const ZED = "M-1-1H1L-1 1H1";
const SPARKS_AT: [x: number, y: number, after: number][] = [[4.2, 2.2, 0], [21.2, 1.2, 0.13], [24, 9.4, 0.27], [1.2, 10.2, 0.41]];

const bell = (u: number) => (u <= 0 || u >= 1 ? 0 : Math.sin(Math.PI * u));
const eased = (u: number) => {
  const c = Math.min(Math.max(u, 0), 1);
  return c * c * (3 - 2 * c);
};

/** Put one effect's parts where they are `time` seconds in; undefined puts them away. */
function show(group: Element, time: number | undefined, place: (part: SVGElement, index: number, time: number) => string): void {
  Array.from(group.children).forEach((child, index) => {
    const part = child as SVGElement;
    if (time !== undefined) part.setAttribute("transform", place(part, index, time));
    else if (part.style.opacity) part.style.opacity = "";
  });
}

/** What stands beside it this instant: the sparks, the stars, the mark and the heart, in the order they are drawn. */
function drawEffects(svg: SVGSVGElement, fx: Effects): void {
  const [sparks, stars, bang, heart] = Array.from(svg.children);
  show(sparks, fx.sparkles, (part, i, time) => {
    const [x, y, after] = SPARKS_AT[i];
    const size = bell((time - after) / 0.75);
    part.style.opacity = String(Math.min(1, size * 1.6));
    return `translate(${x} ${y}) rotate(${(time * 120).toFixed(0)}) scale(${size.toFixed(3)})`;
  });
  show(stars, fx.stars, (part, i, time) => {
    const turn = time * 2 * Math.PI * 0.9 + (i * 2 * Math.PI) / 3;
    part.style.opacity = (Math.min(1, time / 0.2) * (1 - eased((time - 1.5) / 0.6))).toFixed(2);
    return `translate(${(12 + 6.4 * Math.cos(turn)).toFixed(2)} ${(1 + 1.7 * Math.sin(turn)).toFixed(2)}) rotate(${(time * 200).toFixed(0)})`;
  });
  show(bang, fx.bang, (part, _i, time) => {
    part.style.opacity = (1 - eased((time - 0.8) / 0.25)).toFixed(2);
    const pop = eased(time / 0.14) * (1 + 0.28 * Math.sin(time * 26) * Math.max(0, 1 - time / 0.45));
    return `translate(22.2 -0.6) scale(${pop.toFixed(3)})`;
  });
  show(heart, fx.heart, (part, _i, time) => {
    part.style.opacity = (eased(time / 0.2) * (1 - eased((time - 1) / 0.6))).toFixed(2);
    const pop = eased(time / 0.2) * (1 + 0.12 * Math.sin(time * 9));
    return `translate(${(21 + 0.8 * Math.sin(time * 4)).toFixed(2)} ${(2.6 - 3.4 * time).toFixed(2)}) scale(${pop.toFixed(3)})`;
  });
}

/** The system was asked for less motion: no throw, no lean, no glide home. */
function wantsLessMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

interface Sim extends Body {
  held: boolean;
  /** Where in the avatar it was picked up. */
  grabX: number;
  grabY: number;
  /** Where the pointer wants it. */
  wantX: number;
  wantY: number;
  home: { x: number; y: number } | null;
  /** Where this screen has it sit, if it has a seat for it: it goes back there whenever let go. */
  dock: { x: number; y: number } | null;
  /** Where the pointer is, in the room's pixels; null while it is off the page. */
  gaze: { x: number; y: number } | null;
  watching: boolean;
  /** Whether the last frame had it on the move. */
  moved: boolean;
  /** Where it rests, as the room's size will not change it. */
  place: Place;
  tilt: Spring;
  leaf: Spring;
  lookX: Spring;
  lookY: Spring;
  sx: Spring;
  sy: Spring;
  /** What typing does to it, and what that came to on the last frame. */
  nerves: Nerves;
  felt: Felt;
  /** Where the typing is: the field, and its caret in the room's pixels, read again when `stale`. */
  field: HTMLTextAreaElement | null;
  caret: { x: number; y: number } | null;
  stale: boolean;
  /** What it does of its own accord, what that came to on the last frame, and which act the page was told of. */
  actor: Actor;
  acted: Acted;
  shown: ActName | null;
  asleep: boolean;
  /** Behind the composer, or on its way there or out: where it is then is not somewhere it was put. */
  lurking: boolean;
  /** Taken out from behind the composer: it does not run back while the same typing goes on. */
  rescued: boolean;
  /** The middle of the field it hides behind, across the room. */
  lairX: number | null;
  /** How hard the last keys made it start, while it hides. */
  flinch: number;
  /** The middle of the card it waits on, in the room's pixels. */
  card: { x: number; y: number } | null;
  /** It has yet to say hello: it does when it gets where it is going. */
  greeting: boolean;
  /** The press that may turn out to be a poke, and the strokes that may turn out to be petting. */
  pressed: { at: number; x: number; y: number } | null;
  petting: { x: number; way: number; turns: number; since: number };
  last: number;
  frame: number;
}

const still = (): Spring => ({ x: 0, v: 0 });
const within = (value: number, most: number) => Math.max(-most, Math.min(most, value));
const same = (a: Place, b: Place) => a.right === b.right && a.bottom === b.bottom && a.x === b.x && a.y === b.y;

/**
 * The avatar loose in the panel: picked up, dropped anywhere, thrown. Lives
 * inside a positioned container and never leaves it. Only the face takes the
 * pointer; the rest of its box lets a click through to what is under it.
 *
 * @param size its box, in px
 * @param start where it is first put; its own corner when left out
 * @param inertia off, it goes where the pointer goes and stops when let go
 * @param watch off, it does not look at the pointer
 * @param dock selector of the seat this screen keeps for it; while one is on
 *   screen it sits there, and goes back to its own place when there is none
 * @param scene anything that changes when the panel's layout does
 * @param manners what it may do of its own accord; all of it when left out
 * @param waitsOn selector of what waits on the user; while one is on screen it waits by it
 * @param onRest called with where it came to rest, for remembering it
 */
export function FloatingAvatar({
  pose,
  skin,
  size = DEFAULT_SIZE,
  start,
  inertia = true,
  watch = true,
  dock,
  scene = "",
  manners = EVERYTHING,
  waitsOn,
  onRest,
}: {
  pose: AvatarPose | null;
  skin?: AvatarSkin;
  size?: number;
  start?: Place;
  inertia?: boolean;
  watch?: boolean;
  dock?: string;
  scene?: string;
  manners?: Manners;
  waitsOn?: string;
  onRest?: (place: Place) => void;
}): JSX.Element {
  const ref = React.useRef<HTMLDivElement>(null);
  const effects = React.useRef<SVGSVGElement>(null);
  const [held, setHeld] = React.useState(false);
  const [act, setAct] = React.useState<ActName | null>(null);
  const sim = React.useRef<Sim>({
    x: 0, y: 0, vx: 0, vy: 0, held: false, grabX: 0, grabY: 0, wantX: 0, wantY: 0, home: null, dock: null, gaze: null, watching: false, moved: false,
    place: start ?? HOME,
    tilt: still(), leaf: still(), lookX: still(), lookY: still(), sx: { x: 1, v: 0 }, sy: { x: 1, v: 0 },
    nerves: new Nerves(), felt: UNFELT, field: null, caret: null, stale: false,
    actor: new Actor(), acted: RESTING, shown: null, asleep: false, lurking: false, rescued: false, lairX: null, flinch: 0, card: null,
    greeting: true, pressed: null, petting: { x: 0, way: 0, turns: 0, since: 0 },
    last: 0, frame: 0,
  });
  // At work it has its own things to look at; the pointer gets its eyes when it is idle.
  const live = React.useRef({ inertia, onRest, manners, pose, watch: watch && (pose === "idle" || pose === null) });
  live.current = { inertia, onRest, manners, pose, watch: watch && (pose === "idle" || pose === null) };

  // Asked for once per layout: reading offsetParent on every frame makes the browser lay the page out for it.
  const roomRef = React.useRef<HTMLElement | null>(null);
  const room = React.useCallback(
    () => (roomRef.current ??= (ref.current?.offsetParent as HTMLElement | null) ?? null),
    []
  );
  // How far down it may go, measured when the layout changes and not every frame.
  const floor = React.useRef<number | null>(null);
  const bounds = React.useCallback((): Bounds => {
    const box = room();
    const width = box?.clientWidth ?? window.innerWidth;
    const height = Math.min(box?.clientHeight ?? window.innerHeight, floor.current ?? Infinity);
    return { left: MARGIN, top: MARGIN, right: width - size - MARGIN, bottom: height - size - MARGIN };
  }, [room, size]);
  /** Where the screen's seat puts it: its face on the middle of the seat. */
  const seat = React.useCallback((): { x: number; y: number } | null => {
    const box = room();
    const at = dock ? box?.querySelector<HTMLElement>(dock) : null;
    if (!box || !at) return null;
    const outer = box.getBoundingClientRect();
    const inner = at.getBoundingClientRect();
    return {
      x: inner.left + inner.width / 2 - outer.left - size * FACE_AT.x,
      y: inner.top + inner.height / 2 - outer.top - size * FACE_AT.y,
    };
  }, [dock, room, size]);
  /** The middle of what waits on the user, if it is on screen. */
  const waited = React.useCallback((): { x: number; y: number } | null => {
    const box = room();
    const at = waitsOn ? box?.querySelector<HTMLElement>(waitsOn) : null;
    if (!box || !at) return null;
    const outer = box.getBoundingClientRect();
    const inner = at.getBoundingClientRect();
    return { x: inner.left + inner.width / 2 - outer.left, y: inner.top + inner.height / 2 - outer.top };
  }, [room, waitsOn]);

  const paint = React.useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const s = sim.current;
    // Its weight, its nerves and its acting move the same parts: each is added
    // to the others. An act is the whole of what it does, so the keys wait.
    const felt = s.actor.playing ? UNFELT : s.felt;
    const acted = s.acted;
    el.style.transform = `translate3d(${(s.x + acted.dx).toFixed(2)}px, ${(s.y + felt.hop + acted.dy).toFixed(2)}px, 0)`;
    el.style.setProperty("--av-tilt", `${(s.tilt.x + felt.tilt + acted.tilt).toFixed(2)}deg`);
    el.style.setProperty("--av-leaf-lag", `${(s.leaf.x + felt.leaf + acted.leaf).toFixed(2)}deg`);
    el.style.setProperty("--av-look-x", `${within(s.lookX.x + felt.lookX + acted.lookX, EYE_MOST.x).toFixed(3)}px`);
    el.style.setProperty("--av-look-y", `${within(s.lookY.x + felt.lookY + acted.lookY, EYE_MOST.y).toFixed(3)}px`);
    el.style.setProperty("--av-sx", (s.sx.x * felt.sx * acted.sx).toFixed(4));
    el.style.setProperty("--av-sy", (s.sy.x * felt.sy * acted.sy).toFixed(4));
    el.style.setProperty("--av-nerves", s.felt.nerves.toFixed(3));
    el.style.setProperty("--av-lid", acted.lid.toFixed(3));
    el.style.setProperty("--av-glad", acted.glad.toFixed(3));
    el.style.setProperty("--av-sweat", (acted.fx.sweat ?? 0).toFixed(2));
    if (effects.current) drawEffects(effects.current, acted.fx);
  }, []);

  const tick = React.useCallback(
    (now: number) => {
      const s = sim.current;
      const dt = Math.min((now - s.last) / 1000, 1 / 30) || 1 / 60;
      s.last = now;
      const calmed = wantsLessMotion();
      const weighty = live.current.inertia && !calmed;
      const may = live.current.manners;
      let hit = 0;

      // Something waits on the user and it has nothing else on: it waits too.
      const idle = !s.actor.playing || s.actor.playing === "nods-off";
      if (s.card && idle && !s.felt.rapt && !s.held && !calmed) s.actor.play("waits", now);
      const face = { x: s.x + size * FACE_AT.x, y: s.y + size * FACE_AT.y };
      const acted = s.actor.step(now, dt, {
        size,
        card: s.card && { x: s.card.x - face.x, y: s.card.y - face.y },
        pointer: s.gaze && { x: s.gaze.x - face.x, y: s.gaze.y - face.y },
        nerves: s.felt.nerves,
        flinch: s.flinch,
      });
      s.acted = acted;
      s.flinch *= Math.exp(-dt / FLINCH_S);
      // An act never puts it anywhere. The one that hides says how much of it
      // should show over the composer, and it is carried there as to a seat.
      const lair =
        acted.lair !== null && floor.current !== null && s.lairX !== null && !s.held
          ? { x: s.lairX - size * FACE_AT.x, y: floor.current - (acted.lair * size) / LAIR.out }
          : null;
      if (s.lurking && !lair) {
        // Let go: back the way it always goes, to its seat or to where it was left.
        s.lurking = false;
        if (!s.held && !s.dock) s.home = pointOf(bounds(), s.place);
      } else if (lair && !s.lurking) {
        Object.assign(s, { lurking: true, home: null });
      }

      if (s.held) {
        // Under the pointer, exactly. The speed is only measured, to throw with.
        const to = clampTo(bounds(), s.wantX, s.wantY);
        s.vx += ((to.x - s.x) / dt - s.vx) * 0.5;
        s.vy += ((to.y - s.y) / dt - s.vy) * 0.5;
        s.x = to.x;
        s.y = to.y;
      } else if (lair) {
        const w = 2 * Math.PI * acted.pace.f;
        const x = stepSpring({ x: s.x, v: s.vx }, lair.x, dt, w * w, 2 * acted.pace.zeta * w);
        const y = stepSpring({ x: s.y, v: s.vy }, lair.y, dt, w * w, 2 * acted.pace.zeta * w);
        Object.assign(s, { x: x.x, vx: x.v, y: y.x, vy: y.v });
      } else if (s.dock) {
        // Its seat on this screen: it is carried there by a spring, weight and all.
        const x = stepSpring({ x: s.x, v: s.vx }, s.dock.x, dt, DOCKING.stiffness, DOCKING.damping);
        const y = stepSpring({ x: s.y, v: s.vy }, s.dock.y, dt, DOCKING.stiffness, DOCKING.damping);
        Object.assign(s, { x: x.x, vx: x.v, y: y.x, vy: y.v });
        if (wantsLessMotion() || (settled(x, s.dock.x, 0.3) && settled(y, s.dock.y, 0.3))) {
          Object.assign(s, { x: s.dock.x, y: s.dock.y, vx: 0, vy: 0 });
        }
      } else if (s.home && wantsLessMotion()) {
        Object.assign(s, { x: s.home.x, y: s.home.y, vx: 0, vy: 0, home: null });
      } else if (s.home) {
        const x = stepSpring({ x: s.x, v: s.vx }, s.home.x, dt, RETURN.stiffness, RETURN.damping);
        const y = stepSpring({ x: s.y, v: s.vy }, s.home.y, dt, RETURN.stiffness, RETURN.damping);
        Object.assign(s, { x: x.x, vx: x.v, y: y.x, vy: y.v });
        if (settled(x, s.home.x, 0.3) && settled(y, s.home.y, 0.3)) {
          Object.assign(s, { x: s.home.x, y: s.home.y, vx: 0, vy: 0, home: null });
        }
      } else if (weighty) {
        const next = glide(s, dt, bounds());
        hit = next.hit;
        Object.assign(s, { x: next.x, y: next.y, vx: next.vx, vy: next.vy });
        if (hit > DIZZY_AT && may.touch) s.actor.play("dizzy", now);
      } else {
        s.vx = 0;
        s.vy = 0;
      }

      const lean = weighty ? leanFor(s.vx, s.vy) : leanFor(0, 0);
      const to = { tilt: lean.tilt, lookX: lean.lookX, lookY: lean.lookY, sx: lean.stretchX, sy: lean.stretchY };
      const watching = !!s.gaze && live.current.watch && !s.held && !calmed && !s.actor.playing;
      if (watching && s.gaze) {
        // From the middle of the face to the pointer. A face's width away the
        // eyes are at their limit; nearer, they turn in proportion.
        const dx = s.gaze.x - (s.x + size * FACE_AT.x);
        const dy = s.gaze.y - (s.y + size * FACE_AT.y);
        const far = Math.hypot(dx, dy) || 1;
        const pull = Math.min(1, far / (size * 0.9));
        to.lookX = Math.max(-1.7, Math.min(1.7, to.lookX + (dx / far) * pull * WATCH.x));
        to.lookY = Math.max(-1.4, Math.min(1.4, to.lookY + (dy / far) * pull * WATCH.y));
        to.tilt += (dx / far) * pull * WATCH.tilt;
        // The pointer right by it: it stands a little taller, all attention.
        if (far < size * 0.7) {
          to.sy += 0.045;
          to.sx -= 0.025;
        }
      }
      if (watching !== s.watching) {
        s.watching = watching;
        ref.current?.toggleAttribute("data-watching", watching);
      }
      const eyes = watching ? GLANCE : { stiffness: 130, damping: 17 };
      s.tilt = stepSpring(s.tilt, to.tilt, dt, 150, 11);
      // Softer and less damped than the head: it gets there late and overshoots.
      s.leaf = stepSpring(s.leaf, lean.leaf, dt, 85, 5.5);
      s.lookX = stepSpring(s.lookX, to.lookX, dt, eyes.stiffness, eyes.damping);
      s.lookY = stepSpring(s.lookY, to.lookY, dt, eyes.stiffness, eyes.damping);
      s.sx = stepSpring(s.sx, to.sx, dt, 240, 13);
      s.sy = stepSpring(s.sy, to.sy, dt, 240, 13);
      if (hit && weighty) {
        // A wall flattens it for a moment; the spring gives the shape back.
        const dent = Math.min(hit / 2600, 0.5);
        s.sy.v -= dent * 6;
        s.sx.v += dent * 6;
      }
      if (s.stale) {
        // Read once a key, not once a frame: it lays the text out again.
        const box = room()?.getBoundingClientRect();
        const at = s.field?.isConnected ? caretPoint(s.field) : null;
        s.caret = box && at ? { x: at.x - box.left, y: at.y - box.top } : null;
        s.stale = false;
      }
      const felt = s.nerves.step(now, dt, { x: s.x + size * FACE_AT.x, y: s.y + size * FACE_AT.y, size }, s.caret);
      if (felt.rapt !== s.felt.rapt) ref.current?.toggleAttribute("data-rapt", felt.rapt);
      s.felt = felt;
      // Too much typing, and it runs for the composer; no more keys, and it comes out.
      if (!felt.rapt) s.rescued = false;
      const scared = may.hides && felt.rapt && felt.nerves >= BOLTS_AT && !s.rescued;
      if (s.actor.playing === "hides") {
        if (!felt.rapt || floor.current === null) s.actor.play("comes-out", now);
      } else if (scared && !s.actor.playing && !s.held && !s.dock && s.lairX !== null) {
        s.actor.play("hides", now);
      }
      paint();
      if (s.actor.playing !== s.shown) {
        s.shown = s.actor.playing;
        setAct(s.shown);
      }
      if (acted.asleep !== s.asleep) {
        s.asleep = acted.asleep;
        ref.current?.toggleAttribute("data-asleep", acted.asleep);
      }

      const moving = !!(s.held || s.home || s.vx || s.vy || lair);
      if (s.greeting && !moving && !s.actor.playing) {
        // It is where it was going: hello.
        s.greeting = false;
        if (may.greets && !calmed) s.actor.play("greets", now);
      }
      if (s.moved && !moving && !s.dock) {
        // Where it came to rest, whatever its eyes go on doing. A seat is the
        // screen's, not somewhere it was put: that is not remembered.
        const place = placeOf(bounds(), s.x, s.y);
        if (!same(place, s.place)) {
          s.place = place;
          live.current.onRest?.(place);
        }
      }
      s.moved = moving;
      const calm =
        settled(s.tilt, to.tilt, 0.05) && settled(s.leaf, 0, 0.05) &&
        settled(s.lookX, to.lookX) && settled(s.lookY, to.lookY) &&
        settled(s.sx, to.sx, 0.002) && settled(s.sy, to.sy, 0.002);
      if (moving || !calm || s.nerves.stirred(now) || !acted.still || s.actor.playing !== s.shown) {
        s.frame = requestAnimationFrame(tick);
        return;
      }
      // At rest nothing runs: no frame is asked for until it is touched again.
      s.frame = 0;
    },
    [bounds, paint, room, size]
  );

  const wake = React.useCallback(() => {
    const s = sim.current;
    if (s.frame) return;
    s.last = performance.now();
    s.frame = requestAnimationFrame(tick);
  }, [tick]);

  /** Play an act from outside a frame. Asked for less motion, it plays none. */
  const perform = React.useCallback(
    (name: ActName) => {
      if (wantsLessMotion()) return;
      sim.current.actor.play(name, performance.now());
      wake();
    },
    [wake]
  );

  // Placed once: `start` is where it begins, not somewhere it is sent back to.
  const placed = React.useRef(false);
  React.useLayoutEffect(() => {
    if (placed.current || !room()) return;
    placed.current = true;
    Object.assign(sim.current, pointOf(bounds(), sim.current.place));
    paint();
  });
  // The room changes size under it, or it does: at rest it keeps its place,
  // caught in flight it is only kept inside.
  React.useLayoutEffect(() => {
    roomRef.current = null;
    const box = room();
    if (!box) return;
    const wall = box.querySelector<HTMLElement>(FLOOR);
    const field = wall?.querySelector<HTMLElement>(LAIR_OF);
    const fit = () => {
      const s = sim.current;
      const outer = box.getBoundingClientRect();
      floor.current = wall ? wall.getBoundingClientRect().top - outer.top : null;
      const lair = field?.getBoundingClientRect();
      s.lairX = lair ? lair.left + lair.width / 2 - outer.left : null;
      s.card = live.current.manners.waits ? waited() : null;
      const flying = s.held || s.home || s.vx || s.vy;
      if (s.lurking) {
        // Hiding, it follows the composer's edge by itself, and has nowhere to hide when that goes.
        if (!wall) s.actor.stop();
      } else if (s.dock) {
        // The seat moved with the layout: so does it, unless it is on its way there.
        s.dock = seat() ?? s.dock;
        if (!flying) Object.assign(s, s.dock);
      } else {
        Object.assign(s, flying ? clampTo(bounds(), s.x, s.y) : pointOf(bounds(), s.place));
      }
      paint();
    };
    fit();
    // On its seat it is part of the page: it scrolls with what it sits on, off
    // the screen if that goes. Scroll events do not bubble; capturing hears them all.
    const carry = () => {
      const s = sim.current;
      const to = s.dock ? seat() : null;
      if (!s.dock || !to) return;
      if (!s.held) {
        s.x += to.x - s.dock.x;
        s.y += to.y - s.dock.y;
      }
      s.dock = to;
      paint();
    };
    box.addEventListener("scroll", carry, true);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(fit);
    observer?.observe(box);
    // The composer grows with what is typed into it.
    if (wall) observer?.observe(wall);
    return () => {
      box.removeEventListener("scroll", carry, true);
      observer?.disconnect();
    };
  }, [bounds, paint, room, scene, seat, waited]);
  React.useEffect(() => () => cancelAnimationFrame(sim.current.frame), []);

  // A screen with a seat for it: it goes there. One without: back to its own place.
  React.useLayoutEffect(() => {
    const s = sim.current;
    const to = seat();
    if (to) {
      const moved = !s.dock || Math.abs(s.dock.x - to.x) > 0.5 || Math.abs(s.dock.y - to.y) > 0.5;
      Object.assign(s, { dock: to, home: null });
      if (moved) wake();
    } else if (s.dock) {
      Object.assign(s, { dock: null, home: pointOf(bounds(), s.place) });
      wake();
    }
  });

  // The pointer, wherever it is on the page: what it looks at when it has nothing else to do.
  React.useEffect(() => {
    if (!watch) return;
    const seen = (event: PointerEvent) => {
      const box = room()?.getBoundingClientRect();
      if (!box) return;
      sim.current.gaze = { x: event.clientX - box.left, y: event.clientY - box.top };
      wake();
    };
    const gone = () => {
      sim.current.gaze = null;
      wake();
    };
    document.addEventListener("pointermove", seen);
    document.documentElement.addEventListener("mouseleave", gone);
    window.addEventListener("blur", gone);
    return () => {
      document.removeEventListener("pointermove", seen);
      document.documentElement.removeEventListener("mouseleave", gone);
      window.removeEventListener("blur", gone);
      sim.current.gaze = null;
    };
  }, [watch, room, wake]);
  // Set to work, or told not to watch: the eyes go back by themselves.
  React.useEffect(() => wake(), [pose, watch, wake]);
  // The keys, as the composer reports them: a hop to each, its eyes where they land.
  React.useEffect(
    () =>
      onStroke((kind, field) => {
        const s = sim.current;
        if (kind === "calm") s.nerves.calm();
        else if (!wantsLessMotion()) s.nerves.key(kind, performance.now());
        if (kind === "char" || kind === "erase") {
          if (s.actor.playing === "hides") s.flinch = Math.min(1, s.flinch + 0.55);
          else if (DROPPED_FOR_KEYS.has(s.actor.playing)) s.actor.stop();
        }
        if (field) Object.assign(s, { field, stale: true });
        wake();
      }),
    [wake]
  );
  // What waited on the user is gone: it gets on with it. Something does now: the next frame sees it.
  React.useLayoutEffect(() => {
    const s = sim.current;
    s.card = live.current.manners.waits ? waited() : null;
    if (!s.card && s.actor.playing === "waits") s.actor.stop();
    wake();
  }, [waited, wake]);
  // What the panel says happened: a greeting is owed, an answer came, a turn ended well or badly.
  React.useEffect(
    () =>
      onCue((kind) => {
        const s = sim.current;
        const may = live.current.manners;
        if (kind === "greet") {
          // Still saying hello where it was: that one is over, the next is for where it is going.
          if (s.actor.playing === "greets") s.actor.stop();
          s.greeting = true;
          wake();
          return;
        }
        // Hidden, or in someone's hand, it has other things on its mind.
        if (s.held || s.lurking) return;
        if (kind === "answered" || kind === "refused") {
          if (may.waits && s.card) perform(kind === "answered" ? "relieved" : "laments");
        } else if (may.cheers) {
          perform(kind === "went-well" ? "cheers" : "laments");
        }
      }),
    [perform, wake]
  );
  // Nobody about for a while: it nods off. Anyone moving or typing wakes it,
  // and sets it waving again if it had settled down to wait.
  const sleeps = manners.sleeps;
  React.useEffect(() => {
    let timer = 0;
    let armed = -Infinity;
    const nod = () => {
      const s = sim.current;
      const free = !s.actor.playing && !s.held && live.current.pose === "idle";
      if (free) perform("nods-off");
      else timer = window.setTimeout(nod, SLEEP_RETRY_MS);
    };
    const stir = () => {
      const s = sim.current;
      const now = performance.now();
      if (s.actor.playing === "nods-off") perform("wakes");
      else if (s.actor.playing === "waits" && s.acted.still) perform("waits");
      // Once a second is often enough to put the nap off.
      if (!sleeps || now - armed < 1000) return;
      armed = now;
      window.clearTimeout(timer);
      timer = window.setTimeout(nod, SLEEP_AFTER_MS);
    };
    stir();
    const heard = ["pointermove", "pointerdown", "keydown", "wheel"] as const;
    heard.forEach((name) => document.addEventListener(name, stir, true));
    return () => {
      window.clearTimeout(timer);
      heard.forEach((name) => document.removeEventListener(name, stir, true));
    };
  }, [sleeps, perform]);
  // Set to work in its sleep: up it gets.
  React.useEffect(() => {
    if (sim.current.actor.playing === "nods-off" && pose !== "idle" && pose !== null) perform("wakes");
  }, [pose, perform]);

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    const box = room()?.getBoundingClientRect();
    if (event.button !== 0 || !box) return;
    const s = sim.current;
    event.currentTarget.setPointerCapture(event.pointerId);
    // Picked up from behind the composer, it is out, and stays out until this typing is over.
    if (s.lurking) {
      s.actor.stop();
      s.rescued = true;
    }
    Object.assign(s, {
      held: true, home: null, vx: 0, vy: 0, lurking: false,
      grabX: event.clientX - box.left - s.x, grabY: event.clientY - box.top - s.y, wantX: s.x, wantY: s.y,
      pressed: { at: performance.now(), x: event.clientX, y: event.clientY },
    });
    setHeld(true);
    wake();
  };
  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const s = sim.current;
    if (!s.held) {
      stroked(event.clientX);
      return;
    }
    const box = room()?.getBoundingClientRect();
    if (!box) return;
    s.wantX = event.clientX - box.left - s.grabX;
    s.wantY = event.clientY - box.top - s.grabY;
  };
  /** The pointer over its face, not pressed: back and forth often enough, and it is being petted. */
  const stroked = (x: number) => {
    const s = sim.current;
    const pet = s.petting;
    const now = performance.now();
    if (!live.current.manners.touch || s.lurking) return;
    if (now - pet.since > PET.ms) Object.assign(pet, { x, way: 0, turns: 0, since: now });
    const gone = x - pet.x;
    if (Math.abs(gone) < PET.px) return;
    if (pet.way && Math.sign(gone) !== pet.way) pet.turns += 1;
    Object.assign(pet, { x, way: Math.sign(gone) });
    if (pet.turns < PET.turns) return;
    Object.assign(pet, { turns: 0, since: now });
    if (!s.actor.playing || s.actor.playing === "waits" || s.actor.playing === "poked") perform("petted");
  };
  const release = (event: React.PointerEvent<HTMLDivElement>) => {
    const s = sim.current;
    if (!s.held) return;
    s.held = false;
    setHeld(false);
    const press = s.pressed;
    s.pressed = null;
    const brief = press && performance.now() - press.at < POKE.ms;
    const stayed = press && Math.hypot(event.clientX - press.x, event.clientY - press.y) < POKE.px;
    if (brief && stayed && live.current.manners.touch && event.type === "pointerup") perform("poked");
    wake();
  };
  const goHome = () => {
    sim.current.home = pointOf(bounds(), HOME);
    wake();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    const push: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
    };
    const s = sim.current;
    if (event.key === "Home") {
      goHome();
    } else if (push[event.key]) {
      const by = live.current.inertia && !wantsLessMotion() ? NUDGE : 0;
      s.home = null;
      s.vx += push[event.key][0] * by;
      s.vy += push[event.key][1] * by;
      // With no inertia a key is a plain step.
      if (!by) Object.assign(s, clampTo(bounds(), s.x + push[event.key][0] * 16, s.y + push[event.key][1] * 16));
      wake();
    } else {
      return;
    }
    event.preventDefault();
  };

  return (
    <div
      ref={ref}
      className="av-float"
      style={{ width: size, height: size }}
      data-held={held ? "" : undefined}
      data-act={act ?? undefined}
    >
      {/* Acting, its eyes and its body are the script's: the pose stands down. */}
      <AgentAvatar pose={act ? null : pose} size={size} fx skin={skin} backed />
      <svg ref={effects} className="av-float-fx" viewBox="-1 -4 28 28" width={size} height={size} aria-hidden="true">
        <g>
          {SPARKS_AT.map((_, i) => (
            <path key={i} d={SPARK} />
          ))}
        </g>
        <g>
          {[0, 1, 2].map((i) => (
            <path key={i} d={STAR} />
          ))}
        </g>
        <g>
          <g>
            <rect x="-.55" y="-2.7" width="1.1" height="3.1" rx=".55" />
            <circle cy="1.55" r=".65" />
          </g>
        </g>
        <g className="av-float-heart">
          <path d={HEART} />
        </g>
        <g className="av-float-zs">
          {[0, 1, 2].map((i) => (
            <path key={i} d={ZED} />
          ))}
        </g>
      </svg>
      <div
        className="av-float-grip"
        role="button"
        tabIndex={0}
        aria-label={t("Code-AI. Drag to move, arrow keys to push, Home to send back.")}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={release}
        onPointerCancel={release}
        onKeyDown={onKeyDown}
        onDoubleClick={goHome}
      />
    </div>
  );
}
