// The avatar's idle, acted out by a small physical rig and baked to keyframes.
//
// None of the motion is drawn by hand. A script says what the character means
// to do - look there, blink, nod - and each part gets there by its own
// dynamics: the eyes snap, the head has mass and goes past where it stops, the
// body gives under it like jelly, and what grows on the head is carried along
// and thrown by it. The run is sampled once, when the module loads, and handed
// to CSS as keyframes: weight, with no script running while the panel sits idle.
//
// The rig is not the idle's alone: avatar-anxious.ts acts another loop with it.

/** One pass of the idle, in seconds: long enough not to read as a loop. */
export const IDLE_LOOP_S = 14;

/**
 * How a part answers a change in what it is asked to do.
 * `f` is how fast, in Hz. `zeta` is how soon it stops ringing: under 1 it goes
 * past its target and comes back, at 1 it just arrives. `r` is how it sets off:
 * above 0 it starts at once, at 0 it eases in, below 0 it first winds up the
 * other way.
 */
export interface Dynamics {
  f: number;
  zeta: number;
  r: number;
}

/**
 * A second-order system following an input: y + k1 y' + k2 y'' = x + k3 x'.
 * The same three numbers give anticipation, overshoot and settle, so a part's
 * whole manner is its `Dynamics` and nothing else.
 */
export class SecondOrder {
  private k1 = 0;
  private k2 = 0;
  private k3 = 0;
  private last: number;
  private y: number;
  private yd = 0;

  constructor(dynamics: Dynamics, start = 0) {
    this.tune(dynamics);
    this.last = start;
    this.y = start;
  }

  /** Another manner, mid-flight: where it is and how fast it goes carry over. */
  tune({ f, zeta, r }: Dynamics): this {
    this.k1 = zeta / (Math.PI * f);
    this.k2 = 1 / (2 * Math.PI * f) ** 2;
    this.k3 = (r * zeta) / (2 * Math.PI * f);
    return this;
  }

  /** Move `dt` seconds on towards `x`; returns where it now is. */
  step(dt: number, x: number): number {
    const xd = (x - this.last) / dt;
    this.last = x;
    // Too long a step for the stiffness would blow up; this keeps it stable.
    const k2 = Math.max(this.k2, (dt * dt) / 2 + (dt * this.k1) / 2, dt * this.k1);
    this.y += dt * this.yd;
    this.yd += (dt * (x + this.k3 * xd - this.y - this.k1 * this.yd)) / k2;
    return this.y;
  }

  get value(): number {
    return this.y;
  }

  get velocity(): number {
    return this.yd;
  }
}

/**
 * A mass carried on a spring by something that moves. What comes back is how
 * far behind it is: starting and stopping throw it, a steady move does not.
 */
export class Carried {
  private readonly mass: SecondOrder;

  constructor(f: number, zeta: number, start = 0) {
    // r = 2 puts the damping between the mass and its carrier, not the world.
    this.mass = new SecondOrder({ f, zeta, r: 2 }, start);
  }

  lag(dt: number, carrier: number): number {
    return this.mass.step(dt, carrier) - carrier;
  }
}

export const clamp = (value: number, low: number, high: number) => Math.min(Math.max(value, low), high);
/** A limit that is neared and never hit: straight through the middle, flat towards `limit`. */
export const soft = (value: number, limit: number) => limit * Math.tanh(value / limit);

// ---- the parts and their manners ----

const EYES: Dynamics = { f: 4.2, zeta: 0.9, r: 0.2 };
// Heavy: it winds up before it goes, and goes past where it stops.
const HEAD: Dynamics = { f: 1.3, zeta: 0.42, r: -2 };
const NECK: Dynamics = { f: 1.7, zeta: 0.4, r: -0.6 };
const TILT: Dynamics = { f: 1.4, zeta: 0.5, r: -0.3 };
const SHIMMY: Dynamics = { f: 4.2, zeta: 0.22, r: 0 };
const PERK: Dynamics = { f: 2.2, zeta: 0.45, r: 0 };

/** Degrees the head turns, and units it shifts, per unit the gaze moves. */
const TURN_DEG = 2.6;
const SHIFT = 0.36;
/** Units the head rises per unit the gaze does. */
const LIFT = 0.5;
/** How much of the head's turn the eyes give back: they dart, then recentre as the head arrives. */
const EYES_YIELD = 0.36;
/** As far to either side as an eye can go and stay on the face. */
const EYE_REACH = 2.2;
const SHIMMY_DEG = 3.4;
/** A turn follows an arc: the most the head ducks, and the turning speed at which it is halfway there. */
const DUCK = 0.5;
const DUCK_SPEED = 8;
// Eased, or the ringing at the end of a turn would shake the body under it.
const ARC: Dynamics = { f: 1.5, zeta: 1, r: 0 };

