import assert from "node:assert/strict";
import test from "node:test";

import { activityLabel } from "../messages";
import { applyEvent, initialAppState, messagesToItems } from "../store";
import { callTarget, toolKind } from "../tool-target";
import type { EventEnvelope } from "../../src/protocol";

let seq = 0;
function ev(event_type: string, payload: Record<string, unknown> = {}): EventEnvelope {
  seq += 1;
  return {
    event_id: `a${seq}`,
    event_type,
    event_version: 1,
    session_id: "s",
    sequence: seq,
    timestamp: "",
    source: "core",
    payload,
  };
}

function feed(events: [string, Record<string, unknown>?][], state = initialAppState) {
  for (const [type, payload] of events) state = applyEvent(state, ev(type, payload));
  return state;
}

test("a call is described by what it works on", () => {
  assert.equal(callTarget({ path: "src/a.py" }), "src/a.py");
  assert.equal(callTarget({ path: "a.py", start_line: 3, end_line: 9 }), "a.py:3-9");
  assert.equal(callTarget({ command: "npm  test\n--ci" }), "npm test --ci");
  assert.equal(callTarget({ query: "parse_config", path: "src" }), "parse_config in src");
  assert.equal(callTarget({ tasks: [{}, {}] }), "2 tasks");
  assert.ok(callTarget({ path: "deep/".repeat(30) + "file.py" }).endsWith("file.py"));
  // Typed text and credentials never become the target.
  assert.equal(callTarget({ text: "hunter2" }), "");
  assert.equal(callTarget({ password: "hunter2", user: "ana" }), "ana");
  assert.equal(toolKind("read_file"), "read");
  assert.equal(toolKind("edit_code"), "write");
  assert.equal(toolKind("execute_command"), "run");
  assert.equal(toolKind("search_code"), "search");
  assert.equal(toolKind("browser_open"), "web");
});

test("a running tool shows its target on the card and in the status line", () => {
  const state = feed([
    ["status.changed", { state: "EXECUTING_TOOL" }],
    ["tool.call.started", { tool_call_id: "c1", name: "read_file", target: "src/a.py" }],
  ]);
  const card = state.transcript.items.at(-1);
  assert.equal(card?.kind, "tool");
  assert.equal(card?.kind === "tool" && card.target, "src/a.py");
  assert.equal(activityLabel(state.session), "running read_file · src/a.py");

  const two = feed([["tool.call.started", { tool_call_id: "c2", name: "search_code" }]], state);
  assert.equal(activityLabel(two.session), "running 2 tools · read_file, search_code");

  const done = feed(
    [
      ["tool.call.completed", { tool_call_id: "c1", name: "read_file", result: {} }],
      ["tool.call.failed", { tool_call_id: "c2", name: "search_code", message: "x" }],
    ],
    two
  );
  assert.deepEqual(done.session.runningTools, []);
});

test("a saved conversation's calls get their targets back", () => {
  const items = messagesToItems([
    { role: "user", content: "read it" },
    {
      role: "assistant",
      content: "",
      tool_calls: [{ id: "t1", name: "read_file", arguments: { path: "notes.md" } }],
    },
    { role: "tool", content: "{}", tool_call_id: "t1" },
  ]);
  const card = items.find((item) => item.kind === "tool");
  assert.equal(card?.kind === "tool" && card.target, "notes.md");
});

test("a retry takes back the broken attempt and leaves the transcript alone", () => {
  const state = feed([
    ["user.message", { text: "oi" }],
    ["status.changed", { state: "CALLING_MODEL" }],
    ["model.request.started", { step: 0 }],
    ["model.thinking.delta", { text: "hmm" }],
    ["model.stream.delta", { text: "Half an ans" }],
    [
      "model.request.retrying",
      { attempt: 1, max_attempts: 20, reason: "502 Bad Gateway", delay_s: 0.5, discarded: true },
    ],
  ]);
  const items = state.transcript.items;
  assert.deepEqual(
    items.map((item) => item.kind),
    ["user"]
  );
  assert.equal(activityLabel(state.session), "retrying 1/20");
  assert.equal(state.session.retryNote, "502 Bad Gateway");

  const again = feed(
    [["model.request.retrying", { attempt: 2, max_attempts: 20, reason: "timeout", delay_s: 4 }]],
    state
  );
  assert.equal(again.transcript.items.length, 1);
  assert.equal(again.session.retry, "2/20");
  assert.equal(again.session.retryNote, "timeout · 4s");

  const answered = feed([["model.stream.delta", { text: "Whole answer" }]], again);
  assert.equal(answered.session.retry, "");
  assert.equal(answered.session.retryNote, "");
  assert.equal(answered.transcript.items.at(-1)?.kind, "assistant");
});

