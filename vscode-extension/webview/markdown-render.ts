// Markdown to HTML, one top-level block at a time.
//
// An answer arrives in fragments, and rendering it whole on each one rebuilt
// every code block and table already on screen: they lost their highlighting
// for a frame, images reloaded, a selection was dropped. Split into blocks, a
// fragment only ever changes the last one; the rest keep the HTML they had.
//
// No DOM in here, so it runs under node's test runner. Sanitizing is the
// caller's: it needs a document.

import hljs from "highlight.js/lib/common";
import katex from "katex";
import { Marked, type Token, type TokenizerAndRendererExtension } from "marked";

export interface RenderedBlock {
  /** Position among the blocks: stable while the text only grows. */
  key: number;
  html: string;
}

/** A formula never holds a blank line: one ends the paragraph it is in. */
const TEX = String.raw`((?:(?!\n[ \t]*\n)[\s\S])+?)`;
const BLOCK_DOLLARS = new RegExp(String.raw`^ {0,3}\$\$${TEX}\$\$[ \t]*(?:\n|$)`);
const BLOCK_BRACKETS = new RegExp(String.raw`^ {0,3}\\\[${TEX}\\\][ \t]*(?:\n|$)`);
/** Opened and still being written: everything to the end, with no blank line. */
const BLOCK_OPEN = /^ {0,3}(?:\$\$|\\\[)((?:(?!\n[ \t]*\n)[\s\S])*)$/;
const INLINE_DOLLARS = new RegExp(String.raw`^\$\$${TEX}\$\$`);
const INLINE_BRACKETS = new RegExp(String.raw`^\\\[${TEX}\\\]`);
const INLINE_PARENS = new RegExp(String.raw`^\\\(${TEX}\\\)`);
// Pandoc's rule, which keeps "$5 and $10" as money: no space inside either
// dollar, and no digit after the closing one.
const INLINE_DOLLAR = /^\$(?![\s$])((?:\\[\s\S]|[^\\$\n])+?)(?<![\s\\])\$(?!\d)/;

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** The formula typeset, or its source when it is not valid LaTeX. */
function typeset(tex: string, display: boolean): string {
  try {
    return katex.renderToString(tex, { displayMode: display, throwOnError: true, output: "html", strict: "ignore" });
  } catch {
    return `<code class="math-source">${escapeHtml(tex)}</code>`;
  }
}

function blockMath(live: boolean): TokenizerAndRendererExtension {
  return {
    name: "blockMath",
    level: "block",
    start: (src) => src.match(/\n {0,3}(?:\$\$|\\\[)/)?.index,
    tokenizer(src) {
      const closed = BLOCK_DOLLARS.exec(src) ?? BLOCK_BRACKETS.exec(src);
      if (closed) return { type: "blockMath", raw: closed[0], tex: closed[1].trim(), pending: false };
      // Half a formula is not LaTeX. While it is being written its source is
      // shown as it stands, instead of an error that changes with every token.
      const open = live ? BLOCK_OPEN.exec(src) : null;
      if (open) return { type: "blockMath", raw: open[0], tex: open[1].trim(), pending: true };
      return undefined;
    },
    renderer: (token) =>
      token.pending
        ? `<div class="math-display math-pending"><code class="math-source">${escapeHtml(token.tex)}</code></div>\n`
        : `<div class="math-display">${typeset(token.tex, true)}</div>\n`,
  };
}

const inlineMath: TokenizerAndRendererExtension = {
  name: "inlineMath",
  level: "inline",
  start: (src) => src.match(/\$|\\[([]/)?.index,
  tokenizer(src) {
    const display = INLINE_DOLLARS.exec(src) ?? INLINE_BRACKETS.exec(src);
    if (display) return { type: "inlineMath", raw: display[0], tex: display[1].trim(), display: true };
    const inline = INLINE_PARENS.exec(src) ?? INLINE_DOLLAR.exec(src);
    if (inline) return { type: "inlineMath", raw: inline[0], tex: inline[1].trim(), display: false };
    return undefined;
  },
  renderer: (token) => `<span class="math-inline">${typeset(token.tex, token.display)}</span>`,
};

/** Set around the one block still being written; see `code` below. */
let writing = false;

function code(text: string, info: string | undefined): string {
  const lang = (info ?? "").trim().split(/\s+/)[0];
  let body: string;
  if (lang && hljs.getLanguage(lang)) {
    body = hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
  } else if (writing) {
    // Guessing the language of half a block changes its colours as it grows.
    body = escapeHtml(text);
  } else {
    body = hljs.highlightAuto(text).value;
  }
  // Numbers and highlighting are in the HTML from the first paint: added after
  // it, the block changed size and colour under the reader on every fragment.
  const numbers = text.replace(/\n$/, "").split("\n").map((_, i) => i + 1).join("\n");
  const cls = lang ? `hljs language-${escapeHtml(lang)}` : "hljs";
  return (
    `<pre class="has-line-numbers"><span class="code-gutter" aria-hidden="true">${numbers}</span>` +
    `<code class="${cls}">${body}</code></pre>\n`
  );
}

function parserFor(live: boolean): Marked {
  return new Marked({ gfm: true, breaks: true }, { extensions: [blockMath(live), inlineMath], renderer: { code } });
}

const settled = parserFor(false);
const streamed = parserFor(true);

/**
 * `text` as HTML blocks, reusing from `cache` every block whose source did not
 * change. `sanitize` runs once per new block. `cache` is rewritten to hold
 * exactly the blocks of this text, so it never outgrows the message.
 */
export function renderBlocks(
  text: string,
  streaming: boolean,
  cache: Map<string, string>,
  sanitize: (html: string) => string = (html) => html
): RenderedBlock[] {
  const md = streaming ? streamed : settled;
  const tokens = md.lexer(text);
  // A reference link reads differently once its definition arrives.
  const links = Object.keys(tokens.links).length;
  const shown = tokens.filter((token) => token.type !== "space");
  const next = new Map<string, string>();
  const blocks = shown.map((token: Token, key) => {
    const last = streaming && key === shown.length - 1;
    const id = `${last ? "w" : "s"}${links}\u0000${token.type}\u0000${token.raw}`;
    let html = cache.get(id);
    if (html === undefined) {
      writing = last;
      try {
        html = sanitize(md.parser(Object.assign([token], { links: tokens.links })));
      } finally {
        writing = false;
      }
    }
    next.set(id, html);
    return { key, html };
  });
  cache.clear();
  next.forEach((html, id) => cache.set(id, html));
  return blocks;
}
