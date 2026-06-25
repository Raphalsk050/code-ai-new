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

if (watch) {
  const ctxA = await esbuild.context(extensionConfig);
  const ctxB = await esbuild.context(webviewConfig);
  await Promise.all([ctxA.watch(), ctxB.watch()]);
  console.log("watching...");
} else {
  await Promise.all([esbuild.build(extensionConfig), esbuild.build(webviewConfig)]);
  console.log("build complete");
}
