// A card that changes lane flies there: the panel measures every card and
// sub-card before a new board is drawn, and once it is, sends a copy of each
// moved one from its old place to its new one. The copy flies over everything,
// because a lane scrolls and clips what leaves it; the card itself shows again
// on landing.

const FLIGHT_MS = 760;

export type Rects = Map<string, DOMRect>;

/** Copies of the cards about to move, as they look before the board changes: what flies. */
export function captureCards(ids: string[], root: ParentNode = document): Map<string, HTMLElement> {
  const copies = new Map<string, HTMLElement>();
  for (const id of ids) {
    const element = find(root, id);
    if (element) copies.set(id, element.cloneNode(true) as HTMLElement);
  }
  return copies;
}

export function measureCards(root: ParentNode = document): Rects {
  const rects: Rects = new Map();
  root.querySelectorAll<HTMLElement>("[data-card]").forEach((element) => {
    rects.set(element.dataset.card ?? "", element.getBoundingClientRect());
  });
  return rects;
}

function reducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// Cards pulled together leave one after another, in the order given: a task
// first, then its sub-tasks as their agents pick them up.
const STAGGER_MS = 380;
let nextStart = 0;

/**
 * Fly each card in ``moved`` from where ``before`` saw it, one after another.
 * ``landing`` names the cards that arrived in Done: they flash and show their
 * check when they land. A card waiting its turn stays where it was.
 */
export function flyCards(
  before: Rects,
  moved: string[],
  landing: Set<string>,
  copies: Map<string, HTMLElement> = new Map(),
  root: ParentNode = document
): void {
  for (const id of moved) {
    const element = find(root, id);
    const from = before.get(id);
    if (!element) continue;
    if (!from || reducedMotion() || typeof element.animate !== "function") {
      if (landing.has(id)) land(element);
      continue;
    }
    const now = performance.now();
    const start = Math.max(now, nextStart);
    nextStart = start + STAGGER_MS;
    // The card as it was flies; it shows as it is now once it lands.
    const ghost = makeGhost(copies.get(id) ?? element, from);
    element.style.visibility = "hidden";
    const go = () => {
      // Looked up again: the board may have been drawn anew while it waited.
      const current = find(root, id) ?? element;
      fly(current, from, landing.has(id), ghost);
    };
    if (start - now < 16) go();
    else setTimeout(go, start - now);
  }
}

function find(root: ParentNode, id: string): HTMLElement | null {
  return root.querySelector<HTMLElement>(`[data-card="${CSS.escape(id)}"]`);
}

/** A copy of the card, over everything, where it was. */
function makeGhost(element: HTMLElement, at: DOMRect): HTMLElement {
  const ghost = (element.isConnected ? element.cloneNode(true) : element) as HTMLElement;
  ghost.removeAttribute("data-card");
  ghost.classList.add("card-ghost");
  Object.assign(ghost.style, {
    position: "fixed",
    left: `${at.left}px`,
    top: `${at.top}px`,
    width: `${at.width}px`,
    height: `${at.height}px`,
    margin: "0",
    zIndex: "50",
    visibility: "visible",
  });
  document.body.appendChild(ghost);
  return ghost;
}

function fly(element: HTMLElement, from: DOMRect, landing: boolean, ghost: HTMLElement): void {
  element.style.visibility = "hidden";
  const to = element.getBoundingClientRect();
  const dx = from.left - to.left;
  const dy = from.top - to.top;
  if (Math.abs(dx) < 2 && Math.abs(dy) < 2) {
    ghost.remove();
    element.style.visibility = "";
    if (landing) land(element);
    return;
  }
  // The copy flies to where the card is now, its path drawn from where it was;
  // its size stays the old card's, which is what is seen leaving.
  Object.assign(ghost.style, { left: `${to.left}px`, top: `${to.top}px` });
  // A short lift, an arc over the lanes, and a settle: the card is picked up
  // and put down rather than slid.
  const tilt = dx > 0 ? -2.5 : 2.5;
  const animation = ghost.animate(
    [
      { transform: `translate(${dx}px, ${dy}px) rotate(0deg) scale(1)` },
      { transform: `translate(${dx}px, ${dy - 6}px) rotate(${tilt}deg) scale(1.04)`, offset: 0.12 },
      { transform: `translate(${dx * 0.45}px, ${dy * 0.45 - 28}px) rotate(${tilt / 2}deg) scale(1.05)`, offset: 0.55 },
      { transform: "translate(0px, -4px) rotate(0deg) scale(1.02)", offset: 0.88 },
      { transform: "translate(0px, 0px) rotate(0deg) scale(1)" },
    ],
    { duration: FLIGHT_MS, easing: "cubic-bezier(.45,.05,.3,1)" }
  );
  const done = () => {
    ghost.remove();
    element.style.visibility = "";
    if (landing) land(element);
  };
  animation.onfinish = done;
  animation.oncancel = done;
}

function land(element: HTMLElement): void {
  element.classList.remove("landed");
  // Read layout so the class added again restarts its animation.
  void element.offsetWidth;
  element.classList.add("landed");
  setTimeout(() => element.classList.remove("landed"), 900);
}
