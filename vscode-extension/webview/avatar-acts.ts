// What the avatar does of its own accord: it greets, waits to be answered, is
// glad or sorry at how a turn went, nods off, minds being touched, and hides
// from too much typing. Each is a script, as the idle's is; a small live rig
// gets every part there by its own dynamics.
//
// An act never puts the avatar anywhere: it adds to whatever carries it. The
// one that hides only says how much of it should show over the composer, and
// whoever carries it takes it there and back.
//
// No DOM in here.

import { Carried, clamp, type Dynamics, LEAF, SecondOrder, soft } from "./avatar-rig";

export type ActName =
  | "greets" | "waits" | "relieved" | "cheers" | "laments" | "nods-off" | "wakes"
  | "poked" | "petted" | "dizzy" | "hides" | "comes-out";

/** What it may do unasked: the settings turn each of them off. */
export interface Manners {
  greets: boolean;
  waits: boolean;
  cheers: boolean;
  sleeps: boolean;
  touch: boolean;
  hides: boolean;
}

/** Something happened in the panel that it has feelings about. */
export type Cue = "greet" | "answered" | "refused" | "went-well" | "went-wrong";

const hearers = new Set<(cue: Cue) => void>();

export function cue(kind: Cue): void {
  hearers.forEach((hear) => hear(kind));
}

export function onCue(hear: (cue: Cue) => void): () => void {
  hearers.add(hear);
  return () => hearers.delete(hear);
}

interface Point {
  x: number;
  y: number;
}

/** What is around it, in px from the middle of its face. */
export interface Around {
  /** Its box, in px. */
  size: number;
  /** The card it waits on, and the pointer; null when there is none. */
  card: Point | null;
  pointer: Point | null;
  /** How wound up the typing has it, and how hard the last keys made it start: both 0 to 1. */
  nerves: number;
  flinch: number;
}

/** What shows beside it: each is the time, in seconds, since that effect began. */
export interface Effects {
  sparkles?: number;
  stars?: number;
  bang?: number;
  heart?: number;
  /** A drop of sweat: how much of it is there, 0 to 1. */
  sweat?: number;
}

/** What it means to be doing at an instant. Left out, a part is at rest. */
interface Intent {
  /** Off where it stands, in px for a box of 88. */
  x?: number;
  y?: number;
  tilt?: number;
  /** Taller than at rest (above 0) or flatter (below). */
  stretch?: number;
  lookX?: number;
  lookY?: number;
  /** How open its eyes are; left out, it blinks by itself. */
  lid?: number;
  /** Pleased: its eyes are two arches. 0 or 1. */
  glad?: number;
  leaf?: number;
  /** Trembling: of the whole of it, in px, and of the leaf, in degrees. */
  shiver?: number;
  quiver?: number;
  /** How the little it moves off its spot is done. */
  gait?: Dynamics;
  /** Hiding: how much of it shows over the composer, in px for a box of 88, and how it gets there. */
  lair?: number;
  pace?: Dynamics;
  fx?: Effects;
  asleep?: boolean;
  /** Nothing more will change until it is told something new. */
  held?: boolean;
  done?: boolean;
}

interface Act {
  /** Jumps: when, in seconds, and how high, in px for a box of 88. */
  hops?: [at: number, height: number][];
  at(t: number, around: Around): Intent;
}

/** What an act adds to the avatar at one instant, over whatever else moves it. */
export interface Acted {
  tilt: number;
  leaf: number;
  lookX: number;
  lookY: number;
  sx: number;
  sy: number;
  /** Off where it stands, in px: a shiver or a nudge sideways, a jump or a droop up and down. */
  dx: number;
  dy: number;
  lid: number;
  glad: number;
  fx: Effects;
  /** Hiding: how much of it shows over the composer, in px for a box of 88; null when it is not. */
  lair: number | null;
  pace: Dynamics;
  asleep: boolean;
  /** Nothing in it will change until it is told something new: no frame need ask. */
  still: boolean;
}

