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

test("an answer that turns out to be thinking moves into the reasoning block", () => {
  // Qwen3.8's template opens the think block in the prompt: without a server
  // reasoning parser the thinking streams as the answer until a bare </think>.
  const state = feed([
    ["user.message", { text: "oi" }],
    ["model.stream.delta", { text: "O usuario quer um oi." }],
    ["model.reasoning.recovered", {}],
    ["model.thinking.delta", { text: "O usuario quer um oi." }],
    ["model.stream.delta", { text: "Oi!" }],
  ]);
  const items = state.transcript.items;
  assert.deepEqual(
    items.map((item) => item.kind),
    ["user", "thinking", "assistant"],
  );
  assert.equal((items[2] as { text: string }).text, "Oi!");
});

test("cleared tool output shows up as a notice with what it freed", () => {
  const state = feed([["context.results.cleared", { freed_tokens: 18250, active_tokens: 70000 }]]);
  const last = state.transcript.items[state.transcript.items.length - 1];
  assert.equal(last.kind, "notice");
  assert.match((last as { text: string }).text, /-18k tokens/);
});

test("the context meter follows every update, not just the one before a request", () => {
  const state = feed([
    ["usage.updated", { active_context_tokens: 40000, context_budget: 120000 }],
    ["usage.updated", { active_context_tokens: 40250, active_context_estimated: true }],
    ["usage.updated", { active_context_tokens: 41000, active_context_estimated: false }],
  ]);
  assert.equal(state.usage.used, 41000);
  assert.equal(state.usage.estimated, false);
  assert.equal(state.usage.budget, 120000);
});

test("the reasoning block closes itself when the model answers", () => {
  const state = feed([
    ["model.thinking.delta", { text: "hmm" }],
    ["model.response.completed", { finish_reason: "tool_calls" }],
  ]);
  const last = state.transcript.items[state.transcript.items.length - 1];
  assert.equal((last as { streaming?: boolean }).streaming, false);
});

test("the reasoning block closes as soon as the answer starts", () => {
  // The bug this guards: the answer lands after the reasoning, so the block
  // was no longer the last item when the response completed and stayed live -
  // open, with its dot pulsing, under a finished answer.
  const during = feed([
    ["user.message", { text: "ola" }],
    ["model.thinking.delta", { text: "The user said ola." }],
    ["model.stream.delta", { text: "Olá!" }],
  ]);
  const thinking = during.transcript.items.find((item) => item.kind === "thinking");
  assert.equal((thinking as { streaming?: boolean }).streaming, false);
});

test("a reasoning block left live is closed when the response completes", () => {
  // Reasoning, then a tool call and its result: nothing streamed after it.
  const state = feed([
    ["model.thinking.delta", { text: "hmm" }],
    ["tool.call.started", { tool_call_id: "t", name: "read_file" }],
    ["model.response.completed", { finish_reason: "tool_calls" }],
  ]);
  const live = state.transcript.items.filter(
    (item) => item.kind === "thinking" && (item as { streaming?: boolean }).streaming
  );
  assert.equal(live.length, 0);
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

test("the workspace branch lands in the session, not in the transcript", () => {
  const state = feed([["workspace.branch", { branch: "feature/mac" }]]);
  assert.equal(state.session.branch, "feature/mac");
  assert.equal(state.transcript.items.length, initialAppState.transcript.items.length);
  assert.equal(feed([["workspace.branch", { branch: "" }]]).session.branch, "");
});

const notes = (state: ReturnType<typeof feed>) =>
  state.transcript.items.flatMap((item) => (item.kind === "notice" ? [[item.text, item.queued]] : []));

test("a message waiting its turn can be handed to the running one, and the line moves up", () => {
  const state = feed([
    ["user.message", { text: "primeira" }],
    ["user.message.enqueued", { text: "a", position: 1 }],
    ["user.message.enqueued", { text: "b", position: 2 }],
    ["user.message.enqueued", { text: "c", position: 3 }],
    ["user.message.promoted", { text: "b", position: 2 }],
  ]);
  assert.deepEqual(notes(state), [
    ["queued: answered once the running turn ends", "a"],
    ["queued: waiting for the current step to finish", undefined],
    ["queued: answered once the running turn ends (#2 in the queue)", "c"],
  ]);
  // Delivered: its note says so, though others came after it.
  const delivered = applyEvent(state, ev("user.message.delivered", { text: "b" }));
  assert.equal(notes(delivered)[1][0], "queued: delivered to the model");
  assert.equal(notes(delivered)[2][1], "c");
});

test("a message whose own turn has come is in no line any more", () => {
  const state = feed([
    ["user.message", { text: "primeira" }],
    ["user.message.enqueued", { text: "a", position: 1 }],
    ["user.message.enqueued", { text: "b", position: 2 }],
    ["user.message", { text: "a" }],
  ]);
  assert.deepEqual(notes(state), [
    ["queued: its turn has come", undefined],
    ["queued: answered once the running turn ends", "b"],
  ]);
});

test("a turn the runtime stopped says why, and which setting decides it", () => {
  const state = feed([
    ["user.message", { text: "fix it" }],
    ["turn.budget_exhausted", { reason: "model_stalled", limit: 12, setting: "budgets.max_stall_rounds" }],
  ]);
  const last = state.transcript.items[state.transcript.items.length - 1];
  assert.equal(last.kind, "notice");
  assert.match(last.kind === "notice" ? last.text : "", /12 rounds.*budgets\.max_stall_rounds/);
});

test("a turn that ended with nothing said does not end in silence", () => {
  const state = feed([["user.message", { text: "fix it" }], ["turn.no_answer", {}]]);
  const last = state.transcript.items[state.transcript.items.length - 1];
  assert.equal(last.kind, "notice");
});