// The body is soft: carried by where the head is put, it stretches as that
// drops away and flattens when it stops. `GIVE` is how much per unit of lag.
const BODY = { f: 2.6, zeta: 0.5 };
const GIVE = 0.2;
const GIVE_MOST = 0.1;

/** What grows on the head, and how hard the head's turning and its rise and fall throw it. */
export interface Hung {
  f: number;
  zeta: number;
  sway: number;
  heave: number;
}
// The leaf stands on a stem, barely damped: still swinging when all else has stopped.
export const LEAF: Hung = { f: 1.55, zeta: 0.13, sway: 2.3, heave: 6 };
// Ears are stiffer, and standing straight up they do not care for a nod.
export const EARS: Hung = { f: 2.1, zeta: 0.2, sway: 1.5, heave: 0 };
// The hat's tail hangs with a weight on its end: slower, and thrown the other way.
export const TAIL: Hung = { f: 1.15, zeta: 0.17, sway: -0.5, heave: -5.5 };
/** The tail has the head under it: this many degrees in, and no further. */
export const TAIL_IN = 8;

// The lids fall faster than they lift, and lift a little too far.
const LID_SHUT: Dynamics = { f: 11, zeta: 1, r: 0 };
const LID_OPEN: Dynamics = { f: 5.2, zeta: 0.6, r: 0 };
const BLINK_S = 0.1;

// A deep breath fills slowly and is let go at once.
const INHALE: Dynamics = { f: 0.6, zeta: 1, r: 0 };
const EXHALE: Dynamics = { f: 1.6, zeta: 0.45, r: 0 };
/** Units it rises on a full breath, and how much taller it gets. */
const BREATH_RISE = 0.6;
const BREATH_SWELL = 0.035;

// ---- the script: what it means to do, and when ----

/** Where it looks: ahead, at something on the left, across to the right, back, up at a thought. */
const GAZE: [at: number, x: number, y: number][] = [
  [0, 0, 0],
  [3, -2, 0.08],
  [4.7, 2.05, 0.02],
  [6.5, 0, 0],
  [8.3, 0.55, -1.25],
  [9.95, 0, 0],
];
/** Blinks. People blink as they shift their gaze, so most of these ride a glance. */
const BLINKS = [2.98, 4.68, 6.05, 6.31, 9.9, 12.4];
/** How open the eyes are held: wide when something catches it, narrowed to peer, heavy or creased when at ease. */
const LIDS: [from: number, to: number, open: number][] = [
  [0.95, 1.85, 1.06],
  [1.9, 2.65, 0.8],
  [3.05, 3.9, 1.12],
  [3.9, 4.66, 0.78],
  [4.75, 6, 1.08],
  [8.35, 9.85, 1.14],
  [10.35, 11.05, 0.72],
];
/** A deep breath while nothing is going on. */
const BREATH: [from: number, to: number] = [0.8, 1.85];
/** A nod as it settles back to the middle. */
const NOD: [from: number, to: number, depth: number] = [6.98, 7.12, 2.4];
/** The head cocked while it looks up, and the leaf standing to attention. */
const CURIOUS: [from: number, to: number, tilt: number, perk: number] = [8.36, 9.9, 4.5, -10];
/** One flick that the head then rings out by itself: a pleased shimmy. */
const SHAKE: [from: number, to: number] = [10.4, 10.49];

interface Intent {
  gazeX: number;
  gazeY: number;
  lid: number;
  inhale: number;
  nod: number;
  tilt: number;
  perk: number;
  shake: number;
}

export const within = (t: number, from: number, to: number) => t >= from && t < to;

function intentAt(t: number): Intent {
  let gaze = GAZE[0];
  for (const step of GAZE) if (t >= step[0]) gaze = step;
  const held = LIDS.find(([from, to]) => within(t, from, to));
  const curious = within(t, CURIOUS[0], CURIOUS[1]);
  return {
    gazeX: gaze[1],
    gazeY: gaze[2],
    lid: BLINKS.some((at) => within(t, at, at + BLINK_S)) ? 0.03 : held ? held[2] : 1,
    inhale: within(t, BREATH[0], BREATH[1]) ? 1 : 0,
    nod: within(t, NOD[0], NOD[1]) ? NOD[2] : 0,
    tilt: curious ? CURIOUS[2] : 0,
    perk: curious ? CURIOUS[3] : 0,
    shake: within(t, SHAKE[0], SHAKE[1]) ? 1 : 0,
  };
}

