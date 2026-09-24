import assert from "node:assert/strict";
import test from "node:test";

import { buildCatalog } from "../commands";
import {
  declarationOf,
  EMPTY_FORM,
  formFromSpec,
  formProblem,
  mapOf,
  mergeStatus,
  parseImport,
} from "../mcp-form";
import type { McpServerInfo, McpServerSpec } from "../../src/protocol";

const spec: McpServerSpec = {
  name: "github",
  transport: "stdio",
  command: "npx",
  args: ["-y", "@modelcontextprotocol/server-github"],
  env: { GITHUB_TOKEN: "ghp_secret" },
  cwd: "",
  url: "",
  headers: {},
  enabled: true,
  trust: false,
  defer: "auto",
  timeout_s: 60,
  tool_timeout_s: 600,
  source: "config",
  source_path: "",
  problem: "",
};

function server(overrides: Partial<McpServerInfo>): McpServerInfo {
  return {
    name: "github",
    status: "connected",
    error: "",
    transport: "stdio",
    source: "config",
    source_path: "",
    deferred: false,
    group: "",
    server: null,
    tools: [],
    prompts: [],
    resources: false,
    missing_env: [],
    logs: [],
    diagnostics: "",
    spec,
    ...overrides,
  };
}

test("a declaration survives a round trip through the form", () => {
  const declaration = declarationOf(formFromSpec(spec));

  assert.deepEqual(declaration, {
    name: "github",
    transport: "stdio",
    enabled: true,
    trust: false,
    defer: "auto",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-github"],
    env: { GITHUB_TOKEN: "ghp_secret" },
  });
});

test("a remote server keeps its url and headers, and nothing of stdio", () => {
  const form = { ...EMPTY_FORM, name: "api", transport: "http" as const, command: "stale" };
  form.url = " https://mcp.example.com/mcp ";
  form.headers = "Authorization: Bearer ${TOKEN}\nX-Team:core";

  const declaration = declarationOf(form);

  assert.equal(declaration.url, "https://mcp.example.com/mcp");
  assert.deepEqual(declaration.headers, { Authorization: "Bearer ${TOKEN}", "X-Team": "core" });
  assert.equal("command" in declaration, false);
});

test("env lines split on the first = only", () => {
  assert.deepEqual(mapOf("A=1\n\nB = x=y\nbroken\n=nokey", "="), { A: "1", B: "x=y" });
});

test("the form says what is missing before it is saved", () => {
  assert.match(formProblem(EMPTY_FORM, []), /name/);
  assert.match(formProblem({ ...EMPTY_FORM, name: "bad name" }, []), /letters/);
  assert.match(formProblem({ ...EMPTY_FORM, name: "fs" }, ["fs"]), /already/);
  assert.match(formProblem({ ...EMPTY_FORM, name: "fs" }, []), /command/);
  assert.match(formProblem({ ...EMPTY_FORM, name: "api", transport: "http" }, []), /URL/);
  assert.equal(formProblem({ ...EMPTY_FORM, name: "fs", command: "npx" }, []), "");
});

test("a pasted README block adds every server in it", () => {
  const parsed = parseImport(
    JSON.stringify({
      mcpServers: {
        "my server": { command: "npx", args: ["-y", "pkg"] },
        remote: { type: "http", url: "https://x/mcp" },
        empty: {},
      },
    })
  );

  assert.equal(parsed.error, "");
  assert.deepEqual(
    parsed.servers.map((s) => s.name),
    ["my_server", "remote"]
  );
  assert.equal(parsed.servers[0].declaration.name, "my_server");
  assert.equal(parsed.servers[1].declaration.type, "http");
});

test("VS Code's servers block and a bare map both import", () => {
  assert.equal(parseImport('{"servers": {"a": {"url": "https://a"}}}').servers.length, 1);
  assert.equal(parseImport('{"b": {"command": "uvx"}}').servers.length, 1);
});

test("a broken paste says why", () => {
  assert.match(parseImport("{nope").error, /Not valid JSON/);
  assert.match(parseImport("[1]").error, /object/);
  assert.match(parseImport('{"mcpServers": {"x": {}}}').error, /command or a url/);
});

test("a status event updates the card but keeps the secrets the editor needs", () => {
  const masked = { ...spec, env: { GITHUB_TOKEN: "***" } };
  const list = [server({ status: "connecting" })];

  const next = mergeStatus(list, server({ status: "connected", spec: masked }));

  assert.equal(next[0].status, "connected");
  assert.equal(next[0].spec.env.GITHUB_TOKEN, "ghp_secret");
  assert.equal(mergeStatus([], server({ name: "new" })).length, 1);
});

test("MCP prompts in the backend catalog run like workflows", () => {
  const catalog = buildCatalog([
    {
      command: "/mcp:github:review",
      description: "Review a PR",
      completion: "/mcp:github:review ",
      available: true,
      kind: "mcp",
    },
    { command: "/mcp", description: "Servers", completion: "/mcp", available: false },
  ]);

  assert.equal(catalog.find((c) => c.command === "/mcp:github:review")?.action.kind, "asset");
  assert.equal(catalog.find((c) => c.command === "/mcp")?.action.kind, "settings");
  assert.equal(catalog.find((c) => c.command === "/mcp")?.available, true);
});
