import assert from "node:assert/strict";
import test from "node:test";

import { type Felt, isRapt, Nerves, onStroke, type Stroke, stroke, WAIT_MS } from "../avatar-nerves";

const DT = 1 / 60;
const FACE = { x: 400, y: 300, size: 88 };
/** Below it and to the right, where a composer's text would be. */
const CARET = { x: 520, y: 420 };

/** One avatar and a clock: keys go in at given times, and every frame in between is kept. */
function play(caret: { x: number; y: number } | null = CARET) {
  const nerves = new Nerves();
  let now = 1000;
  const frames: Felt[] = [];
  const run = (ms: number) => {
    for (const end = now + ms; now < end; ) {
      now += DT * 1000;
      frames.push(nerves.step(now, DT, FACE, caret));
    }
    return frames[frames.length - 1];
  };
  return {
    nerves,
    frames,
    run,
    key: (kind: "char" | "erase" | "send" = "char") => nerves.key(kind, now),
    /** Steady typing: a key every 150 ms. */
    type(ms: number) {
      for (const end = now + ms; now < end; ) {
        nerves.key("char", now);
        run(150);
      }
    },
    sleep: (ms: number) => (now += ms),
    stirred: () => nerves.stirred(now),
    from: (index: number) => frames.slice(index),
  };
}

test("left alone it adds nothing and asks for no frame", () => {
  const it = play();
  const felt = it.run(500);
  assert.deepEqual(
    { ...felt },
    { nerves: 0, tilt: 0, leaf: 0, lookX: 0, lookY: 0, sx: 1, sy: 1, hop: 0, rapt: false }
  );
  assert.equal(it.stirred(), false);
});

test("a key makes it hop and stretch, and it lands", () => {
  const it = play();
  it.key();
  it.run(600);
  const highest = Math.min(...it.frames.map((frame) => frame.hop));
  assert.ok(highest < -0.5 && highest > -4, `it rose ${-highest}px`);
  assert.ok(it.frames.every((frame) => frame.hop <= 0), "it went through the floor");
  assert.ok(Math.max(...it.frames.map((frame) => frame.sy)) > 1.02, "no stretch");
  assert.ok(Math.abs(it.frames[it.frames.length - 1].hop) < 0.05, "it never landed");
});

test("wound up, it hops higher", () => {
  const hop = (typedMs: number) => {
    const it = play();
    it.type(typedMs);
    it.run(400);
    const before = it.frames.length;
    it.key();
    it.run(300);
    return -Math.min(...it.from(before).map((frame) => frame.hop));
  };
  assert.ok(hop(5000) > hop(0) * 1.4, `${hop(5000)}px against ${hop(0)}px`);
});

test("a bigger avatar hops as much for its size", () => {
  const small = new Nerves();
  const big = new Nerves();
  small.key("char", 1000);
  big.key("char", 1000);
  const a = small.step(1050, 0.05, FACE, null);
  const b = big.step(1050, 0.05, { ...FACE, size: 176 }, null);
  assert.ok(Math.abs(b.hop / a.hop - 2) < 1e-9);
});

test("a character taken back makes it duck instead", () => {
  const it = play();
  it.key("erase");
  it.run(400);
  assert.ok(Math.min(...it.frames.map((frame) => frame.sy)) < 0.975, "it did not duck");
  assert.ok(it.frames.every((frame) => frame.hop === 0), "it hopped");
});

test("keys faster than it can hop count for less", () => {
  const stretch = (gapMs: number) => {
    const it = play();
    it.key();
    it.run(gapMs);
    const before = it.frames.length;
    it.key();
    it.run(250);
    const frames = it.from(before);
    return Math.max(...frames.map((frame) => frame.sy)) - frames[0].sy;
  };
  assert.ok(stretch(17) < stretch(300) * 0.4, `${stretch(17)} against ${stretch(300)}`);
});

test("typed at, its eyes go to the caret and its head leans that way", () => {
  const it = play();
  it.key();
  const felt = it.run(500);
  assert.ok(felt.rapt);
  assert.ok(felt.lookX > 0.8 && felt.lookY > 0.6, `it looks to ${felt.lookX}, ${felt.lookY}`);
  assert.ok(felt.tilt > 1.5, `it leans ${felt.tilt}`);
  // And the other way for a caret on the other side.
  const other = play({ x: 280, y: 420 });
  other.key();
  assert.ok(other.run(500).lookX < -0.8);
});

test("with nowhere to look, it only hops", () => {
  const it = play(null);
  it.key();
  const felt = it.run(500);
  assert.ok(Math.abs(felt.lookX) < 0.01 && Math.abs(felt.lookY) < 0.01);
});

test("steady typing winds it up; the first key finds it already alert", () => {
  const it = play();
  it.key();
  assert.ok(it.run(DT * 1000).nerves >= 0.2);
  it.type(1500);
  const early = it.frames[it.frames.length - 1].nerves;
  it.type(4000);
  const late = it.frames[it.frames.length - 1].nerves;
  assert.ok(early > 0.3 && early < 0.65, `after 1.5 s: ${early}`);
  assert.ok(late > 0.8 && late <= 1, `after 5.5 s: ${late}`);
});