// ---- the run ----

/** Everything the drawing needs at one instant. Angles in degrees, lengths in the glyph's units. */
export interface Pose {
  /** The head: its turn, where it shifts to, how it is squashed. */
  rot: number;
  tx: number;
  ty: number;
  sx: number;
  sy: number;
  /** The eyes, within the face. */
  eyeX: number;
  eyeY: number;
  /** How open the lids are (1 is at rest), and how wide each eye is drawn. */
  lid: number;
  wideL: number;
  wideR: number;
  /** What is on the head, relative to it: the leaf, a rabbit's ears, the hat's tail. */
  leaf: number;
  ears: number;
  tail: number;
}

export const STEP = 1 / 240;
export const SAMPLES_PER_S = 60;

/**
 * A loop, simulated: `at` moves every part one step on and says how it all
 * stands. The loop is run three times and the third kept, by which time every
 * part is in the rhythm it will keep, so the last frame meets the first.
 */
export function run(seconds: number, at: (t: number) => Pose): Pose[] {
  const poses: Pose[] = [];
  const perSample = Math.round(1 / (STEP * SAMPLES_PER_S));
  const steps = Math.round(seconds / STEP);
  for (let pass = 0; pass < 3; pass++) {
    for (let i = 0; i < steps; i++) {
      const pose = at(i * STEP);
      if (pass === 2 && i % perSample === 0) poses.push(pose);
    }
  }
  // The loop closes on its own first frame.
  poses.push(poses[0]);
  return poses;
}

/** The idle, simulated. */
export function simulateIdle(): Pose[] {
  const gazeX = new SecondOrder(EYES);
  const gazeY = new SecondOrder(EYES);
  const turn = new SecondOrder(HEAD);
  const rise = new SecondOrder(NECK);
  const tilt = new SecondOrder(TILT);
  const shimmy = new SecondOrder(SHIMMY);
  const perk = new SecondOrder(PERK);
  const arc = new SecondOrder(ARC);
  const lid = new SecondOrder(LID_OPEN, 1);
  const breath = new SecondOrder(INHALE);
  const body = new Carried(BODY.f, BODY.zeta);
  const leaf = new Carried(LEAF.f, LEAF.zeta);
  const ears = new Carried(EARS.f, EARS.zeta);
  const tail = new Carried(TAIL.f, TAIL.zeta);

  return run(IDLE_LOOP_S, (t) => {
    const intent = intentAt(t);
    const phase = (2 * Math.PI * t) / IDLE_LOOP_S;

    // The eyes go first, and the head goes where they went.
    const gx = gazeX.step(STEP, intent.gazeX);
    const gy = gazeY.step(STEP, intent.gazeY);
    const hx = turn.step(STEP, gx);
    const hy = rise.step(STEP, gy * 0.6 + intent.nod);
    const air = breath.tune(intent.inhale > breath.value ? INHALE : EXHALE).step(STEP, intent.inhale);
    const open = lid.tune(intent.lid < lid.value ? LID_SHUT : LID_OPEN).step(STEP, intent.lid);
    const shut = 1 - clamp(open, 0, 1);

    // A held pose that is quite still reads as dead: a slow sway stays under everything.
    const rot =
      hx * TURN_DEG + tilt.step(STEP, intent.tilt) + shimmy.step(STEP, intent.shake) * SHIMMY_DEG + 0.3 * Math.sin(2 * phase + 0.6);
    // A turn goes along an arc, lowest where it is fastest.
    const speed = turn.velocity ** 2;
    const duck = arc.step(STEP, (DUCK * speed) / (speed + DUCK_SPEED ** 2));
    const ty = hy * LIFT + duck - air * BREATH_RISE + 0.05 * Math.sin(5 * phase);

    // Squash and stretch are the body's lag, and what it gains one way it
    // loses the other. A blink presses it a little; a breath fills it.
    const sy = 1 - soft(body.lag(STEP, ty) * GIVE, GIVE_MOST) + air * BREATH_SWELL - 0.02 * shut;
    const thrown = (part: Carried, by: Hung) => part.lag(STEP, by.sway * rot + by.heave * ty);
    const breeze = 1.3 * Math.sin(3 * phase) + 0.7 * Math.sin(8 * phase + 1.1);
    const leafNow = perk.step(STEP, intent.perk) + breeze + thrown(leaf, LEAF);
    const earsNow = thrown(ears, EARS);
    const swing = thrown(tail, TAIL);

    // The eye has only so far to go: it nears its limit, it does not hit it.
    const eyeX = soft(gx - EYES_YIELD * hx, EYE_REACH);
    const towards = (side: number) => clamp((side * eyeX) / 2, 0, 1);
    return {
      rot,
      tx: hx * SHIFT,
      ty,
      sx: 1 / Math.sqrt(sy),
      sy,
      eyeX,
      eyeY: gy - 0.35 * hy + 0.3 * shut,
      lid: clamp(open, 0.04, 1.3),
      // The eye on the side it turns to is foreshortened: a flat face reads as turning.
      wideL: 1 - 0.12 * towards(-1) + 0.03 * towards(1),
      wideR: 1 - 0.12 * towards(1) + 0.03 * towards(-1),
      leaf: leafNow,
      ears: earsNow,
      tail: swing < 0 ? soft(swing, TAIL_IN) : swing,
    };
  });
}

