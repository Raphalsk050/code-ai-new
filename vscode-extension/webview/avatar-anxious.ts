// The avatar while someone types to it: it cannot wait to read it. The same
// rig as the idle acts it out, to another script, and the run is baked the
// same way. This is the loop every avatar plays; what the floating one adds
// from the keys themselves is in avatar-nerves.ts.

import {
  bake, Carried, clamp, type Dynamics, EARS, type Hung, LEAF, type Pose, run, SecondOrder, soft, STEP, TAIL, TAIL_IN, within,
} from "./avatar-rig";

/** One pass, in seconds: short, as it cannot keep still for long. */
export const ANXIOUS_LOOP_S = 4.8;

// Tenser than at rest: every part gets there sooner and rings for less.
const EYES: Dynamics = { f: 5.6, zeta: 0.85, r: 0.3 };
const HEAD: Dynamics = { f: 2, zeta: 0.5, r: -1 };
const NECK: Dynamics = { f: 2.6, zeta: 0.45, r: -0.4 };
const PERK: Dynamics = { f: 2.6, zeta: 0.4, r: 0 };
const HELD: Dynamics = { f: 3, zeta: 1, r: 0 };
const LID_SHUT: Dynamics = { f: 12, zeta: 1, r: 0 };
const LID_OPEN: Dynamics = { f: 6, zeta: 0.55, r: 0 };
const BLINK_S = 0.09;

const TURN_DEG = 2.2;
const SHIFT = 0.3;
const LIFT = 0.5;
const EYES_YIELD = 0.3;
const EYE_REACH = 2.2;
const BODY = { f: 3, zeta: 0.45 };
const GIVE = 0.2;
const GIVE_MOST = 0.1;

/** Bent over what is being written: units down, and how much that folds it. */
const LEAN = 0.42;
const HUNCH = 0.018;
/** Short breaths: this many to the loop, a third as deep as a calm one. */
const BREATHS = 4;
const BREATH_RISE = 0.2;
const BREATH_SWELL = 0.014;
/** The eyes are held wide the whole time: taller, and a little rounder. */
export const WIDE = 1.14;
export const ROUND = 1.07;

/** Where it looks: along the line being written, up at whoever writes it, back to the line. */
const GAZE: [at: number, x: number, y: number][] = [
  [0, -0.95, 1.25],
  [0.45, -0.35, 1.3],
  [0.85, 0.3, 1.3],
  [1.25, 0.95, 1.28],
  [1.7, 0.15, -0.5],
  [2.3, 1.2, 1.3],
  [2.75, -0.8, 1.3],
  [3.2, -0.15, 1.3],
  [3.65, 0.55, 1.3],
  [4.1, 1.2, 1.25],
];
/** Twice as it looks up, once as it looks back, once as it goes back to the start of the line. */
const BLINKS = [1.66, 1.93, 2.31, 4.74];
/** Wider still while it looks up: is that all of it? */
const LIDS: [from: number, to: number, open: number][] = [[1.75, 2.26, 1.28]];
/** It holds its breath while it looks up, and the leaf stands straighter. */
const HOLD: [from: number, to: number] = [1.7, 2.3];
const PERKED = -9;
const PERKED_MOST = -15;
/** And swallows as it looks back down. */
const GULP: [from: number, to: number, depth: number] = [2.5, 2.62, 1.2];

/** The anxious loop, simulated. */
export function simulateAnxious(): Pose[] {
  const gazeX = new SecondOrder(EYES, GAZE[0][1]);
  const gazeY = new SecondOrder(EYES, GAZE[0][2]);
  const turn = new SecondOrder(HEAD);
  const rise = new SecondOrder(NECK);
  const perk = new SecondOrder(PERK, PERKED);
  const held = new SecondOrder(HELD);
  const lid = new SecondOrder(LID_OPEN, WIDE);
  const body = new Carried(BODY.f, BODY.zeta);
  const leaf = new Carried(LEAF.f, LEAF.zeta);
  const ears = new Carried(EARS.f, EARS.zeta);
  const tail = new Carried(TAIL.f, TAIL.zeta);

  return run(ANXIOUS_LOOP_S, (t) => {
    const phase = (2 * Math.PI * t) / ANXIOUS_LOOP_S;
    let gaze = GAZE[0];
    for (const step of GAZE) if (t >= step[0]) gaze = step;
    const wide = LIDS.find(([from, to]) => within(t, from, to));
    const wanted = BLINKS.some((at) => within(t, at, at + BLINK_S)) ? 0.03 : wide ? wide[2] : WIDE;
    const holding = within(t, HOLD[0], HOLD[1]);

    const gx = gazeX.step(STEP, gaze[1]);
    const gy = gazeY.step(STEP, gaze[2]);
    const hx = turn.step(STEP, gx);
    const hy = rise.step(STEP, gy * 0.6 + (within(t, GULP[0], GULP[1]) ? GULP[2] : 0));
    const open = lid.tune(wanted < lid.value ? LID_SHUT : LID_OPEN).step(STEP, wanted);
    const shut = 1 - clamp(open, 0, 1);
    // Short breaths, and one held in while it looks up.
    const hold = held.step(STEP, holding ? 1 : 0);
    const air = (0.5 - 0.5 * Math.cos(BREATHS * phase)) * (1 - hold) + 0.9 * hold;

    // It cannot keep still: its weight goes from one side to the other.
    const rot = hx * TURN_DEG + 1 * Math.sin(2 * phase + 0.4) + 0.45 * Math.sin(5 * phase + 1.3);
    const ty = LEAN + hy * LIFT - air * BREATH_RISE;
    const sy = 1 - HUNCH - soft(body.lag(STEP, ty) * GIVE, GIVE_MOST) + air * BREATH_SWELL - 0.02 * shut;
    const thrown = (part: Carried, by: Hung) => part.lag(STEP, by.sway * rot + by.heave * ty);
    const leafNow = perk.step(STEP, holding ? PERKED_MOST : PERKED) + thrown(leaf, LEAF);
    const earsNow = thrown(ears, EARS);
    const swing = thrown(tail, TAIL);

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
      wideL: ROUND * (1 - 0.12 * towards(-1) + 0.03 * towards(1)),
      wideR: ROUND * (1 - 0.12 * towards(1) + 0.03 * towards(-1)),
      leaf: leafNow,
      ears: earsNow,
      tail: swing < 0 ? soft(swing, TAIL_IN) : swing,
    };
  });
}

/** The anxious loop as CSS. */
export function bakeAnxious(poses: Pose[] = simulateAnxious()): string {
  return bake("av-a", poses);
}