const STEP: Dynamics = { f: 2.2, zeta: 0.8, r: 0.4 };
const EYES: Dynamics = { f: 5, zeta: 0.85, r: 0.2 };
const HEAD: Dynamics = { f: 2.4, zeta: 0.45, r: -0.5 };
const BODY: Dynamics = { f: 3.4, zeta: 0.42, r: 0 };
const STEM: Dynamics = { f: 2.4, zeta: 0.32, r: 0 };
const LID_SHUT: Dynamics = { f: 12, zeta: 1, r: 0 };
const LID_OPEN: Dynamics = { f: 6, zeta: 0.6, r: 0 };
// The arches spring up, a little past where they stay.
const GLAD: Dynamics = { f: 6.5, zeta: 0.5, r: 0 };
/** What pulls a jump back down, in px/s2 for a box of 88. */
const GRAVITY = 1900;
const BOX = 88;
/** This many frames with nothing changing, and it is still. */
const STILL_FRAMES = 12;

export const RESTING: Acted = {
  tilt: 0, leaf: 0, lookX: 0, lookY: 0, sx: 1, sy: 1, dx: 0, dy: 0, lid: 1, glad: 0, fx: {}, lair: null, pace: STEP,
  asleep: false, still: true,
};

/** One avatar's acting: told what to play, asked once a frame what that adds. */
export class Actor {
  private act: Act | null = null;
  private name: ActName | null = null;
  private began = 0;
  private fired = 0;
  private quiet = STILL_FRAMES;
  private was = "";
  private x = new SecondOrder(STEP);
  private y = new SecondOrder(STEP);
  private tilt = new SecondOrder(HEAD);
  private stretch = new SecondOrder(BODY);
  private lookX = new SecondOrder(EYES);
  private lookY = new SecondOrder(EYES);
  private lid = new SecondOrder(LID_OPEN, 1);
  private glad = new SecondOrder(GLAD);
  private leaf = new SecondOrder(STEM);
  private thrown = new Carried(LEAF.f, LEAF.zeta);
  private body = new Carried(3, 0.45);
  private hop = { y: 0, v: 0 };
  private landing = { x: 0, v: 0 };

  /** Start an act now; what it was doing is dropped where it stands. */
  play(name: ActName, now: number): void {
    this.act = ACTS[name];
    this.name = name;
    this.began = now;
    this.fired = 0;
    this.quiet = 0;
  }

  /** Drop what it is doing: every part goes back to rest by itself. */
  stop(): void {
    this.act = null;
    this.name = null;
    this.quiet = 0;
  }

  get playing(): ActName | null {
    return this.name;
  }

