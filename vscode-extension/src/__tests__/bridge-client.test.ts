import assert from "node:assert/strict";
import test from "node:test";

import { BridgeClient } from "../bridge-client";

// A stand-in bridge: silent for `delay` ms, then one event, then (if asked)
// an answer to every request it reads.
const FAKE = `
const [delay, mode] = process.argv.slice(1);
const out = (m) => process.stdout.write(JSON.stringify(m) + "\\n");
setTimeout(() => {
  out({ jsonrpc: "2.0", method: "event", params: { event_type: "session.started" } });
  require("readline").createInterface({ input: process.stdin }).on("line", (line) => {
    const m = JSON.parse(line);
    if (mode === "answer" && m.id !== undefined) out({ jsonrpc: "2.0", id: m.id, result: { ok: true } });
  });
}, Number(delay));
`;

const bridge = (delayMs: number, mode: "answer" | "mute", allowanceMs: number) =>
  new BridgeClient(process.execPath, ["-e", FAKE, String(delayMs), mode], undefined, allowanceMs);

test("what is asked while the bridge starts waits for it to start", async () => {
  const client = bridge(400, "answer", 5_000);
  try {
    assert.deepEqual(await client.request("getSettings", {}, 100), { ok: true });
  } finally {
    client.dispose();
  }
});

test("once it has spoken, a request has only its own time", async () => {
  const client = bridge(0, "mute", 5_000);
  try {
    await new Promise<void>((resolve) => client.once("event", () => resolve()));
    await assert.rejects(client.request("getSettings", {}, 100), /timed out after 0s/);
  } finally {
    client.dispose();
  }
});

test("a bridge that never speaks is given up on after the allowance", async () => {
  const client = bridge(60_000, "answer", 200);
  try {
    const started = Date.now();
    await assert.rejects(client.request("getSettings", {}, 100), /timed out/);
    assert.ok(Date.now() - started >= 300);
  } finally {
    client.dispose();
  }
});
