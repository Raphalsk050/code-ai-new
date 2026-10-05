import assert from "node:assert/strict";
import test from "node:test";

import { formatEditorContext, MAX_OPEN_FILES_NAMED, otherOpenFiles } from "../editor-context";

test("the active file is named, with the selection when there is one", () => {
  assert.equal(formatEditorContext(null), "");
  assert.equal(
    formatEditorContext({ path: "src/app.ts", language: "typescript" }),
    "[Editor context] Active file: src/app.ts (typescript)\nThe user currently has this file open (no selection)."
  );
  assert.equal(
    formatEditorContext({
      path: "src/app.ts",
      language: "typescript",
      selection: "const x = 1;",
      startLine: 3,
      endLine: 3,
    }),
    "[Editor context] Active file: src/app.ts (typescript)\nUser has selected lines 3-3:\n```typescript\nconst x = 1;\n```"
  );
});

test("the other open files follow, so the model knows what else is on screen", () => {
  assert.equal(
    formatEditorContext({ path: "src/app.ts", language: "typescript", openFiles: ["src/api.ts", "README.md"] }),
    "[Editor context] Active file: src/app.ts (typescript)\n" +
      "The user currently has this file open (no selection).\n" +
      "Other files open in the editor:\n- src/api.ts\n- README.md"
  );
  // An empty list says nothing at all: the text is the one from before the list existed.
  assert.equal(
    formatEditorContext({ path: "a.py", language: "python", openFiles: [] }),
    formatEditorContext({ path: "a.py", language: "python" })
  );
});

test("open files are context even when none of them has the focus", () => {
  assert.equal(
    formatEditorContext({ path: "", language: "", openFiles: ["graph.flow.json", "src/app.ts"] }),
    "[Editor context] Files open in the editor (none of them has the focus):\n- graph.flow.json\n- src/app.ts"
  );
  assert.equal(formatEditorContext({ path: "", language: "" }), "");
});

test("a long list is cut short and says how much it left out", () => {
  const openFiles = Array.from({ length: MAX_OPEN_FILES_NAMED + 12 }, (_, i) => `src/f${i}.ts`);
  const lines = formatEditorContext({ path: "a.ts", language: "typescript", openFiles }).split("\n");
  assert.equal(lines.length, 3 + MAX_OPEN_FILES_NAMED + 1);
  assert.equal(lines[3], "- src/f0.ts");
  assert.equal(lines.at(-1), "- and 12 more");
});

test("the list is in tab order, each file once, without the active one", () => {
  assert.deepEqual(
    otherOpenFiles(["src\\app.ts", "src/api.ts", "README.md", "src/api.ts", ""], "src/app.ts"),
    ["src/api.ts", "README.md"]
  );
  assert.deepEqual(otherOpenFiles(["a.ts", "b.ts"]), ["a.ts", "b.ts"]);
});
