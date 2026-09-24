// The settings panel's MCP section, end to end: what it asks the host for,
// and what each button puts on the wire.

import { settingsSnapshot } from "./harness.mjs";

const SECRET = "ghp_real_token";

function spec(overrides = {}) {
  return {
    name: "github",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-github"],
    env: { GITHUB_TOKEN: SECRET },
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
    ...overrides,
  };
}

function server(overrides = {}) {
  return {
    name: "github",
    status: "connected",
    error: "",
    transport: "stdio",
    source: "config",
    source_path: "",
    deferred: false,
    group: "",
    server: { name: "github", title: "GitHub", version: "1", protocol: "2025-06-18", instructions: "" },
    tools: [
      {
        name: "mcp__github__search_code",
        remote: "search_code",
        title: "Search code",
        description: "Search code",
        read_only: true,
        enabled: true,
      },
    ],
    prompts: [],
    resources: false,
    missing_env: [],
    logs: [],
    diagnostics: "",
    spec: spec(),
    ...overrides,
  };
}

/** Open the settings with a snapshot; returns what the panel asked the host for. */
async function openPanel(ui, servers = [server()]) {
  await ui.fromHost({ type: "settings", settings: settingsSnapshot() });
  await ui.page.click('button[title="Settings"]');
  await ui.page.waitForSelector(".settings-body");
  const asked = await ui.toHost("mcp");
  await ui.fromHost({ type: "mcpServers", list: { enabled: true, servers } });
  await ui.clearToHost();
  return asked;
}

function statusEvent(payload) {
  return {
    type: "event",
    event: {
      event_id: `e-${Math.random()}`,
      event_type: "mcp.server.status",
      event_version: 1,
      session_id: "s",
      sequence: 0,
      timestamp: new Date().toISOString(),
      source: "mcp",
      payload,
    },
  };
}

const field = (label, tag = "input") =>
  `.mcp-editor .settings-field:has(> .settings-field-label:text-is("${label}")) ${tag}`;

