import assert from "node:assert/strict";
import test from "node:test";

import { bakeIdle, Carried, type Dynamics, IDLE_LOOP_S, keep, type Pose, SAMPLES_PER_S, SecondOrder, simulateIdle } from "../avatar-rig";
import { AVATAR_STYLE } from "../styles/avatar";

const DT = 1 / 240;

/** What a part does when its target jumps from 0 to 1. */
function answer(dynamics: Dynamics, seconds = 4): number[] {
  const part = new SecondOrder(dynamics);
  return Array.from({ length: Math.round(seconds / DT) }, () => part.step(DT, 1));
}

test("a part gets where it is told to go, whatever its manner", () => {
  for (const manner of [{ f: 1.3, zeta: 0.42, r: -2 }, { f: 4.2, zeta: 0.9, r: 0.2 }, { f: 0.6, zeta: 1, r: 0 }]) {
    const path = answer(manner, 6);
    assert.ok(Math.abs(path[path.length - 1] - 1) < 0.01, JSON.stringify(manner));
  }
});

test("under-damped it goes past its target; critically damped it does not", () => {
  assert.ok(Math.max(...answer({ f: 1.3, zeta: 0.4, r: 0 })) > 1.15);
  assert.ok(Math.max(...answer({ f: 1.3, zeta: 1, r: 0 })) <= 1.001);
});

test("r below zero winds up the other way first; above zero sets off at once", () => {
  assert.ok(Math.min(...answer({ f: 1.3, zeta: 0.42, r: -2 }).slice(0, 60)) < -0.05, "no wind-up");
  assert.ok(Math.min(...answer({ f: 1.3, zeta: 0.42, r: 0 })) >= 0);
  assert.ok(answer({ f: 1.3, zeta: 0.42, r: 2 })[5] > answer({ f: 1.3, zeta: 0.42, r: 0 })[5] * 3);
});

test("a step far too long for its stiffness does not blow it up", () => {
  const lid = new SecondOrder({ f: 11, zeta: 1, r: 0 });
  for (let i = 0; i < 60; i++) assert.ok(Math.abs(lid.step(0.5, 1) - 1) <= 1);
  assert.ok(Math.abs(lid.value - 1) < 0.5);
});

test("a new manner picks up where the old one left off", () => {
  const part = new SecondOrder({ f: 2, zeta: 0.5, r: 0 });
  for (let i = 0; i < 30; i++) part.step(DT, 1);
  const { value, velocity } = part;
  assert.ok(value > 0 && velocity > 0);
  part.tune({ f: 9, zeta: 1, r: 0 });
  assert.equal(part.value, value);
  assert.equal(part.velocity, velocity);
});

test("what is carried is thrown by a start and a stop, and left alone by a steady move", () => {
  const leaf = new Carried(1.55, 0.13);
  const lags: number[] = [];
  let carrier = 0;
  for (let i = 0; i < 6 * 240; i++) {
    // Still for a second, then four at a steady 10 a second, then still again.
    if (i >= 240 && i < 5 * 240) carrier += 10 * DT;
    lags.push(leaf.lag(DT, carrier));
  }
  const during = (from: number, to: number) => lags.slice(from * 240, to * 240);
  assert.ok(during(0, 1).every((lag) => lag === 0));
  assert.ok(Math.min(...during(1, 1.5)) < -0.5, "not left behind at the start");
  assert.ok(Math.max(...during(4.6, 5).map(Math.abs)) < 0.05, "still lagging on a steady move");
  assert.ok(Math.max(...during(5, 5.5)) > 0.5, "not thrown ahead at the stop");
});

const poses = simulateIdle();
const at = (seconds: number): Pose => poses[Math.round(seconds * SAMPLES_PER_S)];
const span = (from: number, to: number) => poses.slice(Math.round(from * SAMPLES_PER_S), Math.round(to * SAMPLES_PER_S) + 1);
const swing = (of: (pose: Pose) => number, from: number, to: number) => {
  const values = span(from, to).map(of);
  return Math.max(...values) - Math.min(...values);
};
const ANGLES: (keyof Pose)[] = ["rot", "leaf", "ears", "tail"];

