import assert from "node:assert/strict";
import test from "node:test";

import { parseAnsi, stripAnsi } from "../ansi";

test("colour runs become spans and the escapes disappear", () => {
  const spans = parseAnsi("ok \x1b[31;1mFAILED\x1b[0m done\x1b[2K");
  assert.deepEqual(
    spans.map((s) => [s.text, s.color, s.bold]),
    [
      ["ok ", "", false],
      ["FAILED", "red", true],
      [" done", "", false],
    ]
  );
  assert.equal(stripAnsi("\x1b[92mPASS\x1b[39m \x1b[38;5;208mx\x1b[m"), "PASS x");
  assert.equal(parseAnsi("\x1b[92mPASS")[0].color, "green");
});

test("durations read as a clock past a minute and past an hour", async () => {
  const { formatDuration } = await import("../tool-row");
  assert.deepEqual([420, 12_400, 83_000, 3_723_000].map(formatDuration), ["0.4s", "12s", "1:23", "1:02:03"]);
});
