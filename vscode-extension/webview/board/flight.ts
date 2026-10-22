// A card that changes lane flies there: the panel measures every card before a
// new board is drawn, and once it is, sends a copy of each moved card from its
// old place to its new one. The copy flies over everything, because a lane
// scrolls and clips what leaves it; the card itself shows again on landing.

const FLIGHT_MS = 760;

export type Rects = Map<string, DOMRect>;

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

/**
 * Fly each card in ``moved`` from where ``before`` saw it. ``landing`` names the
 * cards that arrived in Done: they flash and show their check when they land.
 */
export function flyCards(before: Rects, moved: string[], landing: Set<string>, root: ParentNode = document): void {
  for (const id of moved) {
    const element = root.querySelector<HTMLElement>(`[data-card="${CSS.escape(id)}"]`);
    const from = before.get(id);
    if (!element) continue;
    if (!from || reducedMotion() || typeof element.animate !== "function") {
      if (landing.has(id)) land(element);
      continue;
    }
    fly(element, from, landing.has(id));
  }
}

function fly(element: HTMLElement, from: DOMRect, landing: boolean): void {
  const to = element.getBoundingClientRect();
  const dx = from.left - to.left;
  const dy = from.top - to.top;
  if (Math.abs(dx) < 2 && Math.abs(dy) < 2) {
    if (landing) land(element);
    return;
  }
  const ghost = element.cloneNode(true) as HTMLElement;
  ghost.removeAttribute("data-card");
  ghost.classList.add("card-ghost");
  Object.assign(ghost.style, {
    position: "fixed",
    left: `${to.left}px`,
    top: `${to.top}px`,
    width: `${to.width}px`,
    height: `${to.height}px`,
    margin: "0",
    zIndex: "50",
  });
  document.body.appendChild(ghost);
  element.style.visibility = "hidden";
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
