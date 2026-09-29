import assert from "node:assert/strict";
import test from "node:test";

import { applyEdits, diffTitle, editsOf, proposedContent, writesToProject } from "../edit-plan";

const FILE = "def load():\n    timeout = raw['timeout']\n    return timeout\n";

test("an edit_code call's proposal is the file with the edit applied", () => {
  const next = proposedContent(
    "edit_code",
    { path: "a.py", old_text: "raw['timeout']", new_text: "float(raw['timeout'])" },
    FILE
  );
  assert.equal(next, "def load():\n    timeout = float(raw['timeout'])\n    return timeout\n");
});

test("a write_file call's proposal is its content", () => {
  assert.equal(proposedContent("write_file", { content: "x = 1\n" }, FILE), "x = 1\n");
  assert.equal(proposedContent("write_file", {}, FILE), null);
  assert.equal(proposedContent("read_file", { path: "a.py" }, FILE), null);
});

test("an edit the tool would refuse has no proposal", () => {
  // Not there.
  assert.equal(applyEdits(FILE, [{ old: "nope", new: "x", expected: 1 }]), null);
  // There twice, expected once.
  assert.equal(applyEdits("a a", [{ old: "a", new: "b", expected: 1 }]), null);
  // Overlapping.
  assert.equal(
    applyEdits("abcdef", [
      { old: "abc", new: "x", expected: 1 },
      { old: "cde", new: "y", expected: 1 },
    ]),
    null
  );
  assert.equal(editsOf({ old_text: "", new_text: "x" }), null);
});

test("several edits apply in file order, as the tool applies them", () => {
  const edits = editsOf({
    edits: [
      { old: "return timeout", new: "return timeout or 30" },
      { old: "def load():", new: "def load(raw):" },
    ],
  });
  assert.ok(edits);
  assert.equal(
    applyEdits(FILE, edits),
    "def load(raw):\n    timeout = raw['timeout']\n    return timeout or 30\n"
  );
  assert.equal(applyEdits("a a", [{ old: "a", new: "b", expected: 2 }]), "b b");
});

test("sandbox writes stay out of the editor, and titles name the file", () => {
  assert.equal(writesToProject({ location: "sandbox" }), false);
  assert.equal(writesToProject({ location: "project" }), true);
  assert.equal(writesToProject({}), true);
  assert.equal(diffTitle("src/app/config.py", "proposed"), "config.py (Code-AI, proposed)");
  assert.equal(diffTitle("src\\config.py", "written"), "config.py (Code-AI)");
});
