// Keeping a scroller on its newest line while text arrives, without ever
// taking the scroll out of the reader's hand.
//
// The old rule was a distance: within 80px of the end, snap to the end on every
// update. A reader who had scrolled up less than that - one notch of a trackpad
// - was dragged back by the next token, and had to out-scroll the stream to get
// away. Here the view follows until the reader moves up, by any means, and
// follows again only when they come back down to the end themselves.

import * as React from "react";

/** Scrolling down to within this of the end is coming back to it. */
export const REJOIN_PX = 48;

export interface ScrollStep {
  /** How far the scroll position moved; negative is up. */
  moved: number;
  /** How much is left below the viewport. */
  below: number;
  /** The content got shorter since the last look, which moves the view by itself. */
  shrunk: boolean;
  /** A pointer is down in the scroller: a scrollbar drag, or a selection. */
  held: boolean;
}

/** Whether the view still follows, after one scroll event. */
export function nextFollowing(following: boolean, step: ScrollStep): boolean {
  // At the very end there is nothing to be away from.
  if (step.below <= 1) return true;
  if (step.moved > 0 && step.below <= REJOIN_PX) return true;
  // Up, and not because the content under the view went away: the reader's
  // doing, whatever they used - the scrollbar, find-in-page, a middle click.
  if (step.moved < 0 && (step.held || !step.shrunk)) return false;
  return following;
}

const UP_KEYS = new Set(["ArrowUp", "PageUp", "Home"]);

function scrollsItself(node: HTMLElement): boolean {
  if (node.scrollHeight <= node.clientHeight + 1) return false;
  const overflow = getComputedStyle(node).overflowY;
  return overflow === "auto" || overflow === "scroll";
}

/** Whether an upward gesture at `target` is spent on a scroller inside `el`. */
function spentInside(target: EventTarget | null, el: HTMLElement): boolean {
  for (let node = target as HTMLElement | null; node && node !== el; node = node.parentElement) {
    if (!(node instanceof HTMLElement) || !scrollsItself(node)) continue;
    // One that keeps its overscroll never passes the gesture on, even at its top.
    if (node.scrollTop > 0 || getComputedStyle(node).overscrollBehaviorY !== "auto") return true;
  }
  return false;
}

function typesInto(target: HTMLElement): boolean {
  return target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
}

export interface Follow {
  /** Spread on the scroller. */
  handlers: {
    onScroll: () => void;
    onWheel: (event: React.WheelEvent) => void;
    onTouchStart: (event: React.TouchEvent) => void;
    onTouchMove: (event: React.TouchEvent) => void;
    onPointerDown: () => void;
  };
  /** The flag itself, for code that scrolls the view somewhere on purpose. */
  following: React.MutableRefObject<boolean>;
  /** The reader is somewhere else: offer the way back. */
  away: boolean;
  /** Back to the end, and following again. */
  rejoin: (smooth?: boolean) => void;
}

/**
 * @param active follow at all; false leaves the scroller where it is
 * @param page the panel's main scroller, the one Page Up and the arrows move
 */
export function useFollow(
  ref: React.RefObject<HTMLElement>,
  { active = true, page = false }: { active?: boolean; page?: boolean } = {}
): Follow {
  const following = React.useRef(true);
  const held = React.useRef(false);
  const last = React.useRef({ top: 0, height: 0 });
  const touchY = React.useRef(0);
  const [away, setAway] = React.useState(false);

  const set = React.useCallback((value: boolean) => {
    following.current = value;
    setAway(!value);
  }, []);

  const pin = React.useCallback(() => {
    const el = ref.current;
    if (!el) return;
    // Not under a pressed pointer: the text would slide out from under a
    // selection being dragged, or the scrollbar out of the hand holding it.
    if (active && following.current && !held.current) el.scrollTop = el.scrollHeight;
    last.current = { top: el.scrollTop, height: el.scrollHeight };
  }, [ref, active]);

  // The reader's intent is taken when they act, not when the scroll event
  // lands: by then the next token may already have pinned the view back.
  const letGo = React.useCallback(
    (target: EventTarget | null) => {
      const el = ref.current;
      if (el && el.scrollTop > 0 && !spentInside(target, el)) set(false);
    },
    [ref, set]
  );

  const onScroll = React.useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const top = el.scrollTop;
    const height = el.scrollHeight;
    const next = nextFollowing(following.current, {
      moved: top - last.current.top,
      below: height - top - el.clientHeight,
      shrunk: height < last.current.height,
      held: held.current,
    });
    last.current = { top, height };
    if (next !== following.current) set(next);
    // A flag set elsewhere (a jump to a request) reaches the button here.
    else setAway(!next);
  }, [ref, set]);

  const onWheel = React.useCallback(
    (event: React.WheelEvent) => {
      // Ctrl zooms and Shift scrolls sideways.
      if (event.deltaY < 0 && !event.ctrlKey && !event.shiftKey) letGo(event.target);
    },
    [letGo]
  );

  const onTouchStart = React.useCallback((event: React.TouchEvent) => {
    touchY.current = event.touches[0]?.clientY ?? 0;
  }, []);
  const onTouchMove = React.useCallback(
    (event: React.TouchEvent) => {
      // A finger moving down pulls the content down: scrolling up.
      if ((event.touches[0]?.clientY ?? 0) - touchY.current > 4) letGo(event.target);
    },
    [letGo]
  );

  const onPointerDown = React.useCallback(() => {
    held.current = true;
    const release = () => {
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
      held.current = false;
      pin();
    };
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
  }, [pin]);

  React.useEffect(() => {
    if (!page) return;
    const onKey = (event: KeyboardEvent) => {
      const el = ref.current;
      const up = UP_KEYS.has(event.key) || (event.key === " " && event.shiftKey);
      if (!el || !up || event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      // From inside the transcript, or from nowhere: a key in a menu or in
      // the composer is not a scroll.
      const here = !target || target === document.body || el.contains(target);
      if (here && !(target && typesInto(target))) letGo(target);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [ref, page, letGo]);

  // After every render: this is where new text makes the content taller.
  React.useLayoutEffect(pin);

  // And when it grows without one - an image loading, a card opening, the
  // panel resized. The scroller comes and goes with the screen it is on, so
  // the observer follows whichever element the ref holds.
  const observed = React.useRef<{ el: HTMLElement; ro: ResizeObserver } | null>(null);
  React.useLayoutEffect(() => {
    const el = ref.current;
    if (observed.current?.el === el) return;
    observed.current?.ro.disconnect();
    observed.current = null;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(pin);
    ro.observe(el);
    for (const child of Array.from(el.children)) ro.observe(child);
    observed.current = { el, ro };
  });
  React.useEffect(() => () => observed.current?.ro.disconnect(), []);

  const rejoin = React.useCallback(
    (smooth = false) => {
      const el = ref.current;
      set(true);
      if (!el) return;
      const still = !smooth || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      el.scrollTo({ top: el.scrollHeight, behavior: still ? "auto" : "smooth" });
    },
    [ref, set]
  );

  return { handlers: { onScroll, onWheel, onTouchStart, onTouchMove, onPointerDown }, following, away, rejoin };
}
