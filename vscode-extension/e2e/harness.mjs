// The webview, loaded in a real browser, wired to a fake extension host.
//
// The webview only ever talks to the host two ways: `postMessage` out through
// `acquireVsCodeApi()`, and `window` message events in. Both are stubbed here,
// so a spec drives the panel exactly as the extension does - no VS Code, no
// bridge, no Python.

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import esbuild from "esbuild";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("..", import.meta.url));

const PAGE = `<!DOCTYPE html>
<html lang="pt-BR">
  <head>
    <meta charset="UTF-8" /><title>Code-AI e2e</title>
    <!-- The variables VSCode injects into a webview (Dark Modern), so what a
         screenshot shows is what the user sees rather than CSS fallbacks. -->
    <style>
      :root {
        --vscode-font-family: "Segoe UI", system-ui, sans-serif;
        --vscode-editor-font-family: Consolas, "Courier New", monospace;
        --vscode-foreground: #cccccc;
        --vscode-editor-background: #1f1f1f;
        --vscode-editor-foreground: #cccccc;
        --vscode-editorWidget-background: #252526;
        --vscode-editor-inactiveSelectionBackground: #3a3d41;
        --vscode-panel-border: #3c3c3c;
        --vscode-descriptionForeground: #9d9d9d;
        --vscode-textLink-foreground: #4daafc;
        --vscode-input-background: #313131;
        --vscode-input-foreground: #cccccc;
        --vscode-input-border: #3c3c3c;
        --vscode-errorForeground: #f85149;
        --vscode-editorWarning-foreground: #cca700;
        --vscode-testing-iconPassed: #3fb950;
      }
    </style>
  </head>
  <body>
    <div id="root"></div>
    <script>
      window.__toHost = [];
      window.__state = undefined;
      window.acquireVsCodeApi = () => ({
        postMessage: (message) => { window.__toHost.push(message); },
        getState: () => window.__state,
        setState: (state) => { window.__state = state; },
      });
    </script>
    <script src="./webview.js"></script>
  </body>
</html>`;

/**
 * Bundle a webview the way `npm run build` does, into a throwaway dir.
 *
 * `entry` picks which one: the chat panel (default) or the orchestrator graph
 * editor, which is a second bundle with the same host contract.
 */
export async function buildWebview(entry = "webview/index.tsx", bundleName = "webview.js") {
  const out = mkdtempSync(join(tmpdir(), "code-ai-e2e-"));
  await esbuild.build({
    entryPoints: [join(root, entry)],
    outfile: join(out, bundleName),
    bundle: true,
    platform: "browser",
    format: "iife",
    target: "es2021",
  });
  writeFileSync(join(out, "index.html"), PAGE.replace("./webview.js", `./${bundleName}`), "utf8");
  return { dir: out, dispose: () => rmSync(out, { recursive: true, force: true }) };
}

export function buildFlowWebview() {
  return buildWebview("webview/flow/index.tsx", "flow.js");
}

/**
 * A browser, preferring the one Playwright ships.
 *
 * The bundled build is not installed everywhere (`playwright install` is a
 * few hundred megabytes through a proxy that may not cooperate), and every
 * Windows machine that runs VS Code already has Edge. Either renders the
 * webview identically - it is the same engine.
 */
export async function launchBrowser() {
  const attempts = [undefined, "msedge", "chrome"];
  const failures = [];
  for (const channel of attempts) {
    try {
      return await chromium.launch(channel ? { channel } : {});
    } catch (err) {
      failures.push(`${channel ?? "bundled chromium"}: ${String(err).split("\n")[0]}`);
    }
  }
  throw new Error(`no chromium available\n  ${failures.join("\n  ")}`);
}

/** The panel, open and talking to a host the spec controls. */
export async function openWebview(browser, dir, options = {}) {
  const viewport = options.viewport ?? { width: 1100, height: 900 };
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on("pageerror", (err) => errors.push(String(err)));
  await page.goto(`file://${join(dir, "index.html").replace(/\\/g, "/")}`);
  await page.waitForSelector(options.ready ?? "#root .app");

  return {
    page,
    errors,
    /** Everything the webview has posted to the host, oldest first. */
    async toHost(type) {
      const all = await page.evaluate(() => window.__toHost);
      return type ? all.filter((m) => m.type === type) : all;
    },
    async clearToHost() {
      await page.evaluate(() => { window.__toHost.length = 0; });
    },
    /** Deliver a host -> webview message, as the extension's postMessage does. */
    async fromHost(message) {
      await page.evaluate(
        (data) => window.dispatchEvent(new MessageEvent("message", { data })),
        message
      );
    },
    /** Deliver a bus event the way the extension forwards one. */
    async event(event_type, payload = {}) {
      await page.evaluate(
        (data) => window.dispatchEvent(new MessageEvent("message", { data })),
        {
          type: "flowEvent",
          event: {
            event_id: `e-${Math.random()}`,
            event_type,
            event_version: 1,
            session_id: "s",
            sequence: 0,
            timestamp: new Date().toISOString(),
            source: "graph",
            payload,
          },
        }
      );
    },
    /** Save a screenshot of the panel into the shots directory. */
    async shot(name) {
      const file = join(shotsDir(), `${name}.png`);
      await page.screenshot({ path: file });
      return file;
    },
  };
}

/** Where e2e screenshots land (git-ignored), so a run can be eyeballed. */
export function shotsDir() {
  const dir = join(root, "e2e", "screenshots");
  mkdirSync(dir, { recursive: true });
  return dir;
}

/** A backend snapshot shaped like the one `getSettings` answers with. */
export function settingsSnapshot(overrides = {}) {
  return {
    model: "qwen2.5-coder:7b",
    api_mode: "ollama",
    base_url: "http://localhost:11434/v1",
    api_key_set: false,
    language: "en",
    permission_mode: "ask",
    reasoning_effort: "medium",
    learn: false,
    inline_hints_enabled: false,
    inline_model: "",
    max_context_tokens: 32768,
    workspace: "C:/projetos/code-ai",
    disabled_tools: [],
    subagent_models: [],
    max_concurrent_subagents: 1,
    temperature: null,
    top_p: null,
    top_k: null,
    supported: {
      api_mode: ["completions", "ollama", "responses"],
      permission_mode: ["always", "ask", "never"],
      reasoning_effort: ["none", "minimal", "low", "medium", "high", "xhigh"],
      tools: [
        { name: "read_file", description: "reads a file", enabled: true },
        { name: "write_file", description: "writes a file", enabled: true },
      ],
    },
    ...overrides,
  };
}
