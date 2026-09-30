// The request the top of the transcript belongs to, pinned over it once the
// request itself has scrolled away. A click scrolls back to it.

import * as React from "react";

import { blockToPin } from "./blocks";
import { t } from "./i18n";
import { UserAvatar } from "./messages";

/** The slice at the top of the transcript the pin covers. */
const PIN_ZONE = 56;
/** Room left above a request the pin scrolls back to. */
const LAND_GAP = 10;
/** Quiet for this long and a programmatic scroll has landed: Chromium 102
 * (VS Code 1.74) has no `scrollend`. The cap covers a scroll that never ends. */
const SETTLE_MS = 140;
const SETTLE_CAP_MS = 1500;
const FLASH_MS = 1100;

export interface PinTarget {
  /** The user block's key. */
  block: string;
  /** Its last message: the one shown, and the one a click lands on. */
  message: string;
}

/**
 * Tracks which request to pin. `onScroll` goes on the scroller; `pinnedRef` is
 * the follow-the-bottom flag, which a click has to drop or the next render
 * would take the scroll straight back down.
 */
export function usePromptPin(
  scrollRef: React.RefObject<HTMLDivElement>,
  pinnedRef: React.MutableRefObject<boolean>
): { target: PinTarget | null; onScroll: () => void; go: () => void } {
  const [target, setTarget] = React.useState<PinTarget | null>(null);
  const frame = React.useRef(0);
  // Set while a click scrolls back to a request: the pin stays down until it
  // lands, instead of flicking through every request it passes on the way.
  const landing = React.useRef(false);

  const measure = React.useCallback(() => {
    frame.current = 0;
    const el = scrollRef.current;
    if (!el || landing.current) return;
    const nodes = Array.from(el.querySelectorAll<HTMLElement>("[data-user-block]"));
    const index = blockToPin(
      nodes.map((node) => node.getBoundingClientRect()),
      el.getBoundingClientRect().top,
      PIN_ZONE
    );
    const node = index >= 0 ? nodes[index] : undefined;
    const next = node ? { block: node.dataset.userBlock ?? "", message: node.dataset.lastMessage ?? "" } : null;
    setTarget((prev) => (prev?.block === next?.block && prev?.message === next?.message ? prev : next));
  }, [scrollRef]);

  const schedule = React.useCallback(() => {
    if (!frame.current) frame.current = requestAnimationFrame(measure);
  }, [measure]);

  // Positions also move without a scroll: reasoning folding away, a tool card
  // opening, the panel resized. The scroller only exists in agent mode, so the
  // observer follows whichever element the ref holds after each render.
  const observed = React.useRef<{ el: HTMLElement; ro: ResizeObserver } | null>(null);
  React.useLayoutEffect(() => {
    const el = scrollRef.current;
    if (observed.current?.el === el) return;
    observed.current?.ro.disconnect();
    observed.current = null;
    if (!el) return;
    const ro = new ResizeObserver(schedule);
    ro.observe(el);
    const inner = el.querySelector(".transcript-inner");
    if (inner) ro.observe(inner);
    observed.current = { el, ro };
    schedule();
  });
  React.useEffect(
    () => () => {
      observed.current?.ro.disconnect();
      cancelAnimationFrame(frame.current);
    },
    []
  );

  const go = React.useCallback(() => {
    const el = scrollRef.current;
    if (!el || !target) return;
    const block = el.querySelector<HTMLElement>(`[data-user-block="${CSS.escape(target.block)}"]`);
    if (!block) return;
    const message = el.querySelector<HTMLElement>(`[data-user-msg="${CSS.escape(target.message)}"]`);
    // The whole request when it fits, else the message that was pinned.
    const fits = block.offsetHeight <= el.clientHeight - 2 * LAND_GAP;
    const anchor = fits || !message ? block : message;
    const top =
      el.scrollTop + anchor.getBoundingClientRect().top - el.getBoundingClientRect().top - LAND_GAP;
    pinnedRef.current = false;
    landing.current = true;
    setTarget(null);
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollTo({ top: Math.max(0, top), behavior: still ? "auto" : "smooth" });
    whenSettled(el, () => {
      landing.current = false;
      flash(message ?? block);
      schedule();
    });
  }, [scrollRef, pinnedRef, target, schedule]);

  return { target, onScroll: schedule, go };
}

/** Call `done` once `el` stops scrolling. */
function whenSettled(el: HTMLElement, done: () => void): void {
  let idle = 0;
  let cap = 0;
  const finish = () => {
    el.removeEventListener("scroll", onScroll);
    clearTimeout(idle);
    clearTimeout(cap);
    done();
  };
  const onScroll = () => {
    clearTimeout(idle);
    idle = window.setTimeout(finish, SETTLE_MS);
  };
  el.addEventListener("scroll", onScroll, { passive: true });
  idle = window.setTimeout(finish, SETTLE_MS);
  cap = window.setTimeout(finish, SETTLE_CAP_MS);
}

/** Mark where a click landed, briefly. */
function flash(node: HTMLElement): void {
  node.classList.remove("flash");
  void node.offsetWidth; // restarts the animation on a second click
  node.classList.add("flash");
  window.setTimeout(() => node.classList.remove("flash"), FLASH_MS);
}

export function PromptPin({
  target,
  text,
  avatar,
  onGo,
}: {
  target: PinTarget | null;
  text: string;
  avatar?: string;
  onGo: () => void;
}): JSX.Element {
  // The text stays while the pin fades out, so it never empties before it goes.
  const shown = React.useRef(text);
  if (target) shown.current = text;
  return (
    <div className="prompt-pin-anchor">
      <button
        type="button"
        className={`prompt-pin ${target ? "shown" : ""}`}
        onClick={onGo}
        tabIndex={target ? 0 : -1}
        aria-hidden={!target}
        title={t("Go to this message")}
      >
        <UserAvatar src={avatar} size={16} />
        <span className="prompt-pin-text">{shown.current}</span>
      </button>
    </div>
  );
}
