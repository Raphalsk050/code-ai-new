import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CHAT_MIN_WIDTH,
  RAIL_DEFAULT_WIDTH,
  RAIL_MAX_WIDTH,
  RAIL_MIN_WIDTH,
  SECTION_MAX_HEIGHT,
  SECTION_MIN_HEIGHT,
  clampRailWidth,
  clampSectionHeight,
  readStoredRailWidth,
  readStoredSectionHeights,
  storeRailWidth,
  storeSectionHeights,
  widthFromDrag,
} from "../rail-layout";

/** A localStorage stand-in; `broken` throws the way a blocked one does. */
function fakeStorage(seed: Record<string, string> = {}, broken = false): Storage {
  const map = new Map(Object.entries(seed));
  const boom = () => {
    throw new DOMException("denied");
  };
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    key: (i: number) => [...map.keys()][i] ?? null,
    getItem: (k: string) => (broken ? boom() : (map.get(k) ?? null)),
    setItem: (k: string, v: string) => {
      if (broken) boom();
      map.set(k, v);
    },
    removeItem: (k: string) => void map.delete(k),
  } as Storage;
}

test("a width inside the allowed range is kept", () => {
  assert.equal(clampRailWidth(300, 1000), 300);
});

test("the rail never shrinks below what its labels need", () => {
  assert.equal(clampRailWidth(10, 1000), RAIL_MIN_WIDTH);
  assert.equal(clampRailWidth(-500, 1000), RAIL_MIN_WIDTH);
});

test("the rail never grows past its maximum", () => {
  assert.equal(clampRailWidth(9000, 4000), RAIL_MAX_WIDTH);
});

test("the conversation keeps its minimum before the rail gets the rest", () => {
  // 700px of panel: the rail may have everything past the chat's floor.
  assert.equal(clampRailWidth(9000, 700), 700 - CHAT_MIN_WIDTH);
});

test("in a panel too narrow for both, the rail stays readable", () => {
  // The user docked it on purpose; a 40px rail would help nobody.
  assert.equal(clampRailWidth(400, 300), RAIL_MIN_WIDTH);
});

test("dragging left widens the rail, dragging right narrows it", () => {
  // The rail is on the right edge, so the sign is the opposite of the pointer.
  assert.equal(widthFromDrag(236, -40, 1000), 276);
  assert.equal(widthFromDrag(236, 40, 1000), 196);
});

test("a drag is clamped like any other width", () => {
  assert.equal(widthFromDrag(236, -9000, 1000), RAIL_MAX_WIDTH);
  assert.equal(widthFromDrag(236, 9000, 1000), RAIL_MIN_WIDTH);
});

test("nonsense falls back to the default rather than collapsing the layout", () => {
  assert.equal(clampRailWidth(Number.NaN, 1000), RAIL_DEFAULT_WIDTH);
});

test("an unmeasured container does not pin the rail to its floor", () => {
  // Before the first layout pass the width reads 0; treating that as "no room"
  // would snap the rail narrow on every mount.
  assert.equal(clampRailWidth(300, 0), 300);
});

test("a stored width survives the round trip", () => {
  const storage = fakeStorage();
  storeRailWidth(321, storage);
  assert.equal(readStoredRailWidth(storage), 321);
});

test("nothing stored reads as nothing, not as zero", () => {
  assert.equal(readStoredRailWidth(fakeStorage()), null);
});

test("junk left by an older build is ignored", () => {
  assert.equal(readStoredRailWidth(fakeStorage({ "code-ai.railWidth": "wide" })), null);
});

test("storage that throws is survivable in both directions", () => {
  const broken = fakeStorage({}, true);
  assert.equal(readStoredRailWidth(broken), null);
  assert.doesNotThrow(() => storeRailWidth(300, broken));
});

test("a missing storage object is survivable too", () => {
  assert.equal(readStoredRailWidth(undefined), null);
  assert.doesNotThrow(() => storeRailWidth(300, undefined));
});

test("a section height stays between its floor and ceiling", () => {
  assert.equal(clampSectionHeight(300), 300);
  assert.equal(clampSectionHeight(10), SECTION_MIN_HEIGHT);
  assert.equal(clampSectionHeight(99999), SECTION_MAX_HEIGHT);
  assert.equal(clampSectionHeight(Number.NaN), SECTION_MIN_HEIGHT);
});

test("section heights survive the round trip, by id", () => {
  const storage = fakeStorage();
  storeSectionHeights({ terminal: 320, plan: 140 }, storage);
  assert.deepEqual(readStoredSectionHeights(storage), { terminal: 320, plan: 140 });
});

test("stored section heights are clamped on the way back in", () => {
  // A height written when the panel was huge must not come back and push the
  // other sections off the rail.
  const storage = fakeStorage({ "code-ai.railSectionHeights": '{"terminal":99999,"plan":1}' });
  assert.deepEqual(readStoredSectionHeights(storage), {
    terminal: SECTION_MAX_HEIGHT,
    plan: SECTION_MIN_HEIGHT,
  });
});

test("anything that is not a map of numbers reads as no heights at all", () => {
  for (const raw of ['["terminal"]', '"nope"', "{", '{"terminal":"tall"}', "null"]) {
    assert.deepEqual(readStoredSectionHeights(fakeStorage({ "code-ai.railSectionHeights": raw })), {});
  }
});

test("section heights survive broken and missing storage", () => {
  assert.deepEqual(readStoredSectionHeights(fakeStorage({}, true)), {});
  assert.deepEqual(readStoredSectionHeights(undefined), {});
  assert.doesNotThrow(() => storeSectionHeights({ plan: 100 }, fakeStorage({}, true)));
});
