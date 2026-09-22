// What a node shows while an agent is working inside it.
//
// An agent node can sit on one step for minutes. Without this the node looks
// exactly like a hung run: same border, same summary, nothing moving.

import assert from "node:assert/strict";
import test from "node:test";

import { activityLabel, elapsed } from "../node-view";
import type { AgentRun } from "../store";
import { initialState, reduce, type EditorState } from "../store";

function agent(partial: Partial<AgentRun> = {}): AgentRun {
  return { name: "swift-otter", profession: "agent", detail: "", steps: 0, since: 0, ...partial };
}

function event(state: EditorState, eventType: string, payload: Record<string, unknown>) {
  return reduce(state, { type: "graphEvent", eventType, payload });
}

test("an agent that has done nothing yet still says it is working", () => {
  assert.equal(activityLabel(agent()), "working");
});

test("the label names the tool, the step and the clock", () => {
  const label = activityLabel(agent({ detail: "read_file", steps: 3, since: Date.now() - 12_000 }));

  assert.match(label, /^read_file · step 3 · 1[12]s$/, label);
});

test("the clock stays short enough to sit on a node", () => {
  assert.equal(elapsed(0), "0s");
  assert.equal(elapsed(59_000), "59s");
  assert.equal(elapsed(60_000), "1m 00s");
  assert.equal(elapsed(125_000), "2m 05s");
  // A clock that ran backwards would render "-3s" and look like a bug.
  assert.equal(elapsed(-3_000), "0s");
});

test("hiring an agent starts its clock", () => {
  const before = Date.now();
  const state = event(initialState(), "graph.agent.started", {
    node_id: "n1",
    agent_name: "swift-otter",
    profession: "architect",
    model: "local-30b",
  });

  const hired = state.run.agents.n1;
  assert.equal(hired.profession, "architect");
  assert.equal(hired.steps, 0);
  assert.ok(hired.since >= before, "the clock never started");
});

test("only a tool call that starts moves the counter", () => {
  let state = event(initialState(), "graph.agent.started", {
    node_id: "n1",
    agent_name: "swift-otter",
    profession: "agent",
  });
  state = event(state, "graph.node.progress", {
    node_id: "n1",
    event: "tool.call.started",
    detail: "read_file",
  });
  // Counting the completion too would make every call worth two.
  state = event(state, "graph.node.progress", {
    node_id: "n1",
    event: "tool.call.completed",
    detail: "read_file",
  });

  assert.equal(state.run.agents.n1.steps, 1);
  assert.equal(state.run.agents.n1.detail, "read_file");
});

test("a progress event with no detail keeps the last one", () => {
  let state = event(initialState(), "graph.agent.started", { node_id: "n1", agent_name: "a" });
  state = event(state, "graph.node.progress", {
    node_id: "n1",
    event: "tool.call.started",
    detail: "grep",
  });
  state = event(state, "graph.node.progress", { node_id: "n1", event: "model.response.completed" });

  // Blanking it would flash "working" between every tool call.
  assert.equal(state.run.agents.n1.detail, "grep");
});

test("progress for a node with no agent is ignored, not a crash", () => {
  const state = event(initialState(), "graph.node.progress", {
    node_id: "ghost",
    event: "tool.call.started",
    detail: "read_file",
  });

  assert.equal(state.run.agents.ghost, undefined);
});
