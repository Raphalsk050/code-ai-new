#!/usr/bin/env node
// Unit tests without a test framework: esbuild (already a dependency) bundles
// the TypeScript, node's own runner executes it. One less toolchain to install,
// and it runs anywhere `npm run build` already runs.
//
// Covers webview/ and the host modules under src/ that hold no `vscode` import
// (the API is only available inside the extension host, so anything touching it
// is left to the integration tests).

import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import esbuild from "esbuild";
import { glob } from "node:fs/promises";

import { webviewPlugins } from "./webview-build.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const out = mkdtempSync(join(tmpdir(), "code-ai-webview-tests-"));

const entries = [];
for await (const file of glob("{webview,src}/**/*.test.ts", { cwd: root })) {
  entries.push(join(root, file));
}
if (entries.length === 0) {
  console.error("no test files found under webview/ or src/");
  process.exit(1);
}

await esbuild.build({
  entryPoints: entries,
  outdir: out,
  outbase: root,
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node18",
  external: ["node:*"],
  plugins: webviewPlugins,
  sourcemap: "inline",
});

// `outbase` mirrors the source tree under `out`, so webview/ and src/ keep
// their own subtrees and two same-named test files cannot collide.
const bundled = entries.map((entry) =>
  join(out, entry.slice(root.length).replace(/\.ts$/, ".js"))
);
const result = spawnSync(process.execPath, ["--test", ...bundled], { stdio: "inherit" });
rmSync(out, { recursive: true, force: true });
process.exit(result.status ?? 1);
