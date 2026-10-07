// What typing does to the avatar. The composer says what happened in it; the
// pose every avatar takes comes from that, and the one that floats also feels
// each key: a hop, a duck, its eyes on the caret, a look up when the keys stop.
//
// No DOM in here: the field a keystroke names is only passed along.

import * as React from "react";

import { settled, type Spring, stepSpring } from "./avatar-motion";

/** In the composer: a character went in, one came out, it was sent, or there is nothing left to wait for. */
export type Stroke = "char" | "erase" | "send" | "calm";

/** This long with no key, in ms, and it gives up waiting. */
export const WAIT_MS = 5200;

type Feeler = (stroke: Stroke, field: HTMLTextAreaElement | null) => void;

let rapt = false;
let giveUp: ReturnType<typeof setTimeout> | undefined;
const watchers = new Set<() => void>();
const feelers = new Set<Feeler>();

/** Rapt or not from here on; rapt, it gives up by itself when no key follows. */
function setRapt(on: boolean): void {
  clearTimeout(giveUp);
  giveUp = on ? setTimeout(() => setRapt(false), WAIT_MS) : undefined;
  if (on === rapt) return;
  rapt = on;
  watchers.forEach((watcher) => watcher());
}

/** Tell the avatars what happened in the composer; `field` is where, for a key. */
export function stroke(kind: Stroke, field: HTMLTextAreaElement | null = null): void {
  feelers.forEach((feeler) => feeler(kind, field));
  setRapt(kind === "char" || kind === "erase");
}

/** Hear every stroke as it happens, as the floating avatar does. */
export function onStroke(feeler: Feeler): () => void {
  feelers.add(feeler);
  return () => feelers.delete(feeler);
}

const watch = (watcher: () => void) => {
  watchers.add(watcher);
  return () => watchers.delete(watcher);
};

/** Whether it is being typed at, or was until a moment ago: the anxious pose. */
export const isRapt = (): boolean => rapt;

export function useRapt(): boolean {
  return React.useSyncExternalStore(watch, isRapt);
}

// ---- what the keys do to one avatar ----

/** A key this long ago, in ms, and it is still being typed at. */
const TYPING_MS = 900;
/** It looks up when the keys stop, and once more while it waits. */
const PEEKS: [from: number, to: number][] = [
  [950, 1750],
  [3300, 3900],
];
/** How long steady typing takes to wind it all the way up, and how fast it comes down, in seconds. */
const WIND_UP_S = 3.2;
const WIND_DOWN_S = 1.3;
/** Waiting, with something written: half wound. */
const WAITING = 0.5;
/** The first key finds it at least this alert. */
const STARTLED = 0.22;
/** No frame for this long, in ms, and it was at rest, not between two frames. */
const ASLEEP_MS = 100;

/** Watching the caret: how far the eyes go, in the glyph's units, and how far the head leans. */
const WATCH = { x: 1.6, y: 1.25, tilt: 4 };
/** Up at whoever is typing: eyes to the middle and a little above it, head to one side, a little taller. */
const PEEK = { x: 0.1, y: -0.45, tilt: 4.5, tall: 0.035 };
const QUIVER_HZ = 7.3;
const QUIVER_DEG = 1.5;
/** What one key gives it: px/s upwards for a box of `HOP_BOX` px, and how much of a stretch. */
const HOP = 62;
const HOP_BOX = 88;
const STRETCH = 1.1;
/** Keys that come faster than this, in ms, count for less: it cannot hop that fast. */
const HOP_EVERY_MS = 110;

const still = (): Spring => ({ x: 0, v: 0 });
const clamp = (value: number, low: number, high: number) => Math.min(Math.max(value, low), high);

/** What the keys add to an avatar at one instant, over whatever else moves it. */
export interface Felt {
  /** How wound up it is: 0 at rest, 1 after a few seconds of steady typing. */
  nerves: number;
  /** Degrees the head tips and the leaf swings. */
  tilt: number;
  leaf: number;
  /** The eyes, in the glyph's units. */
  lookX: number;
  lookY: number;
  sx: number;
  sy: number;
  /** Off the ground, in px: upwards is negative. */
  hop: number;
  /** Typed at, or waited on: the eyes are on the caret, not the loop's own. */
  rapt: boolean;
}

export const UNFELT: Felt = { nerves: 0, tilt: 0, leaf: 0, lookX: 0, lookY: 0, sx: 1, sy: 1, hop: 0, rapt: false };

/** One avatar's nerves: told of every key, asked once a frame what they make of it. */
export class Nerves {
  private level = 0;
  private last = -Infinity;
  private stepped = -Infinity;
  private side = 1;
  private readonly tilt = still();
  private readonly leaf = still();
  private readonly lookX = still();
  private readonly lookY = still();
  private readonly sx: Spring = { x: 1, v: 0 };
  private readonly sy: Spring = { x: 1, v: 0 };
  private readonly hop = still();