test("the loop ends on the frame it began with, and comes into it smoothly", () => {
  assert.equal(poses.length, IDLE_LOOP_S * SAMPLES_PER_S + 1);
  assert.deepEqual(poses[poses.length - 1], poses[0]);
  const last = poses[poses.length - 2];
  for (const key of Object.keys(last) as (keyof Pose)[]) {
    const jump = Math.abs(last[key] - poses[0][key]);
    assert.ok(jump < (ANGLES.includes(key) ? 0.2 : 0.01), `${key} jumps ${jump} at the seam`);
  }
});

test("nothing leaves the face or bends out of shape", () => {
  for (const pose of poses) {
    assert.ok(Math.abs(pose.rot) < 10 && Math.abs(pose.tx) < 1.5 && Math.abs(pose.ty) < 1.5);
    assert.ok(pose.sy > 0.9 && pose.sy < 1.1);
    assert.ok(Math.abs(pose.eyeX) < 2.2 && Math.abs(pose.eyeY) < 1.5);
    assert.ok(pose.lid >= 0.04 && pose.lid <= 1.3);
    assert.ok(Math.abs(pose.leaf) < 30 && Math.abs(pose.ears) < 15 && pose.tail > -8 && pose.tail < 15);
  }
});

test("squash and stretch keep the body's size", () => {
  for (const pose of poses) assert.ok(Math.abs(pose.sx * pose.sx * pose.sy - 1) < 1e-9);
});

test("a glance is led by the eyes; the head winds up, follows, and goes past", () => {
  // It looks to the left at 3 s and holds it until 4.7 s.
  const glance = span(3, 4.6);
  const reaches = (of: (pose: Pose) => number, mark: number) => glance.findIndex((pose) => of(pose) <= mark) / SAMPLES_PER_S;
  const eyes = reaches((pose) => pose.eyeX, -1.2);
  const head = reaches((pose) => pose.rot, -4.5);
  assert.ok(eyes > 0 && eyes < 0.12, `the eyes took ${eyes}s`);
  assert.ok(head - eyes > 0.15, `the head got there ${head - eyes}s after the eyes`);
  assert.ok(Math.max(...span(3, 3.25).map((pose) => pose.rot)) > 0.4, "the head does not wind up");
  assert.ok(Math.min(...glance.map((pose) => pose.rot)) < at(4.5).rot - 1, "the head stops dead");
});

test("the eyes give some of the way back as the head arrives", () => {
  const furthest = Math.min(...span(3, 3.3).map((pose) => pose.eyeX));
  assert.ok(at(4.5).eyeX > furthest + 0.3);
});

test("the leaf is still swinging when the head has come to rest", () => {
  // A second after it swept across to the right.
  assert.ok(swing((pose) => pose.rot, 5.7, 6.4) < 1);
  assert.ok(swing((pose) => pose.leaf, 5.7, 6.4) > 6);
});

test("a nod stretches the body on the way down and squashes it at the bottom", () => {
  const nod = span(6.98, 7.6);
  const bottom = nod.reduce((low, pose) => (pose.ty > low.ty ? pose : low));
  assert.ok(bottom.ty > 0.8);
  assert.ok(Math.max(...nod.slice(0, nod.indexOf(bottom)).map((pose) => pose.sy)) > 1.03, "no stretch going down");
  assert.ok(Math.min(...nod.slice(nod.indexOf(bottom)).map((pose) => pose.sy)) < 0.95, "no squash at the bottom");
  // And through a plain glance it keeps its shape.
  assert.ok(swing((pose) => pose.sy, 3.3, 4.6) < 0.03);
});

test("it blinks six times a loop and never holds its eyes shut", () => {
  let blinks = 0;
  let shut = 0;
  let longest = 0;
  poses.slice(1).forEach((pose, i) => {
    if (pose.lid < 0.2 && poses[i].lid >= 0.2) blinks += 1;
    shut = pose.lid < 0.2 ? shut + 1 : 0;
    longest = Math.max(longest, shut);
  });
  assert.equal(blinks, 6);
  assert.ok(longest / SAMPLES_PER_S < 0.2, `shut for ${longest / SAMPLES_PER_S}s`);
});

