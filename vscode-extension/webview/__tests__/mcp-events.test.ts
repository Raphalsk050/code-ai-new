import assert from "node:assert/strict";
import test from "node:test";

import type { EventEnvelope } from "../../src/protocol";
import { applyEvent, initialAppState, initialValues, missingField } from "../store";
import type { ElicitationField } from "../store";

let sequence = 0;
function event(event_type: string, payload: Record<string, any> = {}): EventEnvelope {
  sequence += 1;
  return {
    event_id: `e-${sequence}`,
    event_type,
    event_version: 1,
    session_id: "s",
    sequence,
    timestamp: new Date().toISOString(),
    source: "test",
    payload,
  };
}

const run = (...events: EventEnvelope[]) => events.reduce(applyEvent, initialAppState);

const FIELDS: ElicitationField[] = [
  { name: "name", title: "Name", description: "", type: "string", required: true },
  { name: "age", title: "Age", description: "", type: "integer", required: false, default: 30 },
  { name: "news", title: "News", description: "", type: "boolean", required: true },
];

test("a server's question waits as a card until it is resolved", () => {
  const asked = run(
    event("mcp.elicitation.requested", { id: "q1", server: "github", message: "Who?", fields: FIELDS })
  );
  assert.equal(asked.elicitations.pending.length, 1);
  assert.equal(asked.elicitations.pending[0].server, "github");

  const answered = applyEvent(asked, event("mcp.elicitation.resolved", { id: "q1", action: "accept" }));
  assert.equal(answered.elicitations.pending.length, 0);
  assert.equal(applyEvent(answered, event("mcp.elicitation.resolved", { id: "q1" })), answered);
});

test("the form starts from the schema's defaults and names what is missing", () => {
  const values = initialValues(FIELDS);

  assert.deepEqual(values, { name: "", age: "30", news: false });
  assert.equal(missingField(FIELDS, values)?.name, "name");
  assert.equal(missingField(FIELDS, { ...values, name: "Rafa" }), null, "a checkbox is never missing");
});

test("progress lands on the running call of that MCP tool", () => {
  const state = run(
    event("tool.call.started", { tool_call_id: "c1", name: "mcp__github__search" }),
    event("mcp.tool.progress", { name: "mcp__github__search", progress: 3, total: 10, message: "pages" }),
    event("mcp.tool.progress", { name: "mcp__other__tool", progress: 1 })
  );

  const tool = state.transcript.items.find((item) => item.kind === "tool");
  assert.equal(tool?.kind === "tool" && tool.detail, "3/10 · pages");
});

test("a failing or waiting server says so once in the chat; a connected one does not", () => {
  const failed = { name: "github", status: "failed", error: "exited with code 1\nstack..." };
  const state = run(
    event("mcp.server.status", { name: "github", status: "connected" }),
    event("mcp.server.status", failed),
    event("mcp.server.status", failed),
    event("mcp.server.status", { name: "repo", status: "needs_approval", source_path: ".mcp.json" }),
    event("mcp.server.log", { server: "github", level: "info", message: "quiet" }),
    event("mcp.server.log", { server: "github", level: "error", message: "rate limited" })
  );

  const notices = state.transcript.items.flatMap((item) => (item.kind === "notice" ? [item.text] : []));
  assert.deepEqual(notices, [
    "MCP server github failed: exited with code 1",
    "MCP server repo (.mcp.json) is waiting for your approval in Settings.",
    "MCP github: rate limited",
  ]);
});
