import assert from "node:assert/strict";
import test from "node:test";

import * as React from "react";

import {
  AVATAR_COLOR_VALUE,
  AVATAR_COLORS,
  AVATAR_GLASSES,
  easterSunday,
  SEASON_LEAD_DAYS,
  seasonalSkin,
  setAvatarGlasses,
  useAvatarGlasses,
} from "../avatar-skin";
import { DEFAULT_PREFS } from "../history";
import { AVATAR_STYLE } from "../styles/avatar";

const on = (year: number, month: number, day: number) => seasonalSkin(new Date(year, month - 1, day, 15, 30));
const ymd = (date: Date) => [date.getFullYear(), date.getMonth() + 1, date.getDate()];

test("easter falls where the calendar says", () => {
  assert.deepEqual(ymd(easterSunday(2024)), [2024, 3, 31]);
  assert.deepEqual(ymd(easterSunday(2025)), [2025, 4, 20]);
  assert.deepEqual(ymd(easterSunday(2026)), [2026, 4, 5]);
  assert.deepEqual(ymd(easterSunday(2027)), [2027, 3, 28]);
  assert.deepEqual(ymd(easterSunday(2038)), [2038, 4, 25]);
});

test("the lead is fifteen days", () => {
  assert.equal(SEASON_LEAD_DAYS, 15);
});

test("halloween goes on fifteen days ahead of the 31st", () => {
  assert.equal(on(2026, 10, 15), "default");
  assert.equal(on(2026, 10, 16), "halloween");
  assert.equal(on(2026, 10, 31), "halloween");
  assert.equal(on(2026, 11, 1), "default");
});

test("christmas is december up to the day, more than the lead", () => {
  assert.equal(on(2026, 11, 30), "default");
  assert.equal(on(2026, 12, 1), "christmas");
  assert.equal(on(2026, 12, 25), "christmas");
  assert.equal(on(2026, 12, 26), "default");
});

test("easter goes on fifteen days ahead, whichever month it lands in", () => {
  assert.equal(on(2026, 3, 20), "default");
  assert.equal(on(2026, 3, 21), "easter");
  assert.equal(on(2026, 4, 5), "easter");
  assert.equal(on(2026, 4, 6), "default");
  assert.equal(on(2027, 3, 12), "default");
  assert.equal(on(2027, 3, 13), "easter");
  assert.equal(on(2027, 3, 28), "easter");
  assert.equal(on(2027, 3, 29), "default");
});

test("the rest of the year it wears nothing", () => {
  for (const [month, day] of [[1, 1], [2, 14], [6, 12], [9, 7], [10, 6]]) {
    assert.equal(on(2026, month, day), "default", `${month}/${day}`);
  }
});

test("every colour but the theme's own has a rule that draws the glyph in it", () => {
  assert.equal(AVATAR_COLORS[0], "theme");
  for (const color of AVATAR_COLORS) {
    const rule = `.agent-av[data-color=${color}] { color: ${AVATAR_COLOR_VALUE[color]}; }`;
    assert.equal(AVATAR_STYLE.includes(rule), color !== "theme", color);
  }
});

test("glasses default to none and list every style, none first", () => {
  assert.deepEqual([...AVATAR_GLASSES], ["none", "round", "square", "sunglasses"]);
  assert.equal(DEFAULT_PREFS.avatarGlasses, "none");
  // The hook outside a render: a dispatcher that answers useSyncExternalStore with the store's current value.
  const internals = (React as unknown as {
    __SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED: { ReactCurrentDispatcher: { current: unknown } };
  }).__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED.ReactCurrentDispatcher;
  const read = () => {
    const before = internals.current;
    internals.current = { useSyncExternalStore: (_subscribe: unknown, snapshot: () => unknown) => snapshot() };
    try {
      return useAvatarGlasses();
    } finally {
      internals.current = before;
    }
  };
  assert.equal(read(), "none");
  setAvatarGlasses("sunglasses");
  assert.equal(read(), "sunglasses");
  setAvatarGlasses("none");
  assert.equal(read(), "none");
});
