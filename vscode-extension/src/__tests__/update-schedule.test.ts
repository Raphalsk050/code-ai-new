import assert from "node:assert/strict";
import test from "node:test";

import { CHECK_EVERY_MS, FIRST_CHECK_MS, nextCheckDelay } from "../update-schedule";

test("a look that got an answer is followed by one an hour later", () => {
  assert.equal(nextCheckDelay(0), CHECK_EVERY_MS);
  assert.equal(CHECK_EVERY_MS, 3_600_000);
  assert.ok(FIRST_CHECK_MS < 60_000);
});

test("a look that got no answer is tried again soon, then less and less often", () => {
  const delays = [1, 2, 3, 4, 5, 9].map(nextCheckDelay);
  assert.equal(delays[0], 30_000);
  assert.deepEqual(delays, [...delays].sort((a, b) => a - b));
  // It settles, and never waits longer than after a look that worked.
  assert.equal(delays[3], delays[5]);
  assert.ok(delays[5] <= CHECK_EVERY_MS);
});
