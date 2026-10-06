import assert from "node:assert/strict";
import test from "node:test";

import { type Body, type Bounds, clampTo, glide, leanFor, settled, type Spring, stepSpring } from "../avatar-motion";

const ROOM: Bounds = { left: 8, top: 8, right: 400, bottom: 600 };

function run(body: Body, seconds: number, dt = 1 / 60): Body & { hits: number } {
  let hits = 0;
  let now: Body = body;
  for (let t = 0; t < seconds; t += dt) {
    const next = glide(now, dt, ROOM);
    if (next.hit) hits += 1;
    now = next;
  }
  return { ...now, hits };
}

test("a throw keeps going, slows, and stops", () => {
  const soon = run({ x: 200, y: 300, vx: 900, vy: 0 }, 0.1);
  assert.ok(soon.x > 260 && soon.vx > 0 && soon.vx < 900, `after 100ms: ${JSON.stringify(soon)}`);
  const rest = run({ x: 200, y: 300, vx: 900, vy: 0 }, 4);
  assert.equal(rest.vx, 0);
  assert.equal(rest.vy, 0);
  assert.ok(rest.x > soon.x);
});

test("it never leaves the room, however hard it is thrown", () => {
  for (const [vx, vy] of [[9000, 0], [-9000, 7000], [0, -20000], [30000, 30000]]) {
    let body: Body = { x: 200, y: 300, vx, vy };
    for (let i = 0; i < 600; i++) {
      body = glide(body, 1 / 60, ROOM);
      assert.ok(body.x >= ROOM.left && body.x <= ROOM.right && body.y >= ROOM.top && body.y <= ROOM.bottom);
    }
    assert.equal(body.vx, 0, `still moving after 10s: ${vx},${vy}`);
  }
});

test("a wall gives back less than it took, and reports the blow", () => {
  const before = { x: 398, y: 300, vx: 1200, vy: 0 };
  const after = glide(before, 1 / 60, ROOM);
  assert.equal(after.x, ROOM.right);
  assert.ok(after.vx < 0 && Math.abs(after.vx) < 1200 * 0.5);
  assert.ok(after.hit > 1000);
  assert.equal(glide({ x: 200, y: 300, vx: 100, vy: 0 }, 1 / 60, ROOM).hit, 0);
});

test("a long frame does not send it flying", () => {
  const body = glide({ x: 200, y: 300, vx: 600, vy: 0 }, 5, ROOM);
  assert.ok(body.x >= ROOM.left && body.x <= ROOM.right);
  let spring: Spring = { x: 0, v: 0 };
  spring = stepSpring(spring, 10, 5, 150, 11);
  assert.ok(Number.isFinite(spring.x) && Math.abs(spring.x - 10) < 1);
});

test("a spring overshoots and then settles on its target", () => {
  let spring: Spring = { x: 0, v: 0 };
  let peak = 0;
  for (let i = 0; i < 240; i++) {
    spring = stepSpring(spring, 10, 1 / 60, 85, 5.5);
    peak = Math.max(peak, spring.x);
  }
  assert.ok(peak > 10.5, `the leaf's spring should overshoot, peaked at ${peak}`);
  assert.ok(settled(spring, 10, 0.05));
});

test("the body leans away from where it goes and looks ahead", () => {
  const right = leanFor(600, 0);
  assert.ok(right.tilt < 0 && right.leaf < right.tilt, "the leaf trails further than the head");
  assert.ok(right.lookX > 0 && right.lookY === 0);
  assert.ok(right.stretchX > 1 && right.stretchY < 1);
  const left = leanFor(-600, 0);
  assert.equal(left.tilt, -right.tilt);
  assert.deepEqual(leanFor(0, 0), { tilt: 0, leaf: 0, lookX: 0, lookY: 0, stretchX: 1, stretchY: 1 });
});

test("no speed bends it out of shape", () => {
  const wild = leanFor(1e6, -1e6);
  assert.ok(Math.abs(wild.tilt) <= 16 && Math.abs(wild.leaf) <= 32);
  assert.ok(Math.abs(wild.lookX) <= 1.5 && Math.abs(wild.lookY) <= 1.3);
  assert.ok(wild.stretchX >= 0.92 && wild.stretchX <= 1.08 && wild.stretchY >= 0.92 && wild.stretchY <= 1.08);
});

test("a room smaller than the avatar still has one place for it", () => {
  assert.deepEqual(clampTo({ left: 8, top: 8, right: -20, bottom: -4 }, 300, 300), { x: 8, y: 8 });
  assert.deepEqual(clampTo(ROOM, -50, 9000), { x: 8, y: 600 });
});
