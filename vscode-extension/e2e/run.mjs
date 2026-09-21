#!/usr/bin/env node
// End-to-end tests for the webview: the real bundle, in a real browser.
//
// `npm run test:webview` covers the modules that hold no DOM. This one covers
// what only shows up once React is mounted - effects firing in the wrong
// order, a form rewriting itself from a message it should have read first.
// Each spec exports `tests`, a map of name -> (ui, assert) => Promise.

import { buildWebview, launchBrowser, openWebview } from "./harness.mjs";
import { tests as settings } from "./settings.e2e.mjs";

const SPECS = { settings };

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const bundle = await buildWebview();
let browser;
try {
  browser = await launchBrowser();
} catch (err) {
  bundle.dispose();
  console.error(String(err));
  process.exit(1);
}

let failed = 0;
let passed = 0;
for (const [spec, tests] of Object.entries(SPECS)) {
  console.log(`\n${spec}`);
  for (const [name, run] of Object.entries(tests)) {
    // A fresh page per test: the webview persists state, and one test leaking
    // into the next is the kind of flake that costs an afternoon.
    const ui = await openWebview(browser, bundle.dir);
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

await browser.close();
bundle.dispose();
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
