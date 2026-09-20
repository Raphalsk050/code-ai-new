import * as React from "react";

export type WidthClass = "xs" | "sm" | "md";

/**
 * The sidebar's own width, in three bands. A webview cannot see the window, and
 * the user drags this panel to whatever width they like, so every layout switch
 * in the UI keys off this instead of a media query.
 *
 *   xs  < 380  icon strip, drawer covers the chat
 *   sm  < 520  icon strip, drawer overlays at 280px
 *   md  >=520  rail docks as a column
 */
export function useContainerWidth<T extends HTMLElement>(): [React.RefObject<T>, WidthClass] {
  const ref = React.useRef<T>(null);
  const [band, setBand] = React.useState<WidthClass>("sm");

  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const classify = (w: number): WidthClass => (w < 380 ? "xs" : w < 520 ? "sm" : "md");
    setBand(classify(el.getBoundingClientRect().width));
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      setBand((prev) => {
        const next = classify(width);
        return next === prev ? prev : next;
      });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return [ref, band];
}
