// A part handed from one driver to another (a pose's loop, an act's script,
// the pointer's pull) would jump to wherever the new one has it this instant.
// Held just before the handover and blended just after, it goes there instead.

/** How long a handover takes, in ms. */
export const BLEND_MS = 360;
/** Away from where it was without a jolt, and onto the new motion without one. */
const EASE = "cubic-bezier(.4,0,.2,1)";
/** The parts the loops move. */
export const LOOPED = ".agent-av-rig, .agent-av-breath, .agent-av-eyes, .agent-av-eye, .agent-av-leaf";

export interface Held {
  parts: Element[];
  looks: { transform: string; opacity: string }[];
}

const running = new WeakMap<Element, Animation>();

/** How each part under `root` looks this instant. */
export function hold(root: ParentNode, selector = LOOPED): Held {
  const parts = Array.from(root.querySelectorAll(selector));
  // Read all before anything is written: one style recalculation for the lot.
  const looks = parts.map((part) => {
    const style = getComputedStyle(part);
    return { transform: style.transform, opacity: style.opacity };
  });
  return { parts, looks };
}

/**
 * Ease each held part from how it looked into whatever drives it now. The
 * missing end keyframe is the part's own value as it goes, so it lands on a
 * loop that is still running rather than on where the loop was.
 */
export function blend(held: Held, ms = BLEND_MS): void {
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
  // The new driver's loops start when style is next worked out, and one that
  // starts after the blend sits on top of it until the next frame: work the
  // style out first. A single read does it for the whole page.
  const first = held.parts.find((part) => part.isConnected);
  if (first) void getComputedStyle(first).transform;
  held.parts.forEach((part, i) => {
    if (!part.isConnected || typeof part.animate !== "function") return;
    // What was held already includes a blend still under way: start from there.
    running.get(part)?.cancel();
    running.set(part, part.animate([{ ...held.looks[i], offset: 0 }], { duration: ms, easing: EASE }));
  });
}

/** Hand the parts over across `change`, which must not wait for a later frame. */
export function across(root: ParentNode | null, change: () => void, selector = LOOPED): void {
  const held = root ? hold(root, selector) : null;
  change();
  if (held) blend(held);
}