export const tests = {
  async "opening the settings asks for the MCP servers"(ui, assert) {
    const asked = await openPanel(ui);

    assert(asked.some((m) => m.action === "list"), "the panel should ask for the list");
    assert(
      (await ui.page.locator(".mcp-server").count()) === 1,
      "the configured server should get a card"
    );
    await ui.page.locator(".mcp-section").scrollIntoViewIfNeeded();
    await ui.shot("mcp-01-server-card");
  },

  async "a project's server starts only when Approve is clicked"(ui, assert) {
    await openPanel(ui, [
      server({
        name: "repo",
        status: "needs_approval",
        source: "claude",
        source_path: "C:/projetos/app/.mcp.json",
        error: "Declared in C:/projetos/app/.mcp.json; approve it to start it.",
        tools: [],
        spec: spec({ name: "repo", source: "claude" }),
      }),
    ]);

    await ui.page.locator(".mcp-section").scrollIntoViewIfNeeded();
    await ui.shot("mcp-02-needs-approval");
    assert(
      (await ui.page.textContent(".mcp-server-source")).includes(".mcp.json"),
      "the card should say which file declared it"
    );
    assert(
      (await ui.page.locator(".mcp-server-foot").count()) === 0,
      "a project's server cannot be edited or removed from here"
    );
    await ui.page.click(".mcp-approve");

    const [sent] = await ui.toHost("mcp");
    assert(sent?.action === "approve" && sent.name === "repo", "Approve should post approve");
  },

  async "adding a server posts its declaration"(ui, assert) {
    await openPanel(ui, []);

    await ui.page.click('.mcp-add button:has-text("Add server")');
    await ui.page.fill(field("Name"), "files");
    await ui.page.fill(field("Command"), "npx");
    await ui.page.fill(
      field("Arguments, one per line", "textarea"),
      "-y\n@modelcontextprotocol/server-filesystem\nC:/dados"
    );
    await ui.page.fill(field("Environment, KEY=value per line", "textarea"), "LOG=debug");
    await ui.page.locator(".mcp-editor").scrollIntoViewIfNeeded();
    await ui.shot("mcp-03-editor");
    await ui.page.click('.mcp-editor button:has-text("Save server")');

    const [sent] = await ui.toHost("mcp");
    assert(sent?.action === "save", "Save should post save");
    assert(sent.server.name === "files", "the name should be on the wire");
    assert(sent.server.args.length === 3, "one argument per line");
    assert(sent.server.env.LOG === "debug", "env lines become a map");
    assert(sent.previous === "", "a new server replaces nothing");
  },

  async "the form says what is missing instead of posting"(ui, assert) {
    await openPanel(ui, []);

    await ui.page.click('.mcp-add button:has-text("Add server")');
    await ui.page.fill(field("Name"), "files");
    await ui.page.click('.mcp-editor button:has-text("Save server")');

    assert((await ui.toHost("mcp")).length === 0, "nothing should be posted");
    assert(
      (await ui.page.textContent(".mcp-editor .mcp-server-error")).includes("command"),
      "the form should ask for the command"
    );
  },

  async "pasting a README block adds every server in it"(ui, assert) {
    await openPanel(ui, []);

    await ui.page.click('.mcp-add button:has-text("Paste JSON")');
    await ui.page.fill(
      ".mcp-editor textarea",
      JSON.stringify({
        mcpServers: {
          github: { command: "npx", args: ["-y", "@modelcontextprotocol/server-github"] },
          remote: { type: "http", url: "https://mcp.example.com/mcp" },
        },
      })
    );
    await ui.page.click('.mcp-editor button:has-text("Add")');

    const sent = await ui.toHost("mcp");
    assert(sent.length === 2, `both servers should be saved, got ${sent.length}`);
    assert(sent[1].server.type === "http", "the remote one keeps its transport");
  },

  async "a status event repaints the card and editing still shows the real env"(ui, assert) {
    await openPanel(ui, [server({ status: "connecting", tools: [] })]);

    await ui.fromHost(
      statusEvent(server({ status: "connected", spec: spec({ env: { GITHUB_TOKEN: "***" } }) }))
    );
    await ui.page.waitForSelector(".mcp-dot-connected");
    await ui.page.click('.mcp-server-foot button:has-text("Edit")');

    const env = await ui.page.inputValue(field("Environment, KEY=value per line", "textarea"));
    assert(env === `GITHUB_TOKEN=${SECRET}`, `the editor should hold the real token, got ${env}`);
  },

  async "a server's tools are switched on its card and saved with the settings"(ui, assert) {
    await ui.fromHost({
      type: "settings",
      settings: settingsSnapshot({
        supported: {
          ...settingsSnapshot().supported,
          tools: [
            ...settingsSnapshot().supported.tools,
            { name: "mcp__github__search_code", description: "Search code", enabled: true },
          ],
        },
      }),
    });
    await ui.page.click('button[title="Settings"]');
    await ui.page.waitForSelector(".settings-body");
    await ui.fromHost({ type: "mcpServers", list: { enabled: true, servers: [server()] } });

    const doctor = await ui.page.locator(".tool-switches .tool-switch-name").allTextContents();
    assert(!doctor.includes("mcp__github__search_code"), "the Doctor list leaves MCP tools out");

    await ui.page.click(".mcp-tools summary");
    await ui.page.click('.mcp-tools .tool-switch:has-text("search_code") input');
    await ui.clearToHost();
    await ui.page.click(".settings-save");

    const [sent] = await ui.toHost("updateSettings");
    assert(
      JSON.stringify(sent.updates.disabled_tools) === '["mcp__github__search_code"]',
      `the tool should be switched off, got ${JSON.stringify(sent.updates.disabled_tools)}`
    );
    assert(sent.updates.mcp_enabled === true, "the MCP switch travels with the settings");
  },
};