// ---- baking ----

/**
 * Which samples a straight line between its neighbours cannot stand in for,
 * each column allowed its own error. Most of a held pose goes; every frame of
 * a blink stays.
 */
export function keep(rows: number[][], tolerance: number[]): number[] {
  const kept = new Uint8Array(rows.length);
  kept[0] = kept[rows.length - 1] = 1;
  const spans: [number, number][] = [[0, rows.length - 1]];
  while (spans.length) {
    const [a, b] = spans.pop()!;
    let worst = 1;
    let at = -1;
    for (let i = a + 1; i < b; i++) {
      const u = (i - a) / (b - a);
      for (let k = 0; k < tolerance.length; k++) {
        const off = Math.abs(rows[i][k] - (rows[a][k] + (rows[b][k] - rows[a][k]) * u)) / tolerance[k];
        if (off > worst) {
          worst = off;
          at = i;
        }
      }
    }
    if (at > 0) {
      kept[at] = 1;
      spans.push([a, at], [at, b]);
    }
  }
  return Array.from(kept.keys()).filter((i) => kept[i]);
}

const n = (value: number, digits = 3) => String(Number(value.toFixed(digits)));

function keyframes(name: string, poses: Pose[], columns: (pose: Pose) => number[], tolerance: number[], css: (v: number[]) => string): string {
  const rows = poses.map(columns);
  const last = rows.length - 1;
  const frames = keep(rows, tolerance).map((i) => `${n((i / last) * 100, 4)}%{transform:${css(rows[i])}}`);
  return `@keyframes ${name}{${frames.join("")}}`;
}

/** A run as CSS: one set of keyframes for each part that moves, named `prefix`-head and so on. */
export function bake(prefix: string, poses: Pose[]): string {
  const swing = (name: string, of: (pose: Pose) => number) =>
    keyframes(name, poses, (p) => [of(p)], [0.25], ([angle]) => `rotate(${n(angle, 2)}deg)`);
  return [
    keyframes(
      `${prefix}-head`,
      poses,
      (p) => [p.rot, p.tx, p.ty, p.sx, p.sy],
      [0.08, 0.02, 0.02, 0.003, 0.003],
      ([rot, tx, ty, sx, sy]) => `rotate(${n(rot)}deg) translate(${n(tx)}px,${n(ty)}px) scale(${n(sx, 4)},${n(sy, 4)})`
    ),
    keyframes(`${prefix}-gaze`, poses, (p) => [p.eyeX, p.eyeY], [0.02, 0.02], ([x, y]) => `translate(${n(x)}px,${n(y)}px)`),
    keyframes(`${prefix}-eye-l`, poses, (p) => [p.wideL, p.lid], [0.01, 0.02], ([wide, lid]) => `scale(${n(wide)},${n(lid)})`),
    keyframes(`${prefix}-eye-r`, poses, (p) => [p.wideR, p.lid], [0.01, 0.02], ([wide, lid]) => `scale(${n(wide)},${n(lid)})`),
    swing(`${prefix}-leaf`, (p) => p.leaf),
    swing(`${prefix}-ears`, (p) => p.ears),
    swing(`${prefix}-tail`, (p) => p.tail),
  ].join("\n");
}

/** The idle as CSS. */
export function bakeIdle(poses: Pose[] = simulateIdle()): string {
  return bake("av-i", poses);
}
