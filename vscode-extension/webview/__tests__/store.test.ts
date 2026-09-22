import assert from "node:assert/strict";
import test from "node:test";

import { applyEvent, initialAppState } from "../store";
import type { EventEnvelope } from "../../src/protocol";

let seq = 0;
function ev(event_type: string, payload: Record<string, unknown> = {}): EventEnvelope {
  seq += 1;
  return {
    event_id: `e${seq}`,
    event_type,
    event_version: 1,
    session_id: "s",
    sequence: seq,
    timestamp: "",
    source: "core",
    payload,
  };
}

function feed(events: [string, Record<string, unknown>?][]) {
  let state = initialAppState;
  for (const [type, payload] of events) state = applyEvent(state, ev(type, payload));
  return state;
}

test("an event no slice handles keeps the same state object", () => {
  const before = initialAppState;
  assert.equal(applyEvent(before, ev("nobody.handles.this")), before);
});

test("streaming deltas merge into one assistant bubble", () => {
  const state = feed([
    ["user.message", { text: "oi" }],
    ["model.stream.delta", { text: "ok" }],
    ["model.stream.delta", { text: " then" }],
  ]);
  const last = state.transcript.items[state.transcript.items.length - 1];
  assert.equal(last.kind, "assistant");
  assert.equal((last as { text: string }).text, "ok then");
});

test("the final answer replaces the streamed duplicate instead of doubling it", () => {
  const state = feed([
    ["model.stream.delta", { text: "pronto" }],
    ["assistant.final", { text: "pronto" }],
  ]);
  assert.equal(state.transcript.items.length, 1);
  assert.equal((state.transcript.items[0] as { streaming: boolean }).streaming, false);
});

test("a tool result lands on its own running card", () => {
  const state = feed([
    ["tool.call.started", { tool_call_id: "t1", name: "read_file" }],
    ["tool.call.started", { tool_call_id: "t2", name: "search_code" }],
    ["tool.call.completed", { tool_call_id: "t2", name: "search_code", result: { matches: [1, 2] } }],
  ]);
  const cards = state.transcript.items.filter((i) => i.kind === "tool") as {
    toolId: string;
    status: string;
    detail: string;
  }[];
  assert.equal(cards[0].status, "running");
  assert.equal(cards[1].status, "done");
  assert.equal(cards[1].detail, "2 matches");
});

test("plan steps are completed-prefix then remaining, current one live", () => {
  const state = feed([
    [
      "planning.plan.created",
      {
        progress: "1/3",
        status: "RUNNING",
        completed_steps: ["ler"],
        remaining_steps: ["editar", "testar"],
        current_step: "editar",
      },
    ],
  ]);
  assert.deepEqual(
    state.plan.steps.map((s) => [s.title, s.status]),
    [
      ["ler", "done"],
      ["editar", "running"],
      ["testar", "pending"],
    ]
  );
});

test("a plan waiting on the user is paused, not spinning", () => {
  const state = feed([
    [
      "planning.plan.waiting",
      { status: "WAITING", completed_steps: [], remaining_steps: ["decidir"], current_step: "decidir" },
    ],
  ]);
  assert.equal(state.plan.steps[0].status, "paused");
});

test("a failed step is marked failed, not just not-done", () => {
  const state = feed([
    [
      "planning.step.failed",
      {
        status: "RUNNING",
        current_step_status: "FAILED",
        completed_steps: [],
        remaining_steps: ["run the suite"],
        current_step: "run the suite",
      },
    ],
  ]);
  assert.equal(state.plan.steps[0].status, "failed");
});

test("sub-agents keep dispatch order and settle in place", () => {
  const state = feed([
    ["subagent.started", { agent_id: "a", agent_type: "explorer", name: "scout", task: "varrer" }],
    ["subagent.started", { agent_id: "b", agent_type: "tester", name: "tess", task: "testar" }],
    ["subagent.progress", { agent_id: "a", event: "tool.call.started", tool: "read_file" }],
    ["subagent.completed", { agent_id: "a", summary: "12 arquivos" }],
    ["subagent.circuit.open", { agent_type: "tester" }],
  ]);
  assert.deepEqual(state.agents.rows.map((r) => r.id), ["a", "b"]);
  assert.equal(state.agents.rows[0].status, "completed");
  assert.deepEqual(state.agents.circuitOpen, ["tester"]);
});

test("the live code window appends by offset and refuses to splice over a gap", () => {
  const opened = feed([
    ["tool.call.progress", { name: "write_file", call_started: true, writes: true, path: "a.py" }],
    ["tool.call.progress", { name: "write_file", code_delta: "um\n", code_offset: 0 }],
    ["tool.call.progress", { name: "write_file", code_delta: "dois\n", code_offset: 3 }],
  ]);
  assert.equal(opened.code.code, "um\ndois\n");
  const gapped = applyEvent(
    opened,
    ev("tool.call.progress", { name: "write_file", code_delta: "x", code_offset: 999 })
  );
  assert.equal(gapped.code.code, "um\ndois\n");
});

test("a non-writing call does not open the code window", () => {
  const state = feed([
    ["tool.call.progress", { name: "search_code", call_started: true, writes: false }],
  ]);
  assert.equal(state.code.visible, false);
});

test("the end of the turn closes the code window", () => {
  const state = feed([
    ["tool.call.progress", { name: "write_file", call_started: true, writes: true, path: "a.py" }],
    ["status.changed", { state: "READY" }],
  ]);
  assert.equal(state.code.visible, false);
});

