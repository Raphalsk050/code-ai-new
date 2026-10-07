import * as React from "react";

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
/** What it never goes under: beneath the composer's glass it could be neither seen nor picked up. */
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
  onRest?: (place: Place) => void;
}): JSX.Element {
  const ref = React.useRef<HTMLDivElement>(null);
  const [held, setHeld] = React.useState(false);
  const sim = React.useRef<Sim>({
    x: 0, y: 0, vx: 0, vy: 0, held: false, grabX: 0, grabY: 0, wantX: 0, wantY: 0, home: null, dock: null, gaze: null, watching: false, moved: false,
    place: start ?? HOME,
    tilt: still(), leaf: still(), lookX: still(), lookY: still(), sx: { x: 1, v: 0 }, sy: { x: 1, v: 0 },
    nerves: new Nerves(), felt: UNFELT, field: null, caret: null, stale: false,
    last: 0, frame: 0,
  });
  // At work it has its own things to look at; the pointer gets its eyes when it is idle.
  const live = React.useRef({ inertia, onRest, watch: watch && (pose === "idle" || pose === null) });
  live.current = { inertia, onRest, watch: watch && (pose === "idle" || pose === null) };

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

  const paint = React.useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const s = sim.current;
    // Its weight and its nerves move the same parts: one is added to the other.
    const felt = s.felt;
    el.style.transform = `translate3d(${s.x.toFixed(2)}px, ${(s.y + felt.hop).toFixed(2)}px, 0)`;
    el.style.setProperty("--av-tilt", `${(s.tilt.x + felt.tilt).toFixed(2)}deg`);
    el.style.setProperty("--av-leaf-lag", `${(s.leaf.x + felt.leaf).toFixed(2)}deg`);
    el.style.setProperty("--av-look-x", `${within(s.lookX.x + felt.lookX, EYE_MOST.x).toFixed(3)}px`);
    el.style.setProperty("--av-look-y", `${within(s.lookY.x + felt.lookY, EYE_MOST.y).toFixed(3)}px`);
    el.style.setProperty("--av-sx", (s.sx.x * felt.sx).toFixed(4));
    el.style.setProperty("--av-sy", (s.sy.x * felt.sy).toFixed(4));
    el.style.setProperty("--av-nerves", felt.nerves.toFixed(3));
  }, []);

  const tick = React.useCallback(
    (now: number) => {
      const s = sim.current;
      const dt = Math.min((now - s.last) / 1000, 1 / 30) || 1 / 60;
      s.last = now;
      const weighty = live.current.inertia && !wantsLessMotion();
      let hit = 0;

      if (s.held) {
        // Under the pointer, exactly. The speed is only measured, to throw with.
        const to = clampTo(bounds(), s.wantX, s.wantY);
        s.vx += ((to.x - s.x) / dt - s.vx) * 0.5;
        s.vy += ((to.y - s.y) / dt - s.vy) * 0.5;
        s.x = to.x;
        s.y = to.y;
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
      } else {
        s.vx = 0;
        s.vy = 0;
      }

      const lean = weighty ? leanFor(s.vx, s.vy) : leanFor(0, 0);
      const to = { tilt: lean.tilt, lookX: lean.lookX, lookY: lean.lookY, sx: lean.stretchX, sy: lean.stretchY };
      const watching = !!s.gaze && live.current.watch && !s.held && !wantsLessMotion();
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
      paint();

      const moving = !!(s.held || s.home || s.vx || s.vy);
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
      if (moving || !calm || s.nerves.stirred(now)) {
        s.frame = requestAnimationFrame(tick);
        return;
      }
      // At rest nothing runs: no frame is asked for until it is touched again.
      s.frame = 0;
    },
    [bounds, paint]
  );

  const wake = React.useCallback(() => {
    const s = sim.current;
    if (s.frame) return;
    s.last = performance.now();
    s.frame = requestAnimationFrame(tick);
  }, [tick]);

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
    const fit = () => {
      const s = sim.current;
      floor.current = wall ? wall.getBoundingClientRect().top - box.getBoundingClientRect().top : null;
      const flying = s.held || s.home || s.vx || s.vy;
      if (s.dock) {
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
  }, [bounds, paint, room, scene, seat]);
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
        if (field) Object.assign(s, { field, stale: true });
        wake();
      }),
    [wake]
  );

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    const box = room()?.getBoundingClientRect();
    if (event.button !== 0 || !box) return;
    const s = sim.current;
    event.currentTarget.setPointerCapture(event.pointerId);
    Object.assign(s, {
      held: true, home: null, vx: 0, vy: 0,
      grabX: event.clientX - box.left - s.x, grabY: event.clientY - box.top - s.y, wantX: s.x, wantY: s.y,
    });
    setHeld(true);
    wake();
  };
  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const s = sim.current;
    const box = room()?.getBoundingClientRect();
    if (!s.held || !box) return;
    s.wantX = event.clientX - box.left - s.grabX;
    s.wantY = event.clientY - box.top - s.grabY;
  };
  const release = () => {
    if (!sim.current.held) return;
    sim.current.held = false;
    setHeld(false);
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
    <div ref={ref} className="av-float" style={{ width: size, height: size }} data-held={held ? "" : undefined}>
      <AgentAvatar pose={pose} size={size} fx skin={skin} backed />
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
