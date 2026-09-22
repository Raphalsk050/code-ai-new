import esbuild from "esbuild";

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

/** Webview bundle: browser IIFE, React inlined. */
const webviewConfig = {
  entryPoints: ["webview/index.tsx"],
  outfile: "dist/webview.js",
  bundle: true,
  platform: "browser",
  format: "iife",
  target: "es2021",
  sourcemap: true,
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
};

const configs = [extensionConfig, webviewConfig, flowConfig];

if (watch) {
  const contexts = await Promise.all(configs.map((config) => esbuild.context(config)));
  await Promise.all(contexts.map((context) => context.watch()));
  console.log("watching...");
} else {
  await Promise.all(configs.map((config) => esbuild.build(config)));
  console.log("build complete");
}
