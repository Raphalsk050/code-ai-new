import assert from "node:assert/strict";
import test from "node:test";

import { type Acted, type ActName, Actor, type Around, cue, type Cue, LAIR, onCue } from "../avatar-acts";

const DT = 1 / 60;
const AROUND: Around = { size: 88, card: { x: -300, y: -120 }, pointer: { x: 40, y: -10 }, nerves: 0, flinch: 0 };
/** The acts that play out and are over; the others last until something ends them. */
const ENDING: ActName[] = ["greets", "relieved", "cheers", "laments", "wakes", "poked", "petted", "dizzy", "comes-out"];
const EVERY: ActName[] = [...ENDING, "waits", "nods-off", "hides"];

/** One avatar and a clock: an act goes in, and every frame after it is kept. */
function stage(around: Partial<Around> = {}) {
  const actor = new Actor();
  const here = { ...AROUND, ...around };
  let now = 1000;
  const frames: Acted[] = [];
  return {
    actor,
    frames,
    here,
    play: (name: ActName) => actor.play(name, now),
    run(seconds: number) {
      for (const end = now + seconds * 1000; now < end; ) {
        now += DT * 1000;
        frames.push(actor.step(now, DT, here));
      }
      return frames[frames.length - 1];
    },
  };
}

const least = (frames: Acted[], of: (frame: Acted) => number) => Math.min(...frames.map(of));
const most = (frames: Acted[], of: (frame: Acted) => number) => Math.max(...frames.map(of));

test("left alone it adds nothing and asks for no frame", () => {
  const it = stage();
  const acted = it.run(0.5);
  assert.equal(it.actor.playing, null);
  assert.equal(acted.still, true);
  assert.deepEqual(
    [acted.tilt, acted.leaf, acted.lookX, acted.lookY, acted.sx, acted.sy, acted.dx, acted.dy, acted.glad, acted.lair],
    [0, 0, 0, 0, 1, 1, 0, 0, 0, null]
  );
});

for (const name of ENDING) {
  test(`${name}: it plays out, and leaves it as it was`, () => {
    const it = stage();
    it.play(name);
    it.run(0.2);
    assert.equal(it.actor.playing, name);
    assert.equal(it.frames[it.frames.length - 1].still, false, "it asks for no frame while it plays");
    const acted = it.run(8);
    assert.equal(it.actor.playing, null, "it never ended");
    assert.equal(acted.still, true, "it goes on asking for frames");
    assert.ok(Math.abs(acted.tilt) < 0.05 && Math.abs(acted.leaf) < 0.05, `left leaning: ${acted.tilt}, ${acted.leaf}`);
    assert.ok(Math.abs(acted.dx) < 0.05 && Math.abs(acted.dy) < 0.05, `left off its spot: ${acted.dx}, ${acted.dy}`);
    assert.ok(Math.abs(acted.sy - 1) < 0.005 && acted.glad < 0.01 && acted.lair === null);
  });
}

test("no act takes it anywhere: what it adds stays about where it stands", () => {
  for (const name of EVERY) {
    const it = stage({ nerves: 1, flinch: 1 });
    it.play(name);
    it.run(9);
    assert.ok(most(it.frames, (f) => Math.abs(f.dx)) < 9, `${name} moved it ${most(it.frames, (f) => Math.abs(f.dx))}px sideways`);
    assert.ok(least(it.frames, (f) => f.dy) > -30 && most(it.frames, (f) => f.dy) < 9, `${name} moved it up or down`);
    assert.ok(least(it.frames, (f) => f.sy) >= 0.72 && most(it.frames, (f) => f.sy) <= 1.2, `${name} bent it out of shape`);
  }
});

test("glad, its eyes are arches; sorry, startled or dizzy, they are not", () => {
  for (const name of ["greets", "relieved", "cheers", "petted", "comes-out"] as ActName[]) {
    const it = stage();
    it.play(name);
    it.run(4);
    assert.ok(most(it.frames, (f) => f.glad) > 0.9, `${name} is never glad`);
  }
  for (const name of ["laments", "wakes", "poked", "dizzy", "waits", "nods-off", "hides"] as ActName[]) {
    const it = stage();
    it.play(name);
    it.run(4);
    assert.ok(most(it.frames, (f) => f.glad) < 0.02, `${name} looks glad`);
  }
});

test("a cheer jumps, lands, and throws sparks; a lament sinks, wilts and sweats", () => {
  const glad = stage();
  glad.play("cheers");
  glad.run(3);
  assert.ok(least(glad.frames, (f) => f.dy) < -10, `it rose ${-least(glad.frames, (f) => f.dy)}px`);
  assert.ok(glad.frames.some((f) => f.fx.sparkles !== undefined), "no sparks");
  assert.ok(glad.frames.every((f) => f.dy < 0.5), "it went through the floor");

  const sorry = stage();
  sorry.play("laments");
  sorry.run(2);
  const low = sorry.frames[sorry.frames.length - 1];
  assert.ok(low.dy > 2 && low.leaf > 25, `it droops ${low.dy}px, its leaf ${low.leaf}deg`);
  assert.ok((low.fx.sweat ?? 0) > 0.9, "it does not sweat");
  assert.ok(least(sorry.frames.slice(0, 20), (f) => f.lid) < 0.3, "it does not flinch first");
});