  key(kind: "char" | "erase" | "send", now: number): void {
    // At rest nothing steps it: what it would have wound down by since is taken off here.
    if (now - this.stepped > ASLEEP_MS) this.level *= Math.exp(-(now - this.stepped) / 1000 / WIND_DOWN_S);
    this.stepped = now;
    const fresh = Math.min(1, (now - this.last) / HOP_EVERY_MS);
    const gain = (0.55 + 0.45 * this.level) * fresh;
    this.last = now;
    this.level = Math.max(this.level, STARTLED);
    this.side = -this.side;
    if (kind === "send") {
      // Out it goes: one jump, and nothing left to wait for.
      this.hop.v -= 250;
      this.sy.v += 2.1;
      this.sx.v -= 1.5;
      this.last = -Infinity;
      return;
    }
    if (kind === "erase") {
      // Taken back: it ducks.
      this.sy.v -= 1.3 * gain;
      this.sx.v += 0.95 * gain;
    } else {
      this.hop.v -= HOP * gain;
      this.sy.v += STRETCH * gain;
      this.sx.v -= STRETCH * 0.72 * gain;
    }
    this.tilt.v += this.side * 16 * gain;
    this.leaf.v -= this.side * 95 * gain;
  }

  /** Nothing left to wait for: it stops watching and winds down. */
  calm(): void {
    this.last = -Infinity;
  }

  /**
   * @param face the middle of its face and the size of its box, in the same pixels as `caret`
   * @param caret where the text is being written; null when nothing says
   */
  step(now: number, dt: number, face: { x: number; y: number; size: number }, caret: { x: number; y: number } | null): Felt {
    const since = now - this.last;
    const typing = since < TYPING_MS;
    const waiting = since < WAIT_MS;
    const want = typing ? 1 : waiting ? WAITING : 0;
    this.stepped = now;
    this.level += (want - this.level) * (1 - Math.exp(-dt / (want > this.level ? WIND_UP_S : WIND_DOWN_S)));

    const to = { tilt: 0, lookX: 0, lookY: 0, sx: 1, sy: 1 };
    const peeking = waiting && PEEKS.some(([from, upTo]) => since >= from && since < upTo);
    if (peeking) {
      to.lookX = PEEK.x * this.side;
      to.lookY = PEEK.y;
      to.tilt = PEEK.tilt * this.side;
      to.sy += PEEK.tall;
      to.sx -= PEEK.tall * 0.6;
    } else if (waiting && caret) {
      // From the middle of the face to the caret, as it watches a pointer.
      const dx = caret.x - face.x;
      const dy = caret.y - face.y;
      const far = Math.hypot(dx, dy) || 1;
      const pull = Math.min(1, far / (face.size * 0.9));
      to.lookX = (dx / far) * pull * WATCH.x;
      to.lookY = (dy / far) * pull * WATCH.y;
      to.tilt = (dx / far) * pull * WATCH.tilt;
    }

    Object.assign(this.tilt, stepSpring(this.tilt, to.tilt, dt, 150, 11));
    Object.assign(this.leaf, stepSpring(this.leaf, 0, dt, 85, 5.5));
    Object.assign(this.lookX, stepSpring(this.lookX, to.lookX, dt, 190, 15));
    Object.assign(this.lookY, stepSpring(this.lookY, to.lookY, dt, 190, 15));
    Object.assign(this.sx, stepSpring(this.sx, to.sx, dt, 240, 13));
    Object.assign(this.sy, stepSpring(this.sy, to.sy, dt, 240, 13));
    Object.assign(this.hop, stepSpring(this.hop, 0, dt, 420, 22));
    if (this.hop.x > 0) {
      // It lands: the ground gives the jump back as a squash.
      this.sy.v -= this.hop.v * 0.035;
      this.sx.v += this.hop.v * 0.026;
      this.hop.x = 0;
      this.hop.v *= -0.25;
    }

    // The leaf trembles on its stem, more the more wound up it is; still while
    // it looks up, and dying away as it gives up waiting.
    const fading = clamp((WAIT_MS - since) / 400, 0, 1);
    const quiver = peeking ? 0 : Math.sin((now / 1000) * 2 * Math.PI * QUIVER_HZ) * QUIVER_DEG * this.level * fading;
    return {
      nerves: this.level,
      tilt: this.tilt.x,
      leaf: this.leaf.x + quiver,
      lookX: this.lookX.x,
      lookY: this.lookY.x,
      sx: this.sx.x,
      sy: this.sy.x,
      hop: (this.hop.x * face.size) / HOP_BOX,
      rapt: waiting,
    };
  }

  /** Whether there is anything left to play out; when not, no frame need ask. */
  stirred(now: number): boolean {
    return (
      now - this.last < WAIT_MS ||
      !settled(this.tilt, 0, 0.05) || !settled(this.leaf, 0, 0.05) ||
      !settled(this.lookX, 0) || !settled(this.lookY, 0) ||
      !settled(this.sx, 1, 0.002) || !settled(this.sy, 1, 0.002) || !settled(this.hop, 0, 0.05)
    );
  }
}
