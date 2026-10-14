import assert from "node:assert/strict";
import test from "node:test";

import { DEFAULT_PREFS, firstChange, mergeHistory, type PersistedState, takeLegacy, upsertActive } from "../history";
import type { Item } from "../store/transcript";

const user = (id: string, text: string): Item => ({ kind: "user", id, text });

test("the first change is the first item that is not the same object", () => {
  const a = user("u1", "fix it");
  const b = user("u2", "and test it");
  assert.equal(firstChange([a, b], [a, b]), 2);
  assert.equal(firstChange([a, b], [a, { ...b }]), 1);
  assert.equal(firstChange([a, b], [a, b, user("u3", "more")]), 2);
  assert.equal(firstChange([a, b], [a]), 1);
  assert.equal(firstChange([], [a]), 0);
});

test("the list keeps a conversation's size, not its items", () => {
  const state: PersistedState = { conversations: [], activeId: null, prefs: DEFAULT_PREFS };
  const next = upsertActive(state, "c-1", [user("u1", "fix the parser"), user("u2", "now")]);
  assert.deepEqual(Object.keys(next.conversations[0]).sort(), ["count", "id", "title", "updatedAt"]);
  assert.equal(next.conversations[0].count, 2);
  assert.equal(mergeHistory(next.conversations, [])[0].count, 2);
});

test("state an older panel saved hands its items over and keeps the list", () => {
  const items = [user("u1", "fix the parser")];
  const old = {
    conversations: [
      { id: "c-1", title: "fix the parser", updatedAt: 5, items },
      { id: "c-2", title: "empty", updatedAt: 3, items: [] },
    ],
    activeId: "c-1",
    prefs: DEFAULT_PREFS,
  } as unknown as PersistedState;
  const { state, handOver } = takeLegacy(old);
  assert.deepEqual(handOver, [{ id: "c-1", items }]);
  assert.deepEqual(state.conversations, [
    { id: "c-1", title: "fix the parser", updatedAt: 5, count: 1 },
    { id: "c-2", title: "empty", updatedAt: 3, count: 0 },
  ]);
  assert.equal(state.activeId, "c-1");
  // Already moved: nothing to do.
  assert.deepEqual(takeLegacy(state), { state, handOver: [] });
});
