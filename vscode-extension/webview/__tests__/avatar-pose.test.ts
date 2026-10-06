import assert from "node:assert/strict";
import test from "node:test";

import { avatarPose } from "../avatar-pose";
import type { Item } from "../store/transcript";

const running = (...names: string[]) => ({
  status: "EXECUTING_TOOL",
  runningTools: names.map((name, i) => ({ id: String(i), name, target: "" })),
});
const calling = { status: "CALLING_MODEL", runningTools: [] };

test("read and search tools search", () => {
  assert.equal(avatarPose(running("read_file"), undefined), "search");
  assert.equal(avatarPose(running("search_code"), undefined), "search");
  assert.equal(avatarPose(running("list_files"), undefined), "search");
  assert.equal(avatarPose(running("web_search"), undefined), "search");
});

test("writing and commands type", () => {
  assert.equal(avatarPose(running("write_file"), undefined), "type");
  assert.equal(avatarPose(running("edit_code"), undefined), "type");
  assert.equal(avatarPose(running("execute_command"), undefined), "type");
});

test("the latest started tool wins", () => {
  assert.equal(avatarPose(running("read_file", "edit_code"), undefined), "type");
  assert.equal(avatarPose(running("edit_code", "read_file"), undefined), "search");
});

test("plans, agents and unknown tools think", () => {
  assert.equal(avatarPose(running("dispatch_agent"), undefined), "think");
  assert.equal(avatarPose(running("submit_plan"), undefined), "think");
  assert.equal(avatarPose(running("mystery"), undefined), "think");
});

test("model output types, a silent model thinks", () => {
  const streaming: Item = { kind: "assistant", id: "a", text: "hi", streaming: true };
  const finished: Item = { kind: "assistant", id: "a", text: "hi", streaming: false };
  const thinking: Item = { kind: "thinking", id: "t", text: "...", streaming: true };
  assert.equal(avatarPose(calling, streaming), "type");
  assert.equal(avatarPose(calling, finished), "think");
  assert.equal(avatarPose(calling, thinking), "think");
  assert.equal(avatarPose(calling, undefined), "think");
});

test("a tool status with no tool listed falls back to the transcript", () => {
  assert.equal(avatarPose({ status: "EXECUTING_TOOL", runningTools: [] }, undefined), "think");
});
