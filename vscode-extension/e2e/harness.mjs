// The webview, loaded in a real browser, wired to a fake extension host.
//
// The webview only ever talks to the host two ways: `postMessage` out through
// `acquireVsCodeApi()`, and `window` message events in. Both are stubbed here,
// so a spec drives the panel exactly as the extension does - no VS Code, no
// bridge, no Python.

import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import esbuild from "esbuild";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("..", import.meta.url));

const PAGE = `<!DOCTYPE html>
<html lang="pt-BR">
  <head><meta charset="UTF-8" /><title>Code-AI e2e</title></head>
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

/** Bundle the webview the way `npm run build` does, into a throwaway dir. */
export async function buildWebview() {
  const out = mkdtempSync(join(tmpdir(), "code-ai-e2e-"));
  await esbuild.build({
    entryPoints: [join(root, "webview/index.tsx")],
    outfile: join(out, "webview.js"),
    bundle: true,
    platform: "browser",
    format: "iife",
    target: "es2021",
  });
  writeFileSync(join(out, "index.html"), PAGE, "utf8");
  return { dir: out, dispose: () => rmSync(out, { recursive: true, force: true }) };
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
export async function openWebview(browser, dir) {
  const page = await browser.newPage({ viewport: { width: 900, height: 900 } });
  const errors = [];
  page.on("pageerror", (err) => errors.push(String(err)));
  await page.goto(`file://${join(dir, "index.html").replace(/\\/g, "/")}`);
  await page.waitForSelector("#root .app");

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
  };
}

/** A backend snapshot shaped like the one `getSettings` answers with. */
export function settingsSnapshot(overrides = {}) {
  return {
    model: "qwen2.5-coder:7b",
    api_mode: "ollama",
    base_url: "http://localhost:11434/v1",
    api_key_set: false,
    language: "pt-BR",
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
