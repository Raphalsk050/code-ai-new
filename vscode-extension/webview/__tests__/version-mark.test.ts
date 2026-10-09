import assert from "node:assert/strict";
import test from "node:test";

import { versionParts } from "../version-mark";

test("the backend's version comes apart into its release and its build", () => {
  assert.deepEqual(versionParts("0.43.0-113"), { release: "0.43.0", build: "113" });
  assert.deepEqual(versionParts("12.0.7-4001"), { release: "12.0.7", build: "4001" });
});

test("a backend that says nothing, or something else, shows no version", () => {
  assert.equal(versionParts(undefined), null);
  assert.equal(versionParts(""), null);
  assert.equal(versionParts("0.43.0"), null);
  assert.equal(versionParts("v0.43.0-113"), null);
  assert.equal(versionParts("0.43.0-113 <b>x</b>"), null);
});
