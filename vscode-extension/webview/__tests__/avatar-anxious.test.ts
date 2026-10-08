import assert from "node:assert/strict";
import test from "node:test";

import { ANXIOUS_LOOP_S, bakeAnxious, simulateAnxious, WIDE } from "../avatar-anxious";
import { type Pose, SAMPLES_PER_S } from "../avatar-rig";
import { AVATAR_STYLE } from "../styles/avatar";

const poses = simulateAnxious();
const at = (seconds: number): Pose => poses[Math.round(seconds * SAMPLES_PER_S)];
const span = (from: number, to: number) => poses.slice(Math.round(from * SAMPLES_PER_S), Math.round(to * SAMPLES_PER_S) + 1);
const open = (pose: Pose) => pose.lid > 0.9;

test("the loop ends on the frame it began with, and the seam is no rougher than the rest", () => {
  assert.equal(poses.length, ANXIOUS_LOOP_S * SAMPLES_PER_S + 1);
  assert.deepEqual(poses[poses.length - 1], poses[0]);
  // The gaze goes back to the start of the line there, as it does in the middle of the loop.
  const last = poses[poses.length - 2];
  for (const key of Object.keys(last) as (keyof Pose)[]) {
    const seam = Math.abs(last[key] - poses[0][key]);
    const inside = Math.max(...poses.slice(1, -1).map((pose, i) => Math.abs(pose[key] - poses[i][key])));
    assert.ok(seam <= inside * 1.05 + 1e-6, `${key} jumps ${seam} at the seam, ${inside} at most elsewhere`);
  }
});

test("nothing leaves the face or bends out of shape", () => {
  for (const pose of poses) {
    assert.ok(Math.abs(pose.rot) < 10 && Math.abs(pose.tx) < 1.5 && Math.abs(pose.ty) < 1.5);
    assert.ok(pose.sy > 0.9 && pose.sy < 1.1);
    // Looking down at a line, its eyes go lower than they ever do at rest.
    assert.ok(Math.abs(pose.eyeX) < 2.2 && pose.eyeY > -1.5 && pose.eyeY < 1.6);
    assert.ok(pose.lid >= 0.04 && pose.lid <= 1.3);
    assert.ok(Math.abs(pose.leaf) < 30 && Math.abs(pose.ears) < 15 && pose.tail > -8 && pose.tail < 15);
    assert.ok(Math.abs(pose.sx * pose.sx * pose.sy - 1) < 1e-9, "squash and stretch do not keep its size");
  }
});

test("it is bent over the line the whole time, eyes wide and on it", () => {
  // But for when it looks up and swallows, from 1.7 s to 3 s.
  const reading = [...span(0.3, 1.6), ...span(3, 4.6)];
  for (const pose of reading) {
    assert.ok(pose.ty > 0.3, `it straightened up: ${pose.ty}`);
    assert.ok(pose.eyeY > 0.6, `its eyes left the line: ${pose.eyeY}`);
  }
  const wide = reading.filter(open);
  assert.ok(wide.length > reading.length * 0.9);
  assert.ok(wide.every((pose) => pose.lid > WIDE - 0.08), "its eyes are not held wide");
});

test("its eyes go along the line and jump back to the start of it", () => {
  assert.ok(at(0.3).eyeX < -0.5 && at(1.6).eyeX > 0.5, "they do not go along the line");
  assert.ok(at(2.6).eyeX > 0.5 && at(3).eyeX < -0.3, "they do not go back to the start");
});

test("it looks up once a loop, wider-eyed, standing taller, holding its breath", () => {
  const up = at(2.1);
  assert.ok(up.eyeY < -0.2, `it does not look up: ${up.eyeY}`);
  assert.ok(up.lid > WIDE + 0.08, `no wider-eyed for it: ${up.lid}`);
  assert.ok(up.ty < at(1.4).ty - 0.4, "it does not stand taller");
  // Breathing moves it up and down; held, it is still.
  const sway = (from: number, to: number) => {
    const heights = span(from, to).map((pose) => pose.ty);
    return Math.max(...heights) - Math.min(...heights);
  };
  assert.ok(sway(2.05, 2.28) < 0.08, `it breathes while it holds its breath: ${sway(2.05, 2.28)}`);
  assert.ok(sway(3.3, 4.5) > 0.12, "it does not breathe");
  assert.ok(up.leaf < at(1.4).leaf - 3, "the leaf does not stand straighter");
});

test("it swallows as it looks back down: the body squashes under it", () => {
  const gulp = span(2.5, 3);
  assert.ok(Math.min(...gulp.map((pose) => pose.sy)) < 0.95, "no squash");
  assert.ok(Math.max(...gulp.map((pose) => pose.ty)) > at(4.4).ty + 0.2, "it does not dip");
});

test("it blinks as its gaze shifts and never holds its eyes shut", () => {
  let blinks = 0;
  let shut = 0;
  let longest = 0;
  poses.slice(1).forEach((pose, i) => {
    if (pose.lid < 0.2 && poses[i].lid >= 0.2) blinks += 1;
    shut = pose.lid < 0.2 ? shut + 1 : 0;
    longest = Math.max(longest, shut);
  });
  assert.ok(blinks >= 3, `${blinks} blinks`);
  assert.ok(longest / SAMPLES_PER_S < 0.2, `shut for ${longest / SAMPLES_PER_S}s`);
});

test("the bake holds a loop for every part, and is not heavier than the idle's", () => {
  const css = bakeAnxious(poses);
  assert.ok(css.length < 40_000, `${css.length} bytes of keyframes`);
  for (const part of ["head", "gaze", "eye-l", "eye-r", "leaf", "ears", "tail"]) {
    const block = new RegExp(`@keyframes av-a-${part}\\{(.*)\\}`).exec(css);
    assert.ok(block, `no keyframes for the ${part}`);
    const steps = [...block[1].matchAll(/([\d.]+)%\{transform:([^}]+)\}/g)];
    assert.equal(steps[0][1], "0", part);
    assert.equal(steps[steps.length - 1][1], "100", part);
    assert.equal(steps[0][2], steps[steps.length - 1][2], `the ${part} does not loop`);
  }
});

test("the stylesheet plays every part of it, with the shiver and the drop", () => {
  for (const name of ["av-a-head", "av-a-gaze", "av-a-eye-l", "av-a-eye-r", "av-a-leaf", "av-a-ears", "av-a-tail", "av-a-shiver", "av-a-drop"]) {
    assert.ok(AVATAR_STYLE.includes(`@keyframes ${name}`), `no keyframes named ${name}`);
    assert.ok(new RegExp(`animation(-name)?:[^;]*\\b${name}\\b`).test(AVATAR_STYLE), `${name} is never played`);
  }
  // And nothing but the anxious pose plays them, save the drop: an act sweats it too.
  const playing = AVATAR_STYLE.match(/[^{}]+\{[^{}]*\bav-a-[a-z-]+[^{}]*\}/g) ?? [];
  assert.equal(playing.length, 10);
  for (const rule of playing) {
    assert.ok(rule.includes("[data-pose=anxious]") || /\[data-act\] \.agent-av-sweat/.test(rule), rule);
  }
});
