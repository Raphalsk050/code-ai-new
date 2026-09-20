// Animation primitives for the webview: Web Animations API only, no library.
//
// Two constraints shape everything here. VS Code 1.74 ships Chromium 102, so
// `linear()` easings, View Transitions and `interpolate-size` are out - springs
// are approximated with an overshooting cubic-bezier. And a turn emits hundreds
// of events a second, so nothing animates per event: only mount, unmount,
// reorder and direct interaction.

import * as React from "react";

export const DUR = { fast: 120, base: 200, slow: 320, drawer: 240 } as const;

/** Decelerating, no overshoot. Panels, fades, height. */
export const EASE_OUT = "cubic-bezier(.22, 1, .36, 1)";
/** Slight overshoot, reads as a spring without a bounce loop. Chips, cards, pills. */
export const EASE_SPRING = "cubic-bezier(.34, 1.4, .52, 1)";
/** Symmetric, for things that leave. */
export const EASE_IN = "cubic-bezier(.4, 0, 1, 1)";

let reducedQuery: MediaQueryList | null = null;

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  if (!reducedQuery) reducedQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  return reducedQuery.matches;
}

/** `el.animate` that is a no-op under reduced motion and on detached nodes. */
export function animate(
  el: Element | null | undefined,
  frames: Keyframe[],
  options: KeyframeAnimationOptions
): Animation | null {
  if (!el || prefersReducedMotion() || typeof (el as any).animate !== "function") return null;
  return (el as HTMLElement).animate(frames, { fill: "both", ...options });
}

/**
 * Keep a node mounted through its exit animation.
 *
 * Returns `mounted` (render it at all) and `phase` ("enter" | "exit"), which the
 * caller puts on the element as a class so CSS drives the transition. Under
 * reduced motion the exit is instant.
 */
export function useMountTransition(
  open: boolean,
  duration = DUR.drawer
): { mounted: boolean; phase: "enter" | "exit" } {
  const [mounted, setMounted] = React.useState(open);
  const [phase, setPhase] = React.useState<"enter" | "exit">(open ? "enter" : "exit");

  React.useEffect(() => {
    if (open) {
      setMounted(true);
      // One frame with the exit class applied, so the enter transition has a
      // from-state to animate out of.
      const raf = requestAnimationFrame(() => setPhase("enter"));
      return () => cancelAnimationFrame(raf);
    }
    setPhase("exit");
    if (prefersReducedMotion()) {
      setMounted(false);
      return;
    }
    const timer = window.setTimeout(() => setMounted(false), duration);
    return () => window.clearTimeout(timer);
  }, [open, duration]);

  return { mounted, phase };
}

/** Play a one-shot enter animation when the node first appears. */
export function useEnter<T extends HTMLElement>(
  frames: Keyframe[] = [
    { opacity: 0, transform: "translateY(6px)" },
    { opacity: 1, transform: "none" },
  ],
  options: KeyframeAnimationOptions = { duration: DUR.base, easing: EASE_OUT }
): React.RefCallback<T> {
  const played = React.useRef(false);
  return React.useCallback(
    (el: T | null) => {
      if (!el || played.current) return;
      played.current = true;
      animate(el, frames, options);
    },
    // Frames and options are literals at the call site; re-running on identity
    // change would replay the animation on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );
}

/**
 * Height animation for a collapsible section. `height: auto` is not animatable
 * in Chromium 102, so the open height is measured and animated explicitly, then
 * released back to auto so the content can still grow.
 */
export function useCollapseHeight(open: boolean): React.RefObject<HTMLDivElement> {
  const ref = React.useRef<HTMLDivElement>(null);
  const first = React.useRef(true);

  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (first.current) {
      first.current = false;
      el.style.height = open ? "auto" : "0px";
      el.style.overflow = open ? "" : "hidden";
      return;
    }
    if (prefersReducedMotion()) {
      el.style.height = open ? "auto" : "0px";
      el.style.overflow = open ? "" : "hidden";
      return;
    }
    const from = el.getBoundingClientRect().height;
    el.style.height = "auto";
    const to = open ? el.scrollHeight : 0;
    el.style.height = `${from}px`;
    el.style.overflow = "hidden";
    const animation = animate(
      el,
      [{ height: `${from}px` }, { height: `${to}px` }],
      { duration: DUR.base, easing: EASE_OUT }
    );
    const settle = () => {
      el.style.height = open ? "auto" : "0px";
      el.style.overflow = open ? "" : "hidden";
      if (animation) animation.cancel();
    };
    if (animation) animation.onfinish = settle;
    else settle();
    return () => {
      if (animation) animation.cancel();
    };
  }, [open]);

  return ref;
}

/**
 * FLIP for a keyed list: rows that move to a new position slide there instead
 * of jumping. Register each row with the returned callback.
 */
export function useFlipList(): (key: string) => React.RefCallback<HTMLElement> {
  const rects = React.useRef(new Map<string, DOMRect>());
  const nodes = React.useRef(new Map<string, HTMLElement>());

  React.useLayoutEffect(() => {
    if (prefersReducedMotion()) return;
    nodes.current.forEach((el, key) => {
      const next = el.getBoundingClientRect();
      const prev = rects.current.get(key);
      rects.current.set(key, next);
      if (!prev) return;
      const dy = prev.top - next.top;
      if (Math.abs(dy) < 1) return;
      animate(el, [{ transform: `translateY(${dy}px)` }, { transform: "none" }], {
        duration: DUR.base,
        easing: EASE_OUT,
      });
    });
  });

  return React.useCallback(
    (key: string) => (el: HTMLElement | null) => {
      if (el) nodes.current.set(key, el);
      else {
        nodes.current.delete(key);
        rects.current.delete(key);
      }
    },
    []
  );
}

/**
 * Material-style press feedback: a circle expanding from the pointer. Attached
 * to the button's own pointerdown, so it costs nothing until it is pressed.
 */
export function ripple(event: React.PointerEvent<HTMLElement>): void {
  if (prefersReducedMotion()) return;
  const host = event.currentTarget;
  const rect = host.getBoundingClientRect();
  const size = Math.max(rect.width, rect.height) * 2;
  const dot = document.createElement("span");
  dot.className = "ripple";
  dot.style.width = dot.style.height = `${size}px`;
  dot.style.left = `${event.clientX - rect.left - size / 2}px`;
  dot.style.top = `${event.clientY - rect.top - size / 2}px`;
  host.appendChild(dot);
  const animation = animate(
    dot,
    [
      { transform: "scale(0)", opacity: 0.35 },
      { transform: "scale(1)", opacity: 0 },
    ],
    { duration: 420, easing: EASE_OUT }
  );
  if (animation) animation.onfinish = () => dot.remove();
  else dot.remove();
}

/** Pulse a value that changed (token meter, counters) without a layout shift. */
export function pulse(el: Element | null): void {
  animate(el, [{ transform: "scale(1)" }, { transform: "scale(1.08)" }, { transform: "scale(1)" }], {
    duration: DUR.slow,
    easing: EASE_SPRING,
  });
}
