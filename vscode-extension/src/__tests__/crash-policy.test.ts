import assert from "node:assert/strict";
import test from "node:test";

import { afterCrash } from "../crash-policy";

test("a backend that dies once is started again", () => {
  const decision = afterCrash([], 1_000);
  assert.equal(decision.restart, true);
  assert.deepEqual(decision.crashes, [1_000]);
});

test("one that keeps dying is left down for the user", () => {
  let crashes: number[] = [];
  const restarts = [0, 1, 2, 3].map((minute) => {
    const decision = afterCrash(crashes, minute * 10_000);
    crashes = decision.crashes;
    return decision.restart;
  });
  assert.deepEqual(restarts, [true, true, true, false]);
});

test("old crashes stop counting once the window has passed", () => {
  const decision = afterCrash([0, 1_000, 2_000], 200_000);
  assert.equal(decision.restart, true);
  assert.deepEqual(decision.crashes, [200_000]);
});
