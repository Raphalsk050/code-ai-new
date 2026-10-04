import assert from "node:assert/strict";
import test from "node:test";

import { ChangeLedger, countLines } from "../change-ledger";

test("lines are counted against the file as it was first seen", async () => {
  const ledger = new ChangeLedger();
  const disk = new Map<string, string | null>([
    ["/w/a.cpp", "one\nTWO\nthree\nfour\n"],
    ["/w/new.cpp", "fresh\nfile\n"],
    ["/w/same.cpp", "kept\n"],
  ]);
  ledger.snapshot("/w/a.cpp", "a.cpp", "one\ntwo\nthree\n", true);
  // A second write's snapshot is the agent's own earlier edit: ignored.
  ledger.snapshot("/w/a.cpp", "a.cpp", "one\nTWO\nthree\n", true);
  ledger.snapshot("/w/new.cpp", "new.cpp", "", false);
  ledger.snapshot("/w/same.cpp", "same.cpp", "kept\n", true);
  ledger.touched("/w/big.bin", "big.bin");

  const list = await ledger.summarize(async (path) => disk.get(path) ?? null);
  assert.deepEqual(list, [
    { path: "a.cpp", added: 2, removed: 1, created: false, revertable: true },
    { path: "new.cpp", added: 2, removed: 0, created: true, revertable: true },
    { path: "big.bin", added: 0, removed: 0, created: false, revertable: false },
  ]);
  assert.equal(ledger.byRelative("new.cpp")?.existed, false);
});

test("a created file that is gone again, or a file put back, drops out", async () => {
  const ledger = new ChangeLedger();
  ledger.snapshot("/w/tmp.txt", "tmp.txt", "", false);
  ledger.snapshot("/w/b.txt", "b.txt", "x\n", true);
  const list = await ledger.summarize(async (path) => (path === "/w/b.txt" ? "x\n" : null));
  assert.deepEqual(list, []);
  assert.deepEqual(countLines("a\nb\n", "a\nc\nd\n"), { added: 2, removed: 1 });
});
