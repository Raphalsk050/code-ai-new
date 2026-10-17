#!/usr/bin/env node
// Builds the page the desktop avatar shows (desktop/avatar.tsx) into one file
// the backend carries: src/code_ai/assets/desktop_avatar/avatar.html, or the
// path given. The VSIX knows nothing of it; the backend's native host loads it.

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import esbuild from "esbuild";

const root = fileURLToPath(new URL("..", import.meta.url));
const out = resolve(process.argv[2] ?? join(root, "..", "src", "code_ai", "assets", "desktop_avatar", "avatar.html"));

const bundle = await esbuild.build({
  entryPoints: [join(root, "desktop", "avatar.tsx")],
  bundle: true,
  write: false,
  platform: "browser",
  format: "iife",
  target: "es2021",
  minify: true,
});
// Inside a script element, nothing in the bundle may close the element.
const script = bundle.outputFiles[0].text.replace(/<\/script/g, "<\/script");

const html = `<!DOCTYPE html>
<html>
  <head><meta charset="UTF-8" /><title>Code-AI</title></head>
  <body><div id="root"></div><script>${script}</script></body>
</html>
`;
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, html);
console.log(`${out} (${Math.round(html.length / 1024)} KB)`);
