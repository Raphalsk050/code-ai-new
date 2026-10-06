// The weight of the avatar: how it keeps going when let go, and how its body
// answers a movement. No DOM in here.
//
// Dragging itself has no lag - the glyph stays under the pointer. Inertia is
// in what follows: the throw after a release, the head leaning away from the
// direction of travel, the leaf arriving late and wobbling to a stop.

export interface Spring {
  x: number;
  v: number;
}

/** One step of a damped spring towards `target`. Stable for any frame time. */
export function stepSpring(spring: Spring, target: number, dt: number, stiffness: number, damping: number): Spring {
  let { x, v } = spring;
  // Small fixed steps: a long frame (a tab coming back) must not blow it up.
  const steps = Math.max(1, Math.ceil(dt / 0.008));
  const h = dt / steps;
  for (let i = 0; i < steps; i++) {
    v += (-stiffness * (x - target) - damping * v) * h;
    x += v * h;
  }
  return { x, v };
}

export function settled(spring: Spring, target: number, slack = 0.01): boolean {
  return Math.abs(spring.x - target) < slack && Math.abs(spring.v) < slack * 10;
}

export interface Body {
  x: number;
  y: number;
  vx: number;
  vy: number;
}

export interface Bounds {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** How fast a throw dies: about half the speed gone every fifth of a second. */
export const FRICTION = 3.4;
/** How much of its speed a wall gives back. */
export const BOUNCE = 0.42;
/** Below this, in px/s, a glide is over. */
export const REST_SPEED = 6;

export function clampTo(bounds: Bounds, x: number, y: number): { x: number; y: number } {
  return {
    x: Math.min(Math.max(x, bounds.left), Math.max(bounds.left, bounds.right)),
    y: Math.min(Math.max(y, bounds.top), Math.max(bounds.top, bounds.bottom)),
  };
}

/**
 * One step of a body thrown and left to itself. `hit` is the speed it struck a
 * wall with, 0 when it did not: what the squash of a landing is made from.
 */
export function glide(body: Body, dt: number, bounds: Bounds): Body & { hit: number } {
  const decay = Math.exp(-FRICTION * dt);
  let vx = body.vx * decay;
  let vy = body.vy * decay;
  let x = body.x + vx * dt;
  let y = body.y + vy * dt;
  let hit = 0;
  if (x < bounds.left || x > bounds.right) {
    hit = Math.max(hit, Math.abs(vx));
    x = x < bounds.left ? bounds.left : bounds.right;
    vx = -vx * BOUNCE;
  }
  if (y < bounds.top || y > bounds.bottom) {
    hit = Math.max(hit, Math.abs(vy));
    y = y < bounds.top ? bounds.top : bounds.bottom;
    vy = -vy * BOUNCE;
  }
  if (Math.hypot(vx, vy) < REST_SPEED) {
    vx = 0;
    vy = 0;
  }
  return { x, y, vx, vy, hit };
}

// + 0: a still body leans by 0, not by the -0 a negated zero gives.
const clamp = (value: number, limit: number) => Math.min(Math.max(value, -limit), limit) + 0;

export interface Lean {
  /** Degrees the head tips, about its base: away from where it is going. */
  tilt: number;
  /** Degrees the leaf trails behind the head. */
  leaf: number;
  /** Where the eyes look, in the glyph's own units: ahead. */
  lookX: number;
  lookY: number;
  /** Stretch along the direction of travel. */
  stretchX: number;
  stretchY: number;
}

/** What a velocity, in px/s, asks of the body. The springs get it there. */
export function leanFor(vx: number, vy: number): Lean {
  return {
    tilt: clamp(-vx * 0.018, 16),
    leaf: clamp(-vx * 0.035, 32),
    lookX: clamp(vx / 420, 1.5),
    lookY: clamp(vy / 420, 1.3),
    stretchX: 1 + clamp(Math.abs(vx) / 9000 - Math.abs(vy) / 14000, 0.08),
    stretchY: 1 + clamp(Math.abs(vy) / 9000 - Math.abs(vx) / 14000, 0.08),
  };
}
