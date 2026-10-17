import * as React from "react";

/** A box on the screen, in physical pixels. */
export interface ScreenBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * Where the panel's corner is on the screen, in CSS pixels. A webview cannot
 * ask, but every pointer event carries the answer: where it is on the page and
 * where it is on the screen. Until the pointer first crosses the panel nobody
 * knows; after that each move keeps it right, the window moved or not.
 */
let corner: { x: number; y: number } | null = null;
const moved = new Set<() => void>();
if (typeof document !== "undefined") {
  document.addEventListener(
    "pointermove",
    (event) => {
      const at = { x: event.screenX - event.clientX, y: event.screenY - event.clientY };
      if (corner && corner.x === at.x && corner.y === at.y) return;
      corner = at;
      moved.forEach((hear) => hear());
    },
    true
  );
}

/** `selector`'s box on the screen, or null while it is not on it or the panel's corner is unknown. */
export function screenBox(selector: string): ScreenBox | null {
  const el = document.querySelector(selector);
  if (!corner || !el || document.visibilityState !== "visible") return null;
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  // Physical pixels: what the desktop's own window is measured in too.
  const scale = window.devicePixelRatio || 1;
  return {
    x: Math.round((corner.x + r.left) * scale),
    y: Math.round((corner.y + r.top) * scale),
    w: Math.round(r.width * scale),
    h: Math.round(r.height * scale),
  };
}

const same = (a: ScreenBox | null, b: ScreenBox | null) =>
  a === b || (!!a && !!b && a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h);

/**
 * While `active`, tell `send` where `selector` is on the screen whenever that
 * changes, and null once it is gone: the avatar loose on the desktop sits on
 * the logo while the home screen is up, as the panel's own does. Says whether
 * a seat is being given, so the screen can leave it empty for the avatar.
 */
export function useDesktopSeat(active: boolean, selector: string, send: (seat: ScreenBox | null) => void): boolean {
  const [given, setGiven] = React.useState(false);
  const tell = React.useRef(send);
  tell.current = send;

  React.useEffect(() => {
    let last: ScreenBox | null = null;
    let queued = 0;
    const measure = () => {
      queued = 0;
      const now = active ? screenBox(selector) : null;
      if (same(now, last)) return;
      last = now;
      tell.current(now);
      setGiven(now !== null);
    };
    const soon = () => {
      queued ||= requestAnimationFrame(measure);
    };
    if (!active) {
      // Left the screen with the seat: the avatar goes back to where it was.
      setGiven(false);
      return;
    }
    moved.add(soon);
    window.addEventListener("resize", soon);
    document.addEventListener("visibilitychange", soon);
    // The seat changes size once the logo makes way for the avatar.
    const el = document.querySelector(selector);
    const observer = el && typeof ResizeObserver !== "undefined" ? new ResizeObserver(soon) : null;
    if (el) observer?.observe(el);
    soon();
    return () => {
      cancelAnimationFrame(queued);
      moved.delete(soon);
      window.removeEventListener("resize", soon);
      document.removeEventListener("visibilitychange", soon);
      observer?.disconnect();
      if (last) tell.current(null);
    };
  }, [active, selector]);

  return given;
}
