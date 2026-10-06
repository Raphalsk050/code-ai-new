import * as React from "react";

import { AgentAvatar } from "./agent-avatar";
import { type Body, type Bounds, clampTo, glide, leanFor, settled, type Spring, stepSpring } from "./avatar-motion";
import type { AvatarPose } from "./avatar-pose";
import type { AvatarSkin } from "./avatar-skin";
import { t } from "./i18n";

/** Large enough to read as the agent in the room, not as one more button. */
const DEFAULT_SIZE = 72;
/** How much of the base the glyph's box takes. */
const GLYPH_SHARE = 0.8;
/** Kept clear of the container's edges. */
const MARGIN = 8;
/** Its own corner is the bottom right, this far up: above the composer. */
const HOME_INSET = { right: 8, bottom: 150 };
/** What an arrow key gives it, in px/s: a push, not a step. */
const NUDGE = 520;
/** Returning home is a spring too, softer than the body's. */
const HOME = { stiffness: 70, damping: 13 };

/** The system was asked for less motion: no throw, no lean, no glide home. */
function wantsLessMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

export interface Point {
  x: number;
  y: number;
}

interface Sim extends Body {
  held: boolean;
  /** Where in the avatar it was picked up. */
  grabX: number;
  grabY: number;
  /** Where the pointer wants it. */
  wantX: number;
  wantY: number;
  home: Point | null;
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

/**
 * The avatar loose in the panel: picked up, dropped anywhere, thrown. Lives
 * inside a positioned container and never leaves it.
 *
 * @param size how wide it is, base included, in px
 * @param start where it is first put, in the container's pixels; the bottom
 *   right corner when left out
 * @param inertia off, it goes where the pointer goes and stops when let go
 * @param onRest called with where it came to rest, for remembering it
 */
export function FloatingAvatar({
  pose,
  skin,
  size = DEFAULT_SIZE,
  start,
  inertia = true,
  bare = false,
  onRest,
}: {
  pose: AvatarPose | null;
  skin?: AvatarSkin;
  size?: number;
  start?: Point;
  inertia?: boolean;
  bare?: boolean;
  onRest?: (at: Point) => void;
}): JSX.Element {
  const ref = React.useRef<HTMLDivElement>(null);
  const [held, setHeld] = React.useState(false);
  const sim = React.useRef<Sim>({
    x: 0, y: 0, vx: 0, vy: 0, held: false, grabX: 0, grabY: 0, wantX: 0, wantY: 0, home: null,
    tilt: still(), leaf: still(), lookX: still(), lookY: still(), sx: { x: 1, v: 0 }, sy: { x: 1, v: 0 },
    last: 0, frame: 0,
  });
  const live = React.useRef({ inertia, onRest });
  live.current = { inertia, onRest };

  const bounds = React.useCallback((): Bounds => {
    const box = ref.current?.offsetParent as HTMLElement | null;
    const width = box?.clientWidth ?? window.innerWidth;
    const height = box?.clientHeight ?? window.innerHeight;
    return { left: MARGIN, top: MARGIN, right: width - size - MARGIN, bottom: height - size - MARGIN };
  }, [size]);

  const home = React.useCallback((): Point => {
    const edge = bounds();
    return clampTo(edge, edge.right - HOME_INSET.right, edge.bottom - HOME_INSET.bottom);
  }, [bounds]);

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
        const x = stepSpring({ x: s.x, v: s.vx }, s.home.x, dt, HOME.stiffness, HOME.damping);
        const y = stepSpring({ x: s.y, v: s.vy }, s.home.y, dt, HOME.stiffness, HOME.damping);
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
      live.current.onRest?.({ x: s.x, y: s.y });
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
    if (placed.current || !ref.current?.offsetParent) return;
    placed.current = true;
    const corner = home();
    Object.assign(sim.current, clampTo(bounds(), start?.x ?? corner.x, start?.y ?? corner.y));
    paint();
  });
  // Kept inside whenever the container changes size under it, or it does.
  React.useLayoutEffect(() => {
    const box = ref.current?.offsetParent;
    if (!box) return;
    const fit = () => {
      Object.assign(sim.current, clampTo(bounds(), sim.current.x, sim.current.y));
      paint();
    };
    fit();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(fit);
    observer.observe(box);
    return () => observer.disconnect();
  }, [bounds, paint]);
  React.useEffect(() => () => cancelAnimationFrame(sim.current.frame), []);

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const s = sim.current;
    const box = (event.currentTarget.offsetParent as HTMLElement).getBoundingClientRect();
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
    if (!s.held) return;
    const box = (event.currentTarget.offsetParent as HTMLElement).getBoundingClientRect();
    s.wantX = event.clientX - box.left - s.grabX;
    s.wantY = event.clientY - box.top - s.grabY;
  };
  const release = () => {
    if (!sim.current.held) return;
    sim.current.held = false;
    setHeld(false);
    wake();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    const push: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
    };
    const s = sim.current;
    if (event.key === "Home") {
      s.home = home();
    } else if (push[event.key]) {
      const by = live.current.inertia && !wantsLessMotion() ? NUDGE : 0;
      s.home = null;
      s.vx += push[event.key][0] * by;
      s.vy += push[event.key][1] * by;
      // With no inertia a key is a plain step.
      if (!by) Object.assign(s, clampTo(bounds(), s.x + push[event.key][0] * 16, s.y + push[event.key][1] * 16));
    } else {
      return;
    }
    event.preventDefault();
    wake();
  };

  return (
    <div
      ref={ref}
      className="av-float"
      style={{ width: size, height: size }}
      role="button"
      tabIndex={0}
      aria-label={t("Code-AI. Drag to move, arrow keys to push, Home to send back.")}
      data-held={held ? "" : undefined}
      data-busy={pose && pose !== "idle" ? "" : undefined}
      data-bare={bare ? "" : undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={release}
      onPointerCancel={release}
      onKeyDown={onKeyDown}
      onDoubleClick={() => {
        sim.current.home = home();
        wake();
      }}
    >
      <span className="av-float-puck" />
      <AgentAvatar pose={pose} size={Math.round(size * GLYPH_SHARE)} fx skin={skin} />
    </div>
  );
}
