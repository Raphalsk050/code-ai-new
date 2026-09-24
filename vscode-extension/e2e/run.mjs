#!/usr/bin/env node
// End-to-end tests for the webviews: the real bundles, in a real browser.
//
// `npm run test:webview` covers the modules that hold no DOM. This one covers
// what only shows up once React is mounted - effects firing in the wrong
// order, a form rewriting itself from a message it should have read first.
// Each spec exports `tests`, a map of name -> (ui, assert) => Promise.
//
// Two bundles, two groups: the chat panel and the orchestrator-graph editor.
// Screenshots land in e2e/shots/ so a run can also be looked at.

import { buildFlowWebview, buildWebview, launchBrowser, openWebview, shotsDir } from "./harness.mjs";
import { tests as settings } from "./settings.e2e.mjs";
import { tests as mcp } from "./mcp.e2e.mjs";
import { tests as flow } from "./flow.e2e.mjs";

const GROUPS = [
  {
    name: "chat",
    build: () => buildWebview(),
    ready: "#root .app",
    // The chat panel is a sidebar: its specs assume the width one has.
    viewport: { width: 900, height: 900 },
    specs: { settings, mcp },
  },
  {
    name: "flow",
    build: () => buildFlowWebview(),
    ready: "#root .flow-app",
    specs: { flow },
  },
];

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const only = process.argv.slice(2).filter((arg) => !arg.startsWith("-"));

let browser;
try {
  browser = await launchBrowser();
} catch (err) {
  console.error(String(err));
  process.exit(1);
}

let failed = 0;
let passed = 0;

for (const group of GROUPS) {
  if (only.length && !only.includes(group.name)) continue;
  const bundle = await group.build();
  try {
    for (const [spec, tests] of Object.entries(group.specs)) {
      console.log(`\n${group.name}/${spec}`);
      for (const [name, run] of Object.entries(tests)) {
        // A fresh page per test: the webview persists state, and one test
        // leaking into the next is the kind of flake that costs an afternoon.
        const ui = await openWebview(browser, bundle.dir, {
          ready: group.ready,
          viewport: group.viewport,
        });
        try {
          await run(ui, assert);
          if (ui.errors.length) throw new Error(`uncaught in the page: ${ui.errors[0]}`);
          console.log(`  ok  ${name}`);
          passed += 1;
        } catch (err) {
          console.log(`  FAIL ${name}`);
          console.log(`       ${String(err.message || err).split("\n")[0]}`);
          failed += 1;
        } finally {
          await ui.page.close();
        }
      }
    }
  } finally {
    bundle.dispose();
  }
}

await browser.close();
console.log(`\n${passed} passed, ${failed} failed`);
console.log(`screenshots: ${shotsDir()}`);
process.exit(failed ? 1 : 0);
