import assert from "node:assert/strict";
import test from "node:test";

import { renderBlocks } from "../markdown-render";

const html = (text: string, streaming = false) =>
  renderBlocks(text, streaming, new Map())
    .map((block) => block.html)
    .join("");

test("a fragment only changes the last block", () => {
  const cache = new Map<string, string>();
  const calls: string[] = [];
  const sanitize = (out: string) => (calls.push(out), out);
  const before = "# Title\n\n```js\nconst a = 1;\n```\n\nFirst para";

  const first = renderBlocks(before, true, cache, sanitize);
  assert.equal(first.length, 3);
  calls.length = 0;
  const second = renderBlocks(`${before}graph grows`, true, cache, sanitize);

  assert.equal(calls.length, 1, "only the paragraph was rendered again");
  assert.equal(second[0].html, first[0].html);
  assert.equal(second[1].html, first[1].html);
  assert.deepEqual(second.map((block) => block.key), [0, 1, 2]);
  assert.match(second[2].html, /paragraph grows/);
});

test("the cache holds the blocks of the current text and no more", () => {
  const cache = new Map<string, string>();
  renderBlocks("one\n\ntwo\n\nthree", false, cache);
  assert.equal(cache.size, 3);
  renderBlocks("one", false, cache);
  assert.equal(cache.size, 1);
});

test("a code block is highlighted and numbered in the html itself", () => {
  const out = html("```python\ndef f():\n    return 1\n```");
  assert.match(out, /<pre class="has-line-numbers"><span class="code-gutter" aria-hidden="true">1\n2<\/span>/);
  assert.match(out, /<code class="hljs language-python">/);
  assert.match(out, /hljs-keyword/);
});

test("a block still being written keeps its colours: no guessing its language", () => {
  const open = "```\nSELECT * FROM t WHERE";
  assert.doesNotMatch(html(open, true), /hljs-/);
  // Closed and settled, it may be guessed once.
  assert.match(html(`${open} a = 1;\n\`\`\``), /hljs-keyword/);
});

test("code content cannot leave its element", () => {
  const out = html("```html\n</code></pre><img src=x onerror=alert(1)>\n```");
  assert.doesNotMatch(out, /<img/);
});

test("formulas are typeset in every delimiter a model uses", () => {
  for (const text of ["$$\nE = mc^2\n$$", "\\[\nE = mc^2\n\\]", "text\n$$E = mc^2$$"]) {
    const out = html(text);
    assert.match(out, /class="math-display"/, text);
    assert.match(out, /class="katex-display"/, text);
  }
  for (const text of ["so $a^2 + b^2 = c^2$ holds", "so \\(a^2 + b^2\\) holds"]) {
    const out = html(text);
    assert.match(out, /<span class="math-inline"><span class="katex">/, text);
    assert.doesNotMatch(out, /katex-display/, text);
  }
});

test("money, shell variables and code are not formulas", () => {
  for (const text of [
    "It costs $5 and $10 more.",
    "Between R$5 e R$10.",
    "Set $HOME and $PATH first.",
    "Run `echo $a + $b` then.",
    "```sh\necho $a$b\n```",
    "An escaped \\$x\\$ stays.",
  ]) {
    assert.doesNotMatch(html(text), /katex/, text);
  }
});

test("a formula that is not LaTeX shows its source, not an error", () => {
  const out = html("$$\n\\frac{a}{\n$$");
  assert.doesNotMatch(out, /katex/);
  assert.match(out, /<code class="math-source">\\frac\{a\}\{<\/code>/);
});

test("half a formula is shown as it is written, only while it streams", () => {
  const half = "Result:\n\n$$\n\\int_0^1 x^2";
  const live = html(half, true);
  assert.match(live, /math-pending/);
  assert.match(live, /\\int_0\^1 x\^2/);
  assert.doesNotMatch(live, /katex/);
  // Settled without closing, it is just text.
  assert.doesNotMatch(html(half), /math-pending/);
  assert.match(html(`${half}\\,dx\n$$`, true), /katex-display/);
});

test("a formula never swallows the paragraph after a blank line", () => {
  const out = html("$$ unclosed\n\nnext paragraph $$ again");
  assert.doesNotMatch(out, /katex/);
  assert.match(out, /next paragraph/);
});

test("a reference link is resolved though blocks render one by one", () => {
  assert.match(html("See [the docs][1].\n\n[1]: https://example.com/docs"), /href="https:\/\/example\.com\/docs"/);
});
