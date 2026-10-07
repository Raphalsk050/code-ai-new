// What the glyph wears. The drawing underneath never changes: the same face,
// the same eyes in the same place, one flat fill. A skin swaps what sits on
// top of the head and, at most, two colours.

import * as React from "react";

export type AvatarSkin = "default" | "halloween" | "christmas" | "easter";
export const AVATAR_SKINS: readonly AvatarSkin[] = ["default", "halloween", "christmas", "easter"];

/** Easter Sunday of `year`, by the anonymous Gregorian algorithm. */
export function easterSunday(year: number): Date {
  const a = year % 19;
  const b = Math.floor(year / 100);
  const c = year % 100;
  const d = Math.floor(b / 4);
  const e = b % 4;
  const g = Math.floor((8 * b + 13) / 25);
  const h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4);
  const k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7;
  const m = Math.floor((a + 11 * h + 19 * l) / 433);
  const month = Math.floor((h + l - 7 * m + 90) / 25);
  const day = (h + l - 7 * m + 33 * month + 19) % 32;
  return new Date(year, month - 1, day);
}

/**
 * The skin a date calls for: the week up to Halloween, December up to
 * Christmas day, Holy Week. Short on purpose - a costume worn for a month
 * stops being one.
 */
export function seasonalSkin(date: Date): AvatarSkin {
  const month = date.getMonth() + 1;
  const day = date.getDate();
  if (month === 10 && day >= 25) return "halloween";
  if (month === 12 && day <= 25) return "christmas";
  const easter = easterSunday(date.getFullYear());
  const today = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const days = Math.round((easter.getTime() - today) / 86_400_000);
  if (days >= 0 && days <= 7) return "easter";
  return "default";
}

export type AvatarColor = "theme" | "blue" | "green" | "purple" | "pink" | "orange" | "red" | "teal" | "yellow" | "grey";
/**
 * What each colour is. The theme's accent comes first and is the default; the
 * rest are fixed mid-tones, so each reads on a dark panel and on a light one.
 */
export const AVATAR_COLOR_VALUE: Record<AvatarColor, string> = {
  theme: "var(--accent)",
  blue: "#3b82f6",
  green: "#2ea043",
  purple: "#a371f7",
  pink: "#e85aad",
  orange: "#f0883e",
  red: "#f85149",
  teal: "#2bb3a3",
  yellow: "#d4a72c",
  grey: "var(--fg-muted)",
};
export const AVATAR_COLORS = Object.keys(AVATAR_COLOR_VALUE) as AvatarColor[];

let worn: AvatarSkin = "default";
let tint: AvatarColor = "theme";
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

/** Dress every avatar on the page at once. */
export function setAvatarSkin(skin: AvatarSkin): void {
  if (skin === worn) return;
  worn = skin;
  listeners.forEach((listener) => listener());
}

/** What the page's avatars wear, unless one is told otherwise. */
export function useAvatarSkin(): AvatarSkin {
  return React.useSyncExternalStore(subscribe, () => worn);
}

/** Colour every avatar on the page at once. */
export function setAvatarColor(color: AvatarColor): void {
  if (color === tint) return;
  tint = color;
  listeners.forEach((listener) => listener());
}

export function useAvatarColor(): AvatarColor {
  return React.useSyncExternalStore(subscribe, () => tint);
}