test("when the keys stop it looks up, holding still, then back at the text", () => {
  const it = play();
  it.type(2000);
  it.run(1300 - 150);
  const up = it.frames[it.frames.length - 1];
  assert.ok(up.lookY < -0.3, `it does not look up: ${up.lookY}`);
  assert.ok(up.sy > 1.02, "it does not stand taller");
  assert.ok(Math.abs(up.tilt) > 2.5, "its head is not to one side");
  // The leaf, trembling until then, is still while it looks.
  const leaf = it.frames.slice(-12).map((frame) => frame.leaf);
  assert.ok(Math.max(...leaf) - Math.min(...leaf) < 0.6, "the leaf still trembles");
  const back = it.run(1100);
  assert.ok(back.lookY > 0.6, `it does not look back down: ${back.lookY}`);
  assert.ok(back.rapt);
});

test("the leaf trembles while it waits, more the more wound up it is", () => {
  const tremble = (typedMs: number) => {
    const it = play();
    it.type(typedMs);
    // Between its two looks up, with the last key's flick long died away.
    it.run(2200);
    const before = it.frames.length;
    it.run(500);
    const leaf = it.from(before).map((frame) => frame.leaf);
    return Math.max(...leaf) - Math.min(...leaf);
  };
  assert.ok(tremble(5000) > 1.3, `barely a tremble: ${tremble(5000)}`);
  assert.ok(tremble(5000) > tremble(300) * 1.25, `${tremble(5000)} against ${tremble(300)}`);
});

test("with no key for a while it gives up, and then asks for nothing more", () => {
  const it = play();
  it.type(3000);
  assert.ok(it.stirred());
  const waiting = it.run(WAIT_MS - 300);
  assert.ok(waiting.rapt && waiting.nerves > 0.4 && waiting.nerves < 0.6, `waiting at ${waiting.nerves}`);
  const done = it.run(2000);
  assert.equal(done.rapt, false);
  assert.ok(Math.abs(done.lookX) < 0.02 && Math.abs(done.lookY) < 0.02, "its eyes stayed on the text");
  assert.ok(Math.abs(done.leaf) < 0.1, "the leaf still trembles");
  assert.equal(it.stirred(), false);
});

test("at rest it winds down by the clock, with no frame asked of it", () => {
  const it = play();
  it.type(5000);
  const wound = it.frames[it.frames.length - 1].nerves;
  // Ten seconds with no frame at all, and then a key.
  it.sleep(10_000);
  it.key();
  const woke = it.run(DT * 1000);
  assert.ok(wound > 0.8 && woke.nerves < 0.3, `from ${wound} to ${woke.nerves}`);
  // Between two frames is not rest: a key there takes nothing off.
  const steady = play();
  steady.type(5000);
  const before = steady.frames[steady.frames.length - 1].nerves;
  steady.key();
  assert.ok(steady.run(DT * 1000).nerves >= before);
});

test("a send is one jump and the end of the wait", () => {
  const it = play();
  it.type(1500);
  const before = it.frames.length;
  it.key("send");
  const after = it.run(DT * 1000);
  assert.equal(after.rapt, false);
  it.run(700);
  const jump = Math.min(...it.from(before).map((frame) => frame.hop));
  assert.ok(jump < -4, `a jump of ${-jump}px`);
  // It comes down flat for a moment.
  assert.ok(Math.min(...it.from(before).map((frame) => frame.sy)) < 0.99, "no squash on landing");
});

test("told there is nothing to wait for, it stops watching at once", () => {
  const it = play();
  it.type(1500);
  it.nerves.calm();
  assert.equal(it.run(DT * 1000).rapt, false);
  assert.ok(Math.abs(it.run(1500).lookY) < 0.02);
});

// ---- what the composer tells them all ----

test("a key makes every avatar rapt; no key for a while and they give up", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  stroke("calm");
  assert.equal(isRapt(), false);
  stroke("char");
  assert.equal(isRapt(), true);
  t.mock.timers.tick(WAIT_MS - 1);
  assert.equal(isRapt(), true);
  // Another key starts the wait again.
  stroke("erase");
  t.mock.timers.tick(WAIT_MS - 1);
  assert.equal(isRapt(), true);
  t.mock.timers.tick(1);
  assert.equal(isRapt(), false);
});

test("sent, or nothing left to wait for, and it is over at once", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  for (const end of ["send", "calm"] as const) {
    stroke("char");
    assert.equal(isRapt(), true);
    stroke(end);
    assert.equal(isRapt(), false, end);
    // And the old wait does not come back to end it again.
    stroke("char");
    t.mock.timers.tick(WAIT_MS - 1);
    assert.equal(isRapt(), true, end);
    stroke("calm");
  }
});

test("every stroke is heard, with the field it was in, until one stops listening", () => {
  const heard: [Stroke, unknown][] = [];
  const stop = onStroke((kind, field) => heard.push([kind, field]));
  const field = { value: "a" } as unknown as HTMLTextAreaElement;
  stroke("char", field);
  stroke("send");
  stop();
  stroke("calm");
  assert.deepEqual(heard, [["char", field], ["send", null]]);
});
