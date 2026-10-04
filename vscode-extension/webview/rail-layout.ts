/**
 * How big the task rail and its sections are, and what the user is allowed to
 * drag them to.
 *
 * Kept apart from the components because the clamping is the part worth being
 * sure about: a splitter that lets the rail eat the conversation, or shrink a
 * section below its own heading, is worse than one that does not move at all.
 */

export const RAIL_DEFAULT_WIDTH = 288;
/** Below this the section headings wrap and the rail stops being readable. */
export const RAIL_MIN_WIDTH = 180;
export const RAIL_MAX_WIDTH = 560;
/** The conversation keeps at least this much, whatever the rail is dragged to. */
export const CHAT_MIN_WIDTH = 320;

const STORAGE_KEY = "code-ai.railWidth";

/**
 * The nearest allowed width to `desired`, given how much room there is.
 *
 * The ceiling is whichever is smaller: the hard maximum, or what leaves the
 * conversation its minimum. In a panel too narrow for both, the floor wins -
 * the user has already chosen to dock the rail, so it stays usable and the
 * conversation scrolls.
 */
export function clampRailWidth(desired: number, containerWidth: number): number {
  if (!Number.isFinite(desired)) return RAIL_DEFAULT_WIDTH;
  const room = Number.isFinite(containerWidth) && containerWidth > 0 ? containerWidth : Infinity;
  const ceiling = Math.max(RAIL_MIN_WIDTH, Math.min(RAIL_MAX_WIDTH, room - CHAT_MIN_WIDTH));
  return Math.round(Math.min(Math.max(desired, RAIL_MIN_WIDTH), ceiling));
}

/** Width for a pointer that has moved `dx` px from where the drag started. */
export function widthFromDrag(startWidth: number, dx: number, containerWidth: number): number {
  // The rail is on the right, so dragging left (negative dx) makes it wider.
  return clampRailWidth(startWidth - dx, containerWidth);
}

/**
 * Per-viewer convenience, so it lives in localStorage. It can be unavailable
 * (private window, blocked site data) and it can hold anything a previous
 * version wrote, so neither a throw nor a bad value may reach the layout.
 */
export function readStoredRailWidth(storage: Storage | undefined = safeStorage()): number | null {
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    if (!raw) return null;
    const value = Number.parseInt(raw, 10);
    return Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

export function storeRailWidth(width: number, storage: Storage | undefined = safeStorage()): void {
  try {
    storage?.setItem(STORAGE_KEY, String(Math.round(width)));
  } catch {
    // Nothing to do: the width is still correct for this session.
  }
}

// ---- section heights (the vertical splitters inside the rail) -------------

/** Enough for a heading plus a couple of rows; below this nothing is readable. */
export const SECTION_MIN_HEIGHT = 72;
export const SECTION_MAX_HEIGHT = 1200;

/**
 * A section height the rail can actually give. `null` means "as tall as its
 * content", which is the state every section starts in: heights only exist
 * once the user has dragged one, so the rail stays self-sizing until then.
 */
export function clampSectionHeight(desired: number): number {
  if (!Number.isFinite(desired)) return SECTION_MIN_HEIGHT;
  return Math.round(Math.min(Math.max(desired, SECTION_MIN_HEIGHT), SECTION_MAX_HEIGHT));
}

const SECTION_KEY = "code-ai.railSectionHeights";

/** Every dragged section height, by section id. Unreadable storage reads empty. */
export function readStoredSectionHeights(
  storage: Storage | undefined = safeStorage()
): Record<string, number> {
  try {
    const raw = storage?.getItem(SECTION_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: Record<string, number> = {};
    for (const [id, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof value === "number" && Number.isFinite(value)) out[id] = clampSectionHeight(value);
    }
    return out;
  } catch {
    return {};
  }
}

export function storeSectionHeights(
  heights: Record<string, number>,
  storage: Storage | undefined = safeStorage()
): void {
  try {
    storage?.setItem(SECTION_KEY, JSON.stringify(heights));
  } catch {
    // Same as the width: losing the preference is not worth failing a drag.
  }
}

function safeStorage(): Storage | undefined {
  try {
    return typeof localStorage === "undefined" ? undefined : localStorage;
  } catch {
    return undefined;
  }
}
