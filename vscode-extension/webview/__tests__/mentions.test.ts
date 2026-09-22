import assert from "node:assert/strict";
import test from "node:test";

import { applyMention, mentionPrefix, rankPaths } from "../mentions";

test("an @ at the caret opens the picker, even when empty", () => {
  assert.equal(mentionPrefix("@", 1), "");
  assert.equal(mentionPrefix("olha o @men", 11), "men");
  assert.equal(mentionPrefix("after a space @src/a", 20), "src/a");
});

test("an @ that is not under the caret is left alone", () => {
  assert.equal(mentionPrefix("mail@example.com", 16), null);
  assert.equal(mentionPrefix("@file already mentioned", 23), null);
  assert.equal(mentionPrefix("", 0), null);
});

test("picking a file replaces the partial and leaves the caret after it", () => {
  const out = applyMention("olha o @men", 11, "src/mentions.py");
  assert.equal(out.text, "olha o @src/mentions.py ");
  assert.equal(out.caret, out.text.length);
});

test("picking keeps whatever came after the caret", () => {
  const out = applyMention("veja @men e me diga", 9, "a/b.py");
  assert.equal(out.text, "veja @a/b.py  e me diga");
});

test("a name match outranks a path match, and shorter wins the tie", () => {
  const ranked = rankPaths(
    ["tests/unit/test_mentions.py", "src/code_ai/ui/terminal/mentions.py", "docs/mentions.md"],
    "mentions"
  );
  assert.equal(ranked[0], "docs/mentions.md");
  assert.equal(ranked[2], "tests/unit/test_mentions.py");
});

test("a path-only match still shows up, after the name matches", () => {
  const ranked = rankPaths(["src/ui/terminal/app.py", "src/app.py"], "terminal");
  assert.deepEqual(ranked, ["src/ui/terminal/app.py"]);
});

test("an empty prefix offers the head of the list", () => {
  assert.equal(rankPaths(["a", "b"], "").length, 2);
});
