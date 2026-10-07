import { copyFileSync, mkdirSync } from "node:fs";

import esbuild from "esbuild";

import { webviewPlugins } from "./scripts/webview-build.mjs";

const watch = process.argv.includes("--watch");

/** Extension host bundle: CommonJS for Node, with `vscode` left external. */
const extensionConfig = {
  entryPoints: ["src/extension.ts"],
  outfile: "dist/extension.js",
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node18",
  external: ["vscode"],
  sourcemap: true,
};

// What is packaged runs React's production build. Its development build checks
// every property of every element on every render, and a long conversation pays
// for that on each token. `--watch` keeps the development build and its warnings.
const shipped = watch ? {} : { minify: true, define: { "process.env.NODE_ENV": '"production"' } };

/** Webview bundle: browser IIFE, React inlined. */
const webviewConfig = {
  entryPoints: ["webview/index.tsx"],
  outfile: "dist/webview.js",
  bundle: true,
  platform: "browser",
  format: "iife",
  target: "es2021",
  plugins: webviewPlugins,
  sourcemap: true,
  ...shipped,
};

/** Orchestrator-graph editor bundle: its own webview, same shape as the chat. */
const flowConfig = {
  entryPoints: ["webview/flow/index.tsx"],
  outfile: "dist/flow.js",
  bundle: true,
  platform: "browser",
  format: "iife",
  target: "es2021",
  sourcemap: true,
  ...shipped,
};

const configs = [extensionConfig, webviewConfig, flowConfig];

/** The codicon font and its stylesheet ship beside the bundles: node_modules is not packaged. */
function copyCodicons() {
  mkdirSync("dist/codicons", { recursive: true });
  for (const file of ["codicon.css", "codicon.ttf"]) {
    copyFileSync(`node_modules/@vscode/codicons/dist/${file}`, `dist/codicons/${file}`);
  }
}
copyCodicons();

if (watch) {
  const contexts = await Promise.all(configs.map((config) => esbuild.context(config)));
  await Promise.all(contexts.map((context) => context.watch()));
  console.log("watching...");
} else {
  await Promise.all(configs.map((config) => esbuild.build(config)));
  console.log("build complete");
}
