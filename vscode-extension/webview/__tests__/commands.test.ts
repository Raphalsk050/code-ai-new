import assert from "node:assert/strict";
import test from "node:test";

import { buildCatalog, matchCommands, resolveCommand } from "../commands";
import type { SlashCommandSpec } from "../../src/protocol";

const spec = (
  command: string,
  available = true,
  kind: SlashCommandSpec["kind"] = "command"
): SlashCommandSpec => ({
  command,
  description: command,
  completion: command + " ",
  available,
  kind,
});

test("the backend catalog is merged with the webview's own commands", () => {
  const catalog = buildCatalog([spec("/compact"), spec("/goal <objetivo>")]);
  assert.ok(catalog.find((c) => c.command === "/new"));
  assert.ok(catalog.find((c) => c.command === "/compact"));
});

test("a command the bridge cannot run is offered but marked unavailable", () => {
  const catalog = buildCatalog([spec("/doctor", false), spec("/quit", false)]);
  assert.equal(catalog.find((c) => c.command === "/doctor")?.available, true); // opens settings
  assert.equal(catalog.find((c) => c.command === "/quit")?.available, false);
});

test("workflows and skills route to the asset runner", () => {
  const catalog = buildCatalog([spec("/deploy", true, "workflow"), spec("/tdd", true, "skill")]);
  assert.equal(catalog.find((c) => c.command === "/deploy")?.action.kind, "asset");
  assert.equal(catalog.find((c) => c.command === "/tdd")?.action.kind, "asset");
});

test("typing a prefix narrows the menu", () => {
  const catalog = buildCatalog([spec("/goal <objetivo>"), spec("/goal start"), spec("/compact")]);
  const matches = matchCommands(catalog, "/goal");
  assert.ok(matches.every((m) => m.command.startsWith("/goal")));
  assert.equal(matchCommands(catalog, "sem barra").length, 0);
});

test("resolving splits the command from its argument", () => {
  const catalog = buildCatalog([spec("/goal <objetivo>")]);
  const resolved = resolveCommand(catalog, "/goal deixar a suíte verde");
  assert.equal(resolved?.command.command, "/goal <objetivo>");
  assert.equal(resolved?.argument, "deixar a suíte verde");
});

test("the longest matching command wins over its prefix", () => {
  const catalog = buildCatalog([spec("/goal <objetivo>"), spec("/goal start")]);
  assert.equal(resolveCommand(catalog, "/goal start")?.command.command, "/goal start");
});

test("permission and planner modes are distinct routes", () => {
  const catalog = buildCatalog([spec("/mode bypass"), spec("/plan"), spec("/act")]);
  assert.deepEqual(catalog.find((c) => c.command === "/mode bypass")?.action, {
    kind: "permission",
    mode: "bypass",
  });
  assert.deepEqual(catalog.find((c) => c.command === "/plan")?.action, {
    kind: "planner",
    mode: "plan",
  });
});

test("text that is not a command resolves to nothing", () => {
  assert.equal(resolveCommand(buildCatalog([]), "só uma pergunta"), null);
});
