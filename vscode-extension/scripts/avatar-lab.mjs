#!/usr/bin/env node
// Builds dist/avatar-lab.html: the avatar's proposals on the real panel, in one
// file that opens in any browser. Not part of the extension - `npm run build`
// and the VSIX know nothing of it.

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import esbuild from "esbuild";

const root = fileURLToPath(new URL("..", import.meta.url));
const out = join(root, "dist", "avatar-lab.html");

const bundle = await esbuild.build({
  entryPoints: [join(root, "lab", "avatar.tsx")],
  bundle: true,
  write: false,
  platform: "browser",
  format: "iife",
  target: "es2021",
  minify: true,
});
// Inside a script element, nothing in the bundle may close the element.
const script = bundle.outputFiles[0].text.replace(/<\/script/g, "<\\/script");

const codicons = join(root, "node_modules", "@vscode", "codicons", "dist");
const font = readFileSync(join(codicons, "codicon.ttf")).toString("base64");
const icons = readFileSync(join(codicons, "codicon.css"), "utf8").replace(
  /src:\s*url\([^)]*\)[^;]*;/,
  `src: url(data:font/ttf;base64,${font}) format("truetype");`
);

// The variables VS Code gives a webview, Dark Modern and Light Modern.
const THEME = `
:root {
  --vscode-font-family: "Segoe UI", system-ui, sans-serif;
  --vscode-editor-font-family: Consolas, "Courier New", monospace;
  --vscode-foreground: #cccccc; --vscode-editor-background: #1f1f1f; --vscode-editor-foreground: #cccccc;
  --vscode-sideBar-background: #181818; --vscode-editorWidget-background: #252526;
  --vscode-panel-border: #3c3c3c; --vscode-descriptionForeground: #9d9d9d;
  --vscode-textLink-foreground: #4daafc; --vscode-input-background: #313131; --vscode-input-foreground: #cccccc;
  --vscode-input-border: #3c3c3c; --vscode-errorForeground: #f85149;
  --vscode-editorWarning-foreground: #cca700; --vscode-testing-iconPassed: #3fb950;
  --vscode-widget-shadow: rgba(0,0,0,.36);
}
:root.lab-light {
  --vscode-foreground: #3b3b3b; --vscode-editor-background: #ffffff; --vscode-editor-foreground: #3b3b3b;
  --vscode-sideBar-background: #f8f8f8; --vscode-editorWidget-background: #f8f8f8;
  --vscode-panel-border: #e5e5e5; --vscode-descriptionForeground: #616161;
  --vscode-textLink-foreground: #005fb8; --vscode-input-background: #ffffff; --vscode-input-foreground: #3b3b3b;
  --vscode-input-border: #cecece; --vscode-errorForeground: #e5484d;
  --vscode-editorWarning-foreground: #bf8803; --vscode-testing-iconPassed: #388a34;
  --vscode-widget-shadow: rgba(0,0,0,.16);
  --vscode-list-hoverBackground: rgba(0,0,0,.05);
}`;

// The page around the panel, in the panel's own tokens.
const LAB = `
html, body { margin: 0; }
.lab-shell { display: grid; grid-template-columns: minmax(360px, 520px) minmax(0, 1fr); height: 100vh; }
.lab-panel { position: relative; height: 100vh; overflow: hidden; background: var(--bg); border-right: 1px solid var(--border); }
.lab-panel #root, .lab-panel .app { height: 100%; }
.lab-float-layer { position: absolute; inset: 0; z-index: 40; pointer-events: none; }
.lab-float-layer > * { pointer-events: auto; }
/* The rail's colour behind cards in the pane's: the panel's own pairing. */
#lab {
  overflow-y: auto; height: 100vh; box-sizing: border-box; padding: 22px 26px 48px;
  background: var(--bg-rail); color: var(--fg); font-size: var(--fs-body); line-height: 1.5;
}
#lab > * { max-width: 720px; }
.lab-head h1 { margin: 0 0 4px; font-size: 18px; font-weight: 600; color: var(--fg-strong); }
.lab-head p, .lab-card p { margin: 0 0 12px; color: var(--fg-muted); }
.lab-card { margin: 14px 0 0; padding: 14px 16px 16px; background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-lg); }
.lab-card h2 { margin: 0 0 4px; font-size: var(--fs-title); font-weight: 600; color: var(--fg-strong); }
.lab-card strong { color: var(--fg-strong); font-weight: 600; }
.lab-choice { display: flex; align-items: center; gap: var(--space-4); margin-top: var(--space-3); }
.lab-label { flex: none; width: 58px; font-size: var(--fs-small); color: var(--fg-muted); }
.lab-seg { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.lab-seg button {
  padding: 3px 11px; border: 1px solid var(--border-strong); border-radius: var(--radius-pill);
  background: none; color: var(--fg); font: inherit; font-size: var(--fs-small); cursor: pointer;
}
.lab-seg button:hover { background: var(--hover); }
.lab-seg button[aria-pressed=true] { color: var(--accent); background: var(--accent-soft); border-color: color-mix(in srgb, var(--accent) 55%, var(--border-strong)); }
.lab-sheet { display: grid; gap: var(--space-2); }
.lab-row {
  display: flex; align-items: center; gap: var(--space-5); width: 100%; padding: 4px 14px 8px;
  border: 1px solid var(--border); border-radius: var(--radius-md); background: none;
  color: var(--accent); font: inherit; text-align: left; cursor: pointer;
}
.lab-row:hover { border-color: var(--border-strong); }
.lab-row[aria-pressed=true] { background: var(--accent-soft); border-color: color-mix(in srgb, var(--accent) 55%, var(--border-strong)); }
.lab-row-name { flex: none; width: 150px; color: var(--fg-strong); }
.lab-row-name small { display: block; font-size: var(--fs-label); color: var(--fg-muted); }
.lab-row-sizes { display: flex; align-items: flex-end; gap: 22px; padding: 12px 0 2px; }
.lab-note { margin: 10px 0 0 !important; font-size: var(--fs-small); }
@media (max-width: 860px) {
  .lab-shell { display: block; height: auto; }
  .lab-panel { height: 640px; border-right: none; border-bottom: 1px solid var(--border); }
  #lab { height: auto; overflow: visible; }
}`;

const html = `<!DOCTYPE html>
<html lang="pt-BR">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Code-AI · avatar</title>
    <style>${THEME}</style>
    <style>${icons}</style>
    <style>${LAB}</style>
  </head>
  <body class="vscode-dark">
    <div class="lab-shell">
      <div class="lab-panel"><div id="root"></div></div>
      <div id="lab"></div>
    </div>
    <script>
      // The host the panel talks to: nobody answers, the page plays it instead.
      window.acquireVsCodeApi = () => ({ postMessage() {}, getState() {}, setState() {} });
    </script>
    <script>${script}</script>
  </body>
</html>
`;

mkdirSync(join(root, "dist"), { recursive: true });
writeFileSync(out, html, "utf8");
console.log(`${out} (${Math.round(html.length / 1024)} KB)`);