test("the eye on the side it looks to is drawn narrower", () => {
  assert.ok(at(4).wideL < 1 && at(4).wideR > 1);
  assert.ok(at(5.8).wideL > 1 && at(5.8).wideR < 1);
});

test("keep drops what a straight line already says and keeps what it cannot", () => {
  const line = Array.from({ length: 50 }, (_, i) => [i * 2]);
  assert.deepEqual(keep(line, [0.01]), [0, 49]);
  const spike = line.map(([value], i) => [i === 20 ? value + 5 : value]);
  assert.deepEqual(keep(spike, [0.01]), [0, 19, 20, 21, 49]);
});

interface Frame {
  /** Percent of the loop. */
  at: number;
  values: number[];
}

function frames(css: string, name: string): Frame[] {
  const block = new RegExp(`@keyframes ${name}\\{(.*)\\}`).exec(css);
  assert.ok(block, `no keyframes named ${name}`);
  return [...block[1].matchAll(/([\d.]+)%\{transform:([^}]+)\}/g)].map((match) => ({
    at: Number(match[1]),
    values: (match[2].match(/-?[\d.]+/g) ?? []).map(Number),
  }));
}

test("the bake follows the run closer than the eye can tell", () => {
  const css = bakeIdle(poses);
  assert.ok(css.length < 60_000, `${css.length} bytes of keyframes`);
  const check = (name: string, column: number, of: (pose: Pose) => number, slack: number) => {
    const steps = frames(css, name);
    assert.equal(steps[0].at, 0, name);
    assert.equal(steps[steps.length - 1].at, 100, name);
    assert.deepEqual(steps[0].values, steps[steps.length - 1].values, `${name} does not loop`);
    poses.forEach((pose, i) => {
      const percent = (i / (poses.length - 1)) * 100;
      const next = Math.max(steps.findIndex((step) => step.at >= percent), 1);
      const [a, b] = [steps[next - 1], steps[next]];
      const drawn = a.values[column] + ((b.values[column] - a.values[column]) * (percent - a.at)) / (b.at - a.at);
      assert.ok(Math.abs(drawn - of(pose)) <= slack, `${name} is ${Math.abs(drawn - of(pose))} off at ${i / SAMPLES_PER_S}s`);
    });
  };
  check("av-i-head", 0, (pose) => pose.rot, 0.09);
  check("av-i-head", 2, (pose) => pose.ty, 0.022);
  check("av-i-head", 4, (pose) => pose.sy, 0.0035);
  check("av-i-gaze", 0, (pose) => pose.eyeX, 0.022);
  check("av-i-eye-l", 1, (pose) => pose.lid, 0.022);
  check("av-i-eye-r", 1, (pose) => pose.lid, 0.022);
  check("av-i-leaf", 0, (pose) => pose.leaf, 0.26);
  check("av-i-ears", 0, (pose) => pose.ears, 0.26);
  check("av-i-tail", 0, (pose) => pose.tail, 0.26);
});

test("the stylesheet plays every part of the idle, and nothing an old Chromium cannot", () => {
  for (const name of ["av-i-breathe", "av-i-head", "av-i-gaze", "av-i-eye-l", "av-i-eye-r", "av-i-leaf", "av-i-ears", "av-i-tail"]) {
    assert.ok(AVATAR_STYLE.includes(`@keyframes ${name}`), `no keyframes named ${name}`);
    assert.ok(new RegExp(`animation(-name)?:[^;]*\\b${name}\\b`).test(AVATAR_STYLE), `${name} is never played`);
  }
  // VS Code 1.74 runs Chromium 102: no linear() easing, no transform properties of their own.
  assert.ok(!AVATAR_STYLE.includes("linear("));
  assert.ok(!/[;{\s](rotate|translate|scale)\s*:/.test(AVATAR_STYLE));
});
