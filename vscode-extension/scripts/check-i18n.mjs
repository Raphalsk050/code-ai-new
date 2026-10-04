#!/usr/bin/env node
// Fails when a webview component shows text that does not go through t()/tf():
// JSX text, string children and user-facing attributes (title, placeholder,
// aria-label, alt, label). A panel that mixes languages is the bug this guards.

import { readFileSync } from "node:fs";
import { glob } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

import ts from "typescript";

const root = fileURLToPath(new URL("..", import.meta.url));
const ATTRS = new Set(["title", "placeholder", "aria-label", "alt", "label"]);
// Text that reads the same in every language: product name, key names, units, symbols.
const NEUTRAL = /^(\((Ctrl|Cmd|Alt|Shift)\+[^)]*\)|Code-AI|MCP|JSON|URL|API|PTY|Esc|Enter|Tab|Ctrl\+[A-Z]|Alt\+[A-Za-z]+|Shift\+[A-Za-z]+|[A-Z]|[$#@/·•×+−\-–:;,.()[\]|%\s\d]+|stdio|http|sse|temperature|top_p|top_k|presence_penalty)$/;
const hasWords = (s) => /[A-Za-zÀ-ÿ]{2,}/.test(s);

const problems = [];

function report(file, node, text) {
  const sf = node.getSourceFile();
  const { line, character } = sf.getLineAndCharacterOfPosition(node.getStart());
  problems.push(`${file}:${line + 1}:${character + 1}  ${JSON.stringify(text.trim())}`);
}

function visit(file, node) {
  if (ts.isJsxText(node)) {
    const text = node.getText().replace(/\s+/g, " ").trim();
    if (text && hasWords(text) && !NEUTRAL.test(text)) report(file, node, text);
  } else if (ts.isJsxExpression(node) && node.expression && (ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent))) {
    // A child expression: every string it can evaluate to, outside a call (t(), tf(), format helpers).
    const scan = (n) => {
      if (ts.isCallExpression(n) || ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n) || ts.isArrowFunction(n)) return;
      if (ts.isElementAccessExpression(n)) return;
      if (ts.isBinaryExpression(n) && [ts.SyntaxKind.EqualsEqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken, ts.SyntaxKind.EqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsToken].includes(n.operatorToken.kind)) return;
      if (ts.isStringLiteralLike(n)) {
        if (hasWords(n.text) && !NEUTRAL.test(n.text)) report(file, n, n.text);
        return;
      }
      if (ts.isTemplateExpression(n)) {
        const parts = [n.head.text, ...n.templateSpans.map((s) => s.literal.text)].join(" ");
        if (hasWords(parts) && !NEUTRAL.test(parts.trim())) report(file, n, parts);
        return;
      }
      ts.forEachChild(n, scan);
    };
    scan(node.expression);
  } else if (ts.isJsxAttribute(node)) {
    const name = node.name.getText();
    const init = node.initializer;
    if (ATTRS.has(name) && init) {
      const expr = ts.isJsxExpression(init) ? init.expression : init;
      const lit = expr && ts.isStringLiteralLike(expr)
        ? expr
        : expr && ts.isTemplateExpression(expr)
          ? { text: [expr.head.text, ...expr.templateSpans.map((s) => s.literal.text)].join(" ") }
          : null;
      // A placeholder that is an example value (a command, URL, JSON, env line) is code, not prose.
      const example = name === "placeholder" && lit && (/[${}=/@"]/.test(lit.text) || /^[a-z0-9_-]+$/.test(lit.text));
      if (lit && !example && hasWords(lit.text) && !NEUTRAL.test(lit.text.trim())) report(file, node, lit.text);
    }
  }
  ts.forEachChild(node, (child) => visit(file, child));
}

for await (const file of glob("webview/**/*.tsx", { cwd: root })) {
  if (file.includes("__tests__")) continue;
  const source = readFileSync(join(root, file), "utf8");
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  visit(file, sf);
}

if (problems.length) {
  console.error(`${problems.length} user-facing string(s) outside t():`);
  for (const p of problems) console.error(`  ${p}`);
  process.exit(1);
}
console.log("i18n: every user-facing string goes through t()");
