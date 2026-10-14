import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { TranscriptStore } from "../transcript-store";

const fresh = () => {
  const dir = mkdtempSync(join(tmpdir(), "code-ai-transcripts-"));
  return { dir, store: new TranscriptStore(dir), done: () => rmSync(dir, { recursive: true, force: true }) };
};
const onDisk = (dir: string, id: string) => JSON.parse(readFileSync(join(dir, `${encodeURIComponent(id)}.json`), "utf8"));

test("a save replaces what follows where the change starts, and reaches the disk", async () => {
  const { dir, store, done } = fresh();
  try {
    assert.equal(await store.save("c-1", 0, ["a", "b", "c"]), true);
    assert.equal(await store.save("c-1", 2, ["c2", "d"]), true);
    assert.deepEqual(await store.load("c-1"), ["a", "b", "c2", "d"]);
    await store.flush();
    assert.deepEqual(onDisk(dir, "c-1"), ["a", "b", "c2", "d"]);
    // A host started again reads it back from there.
    assert.deepEqual(await new TranscriptStore(dir).load("c-1"), ["a", "b", "c2", "d"]);
  } finally {
    done();
  }
});

test("saves that arrive together are applied in the order they came", async () => {
  const { dir, store, done } = fresh();
  try {
    await store.save("c-1", 0, ["a"]);
    await store.flush();
    const later = new TranscriptStore(dir);
    await Promise.all([later.save("c-1", 1, ["b"]), later.save("c-1", 2, ["c"]), later.save("c-1", 3, ["d"])]);
    assert.deepEqual(await later.load("c-1"), ["a", "b", "c", "d"]);
  } finally {
    done();
  }
});

test("a save past what the host has asks for everything", async () => {
  const { store, done } = fresh();
  try {
    await store.save("c-1", 0, ["a"]);
    assert.equal(await store.save("c-1", 5, ["f"]), false);
    assert.deepEqual(await store.load("c-1"), ["a"]);
    assert.equal(await store.save("c-2", 3, ["x"]), false);
  } finally {
    done();
  }
});

test("what an older panel kept is taken over once, never over what is already kept", async () => {
  const { store, done } = fresh();
  try {
    await store.save("c-1", 0, ["newer"]);
    await store.adopt([
      { id: "c-1", items: ["older"] },
      { id: "c-2", items: ["only here"] },
      { id: "c-3", items: [] },
    ]);
    assert.deepEqual(await store.load("c-1"), ["newer"]);
    assert.deepEqual(await store.load("c-2"), ["only here"]);
    assert.equal(await store.load("c-3"), null);
  } finally {
    done();
  }
});

test("a removed conversation is gone from memory and disk", async () => {
  const { dir, store, done } = fresh();
  try {
    await store.save("c-1", 0, ["a"]);
    await store.flush();
    await store.remove("c-1");
    assert.equal(await store.load("c-1"), null);
    assert.equal(await new TranscriptStore(dir).load("c-1"), null);
  } finally {
    done();
  }
});