test("a turn resent after a crash starts over instead of stacking", () => {
  const state = feed([
    ["user.message", { text: "fix it" }],
    ["model.request.started", { step: 0 }],
    ["model.stream.delta", { text: "Looking at" }],
    ["tool.call.started", { tool_call_id: "c1", name: "read_file", target: "a.py" }],
    ["status.changed", { state: "DISCONNECTED" }],
    ["error", { message: "bridge exited (1)" }],
    ["turn.resending", { text: "fix it", attempt: 1, max_attempts: 20 }],
    ["user.message", { text: "fix it" }],
  ]);
  const kinds = state.transcript.items.map((item) => item.kind);
  assert.deepEqual(kinds, ["notice", "user"]);
  const notice = state.transcript.items[0];
  assert.ok(notice.kind === "notice" && notice.text.includes("(1/20)"));
  assert.deepEqual(state.session.runningTools, []);
});

function at(event_type: string, payload: Record<string, unknown>, timestamp: string): EventEnvelope {
  return { ...ev(event_type, payload), timestamp };
}

test("a call knows how long it ran and how many lines it changed", () => {
  let state = applyEvent(initialAppState, at("tool.call.started", { tool_call_id: "w", name: "edit_code", target: "a.py", path: "a.py" }, "2026-10-03T10:00:00.000Z"));
  state = applyEvent(state, at("tool.call.completed", { tool_call_id: "w", name: "edit_code", result: { path: "a.py", diff: "--- a\n+++ b\n@@\n-x\n+y\n+z\n" } }, "2026-10-03T10:00:00.420Z"));
  const card = state.transcript.items.at(-1);
  assert.ok(card?.kind === "tool");
  assert.equal(card.kind === "tool" && card.ms, 420);
  assert.deepEqual(card.kind === "tool" && [card.added, card.removed], [2, 1]);

  const reported = applyEvent(state, at("tool.call.started", { tool_call_id: "n", name: "write_file", path: "b.py" }, ""));
  const done = applyEvent(reported, at("tool.call.completed", { tool_call_id: "n", name: "write_file", result: { lines_added: 7, lines_removed: 0 } }, ""));
  const second = done.transcript.items.at(-1);
  assert.deepEqual(second?.kind === "tool" && [second.added, second.removed, second.ms], [7, 0, undefined]);
});

test("a finished turn ends on a footer with its time, tokens, files and verification", () => {
  let state = initialAppState;
  const step = (type: string, payload: Record<string, unknown>, ts = "") => (state = applyEvent(state, at(type, payload, ts)));
  step("usage.updated", { cumulative: { total_tokens: 1000 } });
  step("user.message", { text: "fix it" }, "2026-10-03T10:00:00Z");
  step("tool.call.started", { tool_call_id: "1", name: "edit_code", target: "a.py", path: "src/a.py" });
  step("tool.call.completed", { tool_call_id: "1", name: "edit_code", result: {} });
  step("tool.call.started", { tool_call_id: "2", name: "write_file", target: "b.py", path: "src/b.py" });
  step("tool.call.failed", { tool_call_id: "2", name: "write_file", message: "denied" });
  step("planning.step.completed", { latest_verification_passed: true });
  step("usage.updated", { cumulative: { total_tokens: 4200 } });
  step("assistant.final", { text: "Done." });
  step("turn.completed", {}, "2026-10-03T10:01:05Z");
  const footer = state.transcript.items.at(-1);
  assert.ok(footer?.kind === "turn");
  assert.deepEqual(footer.kind === "turn" && [footer.ms, footer.tokens, footer.files, footer.verified], [65000, 3200, ["src/a.py"], true]);
  assert.equal(state.transcript.turn, null);

  // A steer mid-turn does not restart the clock.
  step("user.message", { text: "again" }, "2026-10-03T11:00:00Z");
  step("user.message", { text: "and this" }, "2026-10-03T11:00:30Z");
  step("turn.completed", {}, "2026-10-03T11:01:00Z");
  const next = state.transcript.items.at(-1);
  assert.equal(next?.kind === "turn" && next.ms, 60000);
});

test("a turn with nothing to say leaves no footer", () => {
  const state = feed([["user.message", { text: "hi" }], ["assistant.final", { text: "hello" }], ["turn.completed", {}]]);
  assert.equal(state.transcript.items.at(-1)?.kind, "assistant");
});
