import assert from "node:assert/strict";
import test from "node:test";

import { AVATAR_STYLE, IDLE_LOOP_S } from "../styles/avatar";

interface Frame {
  /** Seconds into the loop. */
  at: number;
  transform: string;
}

function frames(name: string): Frame[] {
  const block = new RegExp(`@keyframes ${name} \\{ (.*?) \\}\\n`, "s").exec(AVATAR_STYLE);
  assert.ok(block, `no keyframes named ${name}`);
  return [...block[1].matchAll(/([\d.]+)% \{ transform: ([^;]+);/g)].map((match) => ({
    at: (Number(match[1]) / 100) * IDLE_LOOP_S,
    transform: match[2],
  }));
}

const numbers = (transform: string) => (transform.match(/-?[\d.]+/g) ?? []).map(Number);
const TIMELINES = ["av-i-gaze", "av-i-eye-l", "av-i-eye-r", "av-i-head", "av-i-leaf"];

test("every part of the idle runs forward and ends where it began", () => {
  for (const name of TIMELINES) {
    const steps = frames(name);
    assert.ok(steps.length > 4, name);
    assert.equal(steps[0].at, 0, name);
    assert.equal(steps[steps.length - 1].at, IDLE_LOOP_S, name);
    assert.equal(steps[0].transform, steps[steps.length - 1].transform, `${name} does not loop`);
    steps.reduce((before, step) => {
      assert.ok(step.at > before.at, `${name}: ${step.at}s comes after ${before.at}s`);
      return step;
    });
  }
});

test("a glance is led by the eyes, then the head, then the leaf", () => {
  const furthestLeft = (name: string) =>
    frames(name)
      .filter((step) => step.at > 2.8 && step.at < 4.5)
      .reduce((least, step) => (numbers(step.transform)[0] < numbers(least.transform)[0] ? step : least)).at;
  const eyes = furthestLeft("av-i-gaze");
  const head = furthestLeft("av-i-head");
  const leaf = furthestLeft("av-i-leaf");
  assert.ok(eyes < head && head < leaf, `eyes ${eyes}s, head ${head}s, leaf ${leaf}s`);
  // And the eyes are there before the head has properly set off.
  assert.ok(head - eyes > 0.2);
});

test("everything that arrives goes a little past first", () => {
  const gaze = frames("av-i-gaze").map((step) => numbers(step.transform)[0]);
  const head = frames("av-i-head").map((step) => numbers(step.transform)[0]);
  for (const [name, values, rest] of [["eyes", gaze, -1.55], ["head", head, -2.6]] as const) {
    const at = values.indexOf(rest);
    assert.ok(at > 0, `${name} never rests at ${rest}`);
    assert.ok(values[at - 1] < rest, `${name} stops dead at ${rest}`);
  }
});

test("both eyes blink as one, and shut the same number of times", () => {
  const open = (name: string) => frames(name).map((step) => [step.at, numbers(step.transform)[1]]);
  assert.deepEqual(open("av-i-eye-l"), open("av-i-eye-r"));
  const shut = open("av-i-eye-l").filter(([, lid], i, all) => lid < 0.2 && all[i - 1][1] >= 0.2);
  assert.equal(shut.length, 5, "three single blinks and a double");
});

test("the side the head turns to is the eye drawn narrower", () => {
  const widthAt = (name: string, at: number) =>
    numbers(frames(name).filter((step) => step.at <= at).pop()!.transform)[0];
  // Looking left at 4 s, right at 6 s.
  assert.ok(widthAt("av-i-eye-l", 4) < 1 && widthAt("av-i-eye-r", 4) > 1);
  assert.ok(widthAt("av-i-eye-l", 6) > 1 && widthAt("av-i-eye-r", 6) < 1);
});