  step(now: number, dt: number, around: Around): Acted {
    if (!this.act && this.quiet >= STILL_FRAMES) return RESTING;
    const t = (now - this.began) / 1000;
    const want: Intent = this.act ? this.act.at(t, around) : {};
    const size = around.size / BOX;

    const hops = this.act?.hops ?? [];
    while (this.fired < hops.length && t >= hops[this.fired][0]) {
      this.hop.v = -Math.sqrt(2 * GRAVITY * hops[this.fired][1]);
      this.fired += 1;
    }
    if (this.hop.y < 0 || this.hop.v < 0) {
      this.hop.v += GRAVITY * dt;
      this.hop.y += this.hop.v * dt;
      if (this.hop.y >= 0) {
        // It lands: the ground gives the jump back as a squash.
        this.landing.v -= this.hop.v * 0.02;
        this.hop.y = 0;
        this.hop.v = 0;
      }
    }
    this.landing.v += (-260 * this.landing.x - 15 * this.landing.v) * dt;
    this.landing.x += this.landing.v * dt;

    const gait = want.gait ?? STEP;
    const x = this.x.tune(gait).step(dt, want.x ?? 0);
    const y = this.y.tune(gait).step(dt, want.y ?? 0);
    const up = (y + this.hop.y) / (BOX / 28);
    const tilt = this.tilt.step(dt, want.tilt ?? 0);
    const sy = clamp(
      1 + this.stretch.step(dt, want.stretch ?? 0) + this.landing.x - soft(this.body.lag(dt, up) * 0.2, 0.1),
      0.72,
      1.2
    );

    // Left to itself it blinks: every three seconds or so, a tenth of one.
    const blinking = !!this.act && want.lid === undefined && !want.glad && t % 3.1 > 2.2 && t % 3.1 < 2.29;
    const lidWant = blinking ? 0.03 : want.lid ?? 1;
    const lid = this.lid.tune(lidWant < this.lid.value ? LID_SHUT : LID_OPEN).step(dt, lidWant);

    const shiver = want.shiver ?? 0;
    const tremble = Math.sin(t * 2 * Math.PI * 13) + 0.5 * Math.sin(t * 2 * Math.PI * 7.7 + 1);
    const quiver = (want.quiver ?? 0) * Math.sin(t * 2 * Math.PI * 7.3);
    const leaf = this.leaf.step(dt, want.leaf ?? 0) + this.thrown.lag(dt, LEAF.sway * tilt + LEAF.heave * up) + quiver;

    const acted: Acted = {
      tilt: tilt + tremble * shiver * 0.5,
      leaf,
      lookX: clamp(this.lookX.step(dt, want.lookX ?? 0), -2.2, 2.2),
      lookY: clamp(this.lookY.step(dt, want.lookY ?? 0), -1.6, 1.6),
      sx: 1 / Math.sqrt(sy),
      sy,
      dx: (x + tremble * shiver * 0.62) * size,
      dy: (y + this.hop.y) * size,
      lid: clamp(lid, 0.04, 1.3),
      glad: clamp(this.glad.step(dt, want.glad ?? 0), 0, 1.25),
      fx: want.fx ?? {},
      lair: want.lair ?? null,
      pace: want.pace ?? STEP,
      asleep: want.asleep ?? false,
      still: false,
    };
    if (want.done) this.stop();

    // Still, once nothing has changed that anyone could see and nothing means to:
    // a tenth of a degree or of a pixel, a hundredth of anything else.
    const drawn =
      [acted.tilt, acted.leaf, acted.dx, acted.dy].map((value) => value.toFixed(1)).join() +
      [acted.lookX, acted.lookY, acted.sy, acted.lid, acted.glad].map((value) => value.toFixed(2)).join();
    this.quiet = drawn === this.was && (!this.act || want.held) ? this.quiet + 1 : 0;
    this.was = drawn;
    acted.still = this.quiet >= STILL_FRAMES;
    return acted;
  }
}

// ---- what the scripts are written with ----

const within = (t: number, from: number, to: number) => t >= from && t < to;
/** 0 before `from`, 1 after `to`, eased between. */
const over = (t: number, from: number, to: number) => {
  const u = clamp((t - from) / (to - from), 0, 1);
  return u * u * (3 - 2 * u);
};
/** A swing that dies away: `hz` times a second, gone after `lasts` seconds. */
const ringing = (t: number, from: number, hz: number, lasts: number) =>
  t < from ? 0 : Math.sin((t - from) * 2 * Math.PI * hz) * Math.max(0, 1 - (t - from) / lasts);
/** Towards something: eyes all the way round for anything a box away or more. */
const towards = (to: Point, size: number, reach = 1.5) => {
  const far = Math.hypot(to.x, to.y) || 1;
  const pull = Math.min(1, far / (size * 0.9));
  return { x: (to.x / far) * pull * reach, y: (to.y / far) * pull * reach * 0.8 };
};

// ---- hello ----

/** It is where it was going: a little jump, and a wave. */
const GREETS: Act = {
  hops: [[0.34, 6]],
  at(t) {
    return {
      // It gathers itself, and jumps.
      stretch: within(t, 0.16, 0.34) ? -0.09 : 0,
      lid: t < 0.6 ? 1.15 : 1.05,
      // Pleased to see you, for as long as it waves.
      glad: within(t, 0.72, 1.55) ? 1 : 0,
      tilt: 5 * over(t, 0.5, 0.7) * (1 - over(t, 1.5, 1.8)),
      leaf: within(t, 0.6, 1.55) ? -12 + 17 * Math.sin((t - 0.6) * 2 * Math.PI * 3.3) : 0,
      done: t > 2,
    };
  },
};

// ---- waiting on you ----

const WAIT_LOOP_S = 4.2;
/** It waves and taps this many times over, and then only stands there with its leaf up. */
const WAIT_LOOPS = 5;

