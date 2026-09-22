#!/usr/bin/env node
// Which strings the panels ask `t()` for, and which of them the pt-BR table is
// missing. Run it after adding UI text:
//
//     node e2e/keys.mjs
//
// A missing entry is not a bug - `t()` falls back to English - but a language
// the user picked that is half English reads like a half-finished product.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const CALL = /\bt\(\s*"((?:[^"\\]|\\.)*)"/g;
const ENTRY = /^\s*(?:"((?:[^"\\]|\\.)*)"|([A-Za-z_]\w*))\s*:/gm;

function usedKeys(dir, found = new Set()) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      if (name !== "__tests__") usedKeys(path, found);
      continue;
    }
    if (!/\.tsx?$/.test(path) || path.endsWith("i18n.ts")) continue;
    const text = readFileSync(path, "utf8");
    for (const match of text.matchAll(CALL)) found.add(match[1].replace(/\\"/g, '"'));
  }
  return found;
}

const used = usedKeys(join(root, "webview"));
const table = readFileSync(join(root, "webview", "i18n.ts"), "utf8");
const have = new Set();
for (const match of table.matchAll(ENTRY)) {
  have.add((match[1] ?? match[2]).replace(/\\"/g, '"'));
}

const missing = [...used].filter((key) => !have.has(key) && !have.has(`tab|${key}`)).sort();
console.log(`used ${used.size} | translated ${used.size - missing.length} | missing ${missing.length}`);
for (const key of missing) console.log(JSON.stringify(key));
process.exit(process.argv.includes("--strict") && missing.length ? 1 : 0);