test("questions parse label :: description and bare labels alike", () => {
  const state = feed([
    [
      "interaction.question.requested",
      { question: "git ou mtime?", options: ["git log :: reflete o time", "mtime"] },
    ],
  ]);
  const [q] = state.questions.pending;
  assert.equal(q.options[0].label, "git log");
  assert.equal(q.options[0].description, "reflete o time");
  assert.equal(q.options[1].description, "");
});

test("a question with no options still accepts free text", () => {
  const state = feed([
    ["interaction.question.requested", { question: "qual o alvo?", allow_other: false }],
  ]);
  assert.equal(state.questions.pending[0].allowOther, true);
});

test("usage tracks the budget, the estimate flag and the cumulative total", () => {
  const state = feed([
    [
      "usage.updated",
      {
        active_context_tokens: 1200,
        active_context_estimated: true,
        context_budget: 8000,
        cumulative: { total_tokens: 4000 },
      },
    ],
  ]);
  assert.equal(state.usage.used, 1200);
  assert.equal(state.usage.estimated, true);
  assert.equal(state.usage.budget, 8000);
  assert.equal(state.usage.cumulative, 4000);
});

test("a new turn clears the turn-scoped panels but keeps the terminal", () => {
  const before = feed([
    ["terminal.screen.updated", { session_id: "t1", screen: "$ ls", rows: 30, columns: 100 }],
    ["subagent.started", { agent_id: "a", agent_type: "explorer" }],
    ["interaction.question.requested", { question: "?" }],
    ["planning.plan.created", { completed_steps: [], remaining_steps: ["x"], current_step: "x" }],
  ]);
  const after = applyEvent(before, ev("user.message", { text: "next" }));
  assert.equal(after.plan.steps.length, 0);
  assert.equal(after.agents.rows.length, 0);
  assert.equal(after.questions.pending.length, 0);
  assert.equal(after.terminal, before.terminal);
  assert.equal(after.terminal.rows, 30);
});

test("an approval opens and resolving closes it", () => {
  const open = feed([
    ["tool.approval.requested", { call_id: "c1", tool_name: "execute_command", reason: "build" }],
  ]);
  assert.equal(open.approvals.pending?.call_id, "c1");
  assert.equal(applyEvent(open, ev("tool.approval.resolved", {})).approvals.pending, null);
});

test("the goal loop reports its phase and iteration", () => {
  const state = feed([
    ["goal.defined", { objective: "verde", criteria: ["testes passam"] }],
    ["goal.activated", { max_iterations: 6 }],
    ["goal.iteration.started", { iteration: 2, max_iterations: 6 }],
    ["goal.iteration.completed", { met: 1, summary: "one criterion" }],
  ]);
  assert.equal(state.goal.phase, "running");
  assert.equal(state.goal.iteration, 2);
  assert.equal(state.goal.met, 1);
});

test("index progress fills the session bar and clears when it ends", () => {
  const during = feed([["index.progress", { phase: "embedding", done: 44, total: 100 }]]);
  assert.equal(during.session.indexDone, 44);
  const after = applyEvent(during, ev("index.refreshed", { summary: "ok" }));
  assert.equal(after.session.indexTotal, 0);
});

test("reasoning streams into one open block while the model thinks", () => {
  // The bug this guards: the block existed but was rendered collapsed, so the
  // reasoning only became visible once the turn was over.
  const state = feed([
    ["user.message", { text: "oi" }],
    ["model.thinking.delta", { text: "The " }],
    ["model.thinking.delta", { text: "user " }],
    ["model.thinking.delta", { text: "wants..." }],
  ]);
  const last = state.transcript.items[state.transcript.items.length - 1];
  assert.equal(last.kind, "thinking");
  assert.equal((last as { text: string }).text, "The user wants...");
  assert.equal((last as { streaming?: boolean }).streaming, true);
});

test("the reasoning block closes itself when the model answers", () => {
  const state = feed([
    ["model.thinking.delta", { text: "hmm" }],
    ["model.response.completed", { finish_reason: "tool_calls" }],
  ]);
  const last = state.transcript.items[state.transcript.items.length - 1];
  assert.equal((last as { streaming?: boolean }).streaming, false);
});

test("settling the reasoning block twice is harmless", () => {
  const state = feed([
    ["model.thinking.delta", { text: "hmm" }],
    ["model.response.completed", {}],
    ["model.response.completed", {}],
  ]);
  assert.equal(state.transcript.items.length, 1);
});

test("a completion with no reasoning before it changes nothing", () => {
  const state = feed([
    ["user.message", { text: "oi" }],
    ["model.response.completed", {}],
  ]);
  assert.equal(state.transcript.items.length, 1);
  assert.equal(state.transcript.items[0].kind, "user");
});

test("a message that waits its turn says so, and is not marked as steering", () => {
  const state = feed([
    ["user.message", { text: "faca X" }],
    ["user.message.enqueued", { text: "e depois Y?", position: 1 }],
  ]);
  const items = state.transcript.items;
  assert.equal(items[1].kind, "user");
  assert.equal((items[1] as { text: string }).text, "e depois Y?");
  assert.equal(items[2].kind, "notice");
  assert.match((items[2] as { text: string }).text, /^queued:/);
});

test("the queue position only shows once there is a queue", () => {
  const first = feed([["user.message.enqueued", { text: "a", position: 1 }]]);
  assert.doesNotMatch((first.transcript.items[1] as { text: string }).text, /na fila\)/);
  const third = feed([["user.message.enqueued", { text: "c", position: 3 }]]);
  assert.match((third.transcript.items[1] as { text: string }).text, /#3 in the queue\)/);
});