/** It has asked for something and cannot go on: it looks at the card, puts its leaf up, taps its foot. */
const WAITS: Act = {
  hops: Array.from({ length: WAIT_LOOPS }, (_, loop) => [
    [loop * WAIT_LOOP_S + 1.85, 2.2],
    [loop * WAIT_LOOP_S + 2.12, 2.2],
  ] as [number, number][]).flat(),
  at(t, around) {
    const patient = t >= WAIT_LOOPS * WAIT_LOOP_S;
    const p = patient ? 0.2 : t % WAIT_LOOP_S;
    const card = towards(around.card ?? { x: -around.size * 2, y: -around.size }, around.size);
    const atCard = p < 1.6 || p >= 2.75;
    // The leaf goes up like a hand and waves.
    const waving = !patient && (within(p, 0.25, 1.45) || within(p, 3, 3.9));
    return {
      lookX: atCard ? card.x : 0.1,
      lookY: atCard ? card.y : -0.1,
      tilt: atCard ? 4.5 * Math.sign(card.x || -1) : 3,
      lid: within(p, 1.6, 1.69) || within(p, 2.75, 2.84) ? 0.03 : atCard ? 1 : 1.14,
      leaf: -16 + (waving ? 15 * Math.sin(p * 2 * Math.PI * 3.2) : 0),
      stretch: atCard ? 0.02 : 0,
      fx: { bang: patient ? 0.5 : within(p, 2.9, 3.95) ? p - 2.9 : undefined },
      held: patient,
    };
  },
};

/** Answered: it lets its breath go, nods, and gets on with it. */
const RELIEVED: Act = {
  at(t) {
    return {
      stretch: within(t, 0, 0.35) ? 0.07 : within(t, 0.35, 0.8) ? -0.05 : 0,
      y: within(t, 0.85, 0.99) || within(t, 1.2, 1.34) ? 3.2 : 0,
      gait: { f: 4, zeta: 0.6, r: 0 },
      lid: within(t, 0.35, 0.9) ? 0.5 : 1,
      // Glad of it, all through the nod.
      glad: within(t, 0.42, 1.45) ? 1 : 0,
      lookY: within(t, 0.85, 1.4) ? 0.5 : 0,
      leaf: 6 * (1 - over(t, 0.3, 1.2)),
      done: t > 1.9,
    };
  },
};

// ---- it went well, it went wrong ----

const CHEERS: Act = {
  hops: [[0.24, 17], [0.86, 6]],
  at(t) {
    const glad = within(t, 0.56, 1.75);
    return {
      // It gathers itself, and goes up.
      stretch: within(t, 0, 0.22) ? -0.13 : within(t, 0.22, 0.42) ? 0.07 : 0,
      tilt: 7 * ringing(t, 0.62, 4.6, 1.1),
      // Wide-eyed on the way up, and its eyes arch with pleasure once it is down.
      lid: glad ? 0.42 : t < 0.22 ? 0.9 : t < 0.56 ? 1.18 : 1.05,
      glad: glad ? 1 : 0,
      lookY: within(t, 0.22, 0.6) ? -0.9 : t < 0.22 ? 0.5 : 0,
      leaf: -8 * (1 - over(t, 1.4, 2)),
      fx: { sparkles: within(t, 0.3, 1.9) ? t - 0.3 : undefined },
      done: t > 2.4,
    };
  },
};

const LAMENTS: Act = {
  at(t) {
    const down = over(t, 0.55, 1.15) * (1 - over(t, 2.35, 2.85));
    return {
      // It flinches first, eyes shut tight, and only then droops.
      stretch: within(t, 0, 0.5) ? -0.14 : -0.04 * down - 0.035 * ringing(t, 1.35, 0.9, 1.1),
      shiver: within(t, 0.08, 0.55) ? 0.9 * (1 - over(t, 0.08, 0.55)) : 0,
      y: 4 * down,
      gait: { f: 1.6, zeta: 0.9, r: 0 },
      tilt: 6.5 * down + 4 * ringing(t, 2.4, 6, 0.5),
      lid: t < 0.5 ? 0.05 : t < 2.35 ? 0.55 : 1,
      lookX: -0.35 * down,
      lookY: 1.15 * down,
      // The leaf wilts.
      leaf: 40 * down,
      fx: { sweat: clamp((t - 0.7) / 0.3, 0, 1) * (1 - over(t, 2.2, 2.6)) },
      done: t > 3.1,
    };
  },
};

// ---- nobody about ----

