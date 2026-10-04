import assert from "node:assert/strict";
import test from "node:test";

import { initialAgentStatus, reduceAgentStatus, statusView } from "../agent-status";
import type { EventEnvelope } from "../protocol";

const ev = (event_type: string, payload: Record<string, unknown> = {}): EventEnvelope => ({
  event_id: event_type,
  event_type,
  event_version: 1,
  session_id: "s",
  sequence: 0,
  timestamp: "",
  source: "core",
  payload,
});

test("the status bar shows the context left and what the agent is doing", () => {
  let status = initialAgentStatus;
  assert.equal(statusView(status).text, "$(debug-disconnect) Code-AI");

  for (const event of [
    ev("status.changed", { state: "READY" }),
    ev("usage.updated", { active_context_tokens: 61000, context_budget: 128000, context_threshold: 0.8 }),
  ]) {
    status = reduceAgentStatus(status, event);
  }
  assert.deepEqual(statusView(status), {
    text: "$(hubot) Code-AI 48%",
    tooltip: "Ready · Context 61k of 128k",
    warn: false,
  });

  status = reduceAgentStatus(status, ev("status.changed", { state: "EXECUTING_TOOL" }));
  status = reduceAgentStatus(status, ev("tool.call.started", { name: "web_search" }));
  assert.equal(statusView(status).tooltip, "Running web_search · Context 61k of 128k");

  status = reduceAgentStatus(status, ev("tool.approval.requested", {}));
  assert.equal(statusView(status).text.startsWith("$(bell-dot)"), true);

  status = reduceAgentStatus(status, ev("usage.updated", { active_context_tokens: 110000 }));
  assert.equal(statusView(status).warn, true);
});
