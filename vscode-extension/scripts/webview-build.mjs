// What every bundle of the webview needs beyond esbuild's defaults, shared by
// the build, the unit tests and the e2e harness so the three cannot drift.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));

/**
 * `import css from "katex-style"`: KaTeX's stylesheet as a string, its fonts
 * inlined. Formulas are unreadable without those fonts, and inside the bundle
 * they need no file of their own in the package, the IDE plugin or the page.
 * Only woff2 is kept: every Chromium a webview runs on reads it.
 */
export const katexStyle = {
  name: "katex-style",
  setup(build) {
    build.onResolve({ filter: /^katex-style$/ }, () => ({ path: "katex-style", namespace: "katex-style" }));
    build.onLoad({ filter: /.*/, namespace: "katex-style" }, () => {
      const dist = join(root, "node_modules", "katex", "dist");
      const css = readFileSync(join(dist, "katex.min.css"), "utf8").replace(
        /src:url\((fonts\/[^)]+\.woff2)\) format\("woff2"\)[^;}]*/g,
        (_, file) =>
          `src:url(data:font/woff2;base64,${readFileSync(join(dist, file)).toString("base64")}) format("woff2")`
      );
      if (css.includes("url(fonts/")) throw new Error("katex-style: a font was left pointing at a file");
      return { contents: css, loader: "text" };
    });
  },
};

export const webviewPlugins = [katexStyle];