/** Asleep from here on, in seconds: what it does until then is the nodding off. */
const ASLEEP_AT = 3.6;

/** Lids heavier and heavier, the head dropping, one start, and then asleep until woken. */
const NODS_OFF: Act = {
  at(t) {
    const nod = Math.min(1, over(t, 0.4, 1.5) * (1 - over(t, 1.6, 1.72)) + over(t, 1.95, 3.3));
    return {
      lid: t < 0.5 ? 0.8 : t < 1.55 ? 0.42 : t < 1.75 ? 0.95 : t < 2.9 ? 0.34 : 0.04,
      y: 3.6 * nod,
      gait: { f: 1.4, zeta: 0.85, r: 0 },
      tilt: 7.5 * nod,
      lookY: 0.9 * nod,
      stretch: -0.03 * over(t, 2.9, 3.5),
      leaf: 24 * nod,
      // The breathing and the zs are the stylesheet's from here: nothing left for a frame to do.
      asleep: t >= ASLEEP_AT,
      held: t >= ASLEEP_AT,
    };
  },
};

/** Woken with a start. */
const WAKES: Act = {
  hops: [[0.02, 7]],
  at(t) {
    return {
      lid: t < 0.9 ? 1.28 : within(t, 1.05, 1.14) || within(t, 1.3, 1.39) ? 0.03 : 1,
      stretch: t < 0.2 ? 0.11 : 0,
      // Who? Where?
      lookX: within(t, 0.25, 0.55) ? -1.3 : within(t, 0.55, 0.9) ? 1.3 : 0,
      tilt: 5 * ringing(t, 0.9, 7, 0.45),
      leaf: -14 * (1 - over(t, 0.3, 1.2)),
      done: t > 1.8,
    };
  },
};

// ---- touched ----

/** Poked: it gives under the finger, blinks, and looks to see who that was. */
const POKED: Act = {
  at(t, around) {
    const side = Math.sign(around.pointer?.x ?? 1) || 1;
    const who = towards(around.pointer ?? { x: 60 * side, y: -20 }, around.size);
    return {
      stretch: t < 0.1 ? -0.15 : 0,
      x: t < 0.12 ? -4 * side : 0,
      gait: { f: 5, zeta: 0.45, r: 0 },
      tilt: t < 0.14 ? -8 * side : 0,
      lid: t < 0.16 ? 0.04 : 1.08,
      lookX: t < 0.2 ? 0 : who.x,
      lookY: t < 0.2 ? 0 : who.y,
      done: t > 0.95,
    };
  },
};

/** Stroked: it leans into the hand, eyes arched, and purrs. */
const PETTED: Act = {
  at(t) {
    const liking = over(t, 0, 0.35) * (1 - over(t, 2.1, 2.6));
    return {
      tilt: 6.5 * liking * Math.sin(t * 2 * Math.PI * 0.9),
      lid: 1 - 0.6 * liking,
      glad: liking > 0.5 ? 1 : 0,
      stretch: -0.035 * liking + 0.012 * liking * Math.sin(t * 2 * Math.PI * 3.4),
      shiver: 0.22 * liking,
      leaf: 13 * liking * Math.sin(t * 2 * Math.PI * 3.8),
      lookY: 0.25 * liking,
      fx: { heart: within(t, 0.55, 2.2) ? t - 0.55 : undefined },
      done: t > 2.8,
    };
  },
};

/** Thrown at a wall too hard: it sees stars for a while. The throw itself is not this act's. */
const DIZZY: Act = {
  at(t) {
    const reeling = t < 2.16;
    const fading = 1 - over(t, 1.36, 2.16);
    return {
      tilt: reeling ? 9 * fading * Math.sin(t * 2 * Math.PI * 1.5) : 5 * ringing(t, 2.16, 8, 0.45),
      lid: reeling ? 0.72 : within(t, 2.28, 2.37) ? 0.03 : 1,
      // Its eyes go round.
      lookX: reeling ? 1.15 * fading * Math.cos(t * 2 * Math.PI * 2.1) : 0,
      lookY: reeling ? 0.75 * fading * Math.sin(t * 2 * Math.PI * 2.1) : 0,
      leaf: reeling ? 10 * fading * Math.sin(t * 2 * Math.PI * 1.5 + 1) : 0,
      fx: { stars: reeling ? t : undefined },
      done: t > 2.8,
    };
  },
};

