import assert from "node:assert/strict";
import test from "node:test";

import { AVATAR_COLOR_VALUE, AVATAR_COLORS, easterSunday, seasonalSkin } from "../avatar-skin";
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

test("halloween is the week that ends on the 31st", () => {
  assert.equal(on(2026, 10, 24), "default");
  assert.equal(on(2026, 10, 25), "halloween");
  assert.equal(on(2026, 10, 31), "halloween");
  assert.equal(on(2026, 11, 1), "default");
});

test("christmas is december up to the day", () => {
  assert.equal(on(2026, 11, 30), "default");
  assert.equal(on(2026, 12, 1), "christmas");
  assert.equal(on(2026, 12, 25), "christmas");
  assert.equal(on(2026, 12, 26), "default");
});

test("easter is holy week, whichever month it lands in", () => {
  assert.equal(on(2026, 3, 28), "default");
  assert.equal(on(2026, 3, 29), "easter"); // Palm Sunday
  assert.equal(on(2026, 4, 5), "easter");
  assert.equal(on(2026, 4, 6), "default");
  assert.equal(on(2027, 3, 21), "easter");
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