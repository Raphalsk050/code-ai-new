import assert from "node:assert/strict";
import test from "node:test";

import { nextFollowing, REJOIN_PX, type ScrollStep } from "../follow";

const step = (over: Partial<ScrollStep>): ScrollStep => ({ moved: 0, below: 500, shrunk: false, held: false, ...over });

test("any move up lets go, however small", () => {
  assert.equal(nextFollowing(true, step({ moved: -2, below: 2 })), false);
  assert.equal(nextFollowing(true, step({ moved: -300, below: 300 })), false);
});

test("content getting shorter is not the reader scrolling up", () => {
  assert.equal(nextFollowing(true, step({ moved: -120, below: 40, shrunk: true })), true);
  // Unless they are holding the scrollbar while it happens.
  assert.equal(nextFollowing(true, step({ moved: -120, below: 40, shrunk: true, held: true })), false);
});

test("following resumes only by coming back down to the end", () => {
  assert.equal(nextFollowing(false, step({ moved: 40, below: REJOIN_PX + 1 })), false);
  assert.equal(nextFollowing(false, step({ moved: 40, below: REJOIN_PX })), true);
  // Near the end, but not moving towards it: text growing below is not a return.
  assert.equal(nextFollowing(false, step({ moved: 0, below: 10 })), false);
});

test("at the very end there is nothing to be away from", () => {
  assert.equal(nextFollowing(false, step({ moved: -30, below: 0, shrunk: true })), true);
  assert.equal(nextFollowing(false, step({ moved: 0, below: 0.5 })), true);
});

test("growing text leaves the choice as it was", () => {
  assert.equal(nextFollowing(true, step({ moved: 0, below: 900 })), true);
  assert.equal(nextFollowing(false, step({ moved: 0, below: 900 })), false);
});
