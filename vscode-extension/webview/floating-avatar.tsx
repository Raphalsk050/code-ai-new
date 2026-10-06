import * as React from "react";

import { AgentAvatar } from "./agent-avatar";
import {
  type Body, type Bounds, clampTo, glide, leanFor, type Place, placeOf, pointOf, settled, type Spring, stepSpring,
} from "./avatar-motion";
import type { AvatarPose } from "./avatar-pose";
import type { AvatarSkin } from "./avatar-skin";
import { t } from "./i18n";

/** Its box. The face is six tenths of it: the agent in the room, not one more button. */
const DEFAULT_SIZE = 88;
/** Kept clear of the container's edges. */
const MARGIN = 8;
/** Its own corner is the bottom right: clear of the rail's strip, above the composer. */
const HOME: Place = { right: true, bottom: true, x: 32, y: 150 };
/** What an arrow key gives it, in px/s: a push, not a step. */
const NUDGE = 520;
/** Going home is a spring too, softer than the body's. */
const RETURN = { stiffness: 70, damping: 13 };

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
  /** Where it rests, as the room's size will not change it. */
  place: Place;
  tilt: Spring;
  leaf: Spring;
  lookX: Spring;
  lookY: Spring;
  sx: Spring;
  sy: Spring;
  last: number;
  frame: number;
}

const still = (): Spring => ({ x: 0, v: 0 });
const same = (a: Place, b: Place) => a.right === b.right && a.bottom === b.bottom && a.x === b.x && a.y === b.y;

/**
 * The avatar loose in the panel: picked up, dropped anywhere, thrown. Lives
 * inside a positioned container and never leaves it. Only the face takes the
 * pointer; the rest of its box lets a click through to what is under it.
 *
 * @param size its box, in px
 * @param start where it is first put; its own corner when left out
 * @param inertia off, it goes where the pointer goes and stops when let go
 * @param onRest called with where it came to rest, for remembering it
 */
export function FloatingAvatar({
  pose,
  skin,
  size = DEFAULT_SIZE,
  start,
  inertia = true,
  onRest,
}: {
  pose: AvatarPose | null;
  skin?: AvatarSkin;
  size?: number;
  start?: Place;
  inertia?: boolean;
  onRest?: (place: Place) => void;
}): JSX.Element {
  const ref = React.useRef<HTMLDivElement>(null);
  const [held, setHeld] = React.useState(false);
  const sim = React.useRef<Sim>({
    x: 0, y: 0, vx: 0, vy: 0, held: false, grabX: 0, grabY: 0, wantX: 0, wantY: 0, home: null, place: start ?? HOME,
    tilt: still(), leaf: still(), lookX: still(), lookY: still(), sx: { x: 1, v: 0 }, sy: { x: 1, v: 0 },
    last: 0, frame: 0,
  });
  const live = React.useRef({ inertia, onRest });
  live.current = { inertia, onRest };

  const room = React.useCallback(() => ref.current?.offsetParent as HTMLElement | null, []);
  const bounds = React.useCallback((): Bounds => {
    const box = room();
    const width = box?.clientWidth ?? window.innerWidth;
    const height = box?.clientHeight ?? window.innerHeight;
    return { left: MARGIN, top: MARGIN, right: width - size - MARGIN, bottom: height - size - MARGIN };
  }, [room, size]);

  const paint = React.useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const s = sim.current;
    el.style.transform = `translate3d(${s.x.toFixed(2)}px, ${s.y.toFixed(2)}px, 0)`;
    el.style.setProperty("--av-tilt", `${s.tilt.x.toFixed(2)}deg`);
    el.style.setProperty("--av-leaf-lag", `${s.leaf.x.toFixed(2)}deg`);
    el.style.setProperty("--av-look-x", `${s.lookX.x.toFixed(3)}px`);
    el.style.setProperty("--av-look-y", `${s.lookY.x.toFixed(3)}px`);
    el.style.setProperty("--av-sx", s.sx.x.toFixed(4));
    el.style.setProperty("--av-sy", s.sy.x.toFixed(4));
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
      s.tilt = stepSpring(s.tilt, lean.tilt, dt, 150, 11);
      // Softer and less damped than the head: it gets there late and overshoots.
      s.leaf = stepSpring(s.leaf, lean.leaf, dt, 85, 5.5);
      s.lookX = stepSpring(s.lookX, lean.lookX, dt, 130, 17);
      s.lookY = stepSpring(s.lookY, lean.lookY, dt, 130, 17);
      s.sx = stepSpring(s.sx, lean.stretchX, dt, 240, 13);
      s.sy = stepSpring(s.sy, lean.stretchY, dt, 240, 13);
      if (hit && weighty) {
        // A wall flattens it for a moment; the spring gives the shape back.
        const dent = Math.min(hit / 2600, 0.5);
        s.sy.v -= dent * 6;
        s.sx.v += dent * 6;
      }
      paint();

      const moving = s.held || s.home || s.vx || s.vy;
      const calm =
        settled(s.tilt, 0, 0.05) && settled(s.leaf, 0, 0.05) && settled(s.lookX, 0) && settled(s.lookY, 0) &&
        settled(s.sx, 1, 0.002) && settled(s.sy, 1, 0.002);
      if (moving || !calm) {
        s.frame = requestAnimationFrame(tick);
        return;
      }
      // At rest nothing runs: no frame is asked for until it is touched again.
      s.frame = 0;
      const place = placeOf(bounds(), s.x, s.y);
      if (same(place, s.place)) return;
      s.place = place;
      live.current.onRest?.(place);
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
    const box = room();
    if (!box) return;
    const fit = () => {
      const s = sim.current;
      Object.assign(s, s.frame ? clampTo(bounds(), s.x, s.y) : pointOf(bounds(), s.place));
      paint();
    };
    fit();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(fit);
    observer.observe(box);
    return () => observer.disconnect();
  }, [bounds, paint, room]);
  React.useEffect(() => () => cancelAnimationFrame(sim.current.frame), []);

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