test("waiting, it waves and taps for a while, then stands still with its leaf up", () => {
  const it = stage();
  it.play("waits");
  it.run(8);
  assert.ok(least(it.frames, (f) => f.dy) < -1, "it never taps its foot");
  assert.ok(most(it.frames, (f) => f.leaf) - least(it.frames, (f) => f.leaf) > 15, "it never waves");
  assert.ok(it.frames.some((f) => f.fx.bang !== undefined), "it never says so");
  assert.ok(least(it.frames, (f) => f.lookX) < -0.8, "it does not look at the card");
  assert.ok(it.frames.every((f) => !f.still), "it stops while it should be waving");

  const patient = it.run(22);
  assert.equal(it.actor.playing, "waits", "it gave up waiting");
  assert.equal(patient.still, true, "it goes on asking for frames");
  assert.ok(patient.leaf < -12 && patient.fx.bang !== undefined, "it put its leaf down");
});

test("nodding off ends asleep, and asleep it asks for no frame", () => {
  const it = stage();
  it.play("nods-off");
  it.run(3);
  assert.ok(!it.frames.some((f) => f.asleep), "asleep before it has nodded off");
  // The leaf goes on swinging on its stem for a while after the head has dropped.
  const acted = it.run(6);
  assert.equal(it.actor.playing, "nods-off");
  assert.ok(acted.asleep && acted.still, `asleep ${acted.asleep}, still ${acted.still}`);
  assert.ok(acted.lid < 0.1 && acted.tilt > 5 && acted.leaf > 15, "it does not look asleep");

  it.play("wakes");
  it.run(0.3);
  const woken = it.frames[it.frames.length - 1];
  assert.ok(!woken.asleep && woken.lid > 0.9, "it wakes with its eyes shut");
});

test("hiding, it starts, runs, drops behind the composer and comes up to look", () => {
  const it = stage({ nerves: 0.8 });
  it.play("hides");
  assert.equal(it.run(0.1).lair, null, "it is off before it has started");
  assert.equal(it.run(0.4).lair, LAIR.out, "it does not run there standing");
  assert.equal(it.run(1.5).lair, LAIR.leaf, "more than its leaf shows");
  assert.ok(it.frames[it.frames.length - 1].fx.sweat === 1, "what it had worked up is gone");
  const peeking = it.run(4.5);
  assert.ok(peeking.lair !== null && peeking.lair > LAIR.eyes - 2, `it shows ${peeking.lair}px`);
  assert.ok(it.frames.slice(120).every((f) => !f.still), "it stops trembling");
  assert.equal(it.actor.playing, "hides", "it came out by itself");
});

test("hidden, every key makes it duck", () => {
  const calm = stage();
  calm.play("hides");
  const startled = stage({ flinch: 1 });
  startled.play("hides");
  const up = calm.run(6.5).lair!;
  const down = startled.run(6.5).lair!;
  assert.ok(up - down > 6, `from ${up} to ${down}`);
  assert.ok(down > LAIR.leaf, "a key sends it all the way down");
});

test("coming out, it climbs over the edge and is then let go", () => {
  const it = stage();
  it.play("comes-out");
  assert.equal(it.run(0.5).lair, LAIR.out);
  assert.equal(it.run(0.3).lair, null, "it holds on to where it is");
});

test("poked, it looks to see who that was", () => {
  const it = stage({ pointer: { x: -60, y: 0 } });
  it.play("poked");
  it.run(0.6);
  assert.ok(least(it.frames, (f) => f.sy) < 0.95, "it does not give under the finger");
  assert.ok(it.frames[it.frames.length - 1].lookX < -0.8, "it looks the wrong way");
});

test("what it adds is in its own box's pixels", () => {
  const big = stage();
  const small = stage({ size: 44 });
  big.play("cheers");
  small.play("cheers");
  big.run(1);
  small.run(1);
  const [high, low] = [least(big.frames, (f) => f.dy), least(small.frames, (f) => f.dy)];
  assert.ok(Math.abs(high - 2 * low) < 0.5, `${high} against ${low}`);
});

test("a new act takes over where the last one stood", () => {
  const it = stage();
  it.play("laments");
  const before = it.run(1.5);
  it.play("cheers");
  const after = it.run(DT);
  assert.equal(it.actor.playing, "cheers");
  assert.ok(Math.abs(after.leaf - before.leaf) < 6 && Math.abs(after.tilt - before.tilt) < 2, "it snapped to the new act");
});

test("a cue reaches whoever listens, until they stop", () => {
  const heard: Cue[] = [];
  const stop = onCue((kind) => heard.push(kind));
  cue("greet");
  cue("went-well");
  stop();
  cue("went-wrong");
  assert.deepEqual(heard, ["greet", "went-well"]);
});
