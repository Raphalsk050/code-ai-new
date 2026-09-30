import assert from "node:assert/strict";
import { test } from "node:test";

import { agentKey, blockToPin, endsOnAnswer, groupBlocks } from "../blocks";
import type { Item } from "../store/transcript";

const user = (id: string): Item => ({ kind: "user", id, text: id });
const said = (id: string): Item => ({ kind: "assistant", id, text: id, streaming: false });
const tool = (id: string): Item => ({
  kind: "tool", id, toolId: id, name: "read_file", target: "", path: "", status: "done", detail: "",
});
const notice = (id: string, level: "info" | "success" | "warning" | "error" = "info"): Item => ({
  kind: "notice", id, level, text: id,
});
const agentBlock = (...items: Item[]) => groupBlocks(items)[0];

test("a run from one side is one block, and everything the agent does is its side", () => {
  const blocks = groupBlocks([user("u1"), user("u2"), tool("t1"), said("a1"), notice("n1"), user("u3")]);
  assert.deepEqual(
    blocks.map((b) => [b.speaker, b.items.map((i) => i.id)]),
    [
      ["user", ["u1", "u2"]],
      ["agent", ["t1", "a1", "n1"]],
      ["user", ["u3"]],
    ]
  );
});

test("keys hold while a block grows, and an agent block is keyed by what it answers", () => {
  const before = groupBlocks([user("u1"), tool("t1")]);
  const after = groupBlocks([user("u1"), user("u1b"), tool("t1"), said("a1")]);
  assert.deepEqual(
    before.map((b) => b.key),
    after.map((b) => b.key)
  );
  // The typing line opens the agent's block before any reply: same key.
  assert.equal(after[1].key, agentKey(after[0].key));
  assert.equal(groupBlocks([notice("n0")])[0].key, agentKey(""));
});

test("nothing is pinned at the top or while the request is still on screen", () => {
  const spans = [
    { top: 100, bottom: 260 },
    { top: 900, bottom: 960 },
  ];
  assert.equal(blockToPin(spans, 50, 56), -1, "above the first request");
  assert.equal(blockToPin(spans, 120, 56), -1, "140px of the request still in view");
});

test("the request the top belongs to is pinned once it has scrolled away", () => {
  const spans = [
    { top: 100, bottom: 160 },
    { top: 900, bottom: 960 },
  ];
  assert.equal(blockToPin(spans, 140, 56), 0, "only the part the pin covers is left");
  assert.equal(blockToPin(spans, 500, 56), 0, "deep in the answer");
  assert.equal(blockToPin(spans, 1200, 56), 1, "past the second request");
});

test("the next request coming up to the top takes over from the pin", () => {
  const spans = [
    { top: 100, bottom: 160 },
    { top: 900, bottom: 960 },
  ];
  assert.equal(blockToPin(spans, 860, 56), -1, "request 2 is 40px from the top");
  assert.equal(blockToPin(spans, 830, 56), 0, "request 2 is 70px down: still request 1's answer");
});

test("a turn is done when the model had the last word, quiet notes aside", () => {
  assert.equal(endsOnAnswer(agentBlock(tool("t1"), said("a1"))), true);
  assert.equal(endsOnAnswer(agentBlock(said("a1"), notice("n1", "success"))), true, "an index note after");
  assert.equal(endsOnAnswer(agentBlock(said("a1"), tool("t2"))), false, "a tool after: it went on");
  assert.equal(endsOnAnswer(agentBlock(said("a1"), notice("n1", "error"))), false, "an error after");
  assert.equal(endsOnAnswer(agentBlock(tool("t1"))), false, "no answer at all");
  const streaming: Item = { kind: "assistant", id: "a2", text: "still typ", streaming: true };
  assert.equal(endsOnAnswer(agentBlock(streaming)), false, "the answer is still coming");
});