// ---- behind the composer ----

/** How much of its box shows over the composer: all of it, down to under its eyes, only its leaf. */
export const LAIR = { out: 88, eyes: 70, leaf: 25 };
const DASH: Dynamics = { f: 2.9, zeta: 0.72, r: 0 };
const DROP_IN: Dynamics = { f: 3.6, zeta: 0.62, r: 0 };
const CREEP: Dynamics = { f: 3, zeta: 1, r: 0 };

/**
 * Typed at for too long: it runs to the middle of the composer, drops behind
 * it, and trembles there with only its leaf showing. Then, very slowly, it
 * comes up to look at you over the edge, and stays there, shaking, for as
 * long as the keys keep coming. Each of them makes it start.
 */
const HIDES: Act = {
  hops: [[0.02, 4]],
  at(t, around) {
    const there = t >= 0.78;
    // Up a little, down again at a sound, and then all the way: 0 is hidden, 1 is eyes over the edge.
    const up =
      over(t, 2.7, 3.7) * 0.42 - over(t, 3.85, 4) * 0.3 + over(t, 4.5, 6.3) * 0.88 -
      // And once there, a duck now and then.
      (t > 6.6 && (t - 6.6) % 2.9 < 0.26 ? 0.22 : 0);
    const seen = clamp(up - 0.2 * around.flinch, 0, 1);
    const hiding = t >= 1.15;
    const blinks = within(t, 5.1, 5.19) || within(t, 5.34, 5.43) || (t > 6.6 && (t - 6.6) % 2.9 > 1.5 && (t - 6.6) % 2.9 < 1.59);
    return {
      // A start where it stands, and then it is off.
      lair: t < 0.14 ? undefined : !there ? LAIR.out : LAIR.leaf + (LAIR.eyes - LAIR.leaf) * seen,
      pace: !there ? DASH : t < 1.15 ? DROP_IN : CREEP,
      stretch: t < 0.14 ? 0.09 : within(t, 0.78, 1) ? -0.1 : 0,
      lid: t < 0.14 ? 1.28 : hiding && blinks ? 0.03 : 1.22,
      // Straight at you, once it can see you.
      lookX: seen > 0.5 && t > 6.6 && (t - 6.6) % 5.8 > 3.6 && (t - 6.6) % 5.8 < 4.3 ? 0.9 : 0,
      lookY: seen > 0.3 ? -0.35 : 0.2,
      shiver: hiding ? 1.15 - 0.35 * seen : 0,
      quiver: hiding ? 5 + 5 * around.flinch : 0,
      leaf: hiding ? -6 : 0,
      // What it had worked up before it ran stays on it.
      fx: { sweat: Math.max(clamp((around.nerves - 0.5) * 5, 0, 1), clamp((t - 5.6) / 0.5, 0, 1)) },
    };
  },
};

/** The keys have stopped, or it was sent: it climbs out, and breathes out on its way back. */
const COMES_OUT: Act = {
  hops: [[0.72, 9]],
  at(t) {
    return {
      // Over the edge, and then it is let go: what carries it takes it home.
      lair: t < 0.62 ? LAIR.out : undefined,
      pace: { f: 3, zeta: 0.8, r: 0 },
      lid: t < 0.6 ? 1.15 : within(t, 1.5, 2.1) ? 0.5 : 1,
      // It is over: glad, as it lets its breath go.
      glad: within(t, 1.45, 2.25) ? 1 : 0,
      stretch: within(t, 1.45, 1.8) ? 0.07 : within(t, 1.8, 2.2) ? -0.05 : 0,
      shiver: 0.5 * (1 - over(t, 0, 0.6)),
      leaf: -6 * (1 - over(t, 0.6, 1.4)),
      done: t > 2.6,
    };
  },
};

const ACTS: Record<ActName, Act> = {
  greets: GREETS,
  waits: WAITS,
  relieved: RELIEVED,
  cheers: CHEERS,
  laments: LAMENTS,
  "nods-off": NODS_OFF,
  wakes: WAKES,
  poked: POKED,
  petted: PETTED,
  dizzy: DIZZY,
  hides: HIDES,
  "comes-out": COMES_OUT,
};
